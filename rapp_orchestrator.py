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

sys.path.insert(0, '/home/robert/orange_nuclear')

from rapp_data_lake import DataLake
from rapp_pattern_engine import PatternRecognition
from rapp_agent_openran import AgentOpenRAN
from rapp_a1_interface import A1PolicyInterface
# from rapp_synthetic_generator import SyntheticDataGenerator  # Removed - not available
from rapp_xapp_manager import XAppManager
from rapp_trend_analysis import TrendAnalysis
from energy_command_protocol import EnergyCommand

SLICER_INTENT_PATH = "/tmp/xapp_intents/slicer.txt"
ENERGY_INTENT_PATH = "/tmp/xapp_intents/energy_saver.txt"
RAPP_DECISION_PATH = "/tmp/xapp_intents/rapp_decision.txt"
EXTENDED_METRICS_PATH = "/tmp/xapp_metrics/extended_metrics.json"

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
        
        # Inicializa componentes
        print("[rApp] Inicializando componentes...")
        
        # Data Lake (SQLite)
        self.data_lake = DataLake()
        
        # Pattern Engine (ML)
        self.pattern_engine = PatternRecognition(self.data_lake)
        
        # Trend Analysis (Slope/Predição)
        self.trend_analysis = TrendAnalysis(self.data_lake)
        
        # Energy Command Protocol
        self.energy_cmd = EnergyCommand()
        
        # Agent-Al-OpenRAN
        self.agent = AgentOpenRAN()
        
        # A1 Interface
        self.a1 = A1PolicyInterface()
        
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
        - ENERGY SAVER: Ativo se decision['energy_saver'] == 'ALLOWED'
        
        Args:
            decision: Dict com decisão do rApp
        """
        slicer_state = decision.get('slicer_state', 'UNKNOWN')
        
        # ENERGY SAVER: controlado pelo rApp
        if decision.get('energy_saver') == 'ALLOWED':
            # Slicer OK → Energy pode ativar
            if not self._energy_active:
                self._start_energy_saver()
        else:
            # Slicer com problema ou decisão BLOCKED → Energy para
            if self._energy_active:
                self._stop_energy_saver()
        
        # SLICER: NUNCA para (fallback de segurança)
        # Mesmo se rApp morrer, Slicer continua rodando
        return {
            'slicer_active': self.xapp_manager.is_running("slicer"),
            'energy_active': self._energy_active
        }
    
    def get_xapp_status(self):
        """Retorna status dos xApps."""
        status = self.xapp_manager.get_status()
        status['energy_decision'] = self._energy_active
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
            energy_state = energy_intent.get('INTENT', 'UNKNOWN')
            decision['energy_state'] = energy_state
        
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
        VARIANCE_THRESHOLD_US2 = 100000000000  # Variância alta
        STABILITY_THRESHOLD = 50     # Score mínimo de estabilidade
        
        if network_health:
            cvar_us = network_health['cvar_us']
            variance_us2 = network_health['variance_us2']
            median_us = network_health['median_us']
            p95_us = network_health['p95_us']
            stability_score = network_health['stability_score']
            
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
            cvar_us = self.calculate_median_latency(minutes=5)
            variance_us2 = 0
            stability_score = 100
            decision['network_health'] = {'fallback': True, 'cvar_us': cvar_us}
        
        # Só aplica CVaR se não houve bloco preventivo
        if not decision['preventive_block'] and cvar_us is not None:
            
            # ===== LÓGICA DE 3 FAIXAS =====
            
            # FAIXA 1: NORMAL (< 60ms) - Pode economizar
            if cvar_us < CVAR_NORMAL_US:
                if slicer_state == 'CRITICAL':
                    # CVaR OK mas Slicer detectou problema → rApp sobrescreve
                    if stability_score >= STABILITY_THRESHOLD:
                        decision['energy_saver'] = 'ALLOWED'
                        decision['action'] = 'ACTIVATE_ENERGY_SAVING'
                        decision['reason'] = f'NORMAL: CVaR={cvar_us/1000:.1f}ms < 60ms - Slicer CRITICAL sobrescrito'
                        decision['confidence'] = 0.85
                        self.stats['rap_overrides'] = self.stats.get('rap_overrides', 0) + 1
                    else:
                        decision['energy_saver'] = 'CONDITIONAL'
                        decision['action'] = 'MONITOR'
                        decision['reason'] = f'NORMAL: CVaR={cvar_us/1000:.1f}ms OK, mas estabilidade={stability_score:.0f}%'
                        decision['confidence'] = 0.7
                else:
                    decision['energy_saver'] = 'ALLOWED'
                    decision['action'] = 'ACTIVATE_ENERGY_SAVING'
                    decision['reason'] = f'NORMAL: CVaR={cvar_us/1000:.1f}ms < 60ms - Energia pode economizar'
                    decision['confidence'] = 0.9
            
            # FAIXA 2: PREVENÇÃO (60-80ms) - Analisar tendência
            elif cvar_us < CVAR_CRITICAL_US:
                slope = trend_info.get('slope_ms_per_sec', 0) if trend_info.get('valid') else 0
                
                # Se tendência subindo rápido → BLOCKED preventivo
                if slope > 2:
                    decision['energy_saver'] = 'BLOCKED'
                    decision['action'] = 'PREVENTIVE_BLOCK'
                    decision['reason'] = f'PREVENÇÃO: CVaR={cvar_us/1000:.1f}ms + slope=+{slope:.1f}ms/s → Bloqueio preventivo'
                    decision['confidence'] = 0.8
                    decision['preventive_block'] = True
                    self.stats['preventive_blocks'] = self.stats.get('preventive_blocks', 0) + 1
                
                # Se tendência descendo → ALLOWED (melhorando)
                elif slope < 0:
                    decision['energy_saver'] = 'ALLOWED'
                    decision['action'] = 'ACTIVATE_ENERGY_SAVING'
                    decision['reason'] = f'PREVENÇÃO: CVaR={cvar_us/1000:.1f}ms + slope={slope:.1f}ms/s (melhorando)'
                    decision['confidence'] = 0.75
                
                # Caso contrário → CONDITIONAL (monitorar)
                else:
                    decision['energy_saver'] = 'CONDITIONAL'
                    decision['action'] = 'MONITOR'
                    decision['reason'] = f'PREVENÇÃO: CVaR={cvar_us/1000:.1f}ms (60-80ms) - Monitorando'
                    decision['confidence'] = 0.6
            
            # FAIXA 3: CRÍTICO (≥ 80ms) - Bloquear
            else:
                decision['energy_saver'] = 'BLOCKED'
                decision['action'] = 'PRIORIZE_SLA'
                decision['reason'] = f'CRÍTICO: CVaR={cvar_us/1000:.1f}ms ≥ 80ms - SLA em risco!'
                decision['confidence'] = 1.0
                self.stats['sla_violations'] += 1
        
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
            # Permitido → POWER_DOWN (economizar)
            # Verificar nível de confiança para escolher nível
            confidence = decision.get('confidence', 0)
            if confidence >= 0.8:
                self.energy_cmd.send_power_down(reason=f"ALLOWED: {reason}")
            else:
                self.energy_cmd.send_reduce_power(reason=f"ALLOWED (cautela): {reason}")
                
        elif energy_state == 'CONDITIONAL':
            # Condicional → REDUCE_POWER (economizar parcialmente)
            self.energy_cmd.send_reduce_power(reason=f"CONDITIONAL: {reason}")
            
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
        """Registra decisão no Data Lake."""
        self.data_lake.record_decision(decision)
    
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
        
        while self.running:
            self.cycle += 1
            
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
            
            # 9. Espera próximo ciclo
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
