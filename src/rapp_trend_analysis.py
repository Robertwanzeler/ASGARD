#!/usr/bin/env python3
"""
GreenRAN O-RAN - rApp Trend Analysis Module
===========================================

Responsabilidade: Análise preditiva de tendências
- Regressão Linear Simples para calcular slope
- Detecção de "velocidade" de subida/descida da latência
- Decisões preventivas baseadas em tendência
- Previsão de quando a latência vai atingir limiares críticos

Arquitetura:
    rApp (Cérebro Preditivo)
    ├── Trend Analysis (Slope)        ← MÓDULO NOVO
    ├── CVaR / Variância
    ├── Padrões Sazonais
    └── Decisão Preventiva

Métodos:
    calculate_latency_slope()   - Velocidade de mudança da latência
    detect_trend()              - Classifica tendência (rising/falling/stable)
    predict_threshold_time()    - Previsão de quando atinge limiar
    should_preempt_energy()     - Decisão preventiva

Uso:
    from rapp_trend_analysis import TrendAnalysis
    from rapp_data_lake import DataLake
    
    dl = DataLake()
    trend = TrendAnalysis(dl)
    
    slope_info = trend.calculate_latency_slope(window_minutes=5)
    decision = trend.should_preempt_energy()
"""

import time
from datetime import datetime, timedelta
from rapp_data_lake import DataLake
from greenran_paths import RAPP_DB_PATH


