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
import json

# Corrigir caminho dos modelos - estão em /home/robert/orange_nuclear/models/, não em src/models/
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
MODEL_DIR = os.path.join(PROJECT_DIR, 'models')
CONFIG_DIR = os.path.join(PROJECT_DIR, 'config')

# Thresholds - ML PREDITIVA (defaults, will be overridden by config)
DB_OVERRIDE_THRESHOLD = 0.85  # 85% para override (só sobrescreve se maioria forte)
DB_BOOST_THRESHOLD = 0.80    # 80% para boost de confiança
WINDOW_MINUTES = 1           # Janela de consulta ao banco (1 min = mais atual)
CVAR_CRITICAL_THRESHOLD = 80 # ms - CVaR acima disso = BLOCKED
CVAR_WARNING_THRESHOLD = 60   # ms - CVaR acima disso = CONDITIONAL

# Novas thresholds para predição
PREDICTED_CVAR_BLOCKED = 80  # Se regressor prediz > 80ms = BLOCKED
PREDICTED_CVAR_WARNING = 60  # Se regressor prediz > 60ms = CONDITIONAL
KNOWN_SCENARIO_STAGES = [
    'allowed_bootstrap',
    'allowed_stable',
    'allowed_recovery',
    'camera_conditional',
    'camera_blocked',
    'vehicle_conditional',
    'vehicle_blocked',
    'app2_conditional',
    'app2_blocked',
]
LEGACY_FEATURE_COLS = [
    'cvar_ms',
    'cvar_diff',
    'cvar_rolling_mean',
    'cvar_rolling_std',
    'latency_p95_ms',
    'avg_latency_ms',
    'variance_ms2',
    'total_active_cameras',
    'camera_ratio',
    'total_critical_ues',
    'cvar_zone',
    'throughput_mbps',
    'packet_loss_rate',
    'jitter_ms',
    'tx_rx_ratio',
    'cvar_trend',
    'throughput_trend',
    'packet_loss_trend',
    'cvar_lag_1',
    'cvar_lag_2',
    'cvar_lag_3',
    'cvar_lag_4',
    'cvar_lag_5',
    'throughput_lag_1',
    'throughput_lag_2',
    'throughput_lag_3',
    'packet_loss_lag_1',
    'packet_loss_lag_2',
    'packet_loss_lag_3',
    'latency_lag_1',
    'latency_lag_2',
    'cvar_rolling_3',
    'cvar_rolling_10',
    'cvar_rolling_std_3',
    'cvar_acceleration',
    'latency_acceleration',
    'jitter_trend',
    'cvar_momentum',
    'critical_ue_ratio',
]


def _normalize_stage_name(value):
    stage = str(value or '').strip()
    return stage if stage in KNOWN_SCENARIO_STAGES else 'unknown'


