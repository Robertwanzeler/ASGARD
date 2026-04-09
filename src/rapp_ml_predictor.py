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
from datetime import datetime

MODEL_DIR = os.path.join(os.path.dirname(__file__), 'models')
CONFIG_DIR = os.path.join(os.path.dirname(__file__), 'config')

# Thresholds - ML PREDITIVA (defaults, will be overridden by config)
DB_OVERRIDE_THRESHOLD = 0.85  # 85% para override (só sobrescreve se maioria forte)
DB_BOOST_THRESHOLD = 0.80    # 80% para boost de confiança
WINDOW_MINUTES = 1           # Janela de consulta ao banco (1 min = mais atual)
CVAR_CRITICAL_THRESHOLD = 80 # ms - CVaR acima disso = BLOCKED
CVAR_WARNING_THRESHOLD = 60   # ms - CVaR acima disso = CONDITIONAL

# Novas thresholds para predição
PREDICTED_CVAR_BLOCKED = 80  # Se regressor prediz > 80ms = BLOCKED
PREDICTED_CVAR_WARNING = 60  # Se regressor prediz > 60ms = CONDITIONAL


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
        
        self.feature_cols = [
            # Features originais (17)
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
            # Features de rede (5)
            'throughput_kbps',
            'packet_loss_rate',
            'jitter_ms',
            'tx_rx_ratio',
            'energy_history',
            # Features de TREND (3) - NOVAS
            'cvar_trend',
            'throughput_trend',
            'packet_loss_trend',
            # LAG FEATURES (15) - PREDITIVAS
            'cvar_lag_1', 'cvar_lag_2', 'cvar_lag_3', 'cvar_lag_4', 'cvar_lag_5',
            'throughput_lag_1', 'throughput_lag_2', 'throughput_lag_3',
            'packet_loss_lag_1', 'packet_loss_lag_2', 'packet_loss_lag_3',
            'latency_lag_1', 'latency_lag_2',
            # Rolling e Acceleration (5)
            'cvar_rolling_3', 'cvar_rolling_10', 'cvar_rolling_std_3',
            'cvar_acceleration', 'latency_acceleration',
        ]
        # Histórico para cálculo de trend e lag features (PREDITIVO)
        self._cvar_history = []
        self._prev_throughput = 0
        self._prev_packet_loss = 0
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
        # D) Reset dos históricos de detecção de cenários críticos
        self._cvar_trend_history = []
        self._consecutive_increase_count = 0
        self._last_cvar_trend = 0
        self._critical_zone_count = 0
        print("[ML] Histórico resetado para nova simulação")

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
        cvar_p95_ms = float(metrics.get('cvar_p95_us', 0)) / 1000.0  # P95 = pior 5% dos UEs
        latency_p95_ms = float(metrics.get('latency_p95_per_ue_us', 0)) / 1000.0
        avg_latency_ms = float(metrics.get('global_avg_latency_us', 0)) / 1000.0
        variance_ms2 = float(metrics.get('variance_per_ue_us2', 0)) / 1_000_000.0
        total_cameras = int(metrics.get('total_active_cameras', 0))
        total_ues = int(metrics.get('total_active_ues', 1))
        total_critical = int(metrics.get('total_critical_ues', 0))

        # Usar P95 (85.4ms) como cvar se disponível, senão usar cvar médio
        # P95 representa o pior caso - mais importante para decisões
        effective_cvar = cvar_p95_ms if cvar_p95_ms > cvar_ms else cvar_ms

        self._cvar_history.append(effective_cvar)
        if len(self._cvar_history) > 10:
            self._cvar_history = self._cvar_history[-10:]

        cvar_diff = self._cvar_history[-1] - self._cvar_history[-2] if len(self._cvar_history) >= 2 else 0
        cvar_rolling_mean = np.mean(self._cvar_history) if self._cvar_history else effective_cvar
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
        
        # Features de rede (5)
        throughput_kbps = float(metrics.get('throughput_kbps', 0)) / 1000.0  # Converter para Mbps
        packet_loss_rate = float(metrics.get('packet_loss_rate', 0)) * 100   # Porcentagem
        jitter_ms = float(metrics.get('jitter_ms', 0))
        tx_rx_ratio = float(metrics.get('tx_rx_ratio', 0))
        energy_history = float(metrics.get('energy_history', 0))  # 0-1 (proporção BLOCKED)
        
        # Features de TREND (3) - NOVAS para ML PREDITIVA
        # cvar_trend: diferença entre CVaR atual e 5 ciclos atrás (positivo = subindo)
        cvar_trend = 0
        if len(self._cvar_history) >= 5:
            cvar_trend = cvar_ms - self._cvar_history[-5]
        
        # throughput_trend: diferença de throughput (positivo = aumentando carga)
        throughput_trend = throughput_kbps - self._prev_throughput
        self._prev_throughput = throughput_kbps
        
        # packet_loss_trend: diferença de packet loss (positivo = piorando)
        packet_loss_trend = packet_loss_rate - self._prev_packet_loss
        self._prev_packet_loss = packet_loss_rate
        
        # ============================================================
        # LAG FEATURES - PREDITIVAS (basadas no histórico)
        # ============================================================
        # Atualizar históricos
        self._cvar_history.append(effective_cvar)
        self._throughput_history.append(throughput_kbps)
        self._packet_loss_history.append(packet_loss_rate)
        self._latency_history.append(avg_latency_ms)
        
        # Manter apenas últimos 10 valores
        if len(self._cvar_history) > 10:
            self._cvar_history = self._cvar_history[-10:]
            self._throughput_history = self._throughput_history[-10:]
            self._packet_loss_history = self._packet_loss_history[-10:]
            self._latency_history = self._latency_history[-10:]
        
        # Lag features (1-5)
        cvar_lag_1 = self._cvar_history[-1] if len(self._cvar_history) >= 1 else effective_cvar
        cvar_lag_2 = self._cvar_history[-2] if len(self._cvar_history) >= 2 else cvar_lag_1
        cvar_lag_3 = self._cvar_history[-3] if len(self._cvar_history) >= 3 else cvar_lag_2
        cvar_lag_4 = self._cvar_history[-4] if len(self._cvar_history) >= 4 else cvar_lag_3
        cvar_lag_5 = self._cvar_history[-5] if len(self._cvar_history) >= 5 else cvar_lag_4
        
        throughput_lag_1 = self._throughput_history[-1] if len(self._throughput_history) >= 1 else throughput_kbps
        throughput_lag_2 = self._throughput_history[-2] if len(self._throughput_history) >= 2 else throughput_lag_1
        throughput_lag_3 = self._throughput_history[-3] if len(self._throughput_history) >= 3 else throughput_lag_2
        
        packet_loss_lag_1 = self._packet_loss_history[-1] if len(self._packet_loss_history) >= 1 else packet_loss_rate
        packet_loss_lag_2 = self._packet_loss_history[-2] if len(self._packet_loss_history) >= 2 else packet_loss_lag_1
        packet_loss_lag_3 = self._packet_loss_history[-3] if len(self._packet_loss_history) >= 3 else packet_loss_lag_2
        
        latency_lag_1 = self._latency_history[-1] if len(self._latency_history) >= 1 else avg_latency_ms
        latency_lag_2 = self._latency_history[-2] if len(self._latency_history) >= 2 else latency_lag_1
        
        # Rolling features
        cvar_rolling_3 = np.mean(self._cvar_history[-3:]) if len(self._cvar_history) >= 3 else effective_cvar
        cvar_rolling_10 = np.mean(self._cvar_history) if len(self._cvar_history) >= 10 else cvar_rolling_3
        cvar_rolling_std_3 = np.std(self._cvar_history[-3:]) if len(self._cvar_history) >= 3 else 0
        
        # Acceleration (derivada segunda)
        cvar_acceleration = cvar_diff - (self._cvar_history[-1] - self._cvar_history[-2]) if len(self._cvar_history) >= 2 else 0
        latency_acceleration = 0  # Simplificado
        
        features = np.array([[
            cvar_ms, cvar_diff, cvar_rolling_mean, cvar_rolling_std,
            latency_p95_ms, avg_latency_ms, variance_ms2,
            total_cameras, total_ues, camera_ratio, total_critical,
            hour_sin, hour_cos, is_night, is_weekend, cvar_zone, sim_time_s,
            # Features de rede (5)
            throughput_kbps, packet_loss_rate, jitter_ms, tx_rx_ratio, energy_history,
            # Features de TREND (3)
            cvar_trend, throughput_trend, packet_loss_trend,
            # LAG FEATURES (15) - PREDITIVAS
            cvar_lag_1, cvar_lag_2, cvar_lag_3, cvar_lag_4, cvar_lag_5,
            throughput_lag_1, throughput_lag_2, throughput_lag_3,
            packet_loss_lag_1, packet_loss_lag_2, packet_loss_lag_3,
            latency_lag_1, latency_lag_2,
            # Rolling e Acceleration (5)
            cvar_rolling_3, cvar_rolling_10, cvar_rolling_std_3,
            cvar_acceleration, latency_acceleration,
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
        Predição PREDITIVA combinando ML + contexto.
        
        FLUXO NOVO (PREDITIVO):
        1. Predizer CVaR (regressor) - SE > 80ms = BLOCKED imediato
        2. Classifier decide baseado nas features
        3. Consultar banco SÓ para validação, não para override
        4. Usar predicted_cvar_ms como fator principal
        
        Args:
            metrics: dict com métricas da rede
            
        Returns:
            dict com decisão, confiança, fonte, distribuição do banco
        """
        # 1. Predição do regressor (PRÉ-DIZ O FUTURO!)
        ml_result = self.predict(metrics)
        
        predicted_cvar = ml_result.get('predicted_cvar_ms', 0)
        current_cvar = float(metrics.get('cvar_per_ue_us', 0)) / 1000.0
        
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
        cvar_p95_ms = float(metrics.get('cvar_p95_us', 0)) / 1000.0
        current_cvar_p95 = max(cvar_p95_ms, current_cvar)  # Usar o pior
        
        if current_cvar_p95 >= self.CVAR_CRITICAL_THRESHOLD:
            ml_result['decision'] = 'BLOCKED'
            ml_result['confidence'] = 0.90
            ml_result['source'] = 'current_cvar_critical'
            ml_result['reason'] = f'CVaR P95={current_cvar_p95:.1f}ms ≥ {self.CVAR_CRITICAL_THRESHOLD}ms'
            return ml_result
        
        # === D) DETECÇÃO DE CENÁRIOS CRÍTICOS ===
        # Analisar trend de CVaR para detecção precoce - SEMPRE executar, mesmo após early returns
        # Precisamos calcular ANTES dos returns para detectar deterioração mesmo quando decisão já foi tomada
        cvar_trend = 0
        if len(self._cvar_history) >= 5:
            cvar_trend = self._cvar_history[-1] - self._cvar_history[-5]
        
        # Armazenar no histórico de trends
        self._cvar_trend_history.append(cvar_trend)
        if len(self._cvar_trend_history) > 10:
            self._cvar_trend_history = self._cvar_trend_history[-10:]
        
        # Detectar deterioração rápida (CVaR subiu > 10ms nos últimos 5 ciclos) - REDUZIDO de 15 para 10
        if cvar_trend > 10:
            ml_result['warning'] = 'RAPID_DETERIORATION'
            ml_result['trend_info'] = f'CVaR subiu {cvar_trend:.1f}ms em 5 ciclos'
            print(f"\033[1;33m[ML] ⚠️ ALERTA: Deterioração rápida! CVaR subiu {cvar_trend:.1f}ms\033[0m")
        
        # Detectar zona de cautela (entre warning e critical)
        warning_threshold = self.CVAR_WARNING_THRESHOLD
        critical_threshold = self.CVAR_CRITICAL_THRESHOLD
        if warning_threshold <= predicted_cvar < critical_threshold:
            self._critical_zone_count += 1
            if self._critical_zone_count >= 3:
                ml_result['warning'] = 'NEAR_CRITICAL_ZONE'
                ml_result['trend_info'] = f'CVaR previsto {predicted_cvar:.1f}ms está entre {warning_threshold}ms e {critical_threshold}ms por {self._critical_zone_count} ciclos'
                print(f"\033[1;33m[ML] ⚠️ ALERTA: Zona de cautela! CVaR previsto={predicted_cvar:.1f}ms próximo ao crítico\033[0m")
        else:
            self._critical_zone_count = max(0, self._critical_zone_count - 1)
        
        # Detectar acumulação de risco (aumentos consecutivos)
        if cvar_trend > self._last_cvar_trend and self._last_cvar_trend > 0:
            self._consecutive_increase_count += 1
        else:
            self._consecutive_increase_count = 0
        
        self._last_cvar_trend = cvar_trend
        
        if self._consecutive_increase_count >= 3:
            ml_result['warning'] = 'ACCUMULATING_RISK'
            ml_result['trend_info'] = f'{self._consecutive_increase_count} aumentos consecutivos de CVaR'
            print(f"\033[1;31m[ML] 🔴 ALERTA: Risco acumulado! {self._consecutive_increase_count} ciclos de aumento\033[0m")
        
        # Adicionar info de trend ao resultado (SEMPRE, mesmo após early returns)
        ml_result['cvar_trend'] = cvar_trend
        ml_result['trend_history'] = self._cvar_trend_history.copy()
        
        # === FASE 1: SE REGRESSOR DIZ QUE VAI SER CRÍTICO ===
        if predicted_cvar >= self.CVAR_CRITICAL_THRESHOLD:
            ml_result['decision'] = 'BLOCKED'
            ml_result['confidence'] = 0.95
            ml_result['source'] = 'regressor_prediction'
            ml_result['reason'] = f'CVaR previsto={predicted_cvar:.1f}ms ≥ {self.CVAR_CRITICAL_THRESHOLD}ms'
            return ml_result
        
        # 4. Consulta banco (apenas para validação, não override)
        db_stats = self.query_database_stats()
        
        if not db_stats or db_stats['total'] == 0:
            ml_result['source'] = 'ml_predictive'
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
        
        ml_result['source'] = 'ml_predictive'
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