class TrendAnalysis:
    """
    Módulo de Análise de Tendências para o rApp.
    
    Usa regressão linear simples para calcular a "velocidade"
    de mudança da latência e tomar decisões preventivas.
    
    Conceito:
        slope > 0: Latência SUBINDO (piorando)
        slope < 0: Latência DESCENDO (melhorando)
        slope ≈ 0: Latência ESTÁVEL
    """
    
    def __init__(self, data_lake=None, db_path=str(RAPP_DB_PATH)):
        """
        Inicializa o módulo de Trend Analysis.
        
        Args:
            data_lake: Instância de DataLake (ou cria nova)
            db_path: Caminho do banco de dados se não passar DataLake
        """
        if data_lake is None:
            self.dl = DataLake(db_path)
        else:
            self.dl = data_lake
        
        # Thresholds para classificação de tendência
        self.thresholds = {
            # Slope em ms/segundo
            'rising_fast': 5.0,      # >5ms/s = subindo rápido
            'rising_slow': 2.0,      # 2-5ms/s = subindo devagar
            'stable': 0.5,           # <0.5ms/s = estável
            'falling_slow': -2.0,    # -2 a -5ms/s = descendo devagar
            'falling_fast': -5.0,    # <-5ms/s = descendo rápido
            
            # Limiares de latência
            'critical_latency_ms': 150,   # xApp assume controle
            'warning_latency_ms': 80,     # rApp prevenção
            'good_latency_ms': 50,        # rede OK
            
            # Tempo mínimo para predição confiável (segundos)
            'min_samples': 5,
            'min_window_seconds': 10,
        }
        
        self.current_trend = {}
    
    def calculate_slope(self, x_values, y_values):
        """
        Calcula slope usando regressão linear simples.
        
        Fórmula:
            slope = Σ((xi - x̄)(yi - ȳ)) / Σ((xi - x̄)²)
        
        Args:
            x_values: Lista de tempos (segundos desde início)
            y_values: Lista de valores de latência (µs)
        
        Returns:
            dict com slope, intercept, r_squared
        """
        n = len(x_values)
        
        if n < 2:
            return {
                'slope': 0,
                'intercept': 0,
                'r_squared': 0,
                'n_samples': n,
                'valid': False
            }
        
        # Médias
        x_mean = sum(x_values) / n
        y_mean = sum(y_values) / n
        
        # Cálculos de regressão
        numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(x_values, y_values))
        denominator = sum((x - x_mean) ** 2 for x in x_values)
        
        if denominator == 0:
            return {
                'slope': 0,
                'intercept': y_mean,
                'r_squared': 0,
                'n_samples': n,
                'valid': False
            }
        
        slope = numerator / denominator
        intercept = y_mean - slope * x_mean
        
        # R-squared (coeficiente de determinação)
        y_pred = [intercept + slope * x for x in x_values]
        ss_res = sum((y - yp) ** 2 for y, yp in zip(y_values, y_pred))
        ss_tot = sum((y - y_mean) ** 2 for y in y_values)
        
        r_squared = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0
        
        return {
            'slope': slope,
            'intercept': intercept,
            'r_squared': max(0, r_squared),  # Clamp para não dar negativo
            'n_samples': n,
            'valid': True
        }
    
    def calculate_latency_slope(self, window_minutes=5):
        """
        Calcula a velocidade de mudança da latência.
        
        Args:
            window_minutes: Janela de tempo para análise
        
        Returns:
            dict com:
                - slope_us_per_sec: slope em µs/segundo
                - slope_ms_per_sec: slope em ms/segundo
                - trend: 'rising_fast', 'rising_slow', 'stable', 'falling_slow', 'falling_fast'
                - confidence: confiança no resultado (0-1)
                - time_to_critical_ms: previsão de quando atinge 150ms (None se não subindo)
                - time_to_good_ms: previsão de quando atinge 50ms (None se não descendo)
                - current_latency_ms: latência atual
                - r_squared: qualidade do ajuste linear
        """
        metrics = self.dl.get_recent_metrics(window_minutes)
        
        if not metrics or len(metrics) < self.thresholds['min_samples']:
            return {
                'slope_us_per_sec': 0,
                'slope_ms_per_sec': 0,
                'trend': 'unknown',
                'confidence': 0.0,
                'time_to_critical_ms': None,
                'time_to_good_ms': None,
                'current_latency_ms': 0,
                'r_squared': 0,
                'n_samples': len(metrics) if metrics else 0,
                'valid': False,
                'reason': 'Dados insuficientes'
            }
        
        # Preparar dados para regressão
        # Converter timestamps para segundos desde o primeiro registro
        base_time = metrics[-1]['timestamp']  # Mais antigo
        x_values = []
        y_values = []
        
        for m in reversed(metrics):  # Mais antigo primeiro
            if m['latency_us'] > 0:
                x_values.append(m['timestamp'] - base_time)
                y_values.append(m['latency_us'])
        
        if len(x_values) < self.thresholds['min_samples']:
            return {
                'slope_us_per_sec': 0,
                'slope_ms_per_sec': 0,
                'trend': 'unknown',
                'confidence': 0.0,
                'time_to_critical_ms': None,
                'time_to_good_ms': None,
                'current_latency_ms': 0,
                'r_squared': 0,
                'n_samples': len(x_values),
                'valid': False,
                'reason': 'Dados inválidos'
            }
        
        # Calcular slope
        reg = self.calculate_slope(x_values, y_values)
        
        slope_us_per_sec = reg['slope']
        slope_ms_per_sec = slope_us_per_sec / 1000.0
        
        # Latência atual (último valor)
        current_latency_us = y_values[-1]
        current_latency_ms = current_latency_us / 1000.0
        
        # Classificar tendência
        trend = self._classify_trend(slope_ms_per_sec)
        
        # Calcular confiança baseada em R² e número de amostras
        confidence = self._calculate_confidence(reg['r_squared'], len(x_values))
        
        # Previsão de quando atinge limiares
        time_to_critical = None
        time_to_good = None
        
        if slope_ms_per_sec > self.thresholds['stable']:  # Latência subindo de forma relevante
            # Quando atinge 150ms?
            critical_us = self.thresholds['critical_latency_ms'] * 1000
            if current_latency_us < critical_us:
                time_to_critical = (critical_us - current_latency_us) / slope_us_per_sec
                time_to_critical = round(time_to_critical, 1)  # Segundos
        
        if slope_ms_per_sec < -self.thresholds['stable']:  # Latência descendo de forma relevante
            # Quando atinge 50ms?
            good_us = self.thresholds['good_latency_ms'] * 1000
            if current_latency_us > good_us:
                time_to_good = (good_us - current_latency_us) / slope_us_per_sec
                time_to_good = round(abs(time_to_good), 1)  # Segundos
        
        result = {
            'slope_us_per_sec': slope_us_per_sec,
            'slope_ms_per_sec': slope_ms_per_sec,
            'trend': trend,
            'confidence': confidence,
            'time_to_critical_ms': time_to_critical,
            'time_to_good_ms': time_to_good,
            'current_latency_ms': current_latency_ms,
            'current_latency_us': current_latency_us,
            'r_squared': reg['r_squared'],
            'intercept_us': reg['intercept'],
            'n_samples': len(x_values),
            'valid': True,
            'window_minutes': window_minutes
        }
        
        self.current_trend = result
        return result
    
    def _classify_trend(self, slope_ms_per_sec):
        """
        Classifica a tendência baseada no slope.
        
        Args:
            slope_ms_per_sec: Velocidade em ms/segundo
        
        Returns:
            String: 'rising_fast', 'rising_slow', 'stable', 'falling_slow', 'falling_fast'
        """
        th = self.thresholds
        
        if slope_ms_per_sec > th['rising_fast']:
            return 'rising_fast'
        elif slope_ms_per_sec > th['rising_slow']:
            return 'rising_slow'
        elif abs(slope_ms_per_sec) <= th['stable']:
            return 'stable'
        elif slope_ms_per_sec < th['falling_fast']:
            return 'falling_fast'
        elif slope_ms_per_sec < th['falling_slow']:
            return 'falling_slow'
        else:
            return 'stable'
    
    def _calculate_confidence(self, r_squared, n_samples):
        """
        Calcula confiança no resultado.
        
        Baseada em:
        1. R² (qualidade do ajuste linear)
        2. Número de amostras (mais dados = mais confiável)
        
        Args:
            r_squared: Coeficiente de determinação
            n_samples: Número de amostras
        
        Returns:
            Float 0-1
        """
        # Peso do R²
        r2_weight = min(1.0, r_squared * 2)  # R² de 0.5+ = bom
        
        # Peso das amostras (mínimo 5, ideal 20+)
        sample_weight = min(1.0, n_samples / 20.0)
        
        confidence = (r2_weight * 0.6) + (sample_weight * 0.4)
        return round(confidence, 3)
    
    def should_preempt_energy(self, trend_info=None):
        """
        Decisão preventiva baseada em tendência.
        
        Lógica:
        1. Se slope > 5ms/s E latência > 50ms → BLOCK (preventivo)
        2. Se slope > 2ms/s E latência > 80ms → BLOCK
        3. Se slope < 0 E latência < 50ms → ALLOW
        4. Caso contrário → CONDITIONAL
        
        Args:
            trend_info: Resultado de calculate_latency_slope() (ou None para calcular)
        
        Returns:
            dict com decisão, motivo, e nível de prevenção
        """
        if trend_info is None:
            trend_info = self.calculate_latency_slope()
        
        if not trend_info['valid']:
            return {
                'decision': 'CONDITIONAL',
                'preventive': False,
                'reason': trend_info.get('reason', 'Dados insuficientes'),
                'confidence': 0.0,
                'level': 'unknown'
            }
        
        th = self.thresholds
        slope = trend_info['slope_ms_per_sec']
        current_ms = trend_info['current_latency_ms']
        trend = trend_info['trend']
        time_to_critical = trend_info.get('time_to_critical_ms')
        
        decision = 'CONDITIONAL'
        preventive = False
        level = 'normal'
        reason = ''
        
        # CASO 1: Latência JÁ é crítica (>150ms)
        # → xApp assume controle, rApp bloqueia tudo
        if current_ms > th['critical_latency_ms']:
            decision = 'BLOCKED'
            preventive = False
            level = 'emergency'
            reason = f'CRITICAL: Latência {current_ms:.0f}ms > {th["critical_latency_ms"]}ms - xApp deve assumir'
        
        # CASO 2: Latência subindo rápido E perto do crítico
        # → Decisão PREVENTIVA
        elif slope > th['rising_fast'] and current_ms > th['warning_latency_ms']:
            decision = 'BLOCKED'
            preventive = True
            level = 'high'
            reason = f'PREVENTIVE: Slope +{slope:.1f}ms/s, em {time_to_critical}s atinge {th["critical_latency_ms"]}ms'
        
        # CASO 3: Latência subindo rápido mesmo sem estar perto
        elif slope > th['rising_fast']:
            decision = 'BLOCKED'
            preventive = True
            level = 'medium'
            reason = f'PREVENTIVE: Slope +{slope:.1f}ms/s indica pico iminente'
        
        # CASO 4: Latência subindo devagar E já em warning
        elif slope > th['rising_slow'] and current_ms > th['warning_latency_ms']:
            decision = 'BLOCKED'
            preventive = True
            level = 'medium'
            reason = f'PREVENTIVE: Slope +{slope:.1f}ms/s, latência {current_ms:.0f}ms em warning'
        
        # CASO 5: Latência subindo mas ainda baixa
        elif slope > th['rising_slow']:
            decision = 'CONDITIONAL'
            preventive = False
            level = 'low'
            reason = f'MONITOR: Slope +{slope:.1f}ms/s, mas latência ainda {current_ms:.0f}ms'
        
        # CASO 6: Latência estável e boa
        elif abs(slope) <= th['stable'] and current_ms < th['good_latency_ms']:
            decision = 'ALLOWED'
            preventive = False
            level = 'safe'
            reason = f'SAFE: Latência estável em {current_ms:.0f}ms, slope {slope:.2f}ms/s'
        
        # CASO 7: Latência descendo
        elif slope < 0 and current_ms < th['warning_latency_ms']:
            decision = 'ALLOWED'
            preventive = False
            level = 'safe'
            reason = f'IMPROVING: Latência descendo {slope:.1f}ms/s para {current_ms:.0f}ms'
        
        # CASO 8: Latência descendo mas ainda alta
        elif slope < 0 and current_ms >= th['warning_latency_ms']:
            decision = 'CONDITIONAL'
            preventive = False
            level = 'monitoring'
            reason = f'MONITORING: Latência descendo {slope:.1f}ms/s mas ainda {current_ms:.0f}ms'
        
        # CASO 9: Estável mas alto
        else:
            decision = 'CONDITIONAL'
            preventive = False
            level = 'normal'
            reason = f'NORMAL: Latência {current_ms:.0f}ms, slope {slope:.2f}ms/s'
        
        return {
            'decision': decision,
            'preventive': preventive,
            'reason': reason,
            'confidence': trend_info['confidence'],
            'level': level,
            'slope_ms_per_sec': slope,
            'current_latency_ms': current_ms,
            'time_to_critical_ms': time_to_critical,
            'trend': trend
        }
    
    def get_trend_summary(self, window_minutes=5):
        """
        Retorna resumo completo da análise de tendência.
        
        Returns:
            dict com todas as informações de tendência
        """
        trend_info = self.calculate_latency_slope(window_minutes)
        decision = self.should_preempt_energy(trend_info)
        
        return {
            'timestamp': int(time.time()),
            'trend_analysis': trend_info,
            'preventive_decision': decision,
            'thresholds': self.thresholds
        }
    
    def detect_acceleration(self, window_minutes=5):
        """
        Detecta se a tendência está acelerando ou desacelerando.
        
        Compara slope de duas metades da janela.
        
        Returns:
            dict com informação de aceleração
        """
        metrics = self.dl.get_recent_metrics(window_minutes)
        
        if not metrics or len(metrics) < 6:
            return {
                'acceleration': 'unknown',
                'reason': 'Dados insuficientes'
            }
        
        # Dividir em duas metades
        mid = len(metrics) // 2
        
        # Primeira metade (mais antiga)
        first_half = metrics[mid:]
        # Segunda metade (mais recente)
        second_half = metrics[:mid]
        
        def calc_slope_for_half(half):
            base = half[-1]['timestamp']
            x = [m['timestamp'] - base for m in reversed(half) if m['latency_us'] > 0]
            y = [m['latency_us'] for m in reversed(half) if m['latency_us'] > 0]
            if len(x) < 2:
                return 0
            reg = self.calculate_slope(x, y)
            return reg['slope']
        
        slope_first = calc_slope_for_half(first_half) / 1000  # ms/s
        slope_second = calc_slope_for_half(second_half) / 1000  # ms/s
        
        # Diferença de slope entre metades
        delta = slope_second - slope_first
        
        if abs(delta) < 0.5:
            acceleration = 'steady'
        elif delta > 2:
            acceleration = 'accelerating'
        elif delta > 0.5:
            acceleration = 'slightly_accelerating'
        elif delta < -2:
            acceleration = 'decelerating'
        elif delta < -0.5:
            acceleration = 'slightly_decelerating'
        else:
            acceleration = 'steady'
        
        return {
            'acceleration': acceleration,
            'slope_first_half_ms': slope_first,
            'slope_second_half_ms': slope_second,
            'delta_ms': delta,
            'is_worsening': delta > 0,  # Tendência piorando se delta > 0
            'confidence': min(1.0, len(metrics) / 20.0)
        }
    
    def predict_next_value(self, window_minutes=5, ahead_seconds=30):
        """
        Prediz o valor de latência para N segundos à frente.
        
        Args:
            window_minutes: Janela de análise
            ahead_seconds: Segundos à frente para prever
        
        Returns:
            dict com predição
        """
        trend_info = self.calculate_latency_slope(window_minutes)
        
        if not trend_info['valid']:
            return {
                'predicted_latency_ms': None,
                'confidence': 0.0,
                'reason': 'Dados insuficientes'
            }
        
        slope_us = trend_info['slope_us_per_sec']
        current_us = trend_info['current_latency_us']
        
        # Predição linear simples
        predicted_us = current_us + (slope_us * ahead_seconds)
        predicted_ms = predicted_us / 1000.0
        
        # Ajustar confiança baseada no R² e distância temporal
        confidence = trend_info['confidence'] * (1.0 - (ahead_seconds / 300.0))  # Reduz com distância
        confidence = max(0.1, confidence)
        
        return {
            'predicted_latency_ms': round(predicted_ms, 1),
            'predicted_latency_us': round(predicted_us, 0),
            'ahead_seconds': ahead_seconds,
            'current_latency_ms': trend_info['current_latency_ms'],
            'slope_ms_per_sec': trend_info['slope_ms_per_sec'],
            'confidence': round(confidence, 3),
            'will_exceed_critical': predicted_ms > self.thresholds['critical_latency_ms'],
            'will_exceed_warning': predicted_ms > self.thresholds['warning_latency_ms']
        }


