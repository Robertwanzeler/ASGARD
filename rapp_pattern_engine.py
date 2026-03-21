#!/usr/bin/env python3
"""
GreenRAN O-RAN - rApp Pattern Recognition Engine
==============================================

Responsabilidade: Detecção de padrões usando ML moderado
- Média móvel de métricas
- Detecção de sazonalidade (hora do dia, dia da semana)
- Identificação de janelas de baixa atividade
- Predição de métricas para próxima hora

Métodos:
- Média Móvel Simples (SMA)
- Detecção de Sazonalidade por Hora
- Detecção de Sazonalidade por Dia da Semana
- Análise de Tendência (subindo/descendo/estável)

Uso:
    from rapp_pattern_engine import PatternRecognition
    from rapp_data_lake import DataLake
    
    dl = DataLake()
    pe = PatternRecognition(dl)
    
    pattern = pe.analyze_current()
    window = pe.calculate_energy_window()
"""

import sys
import time
from datetime import datetime
from collections import defaultdict

sys.path.insert(0, '/home/robert/orange_nuclear')
from rapp_data_lake import DataLake

DAY_NAMES = ['Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb', 'Dom']
DAY_NAMES_FULL = ['Segunda', 'Terça', 'Quarta', 'Quinta', 'Sexta', 'Sábado', 'Domingo']

# Thresholds para detecção de padrões
THRESHOLDS = {
    'low_activity_cameras': 1,  # Máximo câmeras para considerar "baixa atividade"
    'low_activity_latency_ms': 50,  # Máximo latência para considerar "baixa atividade"
    'high_confidence_ratio': 0.8,  # 80% das amostras devem confirmar o padrão
    'min_samples_for_pattern': 10,  # Mínimo de amostras para detectar padrão
    'trend_threshold': 0.1,  # 10% de variação para considerar tendência
}


