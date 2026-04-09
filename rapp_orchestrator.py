#!/usr/bin/env python3
"""
GreenRAN O-RAN - rApp-ResourceOptimizer
======================================

Responsabilidade: Orquestrador estratégico Non-RT RIC
- Coordena xApps (Slicer ↔ Energy Saver)
- Usa Data Lake para análise de padrões
- Detecta sazonalidade (ML moderado)
- Interface A1 para Near-RT RIC
- Traduz intenções do Agent-Al

Arquitetura:
    Non-RT RIC (≥1 segundo)
    ├── Data Lake (SQLite)
    ├── Pattern Engine (ML: Média Móvel + Sazonalidade)
    ├── Agent-Al-OpenRAN (Tradução de Intenções)
    └── A1 Policy Interface (JSON → Near-RT RIC)

Interface:
    /tmp/xapp_intents/
    ├── slicer.txt          → xApp SLICER escreve
    ├── energy_saver.txt    → xApp ENERGY SAVER escreve
    └── rapp_decision.txt   ← rApp escreve

    /tmp/rapp_policies/
    ├── energy_policy.json  ← A1: Energia para Near-RT RIC
    └── slice_policy.json  ← A1: Fatia para Near-RT RIC

    /tmp/agent_intent.json → Agent-Al escreve (opcional)

Uso:
    python3 rapp_orchestrator.py [--interval SECONDS] [--synthetic]
"""

import os
import sys
import time
import argparse
import signal
import json
from datetime import datetime
from collections import deque

sys.path.insert(0, '/home/robert/orange_nuclear')

from rapp_data_lake import DataLake
from rapp_pattern_engine import PatternRecognition
from rapp_agent_openran import AgentOpenRAN
from rapp_a1_interface import A1PolicyInterface
from rapp_ml_predictor import MLPredictor
# from rapp_synthetic_generator import SyntheticDataGenerator  # Removed - not available
from rapp_xapp_manager import XAppManager
from rapp_trend_analysis import TrendAnalysis
from energy_command_protocol import EnergyCommand

SLICER_INTENT_PATH = "/tmp/xapp_intents/slicer.txt"
ENERGY_INTENT_PATH = "/tmp/xapp_intents/energy_saver.txt"
RAPP_DECISION_PATH = "/tmp/xapp_intents/rapp_decision.txt"
EXTENDED_METRICS_PATH = "/tmp/xapp_metrics/extended_metrics.json"
XAPP_HEALTH_FILE = "/tmp/xapp_health.json"

DEFAULT_INTERVAL = 1  # Non-RT RIC: ≥1 segundo (O-RAN spec) - 1s para máxima responsividade


