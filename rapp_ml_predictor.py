#!/usr/bin/env python3
"""
GreenRAN ML Predictor (Tempo Real)
====================================
ML predictor com acesso em tempo real ao banco de dados.

Características:
  - Consulta banco de dados a cada predição (últimos 2 min)
  - Override por banco quando BLOCKED ≥ 60%
  - Retreinamento automático 2x por dia
  - Sempre ativo e atualizado

Usage:
    from rapp_ml_predictor import MLPredictor
    predictor = MLPredictor(data_lake=data_lake_instance)
    prediction = predictor.predict_with_db_context(metrics_dict)
"""

import os
import time
import numpy as np
import joblib
from datetime import datetime

MODEL_DIR = os.path.join(os.path.dirname(__file__), 'models')

# Thresholds
DB_OVERRIDE_THRESHOLD = 0.60  # 60% para override
DB_BOOST_THRESHOLD = 0.60     # 60% para boost de confiança
WINDOW_MINUTES = 2            # Janela de consulta ao banco


class MLPredictor:
    """ML prediction module with real-time database access."""

    def __init__(self, model_dir=MODEL_DIR, data_lake=None):
        self.model_dir = model_dir
        self.data_lake = data_lake
        self.classifier = None
        self.regressor = None
        self.clf_scaler = None
        self.reg_scaler = None
        self.label_encoder = None
        self.loaded = False
        self.window_minutes = WINDOW_MINUTES
        self.feature_cols = [
            'cvar_ms',
            'cvar_diff',
            'cvar_rolling_mean',
            'cvar_rolling_std',
            'latency_p95_ms',
            'avg_latency_ms',
            'variance_ms2',
            'total_active_cameras',
            'total_active_ues',
            'camera_ratio',
            'total_critical_ues',
            'hour_sin',
            'hour_cos',
            'is_night',
            'is_weekend',
            'cvar_zone',
            'sim_time_s',
        ]
        self._cvar_history = []
        self._load_models()

    def _load_models(self):
        """Load trained models from disk."""
        try:
            self.classifier = joblib.load(os.path.join(self.model_dir, 'rf_classifier.joblib'))
            self.regressor = joblib.load(os.path.join(self.model_dir, 'rf_regressor.joblib'))
            self.clf_scaler = joblib.load(os.path.join(self.model_dir, 'rf_scaler.joblib'))
            self.reg_scaler = joblib.load(os.path.join(self.model_dir, 'reg_scaler.joblib'))
            self.label_encoder = joblib.load(os.path.join(self.model_dir, 'label_encoder.joblib'))
            self.loaded = True
            classes = list(self.label_encoder.classes_)
            print(f"[ML] Modelos carregados - Classes: {classes}")
        except FileNotFoundError as e:
            print(f"[ML] AVISO: Modelos não encontrados: {e}")
            print(f"[ML] Execute: python3 train_ml_model.py para treinar")
            self.loaded = False

    def query_database_stats(self, window_minutes=None):
        """
        Consulta estatísticas do banco em tempo real.

        Args:
            window_minutes: Janela de consulta (default: self.window_minutes)

        Returns:
            dict com distribuição de classes e totais
        """
        if window_minutes is None:
            window_minutes = self.window_minutes

        if not self.data_lake or not hasattr(self.data_lake, 'conn') or self.data_lake.conn is None:
            return None

        cutoff = int(time.time()) - (window_minutes * 60)

        try:
            cursor = self.data_lake.conn.execute("""
                SELECT d.decision, COUNT(*) as count
                FROM decisions_history d
                WHERE d.timestamp >= ?
                AND d.decision IS NOT NULL
                AND d.decision != ''
                GROUP BY d.decision
            """, (cutoff,))

            stats = {}
            total = 0
            for row in cursor.fetchall():
                if row[0]:
                    stats[row[0]] = row[1]
                    total += row[1]

            if total == 0:
                return None

            distribution = {
                k: round(v / total * 100, 1)
                for k, v in stats.items()
            }

            return {
                'distribution': distribution,
                'counts': stats,
                'total': total,
                'window_minutes': window_minutes,
                'timestamp': int(time.time())
            }
        except Exception as e:
            print(f"[ML] Erro ao consultar banco: {e}")
            return None

    def _prepare_features(self, metrics):
        """Prepare feature vector from metrics dict."""
        now = datetime.now()

        cvar_ms = float(metrics.get('cvar_per_ue_us', 0)) / 1000.0
        latency_p95_ms = float(metrics.get('latency_p95_per_ue_us', 0)) / 1000.0
        avg_latency_ms = float(metrics.get('global_avg_latency_us', 0)) / 1000.0
        variance_ms2 = float(metrics.get('variance_per_ue_us2', 0)) / 1_000_000.0
        total_cameras = int(metrics.get('total_active_cameras', 0))
        total_ues = int(metrics.get('total_active_ues', 1))
        total_critical = int(metrics.get('total_critical_ues', 0))

        self._cvar_history.append(cvar_ms)
        if len(self._cvar_history) > 10:
            self._cvar_history = self._cvar_history[-10:]

        cvar_diff = self._cvar_history[-1] - self._cvar_history[-2] if len(self._cvar_history) >= 2 else 0
        cvar_rolling_mean = np.mean(self._cvar_history) if self._cvar_history else cvar_ms
        cvar_rolling_std = np.std(self._cvar_history) if len(self._cvar_history) >= 2 else 0

        camera_ratio = total_cameras / max(total_ues, 1)

        hour = now.hour
        day_of_week = now.weekday()
        hour_sin = np.sin(2 * np.pi * hour / 24)
        hour_cos = np.cos(2 * np.pi * hour / 24)
        is_night = 1 if (hour >= 22 or hour < 6) else 0
        is_weekend = 1 if day_of_week >= 5 else 0

        if cvar_ms < 60:
            cvar_zone = 0
        elif cvar_ms < 80:
            cvar_zone = 1
        else:
            cvar_zone = 2

        sim_time_s = float(metrics.get('sim_time_s', 0))

        features = np.array([[
            cvar_ms, cvar_diff, cvar_rolling_mean, cvar_rolling_std,
            latency_p95_ms, avg_latency_ms, variance_ms2,
            total_cameras, total_ues, camera_ratio, total_critical,
            hour_sin, hour_cos, is_night, is_weekend, cvar_zone, sim_time_s,
        ]])

        return features

    def predict(self, metrics):
        """Basic ML prediction without database context."""
        if not self.loaded:
            return {
                'decision': None,
                'confidence': 0.0,
                'predicted_cvar_ms': None,
                'source': 'no_model',
                'error': 'Modelos não carregados'
            }

        features = self._prepare_features(metrics)

        features_scaled = self.clf_scaler.transform(features)
        pred_encoded = self.classifier.predict(features_scaled)[0]
        proba = self.classifier.predict_proba(features_scaled)[0]
        decision = self.label_encoder.inverse_transform([pred_encoded])[0]
        confidence = float(np.max(proba))

        reg_features_scaled = self.reg_scaler.transform(features)
        predicted_cvar = float(self.regressor.predict(reg_features_scaled)[0])

        importances = self.classifier.feature_importances_
        top_features = {}
        for feat, imp in sorted(zip(self.feature_cols, importances), key=lambda x: x[1], reverse=True)[:5]:
            top_features[feat] = round(float(imp), 4)

        return {
            'decision': decision,
            'confidence': confidence,
            'predicted_cvar_ms': round(predicted_cvar, 2),
            'feature_importance': top_features,
            'all_probabilities': {
                cls: round(float(p), 4)
                for cls, p in zip(self.label_encoder.classes_, proba)
            },
            'source': 'ml_only'
        }

    def predict_with_db_context(self, metrics):
        """
        Predição combinando ML + contexto do banco de dados.

        Fluxo:
        1. Predição do modelo ML
        2. Consulta banco (últimos 2 min)
        3. Se BLOCKED ≥ 60% no banco → OVERRIDE
        4. Se ALLOWED ≥ 60% no banco → OVERRIDE
        5. Caso contrário → usa predição ML

        Args:
            metrics: dict com métricas da rede

        Returns:
            dict com decisão, confiança, fonte, distribuição do banco
        """
        # 1. Predição do modelo ML
        ml_result = self.predict(metrics)

        # 2. Consulta banco de dados
        db_stats = self.query_database_stats()

        if not db_stats or db_stats['total'] == 0:
            ml_result['source'] = 'ml_only'
            ml_result['db_distribution'] = {}
            ml_result['db_total'] = 0
            return ml_result

        # 3. Distribuição de classes no banco
        distribution = db_stats['distribution']
        ml_result['db_distribution'] = distribution
        ml_result['db_total'] = db_stats['total']

        # 4. Verificar classes do modelo
        ml_classes = set(self.label_encoder.classes_) if self.label_encoder else set()
        db_classes = set(distribution.keys())

        # 5. OVERRIDE por banco
        for decision_class in ['BLOCKED', 'ALLOWED', 'CONDITIONAL']:
            if decision_class in distribution:
                pct = distribution[decision_class]

                # Se classe não existe no modelo mas existe no banco com ≥ 60%
                if decision_class not in ml_classes and pct >= DB_OVERRIDE_THRESHOLD * 100:
                    ml_result['decision'] = decision_class
                    ml_result['confidence'] = pct / 100
                    ml_result['source'] = 'database_override'
                    print(f"[ML] OVERRIDE: {decision_class}={pct}% no banco (classe não no modelo)")
                    return ml_result

                # Se classe existe no modelo e banco mostra ≥ 60%
                if pct >= DB_OVERRIDE_THRESHOLD * 100:
                    ml_result['decision'] = decision_class
                    ml_result['confidence'] = pct / 100
                    ml_result['source'] = 'database_override'
                    return ml_result

        # 6. BOOST de confiança se banco concorda
        if ml_result['decision'] in distribution:
            db_pct = distribution[ml_result['decision']]
            if db_pct >= DB_BOOST_THRESHOLD * 100:
                ml_result['confidence'] = min(0.95, ml_result['confidence'] * 1.2)
                ml_result['source'] = 'ml_boosted'

        return ml_result

    def is_loaded(self):
        """Check if models are loaded."""
        return self.loaded


# Standalone test
if __name__ == "__main__":
    predictor = MLPredictor()

    if predictor.is_loaded():
        test_metrics = {
            'cvar_per_ue_us': 45000,
            'latency_p95_per_ue_us': 50000,
            'global_avg_latency_us': 4000,
            'variance_per_ue_us2': 1000000,
            'total_active_cameras': 3,
            'total_active_ues': 20,
            'total_critical_ues': 0,
            'sim_time_s': 100,
        }

        result = predictor.predict(test_metrics)
        print("\nTeste de predição:")
        print(f"  Decisão: {result['decision']}")
        print(f"  Confiança: {result['confidence']:.4f}")
        print(f"  CVaR previsto: {result['predicted_cvar_ms']}ms")
        print(f"  Fonte: {result['source']}")
    else:
        print("Modelos não carregados. Execute: python3 train_ml_model.py")