class PatternRecognition:
    """
    Engine de reconhecimento de padrões usando ML moderado.
    
    Detecta:
    - Padrões horários (ex: 22:00-06:00 = baixa atividade)
    - Padrões por dia da semana (ex: sábado = menos cameras)
    - Tendências (latência subindo/descendo)
    - Janelas ótimas para economia de energia
    """
    
    def __init__(self, data_lake=None, db_path="/tmp/rapp_data_lake.db"):
        """
        Inicializa o Pattern Engine.
        
        Args:
            data_lake: Instância de DataLake (ou cria nova)
            db_path: Caminho do banco de dados se não passar DataLake
        """
        if data_lake is None:
            self.dl = DataLake(db_path)
        else:
            self.dl = data_lake
        
        self.current_analysis = {}
    
    def calculate_moving_average(self, metric='latency', window_minutes=30):
        """
        Calcula média móvel simples (SMA) de uma métrica.
        
        Args:
            metric: 'latency' ou 'cameras'
            window_minutes: Tamanho da janela
        
        Returns:
            Float com a média móvel.
        """
        return self.dl.calculate_moving_average(metric, window_minutes)
    
    def calculate_exponential_moving_average(self, metric='latency', 
                                           window_minutes=30, alpha=0.3):
        """
        Calcula média móvel exponencial (EMA).
        Dá mais peso aos dados mais recentes.
        
        Args:
            metric: 'latency' ou 'cameras'
            window_minutes: Tamanho da janela
            alpha: Fator de suavização (0-1)
        
        Returns:
            Float com a EMA.
        """
        recent = self.dl.get_recent_metrics(window_minutes)
        
        if not recent:
            return 0
        
        if metric == 'latency':
            values = [m['latency_us'] for m in recent]
        else:
            values = [m['cameras_active'] for m in recent]
        
        if not values:
            return 0
        
        # EMA: primeiro valor é SMA, depois aplica peso exponencial
        ema = values[0]
        for val in values[1:]:
            ema = alpha * val + (1 - alpha) * ema
        
        return ema
    
    def analyze_current(self, window_minutes=30):
        """
        Analisa o estado atual e detecta padrões.
        
        Returns:
            Dict com análise completa:
                - timestamp: momento da análise
                - hour: hora atual
                - day_of_week: dia da semana atual
                - current_latency: latência atual (EMA)
                - current_cameras: câmeras atuais (EMA)
                - low_activity: bool indicando baixa atividade
                - confidence: confiança na detecção (0-1)
                - pattern: tipo de padrão detectado
                - trend: tendência da latência
        """
        now = datetime.now()
        current_hour = now.hour
        current_dow = now.weekday()  # 0=segunda
        
        # Calcula métricas atuais
        current_latency = self.calculate_exponential_moving_average('latency', window_minutes)
        current_cameras = self.calculate_exponential_moving_average('cameras', window_minutes)
        
        # Calcula média móvel simples para comparação
        sma_latency = self.calculate_moving_average('latency', window_minutes)
        sma_cameras = self.calculate_moving_average('cameras', window_minutes)
        
        # Analisa tendência
        trend = self.analyze_trend('latency', window_minutes * 2)
        
        # Detecta baixa atividade
        is_low_activity = (
            current_cameras <= THRESHOLDS['low_activity_cameras'] and
            current_latency < THRESHOLDS['low_activity_latency_ms'] * 1000
        )
        
        # Calcula confiança baseado em dados históricos da mesma hora
        pattern_data = self.dl.get_pattern_data(hour=current_hour, day_of_week=current_dow)
        sample_count = pattern_data['sample_count'] if pattern_data else 0
        
        # Confiança aumenta com mais amostras
        if sample_count >= THRESHOLDS['min_samples_for_pattern']:
            confidence = min(0.95, 0.5 + (sample_count / 100))
        else:
            confidence = 0.3  # Baixa confiança sem dados
        
        # Detecta tipo de padrão
        pattern_type = self._detect_pattern_type(current_hour, current_dow, 
                                                 current_cameras, current_latency)
        
        self.current_analysis = {
            'timestamp': int(time.time()),
            'datetime': now.isoformat(),
            'hour': current_hour,
            'day_of_week': current_dow,
            'day_name': DAY_NAMES[current_dow],
            'current_latency_us': current_latency,
            'current_cameras': current_cameras,
            'sma_latency_us': sma_latency,
            'sma_cameras': sma_cameras,
            'low_activity': is_low_activity,
            'confidence': confidence,
            'pattern': pattern_type,
            'trend': trend,
            'sample_count': sample_count,
            'is_night': current_hour >= 22 or current_hour < 6,
            'is_weekend': current_dow >= 5
        }
        
        return self.current_analysis
    
    def _detect_pattern_type(self, hour, day_of_week, cameras, latency):
        """
        Detecta o tipo de padrão baseado no contexto.
        
        Returns:
            String com tipo de padrão:
                - 'night_low_activity': Noite, baixa atividade
                - 'weekend_low_activity': Fim de semana
                - 'business_hours': Horário comercial ativo
                - 'peak_hours': Horário de pico
                - 'normal': Atividade normal
        """
        is_night = hour >= 22 or hour < 6
        is_weekend = day_of_week >= 5
        is_business = 8 <= hour <= 18
        is_peak = 12 <= hour <= 14 or 18 <= hour <= 20  # Almoço/janta
        
        if is_night and cameras <= 1:
            return 'night_low_activity'
        elif is_weekend and cameras <= 2:
            return 'weekend_low_activity'
        elif is_peak and cameras >= 2:
            return 'peak_hours'
        elif is_business and cameras >= 2:
            return 'business_hours'
        elif cameras <= 1:
            return 'low_activity'
        else:
            return 'normal'
    
    def analyze_trend(self, metric='latency', window_minutes=60):
        """
        Analisa tendência de uma métrica.
        
        Args:
            metric: 'latency' ou 'cameras'
            window_minutes: Janela para análise
        
        Returns:
            String: 'increasing', 'decreasing', ou 'stable'
        """
        # Pega métricas em duas metades da janela
        recent = self.dl.get_recent_metrics(window_minutes)
        
        if not recent or len(recent) < 4:
            return 'unknown'
        
        half = len(recent) // 2
        first_half = recent[half:]  # Mais antigo
        second_half = recent[:half]  # Mais recente
        
        if metric == 'latency':
            first_avg = sum(m['latency_us'] for m in first_half) / len(first_half)
            second_avg = sum(m['latency_us'] for m in second_half) / len(second_half)
        else:
            first_avg = sum(m['cameras_active'] for m in first_half) / len(first_half)
            second_avg = sum(m['cameras_active'] for m in second_half) / len(second_half)
        
        if first_avg == 0:
            first_avg = 1
        
        change_ratio = (second_avg - first_avg) / first_avg
        
        if change_ratio > THRESHOLDS['trend_threshold']:
            return 'increasing'
        elif change_ratio < -THRESHOLDS['trend_threshold']:
            return 'decreasing'
        else:
            return 'stable'
    
    def detect_seasonal_patterns(self, days_back=7):
        """
        Detecta padrões sazonais por hora do dia.
        
        Returns:
            List de dicts com estatísticas por hora:
                - hour: hora do dia
                - avg_cameras: média de câmeras
                - avg_latency: média de latência
                - low_activity_ratio: % do tempo em baixa atividade
                - confidence: confiança no padrão
        """
        hourly_stats = self.dl.get_hourly_stats(days_back * 24)
        
        patterns = defaultdict(lambda: {
            'cameras_sum': 0,
            'latency_sum': 0,
            'count': 0,
            'low_activity_count': 0
        })
        
        for stat in hourly_stats:
            hour = stat['hour']
            patterns[hour]['cameras_sum'] += stat['avg_cameras']
            patterns[hour]['latency_sum'] += stat['avg_latency']
            patterns[hour]['count'] += 1
            if stat['avg_cameras'] <= THRESHOLDS['low_activity_cameras']:
                patterns[hour]['low_activity_count'] += 1
        
        results = []
        for hour in range(24):
            p = patterns[hour]
            if p['count'] > 0:
                avg_cameras = p['cameras_sum'] / p['count']
                avg_latency = p['latency_sum'] / p['count']
                low_ratio = p['low_activity_count'] / p['count']
                
                results.append({
                    'hour': hour,
                    'avg_cameras': avg_cameras,
                    'avg_latency_us': avg_latency,
                    'low_activity_ratio': low_ratio,
                    'confidence': min(0.95, p['count'] / 14),  # Confiança baseada em dias
                    'data_days': p['count']
                })
        
        return sorted(results, key=lambda x: x['hour'])
    
    def detect_day_of_week_pattern(self, days_back=7):
        """
        Detecta padrões por dia da semana.
        
        Returns:
            List de dicts com estatísticas por dia:
                - day_of_week: 0-6
                - day_name: nome do dia
                - avg_cameras: média de câmeras
                - avg_latency: média de latência
                - energy_saves: número de economias permitidas
        """
        daily_stats = self.dl.get_daily_stats(days_back)
        
        patterns = defaultdict(lambda: {
            'cameras_sum': 0,
            'latency_sum': 0,
            'count': 0
        })
        
        for stat in daily_stats:
            dow = stat['day_of_week']
            patterns[dow]['cameras_sum'] += stat['avg_cameras']
            patterns[dow]['latency_sum'] += stat['avg_latency']
            patterns[dow]['count'] += 1
        
        results = []
        for dow in range(7):
            p = patterns[dow]
            if p['count'] > 0:
                results.append({
                    'day_of_week': dow,
                    'day_name': DAY_NAMES[dow],
                    'avg_cameras': p['cameras_sum'] / p['count'],
                    'avg_latency_us': p['latency_sum'] / p['count'],
                    'data_days': p['count'],
                    'is_weekend': dow >= 5
                })
        
        return sorted(results, key=lambda x: x['day_of_week'])
    
    def calculate_energy_window(self, days_back=7):
        """
        Calcula janela ótima de economia de energia.
        
        Baseado em padrões históricos, identifica horários
        onde é seguro desligar células/mmWave.
        
        Returns:
            Dict com:
                - window_start: hora de início (ex: '22:00')
                - window_end: hora de fim (ex: '06:00')
                - confidence: confiança na janela
                - days: dias da semana aplicáveis
                - exclude_days: dias excluídos
                - reason: justificativa
        """
        hourly_patterns = self.detect_seasonal_patterns(days_back)
        daily_patterns = self.detect_day_of_week_pattern(days_back)
        
        # Identifica horários de baixa atividade
        low_activity_hours = []
        for hp in hourly_patterns:
            if hp['low_activity_ratio'] >= THRESHOLDS['high_confidence_ratio']:
                low_activity_hours.append({
                    'hour': hp['hour'],
                    'confidence': hp['confidence'],
                    'low_ratio': hp['low_activity_ratio']
                })
        
        if not low_activity_hours:
            return {
                'window_start': None,
                'window_end': None,
                'confidence': 0.0,
                'hours': [],
                'days': [],
                'exclude_days': list(range(7)),
                'reason': 'Sem dados suficientes para identificar janela'
            }
        
        # Ordena por hora
        low_activity_hours.sort(key=lambda x: x['hour'])
        
        # Identifica blocos contínuos de baixa atividade
        blocks = []
        current_block = [low_activity_hours[0]['hour']]
        
        for i in range(1, len(low_activity_hours)):
            diff = low_activity_hours[i]['hour'] - low_activity_hours[i-1]['hour']
            if diff <= 2:  # Permite gap de até 2 horas
                current_block.append(low_activity_hours[i]['hour'])
            else:
                if len(current_block) >= 3:  # Mínimo 3 horas
                    blocks.append(current_block)
                current_block = [low_activity_hours[i]['hour']]
        
        if len(current_block) >= 3:
            blocks.append(current_block)
        
        # Seleciona melhor bloco (mais horas + maior confiança)
        best_block = max(blocks, key=lambda b: (len(b), 
                          sum(low_activity_hours[low_activity_hours.index({'hour': h})]['confidence'] 
                              for h in b if {'hour': h} in low_activity_hours)))
        
        # Identifica dias da semana aplicáveis
        weekday_patterns = [p for p in daily_patterns if not p['is_weekend']]
        weekend_patterns = [p for p in daily_patterns if p['is_weekend']]
        
        weekday_avg = sum(p['avg_cameras'] for p in weekday_patterns) / len(weekday_patterns) if weekday_patterns else 10
        weekend_avg = sum(p['avg_cameras'] for p in weekend_patterns) / len(weekend_patterns) if weekend_patterns else 10
        
        if weekend_avg < weekday_avg * 0.7:
            include_days = [0, 1, 2, 3, 4, 5, 6]  # Todos os dias
            exclude_days = []
        else:
            include_days = [0, 1, 2, 3, 4]  # Segunda a sexta
            exclude_days = [5, 6]  # Fim de semana
        
        # Formata janela
        start_hour = min(best_block)
        end_hour = max(best_block)
        
        # Se atravessa meia-noite
        if end_hour < start_hour:
            window_start = f"{start_hour:02d}:00"
            window_end = f"{end_hour:02d}:00"
            overnight = True
        else:
            window_start = f"{start_hour:02d}:00"
            window_end = f"{end_hour:02d}:00"
            overnight = False
        
        avg_confidence = sum(low_activity_hours[low_activity_hours.index({'hour': h})]['confidence'] 
                           for h in best_block if {'hour': h} in low_activity_hours) / len(best_block)
        
        return {
            'window_start': window_start,
            'window_end': window_end,
            'overnight': overnight,
            'confidence': avg_confidence,
            'hours': best_block,
            'duration_hours': len(best_block),
            'days': include_days,
            'day_names': [DAY_NAMES[d] for d in include_days],
            'exclude_days': exclude_days,
            'exclude_day_names': [DAY_NAMES[d] for d in exclude_days],
            'reason': f'Padrão de baixa atividade detectado em {len(best_block)} horas consecutivas',
            'pattern_type': 'night_low_activity'
        }
    
    def predict_next_hour(self):
        """
        Prediz métricas para a próxima hora baseado em padrões.
        
        Returns:
            Dict com predição:
                - predicted_cameras: câmeras previstas
                - predicted_latency_us: latência prevista
                - confidence: confiança na predição
                - based_on: fonte dos dados (hora/dia)
        """
        now = datetime.now()
        next_hour = (now.hour + 1) % 24
        next_dow = now.weekday()
        
        # Busca dados históricos da próxima hora
        pattern_data = self.dl.get_pattern_data(hour=next_hour, day_of_week=next_dow)
        
        if pattern_data and pattern_data['sample_count'] >= THRESHOLDS['min_samples_for_pattern']:
            return {
                'predicted_cameras': pattern_data['avg_cameras'],
                'predicted_latency_us': pattern_data['avg_latency'],
                'confidence': min(0.9, pattern_data['sample_count'] / 50),
                'based_on': f'historical_data_hour_{next_hour}_dow_{next_dow}',
                'sample_count': pattern_data['sample_count']
            }
        
        # Fallback: usa média geral
        all_stats = self.dl.get_hourly_stats(168)  # 1 semana
        if all_stats:
            avg_cameras = sum(s['avg_cameras'] for s in all_stats) / len(all_stats)
            avg_latency = sum(s['avg_latency'] for s in all_stats) / len(all_stats)
            return {
                'predicted_cameras': avg_cameras,
                'predicted_latency_us': avg_latency,
                'confidence': 0.3,
                'based_on': 'historical_average',
                'sample_count': len(all_stats)
            }
        
        # Último recurso: usa estado atual
        current = self.analyze_current()
        return {
            'predicted_cameras': current['current_cameras'],
            'predicted_latency_us': current['current_latency_us'],
            'confidence': 0.2,
            'based_on': 'current_state',
            'sample_count': 0
        }
    
    def should_allow_energy_saving(self):
        """
        Decide se deve permitir economia de energia.
        
        Usa múltiplas fontes:
        1. Análise atual (métricas recentes)
        2. Padrões horários históricos
        3. Predição para próxima hora
        4. Tendência atual
        
        Returns:
            Dict com:
                - allowed: bool
                - confidence: float 0-1
                - reason: justificativa
                - recommendation: 'ALLOW', 'DENY', 'CONDITIONAL'
        """
        current = self.analyze_current()
        prediction = self.predict_next_hour()
        trend = current['trend']
        
        # Score inicial
        score = 0.5
        reasons = []
        
        # Fatores positivos (permitir economia)
        if current['low_activity']:
            score += 0.3
            reasons.append('Baixa atividade atual detectada')
        
        if current['is_night']:
            score += 0.2
            reasons.append('Horário noturno')
        
        if current['is_weekend']:
            score += 0.1
            reasons.append('Fim de semana')
        
        if prediction['predicted_cameras'] <= THRESHOLDS['low_activity_cameras']:
            score += 0.15
            reasons.append(f"Predição: {prediction['predicted_cameras']:.1f} câmeras")
        
        # Fatores negativos (negar economia)
        if current['trend'] == 'increasing':
            score -= 0.3
            reasons.append('Tendência de latência SUBINDO')
        
        if prediction['predicted_latency_us'] > THRESHOLDS['low_activity_latency_ms'] * 1000 * 2:
            score -= 0.25
            reasons.append('Predição de alta latência')
        
        # Normaliza score
        score = max(0, min(1, score))
        
        # Decisão
        if score >= 0.7:
            recommendation = 'ALLOW'
        elif score <= 0.3:
            recommendation = 'DENY'
        else:
            recommendation = 'CONDITIONAL'
        
        return {
            'allowed': recommendation == 'ALLOW',
            'conditional': recommendation == 'CONDITIONAL',
            'score': score,
            'confidence': (current['confidence'] + prediction['confidence']) / 2,
            'recommendation': recommendation,
            'reasons': reasons,
            'current_state': current,
            'prediction': prediction
        }
    
    def get_summary(self):
        """
        Retorna resumo completo da análise de padrões.
        
        Returns:
            Dict com informações consolidadas.
        """
        current = self.analyze_current()
        window = self.calculate_energy_window()
        decision = self.should_allow_energy_saving()
        trend = self.analyze_trend('latency', 60)
        
        return {
            'timestamp': int(time.time()),
            'datetime': datetime.now().isoformat(),
            'current_analysis': current,
            'energy_window': window,
            'energy_decision': decision,
            'latency_trend': trend,
            'thresholds': THRESHOLDS
        }


