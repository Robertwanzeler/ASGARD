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
    - Tendências (latência, throughput, jitter, packet loss)
    - Janelas ótimas para economia de energia
    
    Usa métricas estendidas:
    - Latência (us), Jitter (us), Throughput (kbps)
    - Packet Loss Rate, PDCP PDUs, MCS/TB Size
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
        self.extended_thresholds = {
            'good_throughput_kbps': 1000,
            'acceptable_throughput_kbps': 500,
            'good_jitter_us': 1000,
            'acceptable_jitter_us': 5000,
            'good_packet_loss': 0.01,
            'acceptable_packet_loss': 0.05,
            'critical_latency_us': 10000,
            'warning_latency_us': 5000,
        }
    
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
        extended = self.analyze_extended_metrics()
        
        return {
            'timestamp': int(time.time()),
            'datetime': datetime.now().isoformat(),
            'current_analysis': current,
            'energy_window': window,
            'energy_decision': decision,
            'latency_trend': trend,
            'extended_metrics_analysis': extended,
            'thresholds': THRESHOLDS,
            'extended_thresholds': self.extended_thresholds
        }
    
    def get_extended_metrics_history(self, minutes=60):
        """Retorna histórico de métricas estendidas."""
        try:
            if not self.dl.conn:
                return []
            cursor = self.dl.conn.cursor()
            cutoff = int(time.time()) - (minutes * 60)
            cursor.execute("""
                SELECT timestamp, datetime, sim_time_s,
                       global_worst_latency_us, global_avg_latency_us,
                       global_min_latency_us, global_max_latency_us,
                       global_jitter_us, global_packet_loss_rate,
                       total_active_ues, total_tx_bytes, total_rx_bytes,
                       throughput_kbps
                FROM extended_metrics
                WHERE timestamp >= ?
                ORDER BY timestamp ASC
            """, (cutoff,))
            
            columns = [desc[0] for desc in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]
        except Exception as e:
            print(f"[PatternEngine] ERRO ao buscar métricas estendidas: {e}")
            return []
    
    def get_ue_metrics_history(self, imsi=None, minutes=60):
        """Retorna histórico de métricas por UE."""
        try:
            if not self.dl.conn:
                return []
            cursor = self.dl.conn.cursor()
            cutoff = int(time.time()) - (minutes * 60)
            
            if imsi:
                cursor.execute("""
                    SELECT timestamp, imsi, device_type, cell_id,
                           latency_us, latency_avg_us, latency_min_us, latency_max_us,
                           jitter_us, tx_bytes, rx_bytes, throughput_kbps,
                           packet_count, mcs_avg, tb_size_avg, is_critical
                    FROM ue_metrics
                    WHERE timestamp >= ? AND imsi = ?
                    ORDER BY timestamp ASC
                """, (cutoff, imsi))
            else:
                cursor.execute("""
                    SELECT timestamp, imsi, device_type, cell_id,
                           latency_us, latency_avg_us, jitter_us,
                           throughput_kbps, mcs_avg, is_critical
                    FROM ue_metrics
                    WHERE timestamp >= ?
                    ORDER BY timestamp ASC
                """, (cutoff,))
            
            columns = [desc[0] for desc in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]
        except Exception as e:
            print(f"[PatternEngine] ERRO ao buscar métricas UE: {e}")
            return []
    
    def analyze_extended_metrics(self, window_minutes=30):
        """
        Analisa métricas estendidas (throughput, jitter, packet loss).
        
        Returns:
            Dict com análise completa das métricas expandidas.
        """
        history = self.get_extended_metrics_history(window_minutes)
        
        if not history:
            return {
                'status': 'no_data',
                'message': 'Sem dados suficientes para análise estendida',
                'sample_count': 0
            }
        
        latencies = [h['global_avg_latency_us'] for h in history if h['global_avg_latency_us']]
        worst_latencies = [h['global_worst_latency_us'] for h in history if h['global_worst_latency_us']]
        jitters = [h['global_jitter_us'] for h in history if h['global_jitter_us']]
        packet_losses = [h['global_packet_loss_rate'] for h in history if h['global_packet_loss_rate'] is not None]
        throughputs = [h['throughput_kbps'] for h in history if h['throughput_kbps']]
        ues_counts = [h['total_active_ues'] for h in history if h['total_active_ues'] is not None]
        
        # Métricas robustas dos percentis
        latency_p5_list = [h.get('latency_p5_us', 0) for h in history if h.get('latency_p5_us', 0) > 0]
        latency_p95_list = [h.get('latency_p95_us', 0) for h in history if h.get('latency_p95_us', 0) > 0]
        latency_min_nonzero_list = [h.get('latency_min_nonzero_us', 0) for h in history if h.get('latency_min_nonzero_us', 0) > 0]
        
        def avg(lst):
            return sum(lst) / len(lst) if lst else 0
        
        def trend_list(lst):
            if len(lst) < 4:
                return 'unknown'
            half = len(lst) // 2
            first = avg(lst[half:])
            second = avg(lst[:half])
            if first == 0:
                first = 1
            change = (second - first) / first
            if change > 0.1:
                return 'increasing'
            elif change < -0.1:
                return 'decreasing'
            return 'stable'
        
        th = self.extended_thresholds
        avg_latency = avg(latencies)
        avg_jitter = avg(jitters)
        avg_throughput = avg(throughputs)
        avg_packet_loss = avg(packet_losses)
        
        quality_score = 100
        if avg_latency > th['critical_latency_us']:
            quality_score -= 40
        elif avg_latency > th['warning_latency_us']:
            quality_score -= 20
        elif avg_latency < th['warning_latency_us']:
            quality_score += 10
            
        if avg_jitter > th['acceptable_jitter_us']:
            quality_score -= 25
        elif avg_jitter < th['good_jitter_us']:
            quality_score += 10
            
        if avg_packet_loss > th['acceptable_packet_loss']:
            quality_score -= 30
        elif avg_packet_loss < th['good_packet_loss']:
            quality_score += 10
            
        if avg_throughput < th['acceptable_throughput_kbps']:
            quality_score -= 15
        elif avg_throughput > th['good_throughput_kbps']:
            quality_score += 10
        
        quality_score = max(0, min(100, quality_score))
        
        return {
            'status': 'analyzed',
            'sample_count': len(history),
            'window_minutes': window_minutes,
            'global_metrics': {
                'avg_latency_us': avg_latency,
                'max_latency_us': max(worst_latencies) if worst_latencies else 0,
                'avg_jitter_us': avg_jitter,
                'avg_throughput_kbps': avg_throughput,
                'avg_packet_loss_rate': avg_packet_loss,
                'avg_active_ues': avg(ues_counts),
            },
            'trends': {
                'latency': trend_list(latencies),
                'jitter': trend_list(jitters),
                'throughput': trend_list(throughputs),
                'packet_loss': trend_list(packet_losses),
            },
            'quality_score': quality_score,
            'quality_grade': self._quality_grade(quality_score),
            'latency_status': self._metric_status(avg_latency, th['warning_latency_us'], th['critical_latency_us']),
            'jitter_status': self._metric_status(avg_jitter, th['good_jitter_us'], th['acceptable_jitter_us']),
            'throughput_status': 'good' if avg_throughput >= th['good_throughput_kbps'] else ('acceptable' if avg_throughput >= th['acceptable_throughput_kbps'] else 'poor'),
            'packet_loss_status': self._metric_status(avg_packet_loss, th['good_packet_loss'], th['acceptable_packet_loss']),
            'robust_metrics': {
                'avg_p5_latency_us': avg(latency_p5_list) if latency_p5_list else 0,
                'avg_p95_latency_us': avg(latency_p95_list) if latency_p95_list else 0,
                'avg_min_nonzero_latency_us': avg(latency_min_nonzero_list) if latency_min_nonzero_list else 0,
                'p5_count': len(latency_p5_list),
                'p95_count': len(latency_p95_list),
                'min_nonzero_count': len(latency_min_nonzero_list),
            },
        }
    
    def _metric_status(self, value, good_threshold, poor_threshold):
        """Determina status de uma métrica."""
        if value <= good_threshold:
            return 'good'
        elif value <= poor_threshold:
            return 'acceptable'
        return 'poor'
    
    def _quality_grade(self, score):
        """Converte score numérico em letra."""
        if score >= 90:
            return 'A'
        elif score >= 75:
            return 'B'
        elif score >= 60:
            return 'C'
        elif score >= 40:
            return 'D'
        return 'F'
    
    def analyze_ue_performance(self, window_minutes=30):
        """
        Analisa performance por UE.
        
        Returns:
            Dict com ranking de UEs por performance.
        """
        history = self.get_ue_metrics_history(minutes=window_minutes)
        
        if not history:
            return {'status': 'no_data', 'message': 'Sem dados de UEs'}
        
        ue_scores = defaultdict(lambda: {'latencies': [], 'jitters': [], 'throughputs': [], 'packets': 0})
        
        for rec in history:
            imsi = rec['imsi']
            ue_scores[imsi]['latencies'].append(rec.get('latency_us', 0) or 0)
            ue_scores[imsi]['jitters'].append(rec.get('jitter_us', 0) or 0)
            ue_scores[imsi]['throughputs'].append(rec.get('throughput_kbps', 0) or 0)
            ue_scores[imsi]['packets'] += rec.get('packet_count', 0) or 0
        
        results = []
        for imsi, data in ue_scores.items():
            if not data['latencies']:
                continue
            
            avg_lat = sum(data['latencies']) / len(data['latencies'])
            avg_jit = sum(data['jitters']) / len(data['jitters'])
            avg_tp = sum(data['throughputs']) / len(data['throughputs'])
            
            score = 100
            th = self.extended_thresholds
            if avg_lat > th['critical_latency_us']:
                score -= 50
            elif avg_lat > th['warning_latency_us']:
                score -= 25
            if avg_jit > th['acceptable_jitter_us']:
                score -= 25
            if avg_tp < th['acceptable_throughput_kbps']:
                score -= 20
            
            results.append({
                'imsi': imsi,
                'avg_latency_us': avg_lat,
                'avg_jitter_us': avg_jit,
                'avg_throughput_kbps': avg_tp,
                'total_packets': data['packets'],
                'score': max(0, score),
                'grade': self._quality_grade(score)
            })
        
        results.sort(key=lambda x: x['score'], reverse=True)
        
        return {
            'status': 'analyzed',
            'total_ues': len(results),
            'ue_rankings': results,
            'worst_performer': results[-1] if results else None,
            'best_performer': results[0] if results else None
        }
    
    def calculate_network_efficiency(self, window_minutes=30):
        """
        Calcula eficiência geral da rede baseado em métricas estendidas.
        
        Returns:
            Dict com métricas de eficiência.
        """
        extended = self.analyze_extended_metrics(window_minutes)
        ue_analysis = self.analyze_ue_performance(window_minutes)
        
        if extended.get('status') != 'analyzed':
            return {'status': 'no_data'}
        
        th = self.extended_thresholds
        gm = extended['global_metrics']
        
        latency_efficiency = max(0, 100 - (gm['avg_latency_us'] / th['critical_latency_us'] * 100))
        jitter_efficiency = max(0, 100 - (gm['avg_jitter_us'] / th['acceptable_jitter_us'] * 100))
        throughput_efficiency = min(100, gm['avg_throughput_kbps'] / th['good_throughput_kbps'] * 100)
        packet_efficiency = max(0, 100 - (gm['avg_packet_loss_rate'] * 1000))
        
        overall = (latency_efficiency * 0.35 + 
                   jitter_efficiency * 0.20 + 
                   throughput_efficiency * 0.30 + 
                   packet_efficiency * 0.15)
        
        return {
            'status': 'analyzed',
            'overall_efficiency': round(overall, 1),
            'components': {
                'latency_efficiency': round(latency_efficiency, 1),
                'jitter_efficiency': round(jitter_efficiency, 1),
                'throughput_efficiency': round(throughput_efficiency, 1),
                'packet_efficiency': round(packet_efficiency, 1),
            },
            'quality_score': extended['quality_score'],
            'quality_grade': extended['quality_grade'],
            'recommendations': self._generate_recommendations(extended, ue_analysis)
        }
    
    def _generate_recommendations(self, extended, ue_analysis):
        """Gera recomendações baseadas na análise."""
        recs = []
        th = self.extended_thresholds
        
        if extended['latency_status'] == 'poor':
            recs.append({'priority': 'high', 'action': 'Investigar causas de alta latência', 'metric': 'latency'})
        
        if extended['jitter_status'] == 'poor':
            recs.append({'priority': 'high', 'action': 'Reduzir jitter - verificar congestionamento', 'metric': 'jitter'})
        
        if extended['packet_loss_status'] == 'poor':
            recs.append({'priority': 'critical', 'action': 'Packet loss alto - verificar enlace', 'metric': 'packet_loss'})
        
        if extended['throughput_status'] == 'poor':
            recs.append({'priority': 'medium', 'action': 'Melhorar throughput - considerar rebalanceamento', 'metric': 'throughput'})
        
        worst = ue_analysis.get('worst_performer')
        if worst and worst['score'] < 50:
            recs.append({'priority': 'medium', 'action': f'UE {worst["imsi"]} com performance baixa - investigar', 'metric': 'ue_performance'})
        
        if extended['quality_score'] >= 80:
            recs.append({'priority': 'info', 'action': 'Rede em bom estado', 'metric': 'overall'})
        
        return recs


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
    
    # Análise de métricas estendidas (NOVO)
    print("\n[2] Métricas Estendidas (Throughput, Jitter, Packet Loss):")
    extended = pe.analyze_extended_metrics(30)
    if extended.get('status') == 'analyzed':
        gm = extended['global_metrics']
        print(f"    Latência Média: {gm['avg_latency_us']:.1f} us ({extended['latency_status']})")
        print(f"    Jitter Médio: {gm['avg_jitter_us']:.1f} us ({extended['jitter_status']})")
        print(f"    Throughput: {gm['avg_throughput_kbps']:.1f} kbps ({extended['throughput_status']})")
        print(f"    Packet Loss: {gm['avg_packet_loss_rate']*100:.2f}% ({extended['packet_loss_status']})")
        print(f"    Quality Score: {extended['quality_score']:.0f}/100 (Grade: {extended['quality_grade']})")
        print(f"    Tendências: Latência={extended['trends']['latency']}, "
              f"Jitter={extended['trends']['jitter']}, "
              f"Throughput={extended['trends']['throughput']}")
    else:
        print(f"    Status: {extended.get('message', 'Sem dados')}")
    
    # Eficiência da rede (NOVO)
    print("\n[3] Eficiência da Rede:")
    efficiency = pe.calculate_network_efficiency(30)
    if efficiency.get('status') == 'analyzed':
        print(f"    Eficiência Geral: {efficiency['overall_efficiency']:.1f}%")
        comp = efficiency['components']
        print(f"    Latência: {comp['latency_efficiency']:.1f}%")
        print(f"    Jitter: {comp['jitter_efficiency']:.1f}%")
        print(f"    Throughput: {comp['throughput_efficiency']:.1f}%")
        print(f"    Packet Loss: {comp['packet_efficiency']:.1f}%")
        print(f"    Recomendações:")
        for rec in efficiency.get('recommendations', []):
            print(f"      [{rec['priority'].upper()}] {rec['action']}")
    else:
        print(f"    Status: {efficiency.get('message', 'Sem dados')}")
    
    # Padrões horários
    print("\n[4] Padrões Horários (top 5 horas de baixa atividade):")
    hourly = pe.detect_seasonal_patterns(7)
    low_hours = sorted(hourly, key=lambda x: x['low_activity_ratio'], reverse=True)[:5]
    for h in low_hours:
        print(f"    {h['hour']:02d}:00 - Cameras: {h['avg_cameras']:.1f}, "
              f"Low Activity: {h['low_activity_ratio']*100:.0f}%, "
              f"Confidence: {h['confidence']*100:.0f}%")
    
    # Padrões por dia
    print("\n[5] Padrões por Dia da Semana:")
    daily = pe.detect_day_of_week_pattern(7)
    for d in daily:
        weekend_tag = " [FIM DE SEMANA]" if d['is_weekend'] else ""
        print(f"    {d['day_name']}: Cameras: {d['avg_cameras']:.1f}, "
              f"Latency: {d['avg_latency_us']/1000:.0f}ms{weekend_tag}")
    
    # Janela de economia
    print("\n[6] Janela de Economia Calculada:")
    window = pe.calculate_energy_window()
    print(f"    Janela: {window['window_start']} - {window['window_end']}")
    print(f"    Confiança: {window['confidence']*100:.0f}%")
    print(f"    Duração: {window.get('duration_hours', 0)} horas")
    print(f"    Dias: {', '.join(window.get('day_names', []))}")
    print(f"    Motivo: {window['reason']}")
    
    # Predição
    print("\n[7] Predição para Próxima Hora:")
    pred = pe.predict_next_hour()
    for key, value in pred.items():
        print(f"    {key}: {value}")
    
    # Decisão de economia
    print("\n[8] Decisão de Economia de Energia:")
    decision = pe.should_allow_energy_saving()
    print(f"    Recomendação: {decision['recommendation']}")
    print(f"    Score: {decision['score']*100:.0f}%")
    print(f"    Confiança: {decision['confidence']*100:.0f}%")
    print(f"    Motivos:")
    for r in decision['reasons']:
        print(f"      - {r}")
    
    # Resumo
    print("\n[9] Resumo Completo:")
    summary = pe.get_summary()
    print(f"    Timestamp: {summary['datetime']}")
    print(f"    Tendência Latência: {summary['latency_trend']}")
    print(f"    Quality Score: {summary['extended_metrics_analysis'].get('quality_score', 'N/A')}")
    print(f"    Janela Economia: {summary['energy_window']['window_start']} - "
          f"{summary['energy_window']['window_end']}")
    print(f"    Decisão Energia: {summary['energy_decision']['recommendation']}")
    
    print("\n" + "=" * 70)
    print("Teste concluído!")
    print("=" * 70)


if __name__ == "__main__":
    main()