def main():
    """Teste do Trend Analysis"""
    print("=" * 70)
    print("rApp Trend Analysis - Teste")
    print("=" * 70)
    
    dl = DataLake()
    trend = TrendAnalysis(dl)
    
    # Slope da latência
    print("\n[1] Slope da Latência (últimos 5 min):")
    slope = trend.calculate_latency_slope(5)
    print(f"    Slope: {slope['slope_ms_per_sec']:.2f} ms/s")
    print(f"    Tendência: {slope['trend']}")
    print(f"    Confiança: {slope['confidence']*100:.0f}%")
    print(f"    R²: {slope['r_squared']:.3f}")
    print(f"    Latência atual: {slope['current_latency_ms']:.1f}ms")
    print(f"    Amostras: {slope['n_samples']}")
    
    if slope['time_to_critical_ms']:
        print(f"    ⚠️  Em {slope['time_to_critical_ms']:.0f}s atinge {trend.thresholds['critical_latency_ms']}ms")
    
    if slope['time_to_good_ms']:
        print(f"    ✅  Em {slope['time_to_good_ms']:.0f}s atinge {trend.thresholds['good_latency_ms']}ms")
    
    # Decisão preventiva
    print("\n[2] Decisão Preventiva:")
    decision = trend.should_preempt_energy()
    print(f"    Decisão: {decision['decision']}")
    print(f"    Preventivo: {decision['preventive']}")
    print(f"    Nível: {decision['level']}")
    print(f"    Motivo: {decision['reason']}")
    
    # Aceleração
    print("\n[3] Aceleração da Tendência:")
    accel = trend.detect_acceleration(5)
    print(f"    Aceleração: {accel['acceleration']}")
    if 'slope_first_half_ms' in accel:
        print(f"    Slope 1ª metade: {accel['slope_first_half_ms']:.2f} ms/s")
        print(f"    Slope 2ª metade: {accel['slope_second_half_ms']:.2f} ms/s")
        print(f"    Piorando: {accel.get('is_worsening', 'N/A')}")
    else:
        print(f"    Razão: {accel.get('reason', 'N/A')}")
    
    # Predição
    print("\n[4] Predição para 30s à frente:")
    pred = trend.predict_next_value(5, 30)
    if pred['predicted_latency_ms'] is not None:
        print(f"    Latência prevista: {pred['predicted_latency_ms']:.1f}ms")
        print(f"    Confiança: {pred['confidence']*100:.0f}%")
        print(f"    Excederá crítico: {pred['will_exceed_critical']}")
        print(f"    Excederá warning: {pred['will_exceed_warning']}")
    else:
        print(f"    Erro: {pred.get('reason', 'Dados insuficientes')}")
    
    print("\n" + "=" * 70)
    print("Teste concluído!")
    print("=" * 70)


if __name__ == "__main__":
    main()