def main():
    """Teste do Pattern Engine"""
    print("=" * 70)
    print("rApp Pattern Recognition Engine - Teste")
    print("=" * 70)
    
    # Inicializa
    pe = PatternRecognition()
    
    # Análise atual
    print("\n[1] Análise Atual:")
    current = pe.analyze_current()
    for key, value in current.items():
        print(f"    {key}: {value}")
    
    # Padrões horários
    print("\n[2] Padrões Horários (top 5 horas de baixa atividade):")
    hourly = pe.detect_seasonal_patterns(7)
    low_hours = sorted(hourly, key=lambda x: x['low_activity_ratio'], reverse=True)[:5]
    for h in low_hours:
        print(f"    {h['hour']:02d}:00 - Cameras: {h['avg_cameras']:.1f}, "
              f"Low Activity: {h['low_activity_ratio']*100:.0f}%, "
              f"Confidence: {h['confidence']*100:.0f}%")
    
    # Padrões por dia
    print("\n[3] Padrões por Dia da Semana:")
    daily = pe.detect_day_of_week_pattern(7)
    for d in daily:
        weekend_tag = " [FIM DE SEMANA]" if d['is_weekend'] else ""
        print(f"    {d['day_name']}: Cameras: {d['avg_cameras']:.1f}, "
              f"Latency: {d['avg_latency_us']/1000:.0f}ms{weekend_tag}")
    
    # Janela de economia
    print("\n[4] Janela de Economia Calculada:")
    window = pe.calculate_energy_window()
    print(f"    Janela: {window['window_start']} - {window['window_end']}")
    print(f"    Confiança: {window['confidence']*100:.0f}%")
    print(f"    Duração: {window.get('duration_hours', 0)} horas")
    print(f"    Dias: {', '.join(window.get('day_names', []))}")
    print(f"    Motivo: {window['reason']}")
    
    # Predição
    print("\n[5] Predição para Próxima Hora:")
    pred = pe.predict_next_hour()
    for key, value in pred.items():
        print(f"    {key}: {value}")
    
    # Decisão de economia
    print("\n[6] Decisão de Economia de Energia:")
    decision = pe.should_allow_energy_saving()
    print(f"    Recomendação: {decision['recommendation']}")
    print(f"    Score: {decision['score']*100:.0f}%")
    print(f"    Confiança: {decision['confidence']*100:.0f}%")
    print(f"    Motivos:")
    for r in decision['reasons']:
        print(f"      - {r}")
    
    # Resumo
    print("\n[7] Resumo Completo:")
    summary = pe.get_summary()
    print(f"    Timestamp: {summary['datetime']}")
    print(f"    Tendência Latência: {summary['latency_trend']}")
    print(f"    Janela Economia: {summary['energy_window']['window_start']} - "
          f"{summary['energy_window']['window_end']}")
    print(f"    Decisão Energia: {summary['energy_decision']['recommendation']}")
    
    print("\n" + "=" * 70)
    print("Teste concluído!")
    print("=" * 70)


if __name__ == "__main__":
    main()
