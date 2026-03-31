#!/usr/bin/env python3
"""
GreenRAN ML Predictor
=====================
Loads trained ML models and provides prediction interface for rApp Orchestrator.

Usage:
    from rapp_ml_predictor import MLPredictor
    predictor = MLPredictor()
    prediction = predictor.predict(metrics_dict)
"""

import os
import numpy as np
import joblib
from datetime import datetime

MODEL_DIR = os.path.join(os.path.dirname(__file__), 'models')


class MLPredictor:
    """ML prediction module for GreenRAN rApp."""

    def __init__(self, model_dir=MODEL_DIR):
        self.model_dir = model_dir
        self.classifier = None
        self.regressor = None
        self.clf_scaler = None
        self.reg_scaler = None
        self.label_encoder = None
        self.loaded = False
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
            print("[ML] Modelos carregados com sucesso")
        except FileNotFoundError as e:
            print(f"[ML] AVISO: Modelos não encontrados: {e}")
            print(f"[ML] Execute: python3 train_ml_model.py para treinar")
            self.loaded = False

    def _prepare_features(self, metrics):
        """
        Prepare feature vector from metrics dict.

        Args:
            metrics: dict with keys like cvar_per_ue_us, global_avg_latency_us, etc.

        Returns:
            numpy array of features
        """
        now = datetime.now()

        # Basic features
        cvar_ms = float(metrics.get('cvar_per_ue_us', 0)) / 1000.0
        latency_p95_ms = float(metrics.get('latency_p95_per_ue_us', 0)) / 1000.0
        avg_latency_ms = float(metrics.get('global_avg_latency_us', 0)) / 1000.0
        variance_ms2 = float(metrics.get('variance_per_ue_us2', 0)) / 1_000_000.0
        total_cameras = int(metrics.get('total_active_cameras', 0))
        total_ues = int(metrics.get('total_active_ues', 1))
        total_critical = int(metrics.get('total_critical_ues', 0))

        # Update CVaR history for trend calculation
        self._cvar_history.append(cvar_ms)
        if len(self._cvar_history) > 10:
            self._cvar_history = self._cvar_history[-10:]

        # Trend features
        cvar_diff = self._cvar_history[-1] - self._cvar_history[-2] if len(self._cvar_history) >= 2 else 0
        cvar_rolling_mean = np.mean(self._cvar_history) if self._cvar_history else cvar_ms
        cvar_rolling_std = np.std(self._cvar_history) if len(self._cvar_history) >= 2 else 0

        # Camera ratio
        camera_ratio = total_cameras / max(total_ues, 1)

        # Time features
        hour = now.hour
        day_of_week = now.weekday()
        hour_sin = np.sin(2 * np.pi * hour / 24)
        hour_cos = np.cos(2 * np.pi * hour / 24)
        is_night = 1 if (hour >= 22 or hour < 6) else 0
        is_weekend = 1 if day_of_week >= 5 else 0

        # CVaR zone
        if cvar_ms < 60:
            cvar_zone = 0  # green
        elif cvar_ms < 80:
            cvar_zone = 1  # yellow
        else:
            cvar_zone = 2  # red

        sim_time_s = float(metrics.get('sim_time_s', 0))

        features = np.array([[
            cvar_ms,
            cvar_diff,
            cvar_rolling_mean,
            cvar_rolling_std,
            latency_p95_ms,
            avg_latency_ms,
            variance_ms2,
            total_cameras,
            total_ues,
            camera_ratio,
            total_critical,
            hour_sin,
            hour_cos,
            is_night,
            is_weekend,
            cvar_zone,
            sim_time_s,
        ]])

        return features

    def predict(self, metrics):
        """
        Predict decision and CVaR from current metrics.

        Args:
            metrics: dict with network metrics

        Returns:
            dict with:
                - decision: ALLOWED/CONDITIONAL/BLOCKED
                - confidence: float 0-1
                - predicted_cvar_ms: float
                - feature_importance: dict of top features
        """
        if not self.loaded:
            return {
                'decision': None,
                'confidence': 0.0,
                'predicted_cvar_ms': None,
                'error': 'Modelos não carregados'
            }

        features = self._prepare_features(metrics)

        # Classifier prediction
        features_scaled = self.clf_scaler.transform(features)
        pred_encoded = self.classifier.predict(features_scaled)[0]
        proba = self.classifier.predict_proba(features_scaled)[0]
        decision = self.label_encoder.inverse_transform([pred_encoded])[0]
        confidence = float(np.max(proba))

        # Regressor prediction
        reg_features_scaled = self.reg_scaler.transform(features)
        predicted_cvar = float(self.regressor.predict(reg_features_scaled)[0])

        # Feature importance
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
            }
        }

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
        print(f"  Probabilidades: {result['all_probabilities']}")
        print(f"  Top features: {result['feature_importance']}")
    else:
        print("Modelos não carregados. Execute: python3 train_ml_model.py")