def _compute_vehicle_pressure(metrics):
    """Collapse vehicle state into pressure terms compatible with the legacy ML feature space."""
    active_vehicles = int(metrics.get('total_active_vehicles', metrics.get('vehicle_total', 0)) or 0)
    high_risk = int(metrics.get('vehicle_high_risk', 0) or 0)
    medium_risk = int(metrics.get('vehicle_medium_risk', 0) or 0)
    degraded = int(metrics.get('vehicle_degraded_autonomy', 0) or 0)
    max_latency_ms = float(metrics.get('vehicle_max_latency_ms', 0.0) or 0.0)
    max_packet_loss_percent = float(metrics.get('vehicle_max_packet_loss_percent', 0.0) or 0.0)

    if active_vehicles <= 0:
        return {
            'active_vehicles': 0,
            'virtual_critical_ues': 0,
            'latency_ms': 0.0,
            'packet_loss_percent': 0.0,
        }

    virtual_critical_ues = (high_risk * 3) + (degraded * 2) + medium_risk
    if max_latency_ms >= 100.0:
        virtual_critical_ues += 2
    elif max_latency_ms >= 50.0:
        virtual_critical_ues += 1
    if max_packet_loss_percent >= 5.0:
        virtual_critical_ues += 2
    elif max_packet_loss_percent >= 2.0:
        virtual_critical_ues += 1

    return {
        'active_vehicles': active_vehicles,
        'virtual_critical_ues': virtual_critical_ues,
        'latency_ms': max_latency_ms,
        'packet_loss_percent': max_packet_loss_percent,
    }


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
        
        # Load thresholds from config first
        self._load_thresholds()
        
        # D) Detecção de Cenários Críticos - Histórico para análise de trends
        self._cvar_trend_history = []  # Histórico de trends para detecção de padrões
        self._consecutive_increase_count = 0  # Contador de aumentos consecutivos
        self._last_cvar_trend = 0  # último trend registrado
        self._critical_zone_count = 0  # Contador de vezes na zona de cautela
        
        self.feature_cols = list(LEGACY_FEATURE_COLS)
        
        # Histórico para cálculo de trend e lag features causais
        self._cvar_history = []
        self._prev_throughput = 0
        self._prev_packet_loss = 0
        self._prev_jitter = 0
        self._prev_latency = 0
        self._prev_latency_diff = 0
        self._prev_cvar_diff = 0
        # Histórico adicional para lag features
        self._throughput_history = []
        self._packet_loss_history = []
        self._latency_history = []
        self._load_models()
    
    def _load_thresholds(self):
        """Load thresholds from configuration file."""
        config_path = os.path.join(CONFIG_DIR, 'ml_thresholds.json')
        try:
            with open(config_path, 'r') as f:
                config = json.load(f)
            
            # Update instance attributes from config, fallback to class-level defaults if not present
            self.DB_OVERRIDE_THRESHOLD = config.get('db_override_threshold', DB_OVERRIDE_THRESHOLD)
            self.DB_BOOST_THRESHOLD = config.get('db_boost_threshold', DB_BOOST_THRESHOLD)
            self.CVAR_CRITICAL_THRESHOLD = config.get('cvar_critical_ms', CVAR_CRITICAL_THRESHOLD)
            self.CVAR_WARNING_THRESHOLD = config.get('cvar_warning_ms', CVAR_WARNING_THRESHOLD)
            self.PREDICTED_CVAR_BLOCKED = config.get('predicted_cvar_blocked', PREDICTED_CVAR_BLOCKED)
            self.PREDICTED_CVAR_WARNING = config.get('predicted_cvar_warning', PREDICTED_CVAR_WARNING)
            
            print(f"[ML] Thresholds carregados de {config_path}")
        except FileNotFoundError:
            print(f"[ML] AVISO: Arquivo de config não encontrado em {config_path}, usando padrões")
        except json.JSONDecodeError as e:
            print(f"[ML] ERRO: Falha ao decodificar JSON em {config_path}: {e}")
        except Exception as e:
            print(f"[ML] ERRO: Falha ao carregar thresholds: {e}")
    
    def reset_history(self):
        """Reset histórico para nova simulação.
        
        Chamado quando sim_time diminui (nova simulação começou).
        Limpa todos os históricos para evitar dados contaminados.
        """
        self._cvar_history = []
        self._throughput_history = []
        self._packet_loss_history = []
        self._latency_history = []
        self._prev_throughput = 0
        self._prev_packet_loss = 0
        self._prev_jitter = 0
        self._prev_latency = 0
        self._prev_latency_diff = 0
        self._prev_cvar_diff = 0
        # D) Reset dos históricos de detecção de cenários críticos
        self._cvar_trend_history = []
        self._consecutive_increase_count = 0
        self._last_cvar_trend = 0
        self._critical_zone_count = 0
        print("[ML] Histórico resetado para nova simulação")

    def _load_models(self):
        """Load trained models from disk."""
        try:
            best_classifier_path = os.path.join(self.model_dir, 'best_classifier.joblib')
            legacy_classifier_path = os.path.join(self.model_dir, 'rf_classifier.joblib')
            classifier_path = best_classifier_path if os.path.exists(best_classifier_path) else legacy_classifier_path
            self.classifier = joblib.load(classifier_path)
            self.regressor = joblib.load(os.path.join(self.model_dir, 'rf_regressor.joblib'))
            self.clf_scaler = joblib.load(os.path.join(self.model_dir, 'rf_scaler.joblib'))
            self.reg_scaler = joblib.load(os.path.join(self.model_dir, 'reg_scaler.joblib'))
            self.label_encoder = joblib.load(os.path.join(self.model_dir, 'label_encoder.joblib'))
            self.feature_cols = self._load_runtime_feature_cols()
            self.loaded = True
            classes = list(self.label_encoder.classes_)
            print(
                f"[ML] Modelos carregados - Classes: {classes} | "
                f"classifier={os.path.basename(classifier_path)} | features={len(self.feature_cols)}"
            )
        except FileNotFoundError as e:
            print(f"[ML] AVISO: Modelos não encontrados: {e}")
            print(f"[ML] Execute: python3 train_ml_model.py para treinar")
            self.loaded = False

    def _load_runtime_feature_cols(self):
        """Load the feature schema expected by the active model."""
        report_path = os.path.join(self.model_dir, 'training_report.json')
        try:
            with open(report_path, 'r', encoding='utf-8') as f:
                report = json.load(f)
            features = report.get('features')
            if isinstance(features, list) and features:
                return [str(feature) for feature in features]
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass
        return list(LEGACY_FEATURE_COLS)

    def _resolve_stage_name(self, metrics):
        return _normalize_stage_name(
            metrics.get('scenario_stage') or metrics.get('collection_event_stage_name')
        )

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
        vehicle_pressure = _compute_vehicle_pressure(metrics)
        cvar_ms = float(metrics.get('cvar_per_ue_us', 0)) / 1000.0
        cvar_p95_ms = float(metrics.get('cvar_p95_us', 0)) / 1000.0  # P95 = pior 5% dos UEs
        latency_p95_ms = float(metrics.get('latency_p95_per_ue_us', 0)) / 1000.0
        avg_latency_ms = float(metrics.get('global_avg_latency_us', 0)) / 1000.0
        variance_ms2 = float(metrics.get('variance_per_ue_us2', 0)) / 1_000_000.0
        total_cameras = int(metrics.get('total_active_cameras', 0))
        total_ues = int(metrics.get('total_active_ues', 1))
        total_critical = int(metrics.get('total_critical_ues', 0))

        if vehicle_pressure['active_vehicles'] > 0:
            latency_p95_ms = max(latency_p95_ms, vehicle_pressure['latency_ms'])
            avg_latency_ms = max(avg_latency_ms, vehicle_pressure['latency_ms'])
            total_critical += vehicle_pressure['virtual_critical_ues']

        # Usar P95 (85.4ms) como cvar se disponível, senão usar cvar médio
        # P95 representa o pior caso - mais importante para decisões
        effective_cvar = cvar_p95_ms if cvar_p95_ms > cvar_ms else cvar_ms

        cvar_history_preview = (self._cvar_history + [effective_cvar])[-10:]
        cvar_diff = cvar_history_preview[-1] - cvar_history_preview[-2] if len(cvar_history_preview) >= 2 else 0
        cvar_rolling_mean = np.mean(cvar_history_preview) if cvar_history_preview else effective_cvar
        cvar_rolling_std = np.std(cvar_history_preview) if len(cvar_history_preview) >= 2 else 0

        camera_ratio = total_cameras / max(total_ues, 1)

        if cvar_ms < 60:
            cvar_zone = 0
        elif cvar_ms < 80:
            cvar_zone = 1
        else:
            cvar_zone = 2

        # Features de rede (5)
        throughput_kbps = float(metrics.get('throughput_kbps', 0)) / 1000.0  # Converter para Mbps
        packet_loss_rate = float(metrics.get('packet_loss_rate', 0)) * 100   # Porcentagem
        packet_loss_rate = max(packet_loss_rate, vehicle_pressure['packet_loss_percent'])
        jitter_ms = float(metrics.get('jitter_ms', 0))
        tx_rx_ratio = float(metrics.get('tx_rx_ratio', 0))
        prev_cvar_diff = self._prev_cvar_diff

        # Usar o histórico ANTERIOR ao ciclo atual para manter alinhamento causal
        # com o pipeline de treino.
        cvar_history_prev = self._cvar_history[-10:]
        throughput_history_prev = self._throughput_history[-10:]
        packet_loss_history_prev = self._packet_loss_history[-10:]
        latency_history_prev = self._latency_history[-10:]

        cvar_history_preview = (cvar_history_prev + [effective_cvar])[-10:]
        cvar_diff = effective_cvar - cvar_history_prev[-1] if cvar_history_prev else 0
        cvar_rolling_mean = np.mean(cvar_history_preview)
        cvar_rolling_std = np.std(cvar_history_preview) if len(cvar_history_preview) >= 2 else 0

        cvar_lag_1 = cvar_history_prev[-1] if len(cvar_history_prev) >= 1 else effective_cvar
        cvar_lag_2 = cvar_history_prev[-2] if len(cvar_history_prev) >= 2 else cvar_lag_1
        cvar_lag_3 = cvar_history_prev[-3] if len(cvar_history_prev) >= 3 else cvar_lag_2
        cvar_lag_4 = cvar_history_prev[-4] if len(cvar_history_prev) >= 4 else cvar_lag_3
        cvar_lag_5 = cvar_history_prev[-5] if len(cvar_history_prev) >= 5 else cvar_lag_4

        throughput_lag_1 = throughput_history_prev[-1] if len(throughput_history_prev) >= 1 else throughput_kbps
        throughput_lag_2 = throughput_history_prev[-2] if len(throughput_history_prev) >= 2 else throughput_lag_1
        throughput_lag_3 = throughput_history_prev[-3] if len(throughput_history_prev) >= 3 else throughput_lag_2

        packet_loss_lag_1 = packet_loss_history_prev[-1] if len(packet_loss_history_prev) >= 1 else packet_loss_rate
        packet_loss_lag_2 = packet_loss_history_prev[-2] if len(packet_loss_history_prev) >= 2 else packet_loss_lag_1
        packet_loss_lag_3 = packet_loss_history_prev[-3] if len(packet_loss_history_prev) >= 3 else packet_loss_lag_2

        latency_lag_1 = latency_history_prev[-1] if len(latency_history_prev) >= 1 else avg_latency_ms
        latency_lag_2 = latency_history_prev[-2] if len(latency_history_prev) >= 2 else latency_lag_1

        cvar_rolling_3 = np.mean(cvar_history_preview[-3:]) if len(cvar_history_preview) >= 3 else effective_cvar
        cvar_rolling_10 = np.mean(cvar_history_preview)
        cvar_rolling_std_3 = np.std(cvar_history_preview[-3:]) if len(cvar_history_preview) >= 3 else 0

        cvar_acceleration = cvar_diff - prev_cvar_diff if cvar_history_prev else 0
        
        # NOVAS FEATURES PREDITIVAS (adicionais):
        # 1. jitter_trend - tendência do jitter
        jitter_trend = jitter_ms - (self._prev_jitter if hasattr(self, '_prev_jitter') else jitter_ms)
        self._prev_jitter = jitter_ms
        
        # 2. latency_acceleration - aceleração da latência
        latency_diff = avg_latency_ms - (self._prev_latency if hasattr(self, '_prev_latency') else avg_latency_ms)
        latency_acceleration = latency_diff - (self._prev_latency_diff if hasattr(self, '_prev_latency_diff') else 0)
        self._prev_latency = avg_latency_ms
        self._prev_latency_diff = latency_diff
        
        # 3. cvar_momentum - momento do CVaR (força da mudança)
        cvar_momentum = cvar_diff * cvar_acceleration if cvar_diff != 0 else 0
        
        # 4. critical_ue_ratio - proporção de UEs críticos
        critical_ue_ratio = total_critical / max(total_ues, 1)
        
        # ============================================================
        # COMPLETAR TODAS AS FEATURES exatamente como no treino.
        # ============================================================
        # Features de rede
        throughput_mbps = throughput_kbps
        packet_loss_rate_pct = packet_loss_rate
        jitter_ms_val = jitter_ms
        tx_rx_ratio_val = tx_rx_ratio
        
        # Features de TREND (3)
        cvar_trend = 0
        if len(cvar_history_prev) >= 5:
            cvar_trend = effective_cvar - cvar_history_prev[-5]
        
        throughput_trend = throughput_kbps - self._prev_throughput
        self._prev_throughput = throughput_kbps
        
        packet_loss_trend = packet_loss_rate - self._prev_packet_loss
        self._prev_packet_loss = packet_loss_rate
        
        # Acceleration (2)
        cvar_acceleration = cvar_diff - prev_cvar_diff if cvar_history_prev else 0
        self._prev_cvar_diff = cvar_diff

        # Atualizar históricos APÓS construir as features do ciclo atual.
        self._cvar_history.append(effective_cvar)
        self._throughput_history.append(throughput_kbps)
        self._packet_loss_history.append(packet_loss_rate)
        self._latency_history.append(avg_latency_ms)

        if len(self._cvar_history) > 10:
            self._cvar_history = self._cvar_history[-10:]
            self._throughput_history = self._throughput_history[-10:]
            self._packet_loss_history = self._packet_loss_history[-10:]
            self._latency_history = self._latency_history[-10:]
        
        scenario_stage = self._resolve_stage_name(metrics)
        feature_values = {
            'cvar_ms': cvar_ms,
            'cvar_diff': cvar_diff,
            'cvar_rolling_mean': cvar_rolling_mean,
            'cvar_rolling_std': cvar_rolling_std,
            'latency_p95_ms': latency_p95_ms,
            'avg_latency_ms': avg_latency_ms,
            'variance_ms2': variance_ms2,
            'total_active_cameras': total_cameras,
            'camera_ratio': camera_ratio,
            'total_critical_ues': total_critical,
            'cvar_zone': cvar_zone,
            'throughput_mbps': throughput_mbps,
            'packet_loss_rate': packet_loss_rate_pct,
            'jitter_ms': jitter_ms_val,
            'tx_rx_ratio': tx_rx_ratio_val,
            'cvar_trend': cvar_trend,
            'throughput_trend': throughput_trend,
            'packet_loss_trend': packet_loss_trend,
            'cvar_lag_1': cvar_lag_1,
            'cvar_lag_2': cvar_lag_2,
            'cvar_lag_3': cvar_lag_3,
            'cvar_lag_4': cvar_lag_4,
            'cvar_lag_5': cvar_lag_5,
            'throughput_lag_1': throughput_lag_1,
            'throughput_lag_2': throughput_lag_2,
            'throughput_lag_3': throughput_lag_3,
            'packet_loss_lag_1': packet_loss_lag_1,
            'packet_loss_lag_2': packet_loss_lag_2,
            'packet_loss_lag_3': packet_loss_lag_3,
            'latency_lag_1': latency_lag_1,
            'latency_lag_2': latency_lag_2,
            'cvar_rolling_3': cvar_rolling_3,
            'cvar_rolling_10': cvar_rolling_10,
            'cvar_rolling_std_3': cvar_rolling_std_3,
            'cvar_acceleration': cvar_acceleration,
            'latency_acceleration': latency_acceleration,
            'jitter_trend': jitter_trend,
            'cvar_momentum': cvar_momentum,
            'critical_ue_ratio': critical_ue_ratio,
            'stage_unknown': 1.0 if scenario_stage == 'unknown' else 0.0,
        }
        for stage_name in KNOWN_SCENARIO_STAGES:
            feature_values[f'stage_{stage_name}'] = 1.0 if scenario_stage == stage_name else 0.0

        ordered_features = [float(feature_values.get(feature_name, 0.0)) for feature_name in self.feature_cols]
        return np.array([ordered_features], dtype=float)

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

        top_features = {}
        importances = getattr(self.classifier, 'feature_importances_', None)
        if importances is not None:
            for feat, imp in sorted(zip(self.feature_cols, importances), key=lambda x: x[1], reverse=True)[:5]:
                top_features[feat] = round(float(imp), 4)

        return {
            'decision': decision,
            'classifier_decision': decision,
            'raw_classifier_decision': decision,
            'confidence': confidence,
            'predicted_cvar_ms': round(predicted_cvar, 2),
            'raw_predicted_cvar_ms': round(predicted_cvar, 2),
            'feature_importance': top_features,
            'all_probabilities': {
                cls: round(float(p), 4)
                for cls, p in zip(self.label_encoder.classes_, proba)
            },
            'source': 'ml_only'
        }

    def predict_with_db_context(self, metrics):
        """
        Predição combinando ML + contexto.

        Fluxo:
        1. Estimar o estado atual via regressor/classifier
        2. Aplicar guardas de saúde/tendência
        3. Consultar banco só como validação contextual
        
        Args:
            metrics: dict com métricas da rede
            
        Returns:
            dict com decisão, confiança, fonte, distribuição do banco
        """
        # 1. Predição do regressor (PRÉ-DIZ O FUTURO!)
        ml_result = self.predict(metrics)
        
        raw_predicted_cvar = float(ml_result.get('raw_predicted_cvar_ms', ml_result.get('predicted_cvar_ms', 0)) or 0)
        predicted_cvar = raw_predicted_cvar
        current_cvar = float(metrics.get('cvar_per_ue_us', 0)) / 1000.0
        raw_classifier_decision = ml_result.get('raw_classifier_decision', ml_result.get('classifier_decision', ml_result.get('decision')))
        classifier_decision = raw_classifier_decision
        all_probabilities = ml_result.get('all_probabilities', {}) or {}
        blocked_probability = float(all_probabilities.get('BLOCKED', 0.0) or 0.0)
        conditional_probability = float(all_probabilities.get('CONDITIONAL', 0.0) or 0.0)

        # === FASE 2: Se current CVaR (P95) já está crítico ===
        cvar_p95_ms = float(metrics.get('cvar_p95_us', 0)) / 1000.0
        current_cvar_p95 = max(cvar_p95_ms, current_cvar)  # Usar o pior

        # Correção explícita do classifier em rede claramente saudável.
        # Mantém o valor bruto para observabilidade, mas evita que a trilha do
        # classifier continue artificialmente conservadora quando o estado real
        # e o regressor estão alinhados no regime saudável.
        if (
            raw_classifier_decision != 'ALLOWED'
            and current_cvar_p95 < 10.0
            and raw_predicted_cvar < 15.0
            and blocked_probability < 0.30
        ):
            classifier_decision = 'ALLOWED'
            ml_result['classifier_decision'] = 'ALLOWED'
            ml_result['decision'] = 'ALLOWED'
            ml_result['confidence'] = max(float(ml_result.get('confidence', 0.0) or 0.0), 0.55)
            ml_result['source'] = 'classifier_healthy_correction'
            ml_result['reason'] = (
                f'Classifier bruto={raw_classifier_decision} corrigido para ALLOWED '
                f'em rede saudável (CVaR/P95={current_cvar_p95:.1f}ms, regressor={raw_predicted_cvar:.1f}ms)'
            )
        else:
            ml_result['classifier_decision'] = classifier_decision

        # === D) DETECÇÃO DE CENÁRIOS CRÍTICOS ===
        # Analisar trend de CVaR para detecção precoce e anexar ao resultado
        # antes de qualquer decisão de retorno precoce.
        def _apply_warning_logic(cvar_for_warning):
            ml_result.pop('warning', None)
            ml_result.pop('trend_info', None)

            if cvar_trend > 10:
                ml_result['warning'] = 'RAPID_DETERIORATION'
                ml_result['trend_info'] = f'CVaR subiu {cvar_trend:.1f}ms em 5 ciclos'
                print(f"\033[1;33m[ML] ⚠️ ALERTA: Deterioração rápida! CVaR subiu {cvar_trend:.1f}ms\033[0m")

            warning_threshold = self.CVAR_WARNING_THRESHOLD
            critical_threshold = self.CVAR_CRITICAL_THRESHOLD
            if warning_threshold <= cvar_for_warning < critical_threshold:
                self._critical_zone_count += 1
                if self._critical_zone_count >= 3:
                    ml_result['warning'] = 'NEAR_CRITICAL_ZONE'
                    ml_result['trend_info'] = (
                        f'CVaR previsto {cvar_for_warning:.1f}ms está entre '
                        f'{warning_threshold}ms e {critical_threshold}ms por {self._critical_zone_count} ciclos'
                    )
                    print(
                        f"\033[1;33m[ML] ⚠️ ALERTA: Zona de cautela! "
                        f'CVaR previsto={cvar_for_warning:.1f}ms próximo ao crítico\033[0m'
                    )
            else:
                self._critical_zone_count = max(0, self._critical_zone_count - 1)

            if cvar_trend > self._last_cvar_trend and self._last_cvar_trend > 0:
                self._consecutive_increase_count += 1
            else:
                self._consecutive_increase_count = 0

            self._last_cvar_trend = cvar_trend

            if self._consecutive_increase_count >= 3:
                ml_result['warning'] = 'ACCUMULATING_RISK'
                ml_result['trend_info'] = f'{self._consecutive_increase_count} aumentos consecutivos de CVaR'
                print(
                    f"\033[1;31m[ML] 🔴 ALERTA: Risco acumulado! "
                    f'{self._consecutive_increase_count} ciclos de aumento\033[0m'
                )

        cvar_trend = 0
        if len(self._cvar_history) >= 5:
            cvar_trend = self._cvar_history[-1] - self._cvar_history[-5]
        
        # Armazenar no histórico de trends
        self._cvar_trend_history.append(cvar_trend)
        if len(self._cvar_trend_history) > 10:
            self._cvar_trend_history = self._cvar_trend_history[-10:]
        
        # Adicionar info de trend ao resultado (SEMPRE, mesmo após early returns)
        ml_result['cvar_trend'] = cvar_trend
        ml_result['trend_history'] = self._cvar_trend_history.copy()
        _apply_warning_logic(predicted_cvar)

        # Guarda forte para rede claramente saudável: se o classificador está em
        # ALLOWED com confiança razoável e o estado atual está muito saudável,
        # não deixamos o regressor sozinho empurrar para BLOCKED/CONDITIONAL.
        classifier_confidence = float(ml_result.get('confidence', 0.0) or 0.0)
        if (
            classifier_decision == 'ALLOWED'
            and classifier_confidence >= 0.50
            and current_cvar_p95 < 10.0
            and raw_predicted_cvar >= self.CVAR_WARNING_THRESHOLD
        ):
            calibrated_cvar = max(current_cvar_p95 * 1.8, 12.0)
            predicted_cvar = min(raw_predicted_cvar, calibrated_cvar)
            ml_result['predicted_cvar_ms'] = round(predicted_cvar, 2)
            ml_result['raw_classifier_decision'] = classifier_decision
            ml_result['decision'] = 'ALLOWED'
            ml_result['classifier_decision'] = 'ALLOWED'
            ml_result['confidence'] = max(classifier_confidence * 0.9, 0.60)
            ml_result['source'] = 'healthy_classifier_guard'
            ml_result['reason'] = (
                f'Classifier ALLOWED + rede saudável '
                f'(CVaR/P95={current_cvar_p95:.1f}ms) calibra regressor bruto={raw_predicted_cvar:.1f}ms '
                f'para {predicted_cvar:.1f}ms'
            )
            _apply_warning_logic(predicted_cvar)
            return ml_result

        # Se o classificador segue vendo rede saudável/normal e a probabilidade de
        # BLOCKED é baixa, reduzimos o peso do regressor em regimes não críticos.
        if (
            classifier_decision == 'ALLOWED'
            and classifier_confidence >= 0.50
            and blocked_probability <= 0.18
            and conditional_probability <= 0.40
            and current_cvar_p95 < 20.0
            and raw_predicted_cvar >= self.CVAR_WARNING_THRESHOLD
        ):
            calibrated_cvar = max(current_cvar_p95 * 2.2, 18.0)
            predicted_cvar = min(raw_predicted_cvar, calibrated_cvar)
            ml_result['predicted_cvar_ms'] = round(predicted_cvar, 2)
            ml_result['raw_classifier_decision'] = classifier_decision
            ml_result['decision'] = 'ALLOWED' if predicted_cvar < self.CVAR_WARNING_THRESHOLD else 'CONDITIONAL'
            ml_result['classifier_decision'] = ml_result['decision']
            ml_result['confidence'] = max(classifier_confidence * 0.85, 0.58)
            ml_result['source'] = 'classifier_regressor_reconciliation'
            ml_result['reason'] = (
                f'Classifier saudável reconciliou regressor bruto={raw_predicted_cvar:.1f}ms '
                f'para {predicted_cvar:.1f}ms'
            )
            _apply_warning_logic(predicted_cvar)
            return ml_result

        # === FASE 1: SE REGRESSOR DIZ QUE VAI SER CRÍTICO ===
        if predicted_cvar >= self.CVAR_CRITICAL_THRESHOLD:
            ml_result['decision'] = 'BLOCKED'
            ml_result['confidence'] = 0.95
            ml_result['source'] = 'regressor_prediction'
            ml_result['reason'] = f'CVaR previsto={predicted_cvar:.1f}ms ≥ {self.CVAR_CRITICAL_THRESHOLD}ms'
            return ml_result

        # === FASE 2: SE REGRESSOR DIZ QUE VAI TER PROBLEMA ===
        if predicted_cvar >= self.CVAR_WARNING_THRESHOLD:
            ml_result['decision'] = 'CONDITIONAL'
            ml_result['confidence'] = 0.85
            ml_result['source'] = 'regressor_warning'
            ml_result['reason'] = f'CVaR previsto={predicted_cvar:.1f}ms ≥ {self.CVAR_WARNING_THRESHOLD}ms'
            return ml_result

        # === FASE 3: Se current CVaR (P95) já está crítico ===
        if current_cvar_p95 >= self.CVAR_CRITICAL_THRESHOLD:
            ml_result['decision'] = 'BLOCKED'
            ml_result['confidence'] = 0.90
            ml_result['source'] = 'current_cvar_critical'
            ml_result['reason'] = f'CVaR P95={current_cvar_p95:.1f}ms ≥ {self.CVAR_CRITICAL_THRESHOLD}ms'
            return ml_result

        # Regime saudável: o regressor manda mais que o classificador.
        healthy_limit = min(self.CVAR_WARNING_THRESHOLD * 0.55, 35.0)
        if predicted_cvar < healthy_limit and current_cvar_p95 < healthy_limit:
            if ml_result.get('warning') in {'RAPID_DETERIORATION', 'ACCUMULATING_RISK'}:
                ml_result['decision'] = 'CONDITIONAL'
                ml_result['confidence'] = max(float(ml_result.get('confidence', 0.0)), 0.55)
                ml_result['source'] = 'regressor_healthy_trend_guard'
                ml_result['reason'] = (
                    f'CVaR saudável ({predicted_cvar:.1f}ms), mas trend={ml_result["warning"]}'
                )
                return ml_result

            if classifier_decision != 'ALLOWED':
                ml_result['raw_classifier_decision'] = classifier_decision
                ml_result['decision'] = 'ALLOWED'
                ml_result['classifier_decision'] = 'ALLOWED'
                ml_result['confidence'] = max(float(ml_result.get('confidence', 0.0)) * 0.75, 0.55)
                ml_result['source'] = 'regressor_healthy_override'
                ml_result['reason'] = (
                    f'Regressor saudável ({predicted_cvar:.1f}ms) override classifier={classifier_decision}'
                )
                return ml_result
        
        # 4. Consulta banco (apenas para validação, não override)
        db_stats = self.query_database_stats()
        
        if not db_stats or db_stats['total'] == 0:
            ml_result.setdefault('source', 'ml_predictive')
            ml_result['db_distribution'] = {}
            ml_result['db_total'] = 0
            return ml_result
        
        # 5. Distribuição de classes no banco (apenas informativo)
        distribution = db_stats['distribution']
        ml_result['db_distribution'] = distribution
        ml_result['db_total'] = db_stats['total']
        
        # 6. Override SÓ se ML e banco discordam E banco tem 85%+ forte
        ml_decision = ml_result['decision']
        if ml_decision in distribution:
            db_pct = distribution[ml_decision]
            
            # Se ML diz ALLOWED mas banco tem DB_OVERRIDE_THRESHOLD+ de BLOCKED recente
            if ml_decision == 'ALLOWED' and 'BLOCKED' in distribution:
                if distribution['BLOCKED'] >= self.DB_OVERRIDE_THRESHOLD * 100:
                    ml_result['decision'] = 'BLOCKED'
                    ml_result['confidence'] = 0.85
                    ml_result['source'] = 'db_override_cautious'
                    ml_result['reason'] = f'Banco mostra {distribution["BLOCKED"]}% BLOCKED recente'
        
        ml_result.setdefault('source', 'ml_predictive')
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