class RappResourceOptimizer:
    """
    rApp-ResourceOptimizer - Cérebro estratégico Non-RT RIC
    
    Integra todos os componentes:
    - Data Lake: Persistência de métricas
    - Pattern Engine: Detecção de padrões (ML)
    - Agent-Al: Tradução de intenções
    - A1 Interface: Políticas para Near-RT RIC
    """
    
    def __init__(self, interval=DEFAULT_INTERVAL, synthetic_days=0):
        """
        Inicializa o rApp.
        
        Args:
            interval: Intervalo de execução em segundos (mín 1s)
            synthetic_days: Dias de dados sintéticos a gerar (0=desabilitado)
        """
        self.interval = max(1, interval)
        self.running = True
        self.cycle = 0
        
        # Estatísticas
        self.stats = {
            'total_cycles': 0,
            'blocked': 0,
            'allowed': 0,
            'conditional': 0,
            'agent_overrides': 0,
            'rap_overrides': 0,  # rApp sobrescreve Slicer CRITICAL
            'preventive_blocks': 0,  # Blocos preventivos por trend
            'pattern_detected': 0,
            'sla_violations': 0,
            'ack_received': 0,
            'ack_pending': 0,
            'ack_timeout': 0
        }
        
        # Histórico de predições ML para exibição
        self.ml_history = deque(maxlen=50)  # Mantém últimas 50 predições
        self.history_display_interval = 1  # Exibir a cada ciclo (mais frequente)
        
        # Inicializa componentes
        print("[rApp] Inicializando componentes...")
        
        # Data Lake (SQLite)
        self.data_lake = DataLake()
        
        # Pattern Engine (ML)
        self.pattern_engine = PatternRecognition(self.data_lake)
        
        # Trend Analysis (Slope/Predição)
        self.trend_analysis = TrendAnalysis(self.data_lake)
        
        # Energy Command Protocol (com DataLake para gravar comandos)
        self.energy_cmd = EnergyCommand(data_lake=self.data_lake)
        
        # Agent-Al-OpenRAN
        self.agent = AgentOpenRAN()
        
        # A1 Interface
        self.a1 = A1PolicyInterface()

        # ML Predictor (Random Forest / XGBoost) com acesso ao banco
        self.ml_predictor = MLPredictor(data_lake=self.data_lake)

        # Retreinamento automático do ML (2x por dia = a cada 12h)
        self.ml_retrain_interval = 12 * 3600  # 12 horas em segundos
        self.ml_last_retrain = time.time()
        self.ml_retrain_count = 0

        # XApp Manager (controla ciclo de vida dos xApps)
        self.xapp_manager = XAppManager()
        
        # Limpar processos zumbis antes de iniciar
        self.xapp_manager.cleanup_zombies()
        
        # Gera dados sintéticos se solicitado
        if synthetic_days > 0:
            self._generate_synthetic_data(synthetic_days)
        
        # Setup signal handlers
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
        # Cria diretórios
        os.makedirs("/tmp/xapp_intents", exist_ok=True)
        
        # INICIA xApps CONTROLADOS PELO RAPP
        # Slicer SEMPRE inicia com rApp (prioridade)
        self._start_slicer()
        
        # Energy Saver NÃO inicia automaticamente - rApp decide quando ativar
        self._energy_active = False
        
        print(f"[rApp] Inicializado - Intervalo: {self.interval}s")
    
    def _signal_handler(self, signum, frame):
        """Handler para sinais de shutdown."""
        print("\n[rApp] Sinal de shutdown recebido")
        self.running = False
    
    def _start_slicer(self):
        """Inicia o xApp SLICER (prioridade - sempre ativo)."""
        print("[rApp] Iniciando xApp SLICER (prioridade)...")
        if self.xapp_manager.start("slicer"):
            if self.xapp_manager.wait_for_ready("slicer", timeout=10):
                print("[rApp] xApp SLICER pronto e ativo")
            else:
                print("[rApp] AVISO: xApp SLICER pode não estar pronto")
        else:
            print("[rApp] ERRO: Não foi possível iniciar xApp SLICER")
    
    def _start_energy_saver(self):
        """Inicia o xApp ENERGY SAVER (se condições permitirem)."""
        if self._energy_active:
            return True
        
        print("[rApp] Iniciando xApp ENERGY SAVER...")
        if self.xapp_manager.start("energy_saver"):
            if self.xapp_manager.wait_for_ready("energy_saver", timeout=10):
                self._energy_active = True
                print("[rApp] xApp ENERGY SAVER ativo")
                return True
            else:
                print("[rApp] AVISO: xApp ENERGY SAVER pode não estar pronto")
                self._energy_active = True
                return True
        else:
            print("[rApp] ERRO: Não foi possível iniciar xApp ENERGY SAVER")
            return False
    
    def _stop_energy_saver(self):
        """Para o xApp ENERGY SAVER (se estiver ativo)."""
        if not self._energy_active:
            return True
        
        print("[rApp] Parando xApp ENERGY SAVER...")
        if self.xapp_manager.stop("energy_saver"):
            self._energy_active = False
            print("[rApp] xApp ENERGY SAVER parado")
            return True
        else:
            print("[rApp] ERRO ao parar xApp ENERGY SAVER")
            return False
    
    def decide_xapp_activation(self, decision):
        """
        Decide quais xApps devem estar ativos baseado na decisão.
        
        Regras:
        - SLICER: SEMPRE ativo (prioridade)
        - ENERGY SAVER: Ativo se decision['energy_saver'] == 'ALLOWED' ou 'CONDITIONAL'
        - ENERGY SAVER: Desativado apenas se BLOCKED
        
        Args:
            decision: Dict com decisão do rApp
        """
        slicer_state = decision.get('slicer_state', 'UNKNOWN')
        
        # ENERGY SAVER: controlado pelo rApp
        # CONDITIONAL agora ATIVA o Energy Saver (economia moderada ativa)
        energy_decision = decision.get('energy_saver')
        
        if energy_decision in ['ALLOWED', 'CONDITIONAL']:
            # Slicer OK ou CONDITIONAL → Energy pode ativar/continuar
            if not self._energy_active:
                self._start_energy_saver()
        elif energy_decision == 'BLOCKED':
            # Slicer CRITICAL ou decisão BLOCKED → Energy para
            if self._energy_active:
                self._stop_energy_saver()
        # Outros estados (UNKNOWN, etc) → mantém estado atual
        
        # SLICER: NUNCA para (fallback de segurança)
        # Mesmo se rApp morrer, Slicer continua rodando
        return {
            'slicer_active': self.xapp_manager.is_running("slicer"),
            'energy_active': self._energy_active
        }
    
    def get_xapp_status(self):
        """Retorna status dos xApps e salva em arquivo."""
        status = {}
        
        # Status do SLICER
        slicer_pid = self.xapp_manager.get_pid('slicer')
        slicer_running = self.xapp_manager.is_running('slicer')
        status['SLICER'] = {
            'status': 'RUNNING' if slicer_running else 'STOPPED',
            'pid': slicer_pid,
            'last_cycle': getattr(self, 'cycle', 0),
            'total_restarts': 0,
            'last_heartbeat': datetime.now().isoformat()
        }
        
        # Status do ENERGY SAVER
        energy_pid = self.xapp_manager.get_pid('energy_saver')
        energy_running = self.xapp_manager.is_running('energy_saver')
        status['ENERGY'] = {
            'status': 'RUNNING' if energy_running else 'STOPPED',
            'pid': energy_pid,
            'last_cycle': getattr(self, 'cycle', 0),
            'total_restarts': 0,
            'last_heartbeat': datetime.now().isoformat()
        }
        
        # Salvar em arquivo para dashboard
        try:
            with open(XAPP_HEALTH_FILE, 'w') as f:
                json.dump(status, f, indent=2)
        except Exception as e:
            pass
        
        return status
    
    def read_slicer_intent(self):
        """Lê intenção do SLICER."""
        try:
            if not os.path.exists(SLICER_INTENT_PATH):
                return None
            
            intent = {}
            with open(SLICER_INTENT_PATH, 'r') as f:
                for line in f:
                    if '=' in line:
                        key, value = line.strip().split('=', 1)
                        intent[key.strip()] = value.strip()
            return intent
        except Exception as e:
            print(f"[rApp] ERRO ao ler SLICER: {e}")
            return None
    
    def read_energy_intent(self):
        """Lê intenção do ENERGY SAVER."""
        try:
            if not os.path.exists(ENERGY_INTENT_PATH):
                return None
            
            intent = {}
            with open(ENERGY_INTENT_PATH, 'r') as f:
                for line in f:
                    if '=' in line:
                        key, value = line.strip().split('=', 1)
                        intent[key.strip()] = value.strip()
            return intent
        except Exception as e:
            print(f"[rApp] ERRO ao ler ENERGY: {e}")
            return None
    
    def read_extended_metrics(self):
        """Lê métricas estendidas do JSON."""
        try:
            if not os.path.exists(EXTENDED_METRICS_PATH):
                return None
            
            with open(EXTENDED_METRICS_PATH, 'r') as f:
                return json.load(f)
        except Exception as e:
            return None
    
    def record_current_metrics(self, slicer_intent, energy_intent):
        """Registra métricas atuais no Data Lake."""
        latency = 0
        cameras = 0
        energy_state = None
        slicer_state = None
        
        if slicer_intent:
            latency = float(slicer_intent.get('WORST_LATENCY_US', 0))
            cameras = int(slicer_intent.get('ACTIVE_CAMERAS', 0))
            slicer_state = slicer_intent.get('STATE')
        
        if energy_intent:
            energy_state = energy_intent.get('INTENT')
        
        self.data_lake.record_metric(
            timestamp=int(time.time()),
            latency_us=int(latency),
            cameras_active=cameras,
            energy_state=energy_state,
            slicer_state=slicer_state
        )
        
        extended_metrics = self.read_extended_metrics()
        if extended_metrics:
            self.data_lake.record_extended_from_json(
                extended_metrics,
                energy_state=energy_state,
                slicer_state=slicer_state
            )
    
    def calculate_median_latency(self, minutes=5):
        """
        Calcula a mediana da latência dos últimos N minutos.
        
        Args:
            minutes: Número de minutos para buscar
            
        Returns:
            float: Mediana da latência em microssegundos, ou None se não houver dados
        """
        metrics = self.data_lake.get_recent_metrics(minutes)
        
        if not metrics:
            return None
        
        latencies = [m['latency_us'] for m in metrics if m['latency_us'] > 0]
        
        if not latencies:
            return None
        
        sorted_lat = sorted(latencies)
        n = len(sorted_lat)
        
        if n % 2 == 0:
            return (sorted_lat[n//2 - 1] + sorted_lat[n//2]) / 2
        else:
            return sorted_lat[n//2]
    
    def make_decision(self, slicer_intent, energy_intent):
        """
        Toma decisão estratégica.
        
        FLUXO UNIFICADO (Hierárquico):
        0. TREND ANALYSIS (Slope) - Predição preventiva
        1. Pattern Engine retorna análise completa (ML)
        2. CVaR/Variância - Reação a problemas reais
        3. Agent-Al Override (se ativo)
        4. rApp ARBITER - Override final baseado em trend+CVaR
        
        Returns:
            Dict com decisão completa.
        """
        decision = {
            'timestamp': int(time.time()),
            'energy_saver': 'UNKNOWN',
            'action': 'NONE',
            'reason': 'NO_DATA',
            'confidence': 0.0,
            'pattern': None,
            'agent_override': False,
            'agent_policy': None,
            'slicer_state': 'UNKNOWN',
            'energy_state': 'UNKNOWN',
            'pattern_analysis': None,
            'ml_decision': None,
            'trend_analysis': None,
            'preventive_block': False
        }
        
        slicer_state = 'UNKNOWN'
        energy_state = 'UNKNOWN'
        
        if slicer_intent:
            slicer_state = slicer_intent.get('STATE', 'UNKNOWN')
            decision['slicer_state'] = slicer_state
        
        if energy_intent:
            # energy_saver.txt usa ACTION=POWER_DOWN, não INTENT
            energy_state = energy_intent.get('ACTION', 'UNKNOWN')
            decision['energy_state'] = energy_state
            # Também capturar o power level
            decision['energy_power_level'] = energy_intent.get('POWER_LEVEL', 'UNKNOWN')
        
        # ========================================
        # ETAPA 0: TREND ANALYSIS (SLOPE) - PREDITIVA
        # O rApp detecta se latência está SUBINDO antes de bater crítico
        # ========================================
        trend_info = self.trend_analysis.calculate_latency_slope(window_minutes=5)
        trend_decision = self.trend_analysis.should_preempt_energy(trend_info)
        decision['trend_analysis'] = {
            'slope_ms_per_sec': trend_info.get('slope_ms_per_sec', 0),
            'trend': trend_info.get('trend', 'unknown'),
            'confidence': trend_info.get('confidence', 0),
            'time_to_critical': trend_info.get('time_to_critical_ms'),
            'current_latency_ms': trend_info.get('current_latency_ms', 0)
        }
        
        # Decisão preventiva baseada em tendência
        if trend_decision['preventive']:
            decision['energy_saver'] = 'BLOCKED'
            decision['action'] = 'PREVENTIVE_BLOCK'
            decision['reason'] = f"TREND: {trend_decision['reason']}"
            decision['confidence'] = trend_decision['confidence']
            decision['preventive_block'] = True
            self.stats['pattern_detected'] += 1
            print(f"\033[1;35m[rApp] PREVENTIVE: {trend_decision['reason']}\033[0m")
        
        # ========================================
        # ETAPA 1: PATTERN ENGINE (ML) - fonte única
        # ========================================
        pattern_analysis = self.pattern_engine.analyze_current()
        decision['pattern_analysis'] = pattern_analysis
        
        ml_decision = self.pattern_engine.should_allow_energy_saving()
        decision['ml_decision'] = ml_decision
        
        # Só aplica se não houve bloco preventivo por tendência
        if not decision['preventive_block']:
            if ml_decision['recommendation'] == 'ALLOW':
                decision['energy_saver'] = 'ALLOWED'
                decision['action'] = 'ACTIVATE_ENERGY_SAVING'
                decision['reason'] = f"ML: {ml_decision['reasons'][0]}" if ml_decision['reasons'] else 'LOW_ACTIVITY'
                decision['confidence'] = ml_decision['confidence']
                decision['pattern'] = pattern_analysis.get('pattern')
                self.stats['pattern_detected'] += 1
            
            elif ml_decision['recommendation'] == 'CONDITIONAL':
                decision['energy_saver'] = 'CONDITIONAL'
                decision['action'] = 'MONITOR'
                decision['reason'] = f"ML: Score={ml_decision['score']:.2f}"
                decision['confidence'] = ml_decision['confidence']
            
            else:  # DENY
                decision['energy_saver'] = 'BLOCKED'
                decision['action'] = 'AWAIT_CONDITIONS'
                decision['reason'] = f"ML: {ml_decision['reasons'][0]}" if ml_decision['reasons'] else 'HIGH_ACTIVITY'
                decision['confidence'] = ml_decision['confidence']
        
        # ========================================
        # ETAPA 2: CVaR/VARIÂNCIA - Lógica de 3 Faixas
        # NORMAL (<60ms), PREVENÇÃO (60-80ms), CRÍTICO (≥80ms)
        # ========================================
        
        # rApp: Usar métricas avançadas (CVaR, Variância)
        network_health = self.data_lake.get_network_health(window_minutes=5)
        
        # Thresholds com faixa de prevenção de 20ms
        CVAR_NORMAL_US = 60000       # 60ms - Faixa normal
        CVAR_CRITICAL_US = 80000     # 80ms - Faixa crítica (SLA)
        SLOPE_PREVENTION = 2.0       # 2ms/s - Prevenção
        STABILITY_THRESHOLD = 50     # Score mínimo de estabilidade
        
        if network_health:
            cvar_us = network_health['cvar_us']
            variance_us2 = network_health['variance_us2']
            median_us = network_health['median_us']
            p95_us = network_health['p95_us']
            stability_score = network_health['stability_score']
            
            # DEBUG: Log do CVaR calculado
            print(f"[rApp DEBUG] network_health.cvar_us = {cvar_us}us = {cvar_us/1000:.1f}ms")
            
            # Armazenar no decision para debugging
            decision['network_health'] = {
                'median_us': median_us,
                'p95_us': p95_us,
                'cvar_us': cvar_us,
                'variance_us2': variance_us2,
                'stability_score': stability_score
            }
        else:
            # Fallback para mediana se network_health não disponível
            cvar_us = self.calculate_median_latency(minutes=5) or 0
            variance_us2 = 0
            stability_score = 100
            decision['network_health'] = {'fallback': True, 'cvar_us': cvar_us}
            if cvar_us > 0:
                print(f"[rApp DEBUG] Fallback CVaR = {cvar_us}us = {cvar_us/1000:.1f}ms")
            else:
                print(f"[rApp DEBUG] Sem dados de CVaR disponíveis")
        
        # Só aplica CVaR se não houve bloco preventivo e temos dados
        if not decision['preventive_block'] and cvar_us is not None and cvar_us > 0:
            
            # Obter slope para decisões de prevenção
            slope = trend_info.get('slope_ms_per_sec', 0) if trend_info.get('valid') else 0
            
            # ===== LÓGICA DE COORDENAÇÃO rApp-xApps =====
            # REGRAS (refinadas com slope negativo vs zero):
            # 1. CÂMERAS SÃO PRIORIDADE MÁXIMA (se Slicer CRITICAL → BLOCKED)
            # 2. CVaR/UE ≥ 80ms → BLOCKED
            # 3. Slope > 2ms/s → BLOCKED (prevenção)
            # 4a. CVaR < 20ms + slope ≤ 0 → ECO (25% potência)
            # 4b. CVaR < 60ms + slope < -0.01 → ALLOWED (50%) - melhorando
            # 4c. CVaR < 60ms + abs(slope) < 0.01 → ALLOWED (60%) - estável
            # 4d. 60-80ms + slope < -0.01 → ALLOWED (70%) - melhorando
            # 4e. 60-80ms + abs(slope) < 0.01 → ALLOWED (80%) - estável
            # 5. CVaR < 60ms + slope > 0.01 → CONDITIONAL (70%) - piorando
            # 6. 60-80ms + slope > 0.01 → CONDITIONAL (90%) - piorando
            
            # Classificar estado do slope
            SLOPE_TOLERANCE = 0.01  # ms/s
            
            if slope < -SLOPE_TOLERANCE:
                slope_state = 'improving'  # melhorando
            elif slope > SLOPE_TOLERANCE:
                slope_state = 'worsening'  # piorando
            else:
                slope_state = 'stable'     # estável
            
            # REGRA 1: PRIORIDADE MÁXIMA - Câmeras em risco
            if slicer_state == 'CRITICAL':
                decision['energy_saver'] = 'BLOCKED'
                decision['action'] = 'FULL_POWER'
                decision['reason'] = f'CRÍTICO: Slicer CRITICAL - câmeras em risco - prioridade máxima'
                decision['confidence'] = 1.0
                self.stats['sla_violations'] += 1
                print(f"\033[1;31m[rApp] REGRA 1: Slicer CRITICAL - BLOCKED\033[0m")
            
            # REGRA 2: CVaR/UE ≥ 80ms → SLA em risco
            elif cvar_us >= CVAR_CRITICAL_US:
                decision['energy_saver'] = 'BLOCKED'
                decision['action'] = 'FULL_POWER'
                decision['reason'] = f'CRÍTICO: CVaR={cvar_us/1000:.1f}ms ≥ 80ms - SLA em risco!'
                decision['confidence'] = 1.0
                self.stats['sla_violations'] += 1
                print(f"\033[1;31m[rApp] REGRA 2: CVaR ≥ 80ms - BLOCKED\033[0m")
            
            # REGRA 3: Slope > 2ms/s → PREVENÇÃO
            elif slope > SLOPE_PREVENTION:
                decision['energy_saver'] = 'BLOCKED'
                decision['action'] = 'FULL_POWER'
                decision['reason'] = f'PREVENÇÃO: CVaR={cvar_us/1000:.1f}ms + slope=+{slope:.1f}ms/s → Bloqueio preventivo'
                decision['confidence'] = 0.8
                decision['preventive_block'] = True
                self.stats['preventive_blocks'] = self.stats.get('preventive_blocks', 0) + 1
                print(f"\033[1;33m[rApp] REGRA 3: Slope > 2ms/s - BLOCKED\033[0m")
            
            # REGRA 4a: CVaR < 20ms + slope ≤ 0 → ECO MODE (25% potência)
            elif cvar_us < 20000 and slope <= 0:
                decision['energy_saver'] = 'ALLOWED'
                decision['action'] = 'POWER_DOWN_ECO'
                decision['eco_mode'] = True
                decision['reason'] = f'ECO MODE: CVaR={cvar_us/1000:.1f}ms < 20ms + slope={slope:.2f}ms/s ({slope_state}) → Economia extrema (25%)'
                decision['confidence'] = 0.95
                print(f"\033[1;32m[rApp] REGRA 4a: CVaR < 20ms + Slope ≤ 0 - ECO MODE\033[0m")
            
            # REGRA 4b: CVaR < 60ms + slope < -0.01 → ALLOWED (50%) - melhorando
            elif cvar_us < CVAR_NORMAL_US and slope < -SLOPE_TOLERANCE:
                decision['energy_saver'] = 'ALLOWED'
                decision['action'] = 'POWER_DOWN'
                decision['reason'] = f'MELHORANDO: CVaR={cvar_us/1000:.1f}ms < 60ms + slope={slope:.2f}ms/s (descendo) → Economia (50%)'
                decision['confidence'] = 0.9
                print(f"\033[1;32m[rApp] REGRA 4b: CVaR < 60ms + Slope < 0 - ALLOWED 50%\033[0m")
            
            # REGRA 4c: CVaR < 60ms + slope ≈ 0 → ALLOWED (60%) - estável
            elif cvar_us < CVAR_NORMAL_US and abs(slope) < SLOPE_TOLERANCE:
                decision['energy_saver'] = 'ALLOWED'
                decision['action'] = 'POWER_DOWN'
                decision['reason'] = f'ESTÁVEL: CVaR={cvar_us/1000:.1f}ms < 60ms + slope={slope:.2f}ms/s (zero) → Economia moderada (60%)'
                decision['confidence'] = 0.85
                print(f"\033[1;32m[rApp] REGRA 4c: CVaR < 60ms + Slope ≈ 0 - ALLOWED 60%\033[0m")
            
            # REGRA 4d: 60-80ms + slope < -0.01 → ALLOWED (70%) - melhorando
            elif cvar_us < CVAR_CRITICAL_US and slope < -SLOPE_TOLERANCE:
                decision['energy_saver'] = 'ALLOWED'
                decision['action'] = 'POWER_DOWN'
                decision['reason'] = f'MELHORANDO: CVaR={cvar_us/1000:.1f}ms (60-80ms) + slope={slope:.2f}ms/s (descendo) → Economia (70%)'
                decision['confidence'] = 0.8
                print(f"\033[1;33m[rApp] REGRA 4d: 60ms ≤ CVaR < 80ms + Slope < 0 - ALLOWED 70%\033[0m")
            
            # REGRA 4e: 60-80ms + slope ≈ 0 → ALLOWED (80%) - estável
            elif cvar_us < CVAR_CRITICAL_US and abs(slope) < SLOPE_TOLERANCE:
                decision['energy_saver'] = 'ALLOWED'
                decision['action'] = 'POWER_DOWN'
                decision['reason'] = f'ESTÁVEL: CVaR={cvar_us/1000:.1f}ms (60-80ms) + slope={slope:.2f}ms/s (zero) → Economia moderada (80%)'
                decision['confidence'] = 0.75
                print(f"\033[1;33m[rApp] REGRA 4e: 60ms ≤ CVaR < 80ms + Slope ≈ 0 - ALLOWED 80%\033[0m")
            
            # REGRA 5: CVaR < 60ms + slope > 0.01 → CONDITIONAL (70%) - piorando
            elif cvar_us < CVAR_NORMAL_US and slope > SLOPE_TOLERANCE:
                decision['energy_saver'] = 'CONDITIONAL'
                decision['action'] = 'MONITOR'
                decision['reason'] = f'PIORANDO: CVaR={cvar_us/1000:.1f}ms < 60ms + slope=+{slope:.2f}ms/s (subindo) → Monitorar (70%)'
                decision['confidence'] = 0.6
                print(f"\033[1;36m[rApp] REGRA 5: CVaR < 60ms + Slope > 0 - CONDITIONAL 70%\033[0m")
            
            # REGRA 6: 60-80ms + slope > 0.01 → CONDITIONAL (90%) - piorando
            elif cvar_us < CVAR_CRITICAL_US and slope > SLOPE_TOLERANCE:
                decision['energy_saver'] = 'CONDITIONAL'
                decision['action'] = 'MONITOR'
                decision['reason'] = f'PIORANDO: CVaR={cvar_us/1000:.1f}ms (60-80ms) + slope=+{slope:.2f}ms/s (subindo) → Monitorar (90%)'
                decision['confidence'] = 0.4
                print(f"\033[1;31m[rApp] REGRA 6: 60ms ≤ CVaR < 80ms + Slope > 0 - CONDITIONAL 90%\033[0m")
            
            # REGRA 7: Outros casos → CONDITIONAL
            else:
                decision['energy_saver'] = 'CONDITIONAL'
                decision['action'] = 'MONITOR'
                decision['reason'] = f'MONITORANDO: CVaR={cvar_us/1000:.1f}ms, slope={slope:.2f}ms/s ({slope_state})'
                decision['confidence'] = 0.5

        # ========================================
        # ETAPA 2.3: PATTERN ENGINE INTEGRATION
        # Ajusta decisão baseada no padrão detectado
        # ========================================

        if pattern_analysis and not decision['preventive_block']:
            pattern = pattern_analysis.get('pattern', 'unknown')
            activity_level = pattern_analysis.get('activity_level', 'normal')

            # Durante "peak_hours" → ser mais conservador
            if pattern == 'peak_hours' and decision['energy_saver'] == 'ALLOWED':
                # Reduz agressividade da economia em 10%
                original_action = decision['action']
                if 'ECO' in original_action:
                    decision['action'] = 'POWER_DOWN'
                    decision['reason'] += f' [PATTERN: peak_hours - economia reduzida]'
                    decision['confidence'] *= 0.9
                    print(f"\033[1;33m[rApp PATTERN] Peak hours: ECO → POWER_DOWN (10% menos agressivo)\033[0m")

            # Durante "low_activity" → ser mais agressivo na economia
            elif activity_level == 'low' and decision['energy_saver'] == 'ALLOWED':
                original_action = decision['action']
                if 'POWER_DOWN' in original_action and 'ECO' not in original_action:
                    decision['action'] = 'POWER_DOWN_ECO'
                    decision['eco_mode'] = True
                    decision['reason'] += f' [PATTERN: low_activity - economia aumentada]'
                    decision['confidence'] *= 1.1
                    print(f"\033[1;32m[rApp PATTERN] Low activity: POWER_DOWN → ECO (20% mais agressivo)\033[0m")

            decision['pattern'] = pattern
            decision['activity_level'] = activity_level

        # ========================================
        # ETAPA 2.5: ML PREDICTION (Random Forest)
        # Predição baseada em modelo treinado
        # ========================================

        if self.ml_predictor.is_loaded() and not decision['preventive_block']:
            # Buscar últimos dados do Data Lake para novas features
            latest_extended = None
            if self.data_lake:
                latest_extended = self.data_lake.get_latest_extended_metrics(limit=1)
            
            # Extrair novas features
            throughput_kbps = 0
            packet_loss_rate = 0
            jitter_ms = 0
            tx_bytes = 0
            rx_bytes = 0
            
            if latest_extended and len(latest_extended) > 0:
                row = latest_extended[0]
                throughput_kbps = float(row.get('throughput_kbps', 0) or 0)
                packet_loss_rate = float(row.get('global_packet_loss_rate', 0) or 0)
                jitter_ms = float(row.get('global_jitter_us', 0) or 0) / 1000.0
                tx_bytes = int(row.get('total_tx_bytes', 0) or 0)
                rx_bytes = int(row.get('total_rx_bytes', 0) or 0)
            
            # Calcular tx_rx_ratio
            tx_rx_ratio = tx_bytes / max(rx_bytes, 1) if rx_bytes > 0 else 0
            
            # Buscar histórico de decisões
            energy_history = 0
            if self.data_lake:
                recent_decisions = self.data_lake.get_recent_decisions(minutes=5, limit=10)
                if recent_decisions:
                    blocked_count = sum(1 for d in recent_decisions if d.get('decision') == 'BLOCKED')
                    energy_history = blocked_count / len(recent_decisions)
            
            # P95 = pior 5% dos UEs (é o valor crítico que as regras usam)
            p95_value = network_health.get('p95_us', 0) if network_health else 0
            
            ml_metrics = {
                'cvar_per_ue_us': cvar_us if cvar_us else 0,
                'cvar_p95_us': p95_value,
                'latency_p95_per_ue_us': p95_value,
                'global_avg_latency_us': float(row.get('global_avg_latency_us', 0) or 0) if latest_extended else 0,
                'variance_per_ue_us2': variance_us2 if variance_us2 else 0,
                'total_active_cameras': slicer_intent.get('ACTIVE_CAMERAS', 0) if slicer_intent else 0,
                'total_active_ues': slicer_intent.get('ACTIVE_UES', 20) if slicer_intent else 20,
                'total_critical_ues': slicer_intent.get('CRITICAL_UES', 0) if slicer_intent else 0,
                'sim_time_s': float(row.get('sim_time_s', 0) or 0) if latest_extended else 0,
                # Novas features
                'throughput_kbps': throughput_kbps,
                'packet_loss_rate': packet_loss_rate,
                'jitter_ms': jitter_ms,
                'tx_rx_ratio': tx_rx_ratio,
                'energy_history': energy_history,
            }
            
            # DEBUG: Log do P95 que está sendo enviado para ML
            print(f"[rApp DEBUG] ML cvar_p95 = {p95_value}us = {p95_value/1000:.1f}ms")

            # Usar predição com contexto do banco de dados
            ml_result = self.ml_predictor.predict_with_db_context(ml_metrics)
            decision['ml_rf_prediction'] = ml_result

            # Log da predição com fonte
            ml_decision = ml_result.get('decision')
            ml_confidence = ml_result.get('confidence', 0)
            ml_source = ml_result.get('source', 'unknown')
            ml_cvar_prev = ml_result.get('predicted_cvar_ms', 0)
            ml_cvar_trend = ml_result.get('cvar_trend', 0)
            ml_warning = ml_result.get('warning', None)
            ml_trend_info = ml_result.get('trend_info', '')
            db_dist = ml_result.get('db_distribution', {})
            db_total = ml_result.get('db_total', 0)

            if ml_decision:
                source_color = {
                    'ml_only': '\033[0;36m',
                    'ml_boosted': '\033[1;36m',
                    'database_override': '\033[1;33m',
                }.get(ml_source, '\033[0m')

                print(f"{source_color}[rApp ML] {ml_decision} (conf={ml_confidence:.2f}, "
                      f"CVaR={ml_cvar_prev}ms, trend={ml_cvar_trend:.1f}ms, fonte={ml_source}, "
                      f"banco={db_total}regs)\033[0m")

                # D) Exibir avisos de detecção de cenários críticos
                if ml_warning:
                    warning_colors = {
                        'RAPID_DETERIORATION': '\033[1;33m',  # Amarelo
                        'NEAR_CRITICAL_ZONE': '\033[1;33m',  # Amarelo
                        'ACCUMULATING_RISK': '\033[1;31m',   # Vermelho
                    }
                    color = warning_colors.get(ml_warning, '\033[0m')
                    print(f"{color}[rApp ML] ⚠️ {ml_warning}: {ml_trend_info}\033[0m")

                if db_dist:
                    dist_str = ', '.join(f"{k}:{v}%" for k, v in db_dist.items())
                    print(f"\033[0;90m[rApp ML] Distribuição banco: {dist_str}\033[0m")

            # ML influences decision based on confidence
            ml_prediction_valid = True
            if ml_decision and ml_confidence > 0.45:
                
                # Validar predição ML contra CVaR real
                # Se ML prevê muito baixo mas CVaR real é alto, desconsiderar
                cvar_ms = cvar_us / 1000.0 if cvar_us else 0
                if ml_cvar_prev > 0 and cvar_ms > 0:
                    # Se ML prevê < 15ms mas real > 60ms, subestimou muito
                    if ml_cvar_prev < 15 and cvar_ms > 60:
                        print(f"\033[1;33m[rApp] AVISO: ML subestimou muito ({ml_cvar_prev:.1f}ms vs {cvar_ms:.1f}ms real)\033[0m")
                        ml_prediction_valid = False
                    # Se ML prevê ≤70ms mas real > 70ms, subestimou CRÍTICO (apenas se diff > 20ms)
                    elif ml_cvar_prev <= 70 and cvar_ms > 70 and (cvar_ms - ml_cvar_prev) > 20:
                        print(f"\033[1;31m[rApp] AVISO: ML subestimou CRÍTICO ({ml_cvar_prev:.1f}ms vs {cvar_ms:.1f}ms real)\033[0m")
                        ml_prediction_valid = False
                    # Se ML prevê > 80ms mas real < 20ms, superestimou muito
                    elif ml_cvar_prev > 80 and cvar_ms < 20:
                        print(f"\033[1;33m[rApp] AVISO: ML superestimou ({ml_cvar_prev:.1f}ms vs {cvar_ms:.1f}ms real)\033[0m")
                        ml_prediction_valid = False
                
                decision['ml_influenced'] = False

                # Só influencia se predição for válida
                if not ml_prediction_valid:
                    print(f"\033[1;33m[rApp] ML ignorada - predição inválida\033[0m")
                elif not decision['preventive_block'] and decision['energy_saver'] != 'BLOCKED':
                    rule_decision = decision['energy_saver']

                    # ML says ALLOWED + Rules say CONDITIONAL + conf > 45% → ALLOWED
                    if ml_prediction_valid and ml_decision == 'ALLOWED' and rule_decision == 'CONDITIONAL' and ml_confidence > 0.45:
                        decision['energy_saver'] = 'ALLOWED'
                        decision['action'] = 'POWER_DOWN'
                        decision['reason'] = f'ML OVERRIDE: {decision["reason"]} + ML={ml_decision} (conf={ml_confidence:.0%}, fonte={ml_source})'
                        decision['confidence'] = ml_confidence
                        decision['ml_influenced'] = True
                        print(f"\033[1;32m[rApp ML] OVERRIDE: CONDITIONAL → ALLOWED (ML conf={ml_confidence:.0%})\033[0m")

                    # ML says BLOCKED + Rules say ALLOWED + conf > 45% → CONDITIONAL
                    elif ml_prediction_valid and ml_decision == 'BLOCKED' and rule_decision == 'ALLOWED' and ml_confidence > 0.45:
                        decision['energy_saver'] = 'CONDITIONAL'
                        decision['action'] = 'MONITOR'
                        decision['reason'] = f'ML CAUTION: {decision["reason"]} + ML={ml_decision} (conf={ml_confidence:.0%}, fonte={ml_source})'
                        decision['confidence'] = ml_confidence
                        decision['ml_influenced'] = True
                        print(f"\033[1;33m[rApp ML] CAUTION: ALLOWED → CONDITIONAL (ML conf={ml_confidence:.0%})\033[0m")
                    
                    # D) B) ML com aviso de cenários críticos mesmo com confiança menor
                    elif ml_warning and ml_warning in ['ACCUMULATING_RISK', 'RAPID_DETERIORATION', 'NEAR_CRITICAL_ZONE']:
                        # Mesmo com confiança baixa, se ML detectou cenário crítico, tomar precaução
                        if rule_decision == 'ALLOWED':
                            decision['energy_saver'] = 'CONDITIONAL'
                            decision['action'] = 'MONITOR'
                            decision['reason'] = f'ML CRITICAL WARNING: {decision["reason"]} + {ml_warning}: {ml_trend_info}'
                            decision['confidence'] = max(ml_confidence, 0.75)
                            decision['ml_influenced'] = True
                            print(f"\033[1;31m[rApp ML] CRITICAL WARNING: ALLOWED → CONDITIONAL ({ml_warning})\033[0m")

                # Log concordance (only if ML was considered)
                if ml_decision and ml_confidence > 0.45:
                    if ml_prediction_valid:
                        if ml_decision == decision['energy_saver']:
                            print(f"\033[0;32m[rApp ML] ✓ Concordância ML={ml_decision} == Regras={decision['energy_saver']}\033[0m")
                        else:
                            print(f"\033[0;33m[rApp ML] ✗ Discordância ML={ml_decision} != Regras={decision['energy_saver']}\033[0m")
                    else:
                        print(f"\033[0;33m[rApp ML] ✗ ML desconsiderada - predição inválida\033[0m")

        # ========================================
        # ETAPA 3: AGENT-AL OVERRIDE
        # ========================================
        agent_intent = self.agent.read_intent()
        if agent_intent:
            agent_policy = self.agent.translate_to_policy(agent_intent)
            if agent_policy:
                old_decision = decision.copy()
                decision = self.agent.apply_policy(decision, agent_policy)
                if decision != old_decision:
                    decision['agent_override'] = True
                    self.stats['agent_overrides'] += 1
        
        # ========================================
        # ETAPA 4: rApp ARBITER FINAL (TREND)
        # Consolidação final - tendência rápida sempre bloqueia
        # ========================================
        
        # Se tendência diz SUBINDO muito rápido (>5ms/s) → SEMPRE bloquear
        if trend_info.get('valid') and trend_info['slope_ms_per_sec'] > 5 and trend_info['current_latency_ms'] > 50:
            decision['energy_saver'] = 'BLOCKED'
            decision['action'] = 'PREVENTIVE_BLOCK'
            decision['reason'] = f"ARBITER_TREND: Slope +{trend_info['slope_ms_per_sec']:.1f}ms/s > 5ms/s - Prevenção total"
            decision['confidence'] = min(0.9, trend_info['confidence'] + 0.1)
            decision['preventive_block'] = True
            self.stats['sla_violations'] += 1
        
        return decision
    
    def write_decision(self, decision):
        """Escreve decisão no arquivo."""
        try:
            with open(RAPP_DECISION_PATH, 'w') as f:
                f.write("=" * 70 + "\n")
                f.write("              rApp-ResourceOptimizer - DECISION\n")
                f.write("=" * 70 + "\n")
                f.write(f"Timestamp: {decision['timestamp']}\n")
                f.write(f"Cycle: {self.cycle}\n")
                f.write("\n")
                
                # SLICER
                f.write("+--------------------- SLICER STATUS ----------------------+\n")
                f.write(f"|  Estado: {decision['slicer_state']:<46}|\n")
                if decision['slicer_state'] == 'CRITICAL':
                    f.write("|  \033[1;31mPROBLEMA: Latencia > 100ms (SLA VIOLADO!)\033[0m             |\n")
                elif decision['slicer_state'] == 'NORMAL':
                    f.write("|  OK: Latencia < 100ms                                  |\n")
                elif decision['slicer_state'] == 'IDLE':
                    f.write("|  OK: Nenhuma camera ativa                              |\n")
                else:
                    f.write("|  Aguardando dados...                                   |\n")
                f.write("+----------------------------------------------------+\n")
                f.write("\n")
                
                # ENERGY SAVER
                f.write("+-------------------- ENERGY SAVER -----------------------+\n")
                f.write(f"|  Intencao: {decision['energy_state']:<44}|\n")
                if decision['energy_state'] == 'ENERGY_SAVE':
                    f.write("|  Quer desligar para economia                           |\n")
                elif decision['energy_state'] == 'INTERVENTION':
                    f.write("|  Mantendo recursos ativos                             |\n")
                else:
                    f.write("|  Aguardando dados...                                  |\n")
                f.write("+----------------------------------------------------+\n")
                f.write("\n")
                
                # Network Health (métricas avançadas)
                if decision.get('network_health'):
                    nh = decision['network_health']
                    f.write("+---------------- NETWORK HEALTH ----------------------+\n")
                    f.write(f"|  CVaR (95%): {nh.get('cvar_us', 0)/1000:.1f}ms{' '*42}|\n")
                    f.write(f"|  P95: {nh.get('p95_us', 0)/1000:.1f}ms{' '*48}|\n")
                    f.write(f"|  Mediana: {nh.get('median_us', 0)/1000:.1f}ms{' '*43}|\n")
                    f.write(f"|  Estabilidade: {nh.get('stability_score', 0):.0f}%{' '*39}|\n")
                    f.write("+----------------------------------------------------+\n")
                    f.write("\n")
                
                # Trend Analysis (Slope)
                if decision.get('trend_analysis'):
                    ta = decision['trend_analysis']
                    slope_ms = ta.get('slope_ms_per_sec', 0)
                    trend = ta.get('trend', 'unknown')
                    current_ms = ta.get('current_latency_ms', 0)
                    time_crit = ta.get('time_to_critical')
                    
                    f.write("+---------------- TREND ANALYSIS ----------------------+\n")
                    f.write(f"|  Slope: {slope_ms:+.2f} ms/s{' '*42}|\n")
                    f.write(f"|  Tendencia: {trend:<40}|\n")
                    f.write(f"|  Latencia atual: {current_ms:.1f}ms{' '*32}|\n")
                    if time_crit and time_crit > 0:
                        f.write(f"|  ⚠  Em {time_crit:.0f}s atinge 150ms!{' '*29}|\n")
                    if decision.get('preventive_block'):
                        f.write(f"|  [PREVENTIVE BLOCK ATIVO]{' '*29}|\n")
                    f.write("+----------------------------------------------------+\n")
                    f.write("\n")
                
                # Pattern Analysis
                if decision['pattern_analysis']:
                    pa = decision['pattern_analysis']
                    f.write("+-------------------- ML ANALYSIS -----------------------+\n")
                    f.write(f"|  Hora: {pa['hour']:02d}:00 ({pa['day_name']})                            |\n")
                    f.write(f"|  Cameras: {pa['current_cameras']:.1f} | Latencia: {pa['current_latency_us']/1000:.0f}ms              |\n")
                    f.write(f"|  Padrao: {pa['pattern']:<46}|\n")
                    f.write(f"|  Atividade: {'Baixa' if pa['low_activity'] else 'Normal':<47}|\n")
                    f.write("+----------------------------------------------------+\n")
                    f.write("\n")
                
                # rApp DECISION
                f.write("=" * 70 + "\n")
                f.write("                      rApp DECISION\n")
                f.write("=" * 70 + "\n")
                
                status = decision['energy_saver']
                if status == 'BLOCKED':
                    status_str = "\033[1;31m[BLOCKED]\033[0m"
                elif status == 'ALLOWED':
                    status_str = "\033[1;32m[ALLOWED]\033[0m"
                else:
                    status_str = "\033[1;33m[CONDITIONAL]\033[0m"
                
                f.write(f"|  Energy Saver: {status_str:<49}|\n")
                f.write(f"|  Acao: {decision['action']:<54}|\n")
                f.write(f"|  Motivo: {decision['reason']:<53}|\n")
                f.write(f"|  Confianca: {decision['confidence']*100:.0f}%{' '*47}|\n")
                
                if decision['agent_override']:
                    f.write("|  \033[1;35m>>> AGENT-AL OVERRIDE: Politica aplicada\033[0m            |\n")
                
                if decision['pattern']:
                    f.write(f"|  Padrao detectado: {decision['pattern']:<40}|\n")
                
                f.write("=" * 70 + "\n")
                f.write("\n")
                
                # Raw data for parsing
                f.write(f"rApp=orchestrator\n")
                f.write(f"TIMESTAMP={decision['timestamp']}\n")
                f.write(f"CYCLE={self.cycle}\n")
                f.write(f"ENERGY_SAVER={decision['energy_saver']}\n")
                f.write(f"ACTION={decision['action']}\n")
                f.write(f"REASON={decision['reason']}\n")
                f.write(f"CONFIDENCE={decision['confidence']}\n")
                f.write(f"SLICER_STATE={decision['slicer_state']}\n")
                f.write(f"ENERGY_STATE={decision['energy_state']}\n")
                f.write(f"AGENT_OVERRIDE={str(decision['agent_override']).lower()}\n")
                f.write(f"PATTERN={decision['pattern'] or 'none'}\n")
                f.write(f"PREVENTIVE_BLOCK={str(decision.get('preventive_block', False)).lower()}\n")
                if decision.get('trend_analysis'):
                    ta = decision['trend_analysis']
                    f.write(f"SLOPE_MS_PER_SEC={ta.get('slope_ms_per_sec', 0):.2f}\n")
                    f.write(f"TREND={ta.get('trend', 'unknown')}\n")
                    f.write(f"CURRENT_LATENCY_MS={ta.get('current_latency_ms', 0):.1f}\n")
                    tc = ta.get('time_to_critical')
                    if tc:
                        f.write(f"TIME_TO_CRITICAL={tc:.1f}\n")
                f.flush()
            
            return True
        except Exception as e:
            print(f"[rApp] ERRO ao escrever decisao: {e}")
            return False
    
    def send_a1_policies(self, decision):
        """Envia políticas via interface A1 com confirmação ACK."""
        pattern_info = None
        if decision.get('pattern_analysis'):
            pa = decision['pattern_analysis']
            pattern_info = {
                'window_start': f"{pa['hour']:02d}:00",
                'window_end': f"{(pa['hour']+1)%24:02d}:00",
                'confidence': pa['confidence'],
                'reason': pa['pattern']
            }
        
        self.a1.send_energy_policy(decision, pattern_info)
        self.a1.send_slice_policy(
            decision['slicer_state'],
            {'active': int(decision['pattern_analysis']['current_cameras'])} if decision.get('pattern_analysis') else None
        )
    
    def send_energy_command(self, decision):
        """
        Envia comando JSON para xApp Energy Saver.
        
        Converte a decisão do rApp em comando específico para o atuador.
        
        Args:
            decision: Decisão do rApp
        """
        energy_state = decision['energy_saver']
        reason = decision['reason']
        
        # Mapear decisão do rApp para comando do atuador
        if energy_state == 'BLOCKED' or decision.get('preventive_block'):
            # Bloqueio → FULL_POWER (não economizar)
            self.energy_cmd.send_full_power(reason=f"BLOCKED: {reason}")
            
        elif energy_state == 'ALLOWED':
            # Verificar se é MODO ECO (CVaR < 20ms)
            if decision.get('eco_mode'):
                # ECO MODE → POWER_DOWN_ECO (10% potência)
                self.energy_cmd.send_power_down_eco(reason=f"ALLOWED (ECO): {reason}")
            else:
                # Permitido → POWER_DOWN (economizar)
                # Verificar nível de confiança para escolher nível
                confidence = decision.get('confidence', 0)
                if confidence >= 0.8:
                    self.energy_cmd.send_power_down(reason=f"ALLOWED: {reason}")
                else:
                    self.energy_cmd.send_reduce_power(reason=f"ALLOWED (cautela): {reason}")
                
        elif energy_state == 'CONDITIONAL':
            # Condicional → CONDITIONAL_REDUCE (economia moderada ativa)
            self.energy_cmd.send_conditional_reduce(reason=f"CONDITIONAL: {reason}")
            
        else:
            # Estado desconhecido → MAINTAIN
            self.energy_cmd.send_maintain(reason=f"UNKNOWN: {reason}")
    
    def check_and_handle_acks(self):
        """Verifica ACKs pendentes e atualiza estatísticas."""
        pending = self.a1.check_pending_acks()
        
        for ptype, info in pending.items():
            if not info['pending']:
                self.stats['ack_received'] += 1
            else:
                self.stats['ack_pending'] += 1
                if info['attempts'] >= 3:
                    self.stats['ack_timeout'] += 1
    
    def record_decision(self, decision):
        """Registra decisão no Data Lake e log estruturado."""
        self.data_lake.record_decision(decision)

        # Structured logging to JSONL file
        try:
            ml_prediction = decision.get('ml_rf_prediction', {})
            log_entry = {
                'timestamp': decision.get('timestamp', int(time.time())),
                'datetime': datetime.now().isoformat(),
                'cycle': self.cycle,
                'decision': decision.get('energy_saver', 'UNKNOWN'),
                'action': decision.get('action', 'NONE'),
                'reason': decision.get('reason', ''),
                'confidence': decision.get('confidence', 0),
                'cvar_ms': decision.get('network_health', {}).get('cvar_us', 0) / 1000,
                'slope_ms_per_sec': decision.get('trend_analysis', {}).get('slope_ms_per_sec', 0),
                'pattern': decision.get('pattern', 'unknown'),
                'ml_influenced': decision.get('ml_influenced', False),
                'ml_source': ml_prediction.get('source', 'unknown'),
                'ml_confidence': ml_prediction.get('confidence', 0),
                'ml_predicted_cvar_ms': ml_prediction.get('predicted_cvar_ms', 0),
                'preventive_block': decision.get('preventive_block', False),
                'eco_mode': decision.get('eco_mode', False),
                'slicer_state': decision.get('slicer_state', 'UNKNOWN')
            }

            with open('/tmp/rapp_decisions.jsonl', 'a') as f:
                f.write(json.dumps(log_entry) + '\n')
        except Exception as e:
            print(f"[rApp] Erro ao gravar log estruturado: {e}")
    
    def update_stats(self, decision):
        """Atualiza estatísticas."""
        self.stats['total_cycles'] += 1
        
        if decision['energy_saver'] == 'BLOCKED':
            self.stats['blocked'] += 1
        elif decision['energy_saver'] == 'ALLOWED':
            self.stats['allowed'] += 1
        elif decision['energy_saver'] == 'CONDITIONAL':
            self.stats['conditional'] += 1
        
        if decision.get('preventive_block'):
            self.stats['preventive_blocks'] += 1
    
    def print_status(self, decision, xapp_status=None):
        """Imprime status do ciclo."""
        if xapp_status is None:
            xapp_status = {'slicer_active': True, 'energy_active': self._energy_active}
        
        if self.cycle % 5 == 0 or decision['energy_saver'] == 'BLOCKED' or decision['agent_override']:
            print("")
            print("=" * 70)
            print("                    rApp-ResourceOptimizer")
            print("=" * 70)
            print(f"  Ciclo: {self.cycle}  |  Tempo: {datetime.now().strftime('%H:%M:%S')}")
            
            # Status da conexão de dados
            if hasattr(self, 'data_fresh'):
                if self.data_fresh:
                    status_icon = "🟢"
                    status_text = "ONLINE"
                else:
                    status_icon = "🔴"
                    status_text = "OFFLINE"
                last_data = getattr(self, 'last_sim_time', 0)
                print(f"  Status: {status_icon} {status_text} | sim_time: {last_data}s")
            print("")
            
            # xAPPS STATUS
            slicer_active = xapp_status.get('slicer_active', False)
            energy_active = xapp_status.get('energy_active', False)
            slicer_status = "\033[1;32m[ATIVO]\033[0m" if slicer_active else "\033[1;31m[PARADO]\033[0m"
            energy_status = "\033[1;32m[ATIVO]\033[0m" if energy_active else "\033[1;33m[PARADO]\033[0m"
            print(f"  xApp SLICER:      {slicer_status} (prioridade - sempre ativo)")
            print(f"  xApp ENERGY:      {energy_status} (controlado pelo rApp)")
            print("")
            
            # SLICER STATE
            slicer = decision['slicer_state']
            if slicer == 'CRITICAL':
                slicer_display = "\033[1;31m[CRITICAL]\033[0m <- PROBLEMA: Latencia > 100ms!"
            elif slicer == 'NORMAL':
                slicer_display = "\033[1;32m[NORMAL]\033[0m <- OK"
            elif slicer == 'IDLE':
                slicer_display = "\033[1;32m[IDLE]\033[0m <- OK: Nenhuma camera"
            else:
                slicer_display = "[UNKNOWN]"
            
            print(f"  SLICER STATE:     {slicer_display}")
            
            # ENERGY
            energy = decision['energy_state']
            if energy == 'ENERGY_SAVE':
                energy_display = "[ENERGY_SAVE] <- Oportunidade"
            elif energy == 'INTERVENTION':
                energy_display = "[INTERVENTION] <- Mantendo ativos"
            else:
                energy_display = "[UNKNOWN]"
            
            print(f"  ENERGY STATE:     {energy_display}")
            
            # Pattern
            if decision.get('pattern_analysis'):
                pa = decision['pattern_analysis']
                print(f"  ML:               Pattern={pa['pattern']}, Cameras={pa['current_cameras']:.1f}")
            
            # Trend Analysis
            if decision.get('trend_analysis'):
                ta = decision['trend_analysis']
                slope = ta.get('slope_ms_per_sec', 0)
                trend_str = ta.get('trend', 'unknown')
                current_ms = ta.get('current_latency_ms', 0)
                time_crit = ta.get('time_to_critical')
                
                # Formatar tendência
                if trend_str == 'rising_fast':
                    trend_display = f"\033[1;31m↑ RAPIDO (+{slope:.1f}ms/s)\033[0m"
                elif trend_str == 'rising_slow':
                    trend_display = f"\033[1;33m↑ DEVAGAR (+{slope:.1f}ms/s)\033[0m"
                elif trend_str == 'falling_fast':
                    trend_display = f"\033[1;32m↓ RAPIDO ({slope:.1f}ms/s)\033[0m"
                elif trend_str == 'falling_slow':
                    trend_display = f"\033[1;32m↓ DEVAGAR ({slope:.1f}ms/s)\033[0m"
                else:
                    trend_display = f"\033[1;36m→ ESTAVEL ({slope:.2f}ms/s)\033[0m"
                
                print(f"  TREND:            {trend_display}, Lat={current_ms:.0f}ms")
                
                if time_crit and time_crit > 0:
                    print(f"  ⚠  PREVISAO: Atinge 150ms em {time_crit:.0f}s!")
                if decision.get('preventive_block'):
                    print(f"  \033[1;35m>>> PREVENTIVE BLOCK: Prevenindo pico de latencia\033[0m")
            
            print("")
            print("-" * 70)
            
            # DECISION
            status = decision['energy_saver']
            if status == 'BLOCKED':
                status_display = "\033[1;31m[BLOCKED]\033[0m"
            elif status == 'ALLOWED':
                status_display = "\033[1;32m[ALLOWED]\033[0m"
            else:
                status_display = "\033[1;33m[CONDITIONAL]\033[0m"
            
            print(f"  DECISAO: Energy Saver = {status_display}")
            print(f"  Motivo: {decision['reason']}")
            print(f"  Confianca: {decision['confidence']*100:.0f}%")
            
            if decision['agent_override']:
                print(f"\033[1;35m  >>> AGENT-AL OVERRIDE: {decision['agent_policy']}\033[0m")

            print("=" * 70)

    def check_ml_retrain(self):
        """Verifica se é hora de retreinar o ML."""
        now = time.time()
        elapsed = now - self.ml_last_retrain

        if elapsed >= self.ml_retrain_interval:
            print(f"\033[1;35m[rApp ML] Retreinamento automático (a cada 12h)...\033[0m")
            self.retrain_ml()
            self.ml_last_retrain = now
            self.ml_retrain_count += 1

    def retrain_ml(self):
        """Retreina o modelo ML com dados recentes do banco."""
        try:
            import subprocess
            import sys

            script_path = os.path.join(os.path.dirname(__file__), 'train_ml_model.py')
            result = subprocess.run(
                [sys.executable, script_path, '--retrain', '--hours', '24', '--output', './models'],
                capture_output=True,
                text=True,
                timeout=300,
                cwd=os.path.dirname(__file__)
            )

            if result.returncode == 0:
                print(f"\033[1;32m[rApp ML] Retreinamento concluído (#{self.ml_retrain_count + 1})\033[0m")

                # Recarregar modelos
                self.ml_predictor._load_models()

                # Mostrar accuracy do novo modelo
                report_path = os.path.join(os.path.dirname(__file__), 'models', 'training_report.json')
                if os.path.exists(report_path):
                    import json
                    with open(report_path, 'r') as f:
                        report = json.load(f)
                        accuracy = report.get('classifier', {}).get('random_forest_accuracy', 0)
                        classes = report.get('classifier', {}).get('classes', [])
                        print(f"\033[1;32m[rApp ML] Nova accuracy: {accuracy:.2%}, Classes: {classes}\033[0m")
                
                # Log do stdout para debug (últimas 500 chars)
                if result.stdout:
                    print(f"\033[1;34m[rApp ML] Log do treino: {result.stdout[-500:]}\033[0m")
            else:
                print(f"\033[1;31m[rApp ML] Erro no retreinamento (código {result.returncode}):\033[0m")
                print(f"\033[1;31m[rApp ML] STDERR: {result.stderr[-500:]}\033[0m")
                if result.stdout:
                    print(f"\033[1;31m[rApp ML] STDOUT: {result.stdout[-500:]}\033[0m")

        except subprocess.TimeoutExpired:
            print(f"\033[1;31m[rApp ML] Timeout no retreinamento (>5min)\033[0m")
        except Exception as e:
            print(f"\033[1;31m[rApp ML] Erro no retreinamento: {e}\033[0m")

    def _record_ml_history(self, decision):
        """Record ML prediction for history display."""
        try:
            # Extract ML prediction info
            ml_result = decision.get('ml_rf_prediction', {})
            ml_decision = ml_result.get('decision', 'NONE')
            ml_confidence = ml_result.get('confidence', 0.0)
            predicted_cvar = ml_result.get('predicted_cvar_ms', 0.0)
            ml_influenced = decision.get('ml_influenced', False)
            
            # Extract source and reason
            ml_source = ml_result.get('source', 'unknown')
            ml_reason = ml_result.get('reason', '')
            
            # D) Extract warning and trend info for critical scenario detection
            ml_warning = ml_result.get('warning', '')
            ml_trend = ml_result.get('cvar_trend', 0.0)
            ml_trend_info = ml_result.get('trend_info', '')
            
            # Get final decision (after rules applied)
            rule_decision = decision.get('energy_saver', 'UNKNOWN')
            
            # Calculate concordance
            concordance = (ml_decision == rule_decision) if ml_decision != 'NONE' else False
            
            # Create history entry
            history_entry = {
                'timestamp': datetime.fromtimestamp(decision['timestamp']).strftime('%Y-%m-%d %H:%M:%S'),
                'ml_decision': ml_decision,
                'rule_decision': rule_decision,
                'ml_confidence': ml_confidence,
                'predicted_cvar': predicted_cvar,
                'ml_influenced': ml_influenced,
                'concordance': concordance,
                'source': ml_source,
                'reason': ml_reason,
                'ml_warning': ml_warning,
                'ml_trend': ml_trend,
                'ml_trend_info': ml_trend_info
            }
            
            # Add to history deque (automatically removes oldest when maxlen reached)
            self.ml_history.append(history_entry)
            
        except Exception as e:
            # Don't let history recording break the main loop
            pass  # Silently fail to avoid disrupting operation

    def _display_ml_history(self):
        """Display formatted ML prediction history."""
        if not self.ml_history:
            return
        
        # Only display if we have enough entries to make it worthwhile
        if len(self.ml_history) < 2:
            return
        
        print("\n" + "=" * 130)
        print("                           HISTÓRICO DE PREDIÇÕES ML")
        print("=" * 130)
        print(f"{'Timestamp':<20} {'Decisão ML':<12} {'Decisão Regras':<15} {'Conf. ML':<8} {'CVaR Previsto':<12} {'ML Inf.':<8} {'Concordância':<12} {'Fonte':<20} {'Reason'}")
        print("-" * 130)
        
        # Show last 10 entries or all if less than 10
        display_entries = list(self.ml_history)[-10:] if len(self.ml_history) > 10 else list(self.ml_history)
        
        for entry in display_entries:
            ml_influenced_str = "Sim" if entry['ml_influenced'] else "Não"
            concordance_str = "✓" if entry['concordance'] else "✗"
            # Truncate reason if too long
            reason_display = entry['reason'][:35] + "..." if len(entry['reason']) > 35 else entry['reason']
            
            print(f"{entry['timestamp']:<20} {entry['ml_decision']:<12} {entry['rule_decision']:<15} "
                  f"{entry['ml_confidence']*100:>6.0f}%    {entry['predicted_cvar']:>8.1f} ms    "
                  f"{ml_influenced_str:<8} {concordance_str:<12} {entry['source']:<20} {reason_display}")
        print("=" * 130)

    def print_final_stats(self):
        """Imprime estatísticas finais."""
        total = self.stats['total_cycles']
        
        print("")
        print("=" * 70)
        print("              rApp-ResourceOptimizer - ESTATÍSTICAS FINAIS")
        print("=" * 70)
        print(f"  Ciclos totais: {total}")
        print(f"  Decisões BLOCKED: {self.stats['blocked']} ({self.stats['blocked']/total*100:.1f}%)")
        print(f"  Decisões ALLOWED: {self.stats['allowed']} ({self.stats['allowed']/total*100:.1f}%)")
        print(f"  Decisões CONDITIONAL: {self.stats['conditional']} ({self.stats['conditional']/total*100:.1f}%)")
        print(f"  Agent-Al Overrides: {self.stats['agent_overrides']}")
        print(f"  rApp Overrides (CRITICAL→OK): {self.stats.get('rap_overrides', 0)}")
        print(f"  Blocos Preventivos (Trend): {self.stats.get('preventive_blocks', 0)}")
        print(f"  Padrões detectados: {self.stats['pattern_detected']}")
        print(f"  SLA Violations: {self.stats['sla_violations']}")
        print(f"  ACK Recebidos: {self.stats['ack_received']}")
        print(f"  ACK Pendentes: {self.stats['ack_pending']}")
        print(f"  ACK Timeout: {self.stats['ack_timeout']}")
        print("")
        
        # Database stats
        db_stats = self.data_lake.get_database_stats()
        print(f"  Data Lake:")
        print(f"    Métricas: {db_stats['metrics_count']}")
        print(f"    Decisões: {db_stats['decisions_count']}")
        print(f"    Primeiro registro: {db_stats['first_record']}")
        print(f"    Último registro: {db_stats['last_record']}")
        print("=" * 70)
    
    def run(self):
        """Loop principal do rApp."""
        print("\n" + "=" * 70)
        print("         rApp-ResourceOptimizer - Non-RT RIC")
        print("=" * 70)
        print(f"  Intervalo: {self.interval}s (Non-RT: >=1s)")
        print(f"  Data Lake: /tmp/rapp_data_lake.db")
        print(f"  A1 Policies: /tmp/rapp_policies/")
        print("=" * 70)
        
        # Timeout para detectar quando dados param ( watchdog )
        # Usar SIM_TIME (tempo de simulação) ao invés de timestamp do sistema
        SIM_TIMEOUT_SECONDS = 5  # Se sim_time não avanza por 5 ciclos, parar de decidir
        
        # Status da conexão (para exibir no dashboard)
        self.data_fresh = True
        self.last_sim_time = 0
        
        # Para detecção de transição de simulação
        self.last_sim_time_for_reset = None
        
        while self.running:
            self.cycle += 1
            
            # ========================================
            # 0. WATCHDOG: Verificar se dados estão chegando
            # Usa sim_time (tempo de simulação) ao invés de timestamp do sistema
            # Se sim_time não avanza, a simulação parou
            # ========================================
            
            # Buscar último sim_time no DB (fonte confiável)
            # ULTIMO dado por TIMESTAMP (não MAX sim_time, que pode ser de simulação antiga)
            new_sim_time = 0
            try:
                cursor = self.data_lake.conn.cursor()
                cursor.execute("SELECT sim_time_s FROM extended_metrics ORDER BY timestamp DESC LIMIT 1")
                result = cursor.fetchone()
                if result and result[0]:
                    new_sim_time = result[0]
            except:
                pass
            
            # Verificar watchdog usando SIM_TIME do DB (não timestamp do sistema)
            # Comparar sim_time atual com sim_time do ciclo anterior
            if self.last_sim_time > 0 and new_sim_time > 0:
                # PRIMEIRO: verificar se é nova simulação (sim_time resetou)
                # Isso DEVE ser verificado antes do diff, porque diff negativo significa reset
                if new_sim_time < 10:
                    # Nova simulação começou (sim_time resetou para ~0)
                    if not self.data_fresh:
                        print(f"\n✅  [rApp] Nova simulação detectada! sim_time={new_sim_time}s")
                        self.data_fresh = True
                    self.ml_predictor.reset_history()
                    print(f"    → Histórico ML resetado para nova simulação")
                else:
                    sim_time_diff = new_sim_time - self.last_sim_time
                    
                    # Se diff é NEGATIVO, significa nova simulação (reset)
                    if sim_time_diff < 0:
                        if not self.data_fresh:
                            print(f"\n✅  [rApp] Nova simulação detectada! sim_time={new_sim_time}s (reset)")
                            self.data_fresh = True
                        self.ml_predictor.reset_history()
                        print(f"    → Histórico ML resetado")
                    # Se sim_time não avançou (diff ~= 0), simulação terminou
                    elif sim_time_diff <= 0.1:
                        if self.data_fresh:
                            print(f"\n⚠️  [rApp] SIMULAÇÃO PARADA! sim_time={new_sim_time}s (último: {self.last_sim_time}s)")
                            print(f"    → Entrando em modo OFFLINE - aguardando novos dados...")
                            self.data_fresh = False
                        
                        time.sleep(self.interval)
                        self.last_sim_time = new_sim_time
                        continue
                    else:
                        if not self.data_fresh:
                            print(f"\n✅  [rApp] Simulação ativa! sim_time={new_sim_time}s")
                            self.data_fresh = True
            
            # Update last_sim_time
            if new_sim_time > 0:
                self.last_sim_time = new_sim_time
            
            # 1. Lê intenções dos xApps
            slicer_intent = self.read_slicer_intent()
            energy_intent = self.read_energy_intent()
            
            # 2. Registra métricas no Data Lake
            self.record_current_metrics(slicer_intent, energy_intent)
            
            # 3. Toma decisão estratégica
            decision = self.make_decision(slicer_intent, energy_intent)
            
            # 3.1 Controla xApps baseado na decisão
            xapp_status = self.decide_xapp_activation(decision)
            
            # 4. Escreve decisão
            self.write_decision(decision)
            
            # 5. Envia políticas A1
            self.send_a1_policies(decision)
            
            # 5.1 Envia comando JSON para Energy Saver (atuador puro)
            self.send_energy_command(decision)
            
            # 5.2 Verifica ACKs pendentes
            self.check_and_handle_acks()
            
            # 6. Registra decisão no Data Lake
            self.record_decision(decision)
            
            # 7. Atualiza estatísticas
            self.update_stats(decision)
            
            # 8. Imprime status
            self.print_status(decision, xapp_status)
            
            # 8.5. Armazena predição ML para histórico
            self._record_ml_history(decision)
            
            # 8.6. Exibe histórico de predições ML periodicamente
            if self.cycle % self.history_display_interval == 0 and len(self.ml_history) > 0:
                self._display_ml_history()
            
            # 9. Verifica se é hora de retreinar ML
            self.check_ml_retrain()
            
            # 10. Salva status dos xApps para dashboard
            self.get_xapp_status()

            # 10. Espera próximo ciclo
            time.sleep(self.interval)
        
        # Shutdown
        print("\n[rApp] Encerrando...")
        
        # Para Energy Saver (rApp controla)
        self._stop_energy_saver()
        
        # Mantém Slicer rodando (fallback de segurança)
        # Slicer é prioridade e deve continuar mesmo se rApp morrer
        if self.xapp_manager.is_running("slicer"):
            print("[rApp] Mantendo xApp SLICER ativo (fallback de segurança)")
        
        self.print_final_stats()
        
        # Cleanup
        self.data_lake.close()
        self.agent.clear_intent()
        
        print("[rApp] Encerrado (Slicer continua rodando)")


def main():
    parser = argparse.ArgumentParser(
        description='GreenRAN rApp-ResourceOptimizer - Non-RT RIC'
    )
    parser.add_argument(
        '--interval', '-i',
        type=float,
        default=DEFAULT_INTERVAL,
        help=f'Intervalo em segundos (min: 1, default: {DEFAULT_INTERVAL})'
    )
    parser.add_argument(
        '--synthetic', '-s',
        type=int,
        default=0,
        help='Dias de dados sintéticos a gerar para ML (default: 0)'
    )
    parser.add_argument(
        '--agent-intent', '-a',
        type=str,
        help='Tipo de intenção do Agent-Al (ex: PRIORIZAR_VIGILANCIA)'
    )
    
    args = parser.parse_args()
    
    # Cria rApp
    rapp = RappResourceOptimizer(
        interval=args.interval,
        synthetic_days=args.synthetic
    )
    
    # Se especificou intenção do Agent
    if args.agent_intent:
        print(f"[rApp] Criando intenção Agent-Al: {args.agent_intent}")
        rapp.agent.create_intent_file(args.agent_intent)
    
    # Executa
    rapp.run()


if __name__ == '__main__':
    main()
