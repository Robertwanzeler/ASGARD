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
import re
from datetime import datetime
from collections import deque
from greenran_paths import (
    DRL_VENV_SITE_PACKAGES,
    MODELS_DIR,
    PROJECT_ROOT,
    STATE_DIR,
    SLICER_INTENT_PATH,
    ENERGY_INTENT_PATH,
    VEHICLE_INTENT_PATH,
    RAPP_DECISION_PATH,
    EXTENDED_METRICS_JSON_PATH,
    XAPP_HEALTH_PATH,
    XAPP_INTENTS_DIR,
    ensure_runtime_dirs,
    as_str,
    get_fixed_active_cameras,
    get_fixed_total_ues,
)
from greenran_runtime import load_runtime_config

# Add drlexp venv to path for DRL imports
DRL_VENV_PATH = as_str(DRL_VENV_SITE_PACKAGES)
if os.path.exists(DRL_VENV_PATH) and DRL_VENV_PATH not in sys.path:
    sys.path.insert(0, DRL_VENV_PATH)

from rapp_data_lake import DataLake
from rapp_pattern_engine import PatternRecognition
from rapp_agent_openran import AgentOpenRAN
from rapp_a1_interface import A1PolicyInterface
from rapp_ml_predictor import MLPredictor
from rapp_online_retrain_runtime import (
    build_online_retrain_command,
    load_online_retrain_manifest,
    online_retrain_manifest_path,
    summarize_online_retrain_manifest,
)
from rapp_armd_runtime import ARMDRuntimeAdvisor
from rapp_rl_policy import build_runtime_rl_policy
from rapp_sac_resource_model import compute_shared_resource_snapshot
from rapp_marl_shadow import MARLShadowRuntimeEvaluator
from rapp_network_improvement import build_network_improvement

# from rapp_synthetic_generator import SyntheticDataGenerator  # Removed - not available
from rapp_xapp_manager import XAppManager
from rapp_trend_analysis import TrendAnalysis
from energy_command_protocol import EnergyCommand
from vehicle_policy_runtime import (
    evaluate_vehicle_policy as shared_evaluate_vehicle_policy,
    get_vehicle_metrics as shared_get_vehicle_metrics,
)

SLICER_INTENT_PATH = as_str(SLICER_INTENT_PATH)
ENERGY_INTENT_PATH = as_str(ENERGY_INTENT_PATH)
VEHICLE_INTENT_PATH = as_str(VEHICLE_INTENT_PATH)
RAPP_DECISION_PATH = as_str(RAPP_DECISION_PATH)
EXTENDED_METRICS_PATH = as_str(EXTENDED_METRICS_JSON_PATH)
XAPP_HEALTH_FILE = as_str(XAPP_HEALTH_PATH)
APP2_MONITORING_PATH = as_str(STATE_DIR / "app2_monitoramento" / "monitoring_snapshot.json")
APP2_SENSORS_PATH = as_str(STATE_DIR / "app2_monitoramento" / "sensors" / "latest.json")
APP1_MONITORING_PATH = as_str(STATE_DIR / "app1_vigilancia" / "monitoring_snapshot.json")
APP3_MONITORING_PATH = as_str(STATE_DIR / "app3_veicular" / "monitoring_snapshot.json")
ARTICLE00_SCENARIO_CONTROL_PATH = as_str(STATE_DIR / "article00_scenario_control.json")
RAPP_DECISIONS_LOG_PATH = as_str(STATE_DIR / "rapp_decisions.jsonl")

RUNTIME_CONFIG = load_runtime_config()
DEFAULT_INTERVAL = max(1, float(RUNTIME_CONFIG["orchestrator"]["interval_seconds"]))
FIXED_TOTAL_UES = get_fixed_total_ues()
FIXED_ACTIVE_CAMERAS = get_fixed_active_cameras()

_INT_RE = re.compile(r"^[+-]?\d+$")
_FLOAT_RE = re.compile(r"^[+-]?(?:\d+\.\d*|\d*\.\d+|\d+)(?:[eE][+-]?\d+)?$")


def _safe_read_json_file(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _load_app1_camera_override():
    control = _safe_read_json_file(ARTICLE00_SCENARIO_CONTROL_PATH)
    override = control.get("app1_camera_override", {}) or {}
    if not override.get("enabled", False):
        return {}
    return override


def _load_app1_monitoring_snapshot():
    snapshot = _safe_read_json_file(APP1_MONITORING_PATH)
    return snapshot if isinstance(snapshot, dict) else {}


def _load_app2_sensor_override():
    control = _safe_read_json_file(ARTICLE00_SCENARIO_CONTROL_PATH)
    override = control.get("app2_sensor_override", {}) or {}
    if not override.get("enabled", False):
        return {}
    return override


def _load_vehicle_override():
    control = _safe_read_json_file(ARTICLE00_SCENARIO_CONTROL_PATH)
    override = control.get("vehicle_override", {}) or {}
    if not override.get("enabled", False):
        return {}
    return override


def _load_network_health_override():
    control = _safe_read_json_file(ARTICLE00_SCENARIO_CONTROL_PATH)
    override = control.get("network_health_override", {}) or {}
    if not override.get("enabled", False):
        return {}
    slope_ms_per_sec = float(override.get("slope_ms_per_sec", 0.0) or 0.0)
    current_latency_ms = float(override.get("current_latency_ms", 0.0) or 0.0)
    time_to_critical_ms = override.get("time_to_critical_ms")
    time_to_good_ms = override.get("time_to_good_ms")
    if time_to_critical_ms is None and slope_ms_per_sec > 0.0 and current_latency_ms < 150.0:
        time_to_critical_ms = round((150.0 - current_latency_ms) / slope_ms_per_sec, 1)
    if time_to_good_ms is None and slope_ms_per_sec < 0.0 and current_latency_ms > 50.0:
        time_to_good_ms = round((current_latency_ms - 50.0) / abs(slope_ms_per_sec), 1)
    normalized = dict(override)
    normalized.update({
        "enabled": True,
        "valid": bool(override.get("valid", True)),
        "slope_ms_per_sec": slope_ms_per_sec,
        "slope_us_per_sec": float(override.get("slope_us_per_sec", slope_ms_per_sec * 1000.0) or 0.0),
        "trend": str(override.get("trend", override.get("mode", "scenario_control")) or "scenario_control"),
        "confidence": float(override.get("confidence", 1.0) or 0.0),
        "time_to_critical_ms": time_to_critical_ms,
        "time_to_good_ms": time_to_good_ms,
        "current_latency_ms": current_latency_ms,
        "current_latency_us": float(override.get("current_latency_us", current_latency_ms * 1000.0) or 0.0),
        "r_squared": float(override.get("r_squared", 1.0) or 0.0),
        "n_samples": int(override.get("n_samples", 999) or 0),
        "window_minutes": float(override.get("window_minutes", 5.0) or 0.0),
    })
    return normalized


def _coerce_intent_value(raw_value):
    """Converte valores escalares do intent para tipos numéricos quando possível."""
    value = raw_value.strip()
    if not value:
        return value

    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False

    if value.endswith("%"):
        return value

    if _INT_RE.match(value):
        try:
            return int(value)
        except ValueError:
            return value

    if _FLOAT_RE.match(value):
        try:
            return float(value)
        except ValueError:
            return value

    return value


def evaluate_vehicle_policy(vehicle_metrics):
    return shared_evaluate_vehicle_policy(vehicle_metrics)


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
        self.app2_connectivity_history = deque(maxlen=3)
        self._resource_allocation_prev = {'r_ran': 0.5, 'r_ai': 0.5}
        self.marl_shadow_evaluator = MARLShadowRuntimeEvaluator(RUNTIME_CONFIG.get('tasam_advisor', {}))
        
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

        ml_runtime_cfg = RUNTIME_CONFIG.get('ml', {})
        self.ml_enabled = bool(ml_runtime_cfg.get('enabled', True))
        self.ml_retrain_enabled = self.ml_enabled and bool(ml_runtime_cfg.get('retrain_enabled', True))
        self.ml_predictor = None
        self.ml_invalid_streak = 0
        if self.ml_enabled:
            # ML Predictor (Random Forest / XGBoost) com acesso ao banco
            self.ml_predictor = MLPredictor(data_lake=self.data_lake)
            print("[rApp] ML runtime enabled")
        else:
            print("[rApp] ML runtime disabled by config; DRL/rules only")

        # Runtime RL policy selection. The live allocator remains heuristic while
        # TA-SAM MARL stays in shadow/control-gated evaluation.
        self.rl_policy = None
        self.drl_predictor = None
        try:
            self.rl_policy = build_runtime_rl_policy()
            self.rl_policy.load_models()
            meta = self.rl_policy.metadata
            print(
                f"[rApp] RL policy loaded: {meta.policy_id} "
                f"({meta.algorithm}, domain={meta.decision_domain})"
            )
            if meta.legacy_runtime_compatible:
                self.drl_predictor = self.rl_policy
                print("[rApp] RL policy attached to legacy energy runtime")
            else:
                print("[rApp] RL policy attached to heuristic live path with TA-SAM shadow support")
        except Exception as e:
            print(f"[rApp] RL policy load failed: {e}")
            self.rl_policy = None
            self.drl_predictor = None

        # Retreinamento automático do ML (2x por dia = a cada 12h)
        self.ml_retrain_interval = 12 * 3600  # 12 horas em segundos
        self.ml_last_retrain = time.time()
        self.ml_retrain_count = 0

        # XApp Manager (controla ciclo de vida dos xApps)
        self.xapp_manager = XAppManager()
        self.armd_runtime = ARMDRuntimeAdvisor()
        if self.armd_runtime.loaded and self.armd_runtime.enabled:
            print(
                f"[rApp] ARMD-GreenRAN ativo "
                f"(threshold={self.armd_runtime.threshold}, subset={self.armd_runtime.subset_size}, mode={self.armd_runtime.mode})"
            )
        else:
            print(f"[rApp] ARMD-GreenRAN indisponível: {self.armd_runtime.load_error or 'disabled'}")
        
        # Limpar processos zumbis antes de iniciar
        self.xapp_manager.cleanup_zombies()
        
        # Gera dados sintéticos se solicitado
        if synthetic_days > 0:
            self._generate_synthetic_data(synthetic_days)
        
        # Setup signal handlers
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
        # Cria diretórios
        ensure_runtime_dirs()
        os.makedirs(as_str(XAPP_INTENTS_DIR), exist_ok=True)
        
        # INICIA xApps CONTROLADOS PELO RAPP
        # Slicer SEMPRE inicia com rApp (prioridade)
        self._energy_xapp_disabled = False
        self._vehicle_xapp_disabled = False
        self._start_slicer()
        self._start_vehicle_control()

        # Energy Saver NÃO inicia automaticamente - rApp decide quando ativar
        self._energy_active = False
        self._vehicle_active = self.xapp_manager.is_running("vehicle_control")
        
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
        if self._energy_xapp_disabled:
            return False
        if not self.xapp_manager.is_available("energy_saver"):
            self._energy_xapp_disabled = True
            print("[rApp] xApp ENERGY SAVER indisponível neste build; novas tentativas serão ignoradas")
            return False

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
            if not self.xapp_manager.is_available("energy_saver"):
                self._energy_xapp_disabled = True
                print("[rApp] xApp ENERGY SAVER indisponível após tentativa; novas tentativas serão ignoradas")
            else:
                print("[rApp] ERRO: Não foi possível iniciar xApp ENERGY SAVER")
            return False

    def _start_vehicle_control(self):
        """Inicia o xApp VEHICLE CONTROL (sempre ativo para o App3)."""
        if self._vehicle_xapp_disabled:
            return False
        if not self.xapp_manager.is_available("vehicle_control"):
            self._vehicle_xapp_disabled = True
            print("[rApp] xApp VEHICLE CONTROL indisponível neste build; novas tentativas serão ignoradas")
            return False

        print("[rApp] Iniciando xApp VEHICLE CONTROL (App3)...")
        if self.xapp_manager.start("vehicle_control"):
            if self.xapp_manager.wait_for_ready("vehicle_control", timeout=10):
                self._vehicle_active = True
                print("[rApp] xApp VEHICLE CONTROL pronto e ativo")
            else:
                self._vehicle_active = True
                print("[rApp] AVISO: xApp VEHICLE CONTROL pode não estar pronto")
        else:
            self._vehicle_active = False
            if not self.xapp_manager.is_available("vehicle_control"):
                self._vehicle_xapp_disabled = True
                print("[rApp] xApp VEHICLE CONTROL indisponível após tentativa; novas tentativas serão ignoradas")
            else:
                print("[rApp] ERRO: Não foi possível iniciar xApp VEHICLE CONTROL")
    
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
            'energy_active': self._energy_active,
            'vehicle_active': self.xapp_manager.is_running("vehicle_control"),
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

        vehicle_pid = self.xapp_manager.get_pid('vehicle_control')
        vehicle_running = self.xapp_manager.is_running('vehicle_control')
        status['VEHICLE'] = {
            'status': 'RUNNING' if vehicle_running else 'STOPPED',
            'pid': vehicle_pid,
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
                        intent[key.strip()] = _coerce_intent_value(value)
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
                        intent[key.strip()] = _coerce_intent_value(value)
            return intent
        except Exception as e:
            print(f"[rApp] ERRO ao ler ENERGY: {e}")
            return None

    def read_vehicle_intent(self):
        """Lê intenção do xApp VEHICLE CONTROL."""
        try:
            if not os.path.exists(VEHICLE_INTENT_PATH):
                return None

            intent = {}
            with open(VEHICLE_INTENT_PATH, 'r') as f:
                for line in f:
                    if '=' in line:
                        key, value = line.strip().split('=', 1)
                        intent[key.strip()] = _coerce_intent_value(value)
            return intent
        except Exception as e:
            print(f"[rApp] ERRO ao ler VEHICLE CONTROL: {e}")
            return None
    
    def read_extended_metrics(self):
        """Lê métricas estendidas do JSON."""
        if not os.path.exists(EXTENDED_METRICS_PATH):
            return None

        for attempt in range(3):
            try:
                with open(EXTENDED_METRICS_PATH, 'r') as f:
                    return json.load(f)
            except json.JSONDecodeError:
                if attempt < 2:
                    time.sleep(0.05)
                    continue
                return None
            except Exception:
                return None

        return None

    def _get_latest_sim_time(self):
        """Obtém o sim_time mais recente sem deixar DB antigo bloquear novo run."""
        db_sim_time = 0
        db_timestamp = 0

        try:
            cursor = self.data_lake.conn.cursor()
            cursor.execute("SELECT timestamp, sim_time_s FROM extended_metrics ORDER BY timestamp DESC LIMIT 1")
            result = cursor.fetchone()
            if result:
                db_timestamp = int(result[0] or 0)
                db_sim_time = float(result[1] or 0)
        except Exception:
            pass

        live_sim_time = 0
        live_timestamp = 0
        extended_metrics = self.read_extended_metrics()
        if extended_metrics:
            try:
                live_sim_time = float(
                    (extended_metrics.get('sim_time_range', {}) or {}).get('end', 0) or 0
                )
                live_timestamp = int((extended_metrics.get('timestamp') or 0) / 1000)
            except (TypeError, ValueError):
                live_sim_time = 0

        if live_sim_time > 0:
            if db_sim_time <= 0:
                return live_sim_time

            # Novo run: JSON vivo resetou o sim_time, mas o DB ainda tem 600s do run anterior.
            if live_timestamp >= db_timestamp and live_sim_time + 10 < db_sim_time:
                return live_sim_time

            # DB ficou para trás ou perdeu ciclos; use o JSON corrente.
            if live_timestamp > db_timestamp + max(2, int(self.interval)):
                return live_sim_time

        return db_sim_time if db_sim_time > 0 else live_sim_time

    def _get_camera_sla_metrics(self, slicer_intent=None):
        """
        Extrai métricas reais de SLA das câmeras.

        Prioriza o snapshot de métricas estendidas por UE e cai para o
        intent do slicer apenas como fallback quando necessário.
        """
        metrics = {
            'latency_ms': 0.0,
            'throughput_mbps': 0.0,
            'active_cameras': 0,
            'critical_cameras': 0,
            'observed_cameras': 0,
            'throughput_ready': False,
            'throughput_source': 'unavailable',
            'sim_time_s': 0.0,
        }

        override = _load_app1_camera_override()
        if override:
            metrics.update({
                'latency_ms': float(override.get('latency_ms', 0.0) or 0.0),
                'throughput_mbps': float(override.get('throughput_mbps', 0.0) or 0.0),
                'active_cameras': int(override.get('active_cameras', 0) or 0),
                'critical_cameras': int(override.get('critical_cameras', 0) or 0),
                'observed_cameras': int(
                    override.get('observed_cameras', override.get('active_cameras', 0)) or 0
                ),
                'throughput_ready': bool(override.get('throughput_ready', True)),
                'throughput_source': str(
                    override.get('throughput_source', 'article00_control') or 'article00_control'
                ),
            })
            return metrics

        camera_mode = str(os.environ.get("GREENRAN_APP1_CAMERA_SOURCE_MODE", "real") or "real").strip().lower()
        if camera_mode == "simulated":
            snapshot = _load_app1_monitoring_snapshot()
            network = snapshot.get('network', {}) if isinstance(snapshot, dict) else {}
            camera_sla = snapshot.get('camera_sla', {}) if isinstance(snapshot, dict) else {}
            if network and camera_sla:
                metrics.update({
                    'latency_ms': float(network.get('max_camera_latency_ms', 0.0) or 0.0),
                    'throughput_mbps': float(network.get('min_camera_throughput_mbps', 0.0) or 0.0),
                    'active_cameras': int(network.get('active_cameras', 0) or 0),
                    'critical_cameras': int(snapshot.get('cameras', {}).get('critical_active', 0) or 0),
                    'observed_cameras': int(network.get('observed_camera_metrics', network.get('active_cameras', 0)) or 0),
                    'throughput_ready': bool(network.get('camera_metrics_ready', False)),
                    'throughput_source': 'app1_simulated_snapshot',
                })
                return metrics

        extended_metrics = self.read_extended_metrics()
        if extended_metrics:
            try:
                metrics['sim_time_s'] = float(
                    (extended_metrics.get('sim_time_range', {}) or {}).get('end', 0) or 0
                )
            except (TypeError, ValueError):
                metrics['sim_time_s'] = 0.0

            ue_metrics = extended_metrics.get('ue_metrics', {}) or {}
            camera_entries = [
                ue_data for ue_data in ue_metrics.values()
                if ue_data.get('device_type') == 'camera'
            ]

            if camera_entries:
                def _camera_real_throughput_kbps(entry):
                    candidates = (
                        float(entry.get('rx_throughput_kbps', 0) or 0),
                        float(entry.get('throughput_kbps', 0) or 0),
                        float(entry.get('total_pdcp_throughput_kbps', 0) or 0),
                        float(entry.get('tx_throughput_kbps', 0) or 0),
                        float(entry.get('cu_up_throughput_kbps', 0) or 0),
                    )
                    return max(candidates)

                worst_latency_us = max(float(entry.get('latency_us', 0) or 0) for entry in camera_entries)
                min_throughput_kbps = min(
                    _camera_real_throughput_kbps(entry)
                    for entry in camera_entries
                )
                critical_cameras = sum(
                    1 for entry in camera_entries
                    if float(entry.get('latency_us', 0) or 0) >= 100000
                )
                observed_cameras = sum(
                    1 for entry in camera_entries
                    if (
                        bool(entry.get('has_latency_samples'))
                        or int(entry.get('packet_count', 0) or 0) > 0
                        or float(entry.get('rx_bytes', 0) or 0) > 0
                        or float(entry.get('tx_bytes', 0) or 0) > 0
                        or _camera_real_throughput_kbps(entry) > 0
                    )
                )
                throughput_sources = {
                    str(entry.get('throughput_source', '') or '').strip()
                    for entry in camera_entries
                    if str(entry.get('throughput_source', '') or '').strip()
                }

                metrics.update({
                    'latency_ms': worst_latency_us / 1000.0,
                    'throughput_mbps': min_throughput_kbps / 1000.0,
                    'active_cameras': len(camera_entries),
                    'critical_cameras': critical_cameras,
                    'observed_cameras': observed_cameras,
                    'throughput_ready': observed_cameras == len(camera_entries),
                    'throughput_source': ','.join(sorted(throughput_sources)) or 'pdcp_rx_window',
                })
                return metrics

            global_metrics = extended_metrics.get('global_metrics', {}) or {}
            metrics.update({
                'latency_ms': float(global_metrics.get('global_worst_camera_latency_us', 0) or 0) / 1000.0,
                'active_cameras': int(extended_metrics.get('active_cameras', 0) or 0),
                'critical_cameras': int(extended_metrics.get('critical_cameras', 0) or 0),
            })

        if slicer_intent:
            p95_latency_us = float(slicer_intent.get('P95_LATENCY_US', 0) or 0)
            metrics['latency_ms'] = max(metrics['latency_ms'], p95_latency_us / 1000.0)
            metrics['active_cameras'] = max(metrics['active_cameras'], int(slicer_intent.get('ACTIVE_CAMERAS', 0) or 0))
            metrics['critical_cameras'] = max(metrics['critical_cameras'], int(slicer_intent.get('CRITICAL_CAMERAS', 0) or 0))
            metrics['throughput_ready'] = (
                metrics['active_cameras'] > 0
                and metrics['observed_cameras'] >= metrics['active_cameras']
            )

        return metrics

    def _get_app2_gateway_metrics(self):
        """
        Lê o snapshot vivo do App2 e resume a saúde dos sensores/gateways.

        App2 representa a camada mMTC/monitoramento ambiental. Os sensores não
        são UEs individuais; a decisão usa conectividade agregada por gateway.
        """
        metrics = {
            'available': False,
            'stale': False,
            'age_seconds': None,
            'total_sensors': 0,
            'active_sensors': 0,
            'connected_sensors': 0,
            'error_sensors': 0,
            'low_battery_sensors': 0,
            'connected_ratio': 1.0,
            'error_ratio': 0.0,
            'packet_loss_percent': 0.0,
            'delivery_success_percent': 100.0,
            'avg_latency_ms': 0.0,
            'avg_battery_percent': 100.0,
            'avg_rssi_dbm': 0.0,
            'network_utilization_percent': 0.0,
            'gateways': 0,
            'connectivity_modes': 0,
        }

        try:
            override = _load_app2_sensor_override()
            if override:
                total_sensors = max(0, int(override.get('total_sensors', 0) or 0))
                connected_sensors = max(0, min(total_sensors, int(override.get('connected_sensors', total_sensors) or total_sensors)))
                error_sensors = max(0, min(total_sensors, int(override.get('error_sensors', total_sensors - connected_sensors) or (total_sensors - connected_sensors))))
                low_battery_sensors = max(0, min(total_sensors, int(override.get('low_battery_sensors', 0) or 0)))
                connected_ratio = connected_sensors / max(total_sensors, 1)
                error_ratio = error_sensors / max(total_sensors, 1)
                metrics.update({
                    'available': total_sensors > 0,
                    'stale': False,
                    'age_seconds': 0.0,
                    'total_sensors': total_sensors,
                    'active_sensors': connected_sensors,
                    'connected_sensors': connected_sensors,
                    'error_sensors': error_sensors,
                    'low_battery_sensors': low_battery_sensors,
                    'connected_ratio': connected_ratio,
                    'error_ratio': error_ratio,
                    'packet_loss_percent': float(override.get('packet_loss_percent', 0) or 0),
                    'delivery_success_percent': float(override.get('delivery_success_percent', 100) or 100),
                    'avg_latency_ms': float(override.get('avg_latency_ms', 0) or 0),
                    'avg_battery_percent': float(override.get('avg_battery_percent', 100) or 100),
                    'avg_rssi_dbm': float(override.get('avg_rssi_dbm', 0) or 0),
                    'network_utilization_percent': float(override.get('network_utilization_percent', 0) or 0),
                    'gateways': 1,
                    'connectivity_modes': 1,
                    'scenario_mode': str(override.get('mode', 'scenario_control_override') or 'scenario_control_override'),
                })
                return metrics

            if not os.path.exists(APP2_MONITORING_PATH):
                return metrics

            with open(APP2_MONITORING_PATH, 'r') as f:
                snapshot = json.load(f)

            sensors = snapshot.get('sensors', {}) or {}
            readings = snapshot.get('readings', {}) or {}
            network = snapshot.get('network', {}) or {}

            total_sensors = int(sensors.get('total', 0) or 0)
            connected_sensors = int(sensors.get('connected', 0) or 0)
            error_sensors = int(sensors.get('error', 0) or 0)
            low_battery_sensors = int(sensors.get('low_battery', 0) or 0)
            connected_ratio = connected_sensors / max(total_sensors, 1)
            error_ratio = error_sensors / max(total_sensors, 1)

            timestamp_text = snapshot.get('timestamp')
            age_seconds = None
            if timestamp_text:
                try:
                    ts = datetime.fromisoformat(str(timestamp_text).replace('Z', '+00:00'))
                    if ts.tzinfo is not None:
                        age_seconds = max(0.0, time.time() - ts.timestamp())
                    else:
                        age_seconds = max(0.0, time.time() - ts.timestamp())
                except (TypeError, ValueError, OSError):
                    age_seconds = None

            if age_seconds is None:
                try:
                    age_seconds = max(0.0, time.time() - os.path.getmtime(APP2_MONITORING_PATH))
                except OSError:
                    age_seconds = None

            metrics.update({
                'available': True,
                'stale': bool(age_seconds is not None and age_seconds > 45),
                'age_seconds': age_seconds,
                'total_sensors': total_sensors,
                'active_sensors': int(sensors.get('active', 0) or 0),
                'connected_sensors': connected_sensors,
                'error_sensors': error_sensors,
                'low_battery_sensors': low_battery_sensors,
                'connected_ratio': connected_ratio,
                'error_ratio': error_ratio,
                'packet_loss_percent': float(network.get('packet_loss_percent', 0) or 0),
                'delivery_success_percent': float(network.get('delivery_success_percent', 100) or 100),
                'avg_latency_ms': float(network.get('avg_latency_ms', 0) or 0),
                'avg_battery_percent': float(readings.get('avg_battery_percent', 100) or 100),
                'avg_rssi_dbm': float(network.get('avg_rssi_dbm', 0) or 0),
                'network_utilization_percent': float(network.get('network_utilization_percent', 0) or 0),
                'gateways': len(sensors.get('gateways', []) or []),
                'connectivity_modes': len(sensors.get('connectivity_modes', []) or []),
            })
        except Exception as e:
            metrics['error'] = str(e)
            print(f"[rApp] Erro ao ler App2 monitoring: {e}")

        return metrics

    def _get_vehicle_metrics(self):
        return shared_get_vehicle_metrics()

    def _should_keep_vehicle_collection_active(self):
        """
        Permite continuar a coleta veicular mesmo quando o sim_time do ns-3
        deixa de avançar, desde que o App3 continue publicando snapshot fresco.
        """
        vehicle_override = _load_vehicle_override()
        if not vehicle_override:
            return False

        vehicle_metrics = self._get_vehicle_metrics()
        if not vehicle_metrics.get('available'):
            return False
        if vehicle_metrics.get('stale'):
            return False
        if int(vehicle_metrics.get('total_vehicles', 0) or 0) <= 0:
            return False
        return True
    
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

        app2_snapshot = _safe_read_json_file(APP2_MONITORING_PATH)
        app2_sensors = _safe_read_json_file(APP2_SENSORS_PATH)
        if isinstance(app2_snapshot, dict) and app2_snapshot:
            self.data_lake.record_app2_snapshot(
                snapshot=app2_snapshot,
                sensors=app2_sensors if isinstance(app2_sensors, list) else [],
            )

        app3_snapshot = _safe_read_json_file(APP3_MONITORING_PATH)
        if isinstance(app3_snapshot, dict) and app3_snapshot:
            self.data_lake.record_app3_snapshot(snapshot=app3_snapshot)

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
    
    def make_decision(self, slicer_intent, energy_intent, vehicle_intent=None):
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
            'ml_rf_prediction': {},
            'ml_influenced': False,
            'trend_analysis': None,
            'preventive_block': False,
            'camera_metrics': None,
            'vehicle_metrics': None,
            'vehicle_state': 'UNKNOWN',
            'app2_metrics': None,
            'drl_prediction': {},
            'drl_influenced': False,
            'drl_rejected_reason': '',
            'drl_policy_action': '',
            'drl_policy_applied': False,
            'ml_risk_cap_applied': False,
            'resource_allocation': {},
            'rl_policy_runtime': {},
            'tasam_advisor': {},
            'advisor_arbitration': {},
        }

        if self.rl_policy is not None:
            meta = self.rl_policy.metadata
            decision['rl_policy_runtime'] = {
                'policy_id': meta.policy_id,
                'family': meta.family,
                'algorithm': meta.algorithm,
                'decision_domain': meta.decision_domain,
                'action_semantics': meta.action_semantics,
                'legacy_runtime_compatible': meta.legacy_runtime_compatible,
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
        if vehicle_intent:
            decision['vehicle_state'] = str(vehicle_intent.get('STATE', 'UNKNOWN') or 'UNKNOWN').upper()
        
        # ========================================
        # REGRA DE PRIORIDADE DA PROPOSTA
        # 1) Câmeras/eMBB: 25 Mbps mínimos por câmera e latência protegida.
        # 2) App2/mMTC: saúde agregada de sensores/gateways.
        # ========================================
        
        camera_metrics = self._get_camera_sla_metrics(slicer_intent)
        camera_latency_ms = camera_metrics['latency_ms']
        camera_throughput_mbps = camera_metrics['throughput_mbps']
        active_cameras = camera_metrics['active_cameras']
        critical_cameras = camera_metrics['critical_cameras']
        observed_cameras = int(camera_metrics.get('observed_cameras', 0) or 0)
        throughput_ready = bool(camera_metrics.get('throughput_ready', False))
        sim_time_s = float(camera_metrics.get('sim_time_s', 0) or 0)

        decision['camera_metrics'] = {
            'latency_ms': camera_latency_ms,
            'throughput_mbps': camera_throughput_mbps,
            'active_cameras': active_cameras,
            'critical_cameras': critical_cameras,
            'observed_cameras': observed_cameras,
            'throughput_ready': throughput_ready,
            'throughput_source': camera_metrics.get('throughput_source', 'unavailable'),
            'sim_time_s': sim_time_s,
        }

        shared_resource_cfg = RUNTIME_CONFIG.get('shared_resources', {})
        CAMERA_THROUGHPUT_MIN_MBPS = max(
            1.0,
            float(shared_resource_cfg.get('camera_throughput_target_mbps', 25.0) or 25.0),
        )
        CAMERA_THROUGHPUT_WARNING_MBPS = max(
            CAMERA_THROUGHPUT_MIN_MBPS,
            float(shared_resource_cfg.get('camera_throughput_guard_mbps', 30.0) or 30.0),
        )
        CAMERA_LATENCY_WARNING_MS = max(
            1.0,
            float(shared_resource_cfg.get('camera_latency_warning_ms', 80.0) or 80.0),
        )
        CAMERA_LATENCY_BLOCK_MS = max(
            CAMERA_LATENCY_WARNING_MS,
            float(shared_resource_cfg.get('camera_latency_target_ms', 100.0) or 100.0),
        )

        # A política de câmera precisa continuar válida mesmo quando o SLICER
        # ainda não publicou intent; a fonte primária é App1/override.
        if active_cameras > 0 and not throughput_ready:
            decision['energy_saver'] = 'CONDITIONAL'
            decision['action'] = 'FULL_POWER_GUARD'
            decision['reason'] = (
                f'CÂMERA: aguardando amostras de throughput '
                f'({observed_cameras}/{active_cameras} câmeras observadas, sim_time={sim_time_s:.1f}s)'
            )
            decision['confidence'] = 0.6
            decision['priority_violation'] = 'THROUGHPUT_WARMUP'
            print(
                f"\033[1;33m[rApp] CÂMERA: aguardando amostras de throughput "
                f"({observed_cameras}/{active_cameras}, sim_time={sim_time_s:.1f}s) - FULL_POWER_GUARD\033[0m"
            )

        # REGRA: Throughput < 25Mbps → BLOCKED (SLA mínimo violado)
        elif active_cameras > 0 and camera_throughput_mbps < CAMERA_THROUGHPUT_MIN_MBPS:
            decision['energy_saver'] = 'BLOCKED'
            decision['action'] = 'FULL_POWER'
            decision['reason'] = (
                f'CÂMERA SLA: Throughput {camera_throughput_mbps:.1f}Mbps '
                f'< {CAMERA_THROUGHPUT_MIN_MBPS:.0f}Mbps (mínimo)'
            )
            decision['confidence'] = 1.0
            decision['priority_violation'] = 'THROUGHPUT'
            self.stats['sla_violations'] += 1
            print(
                f"\033[1;31m[rApp] CÂMERA SLA: Throughput {camera_throughput_mbps:.1f}Mbps "
                f"< {CAMERA_THROUGHPUT_MIN_MBPS:.0f}Mbps - BLOCKED\033[0m"
            )

        # REGRA: Latência ≥ limite duro → BLOCKED
        elif active_cameras > 0 and camera_latency_ms >= CAMERA_LATENCY_BLOCK_MS:
            decision['energy_saver'] = 'BLOCKED'
            decision['action'] = 'FULL_POWER'
            decision['reason'] = f'CÂMERA SLA: Latência {camera_latency_ms:.1f}ms >= {CAMERA_LATENCY_BLOCK_MS:.0f}ms'
            decision['confidence'] = 1.0
            decision['priority_violation'] = 'LATENCY'
            self.stats['sla_violations'] += 1
            print(
                f"\033[1;31m[rApp] CÂMERA SLA: Latência {camera_latency_ms:.1f}ms "
                f">= {CAMERA_LATENCY_BLOCK_MS:.0f}ms - BLOCKED\033[0m"
            )

        # REGRA: Throughput na faixa 25-30Mbps → CONDITIONAL (faixa de cautela)
        elif active_cameras > 0 and camera_throughput_mbps < CAMERA_THROUGHPUT_WARNING_MBPS:
            decision['energy_saver'] = 'CONDITIONAL'
            decision['action'] = 'FULL_POWER_GUARD'
            decision['reason'] = (
                f'CÂMERA: Throughput {camera_throughput_mbps:.1f}Mbps em '
                f'[{CAMERA_THROUGHPUT_MIN_MBPS:.0f}-{CAMERA_THROUGHPUT_WARNING_MBPS:.0f}Mbps] - margem protegida'
            )
            decision['confidence'] = 0.85
            decision['priority_violation'] = 'THROUGHPUT_WARNING'
            print(
                f"\033[1;33m[rApp] CÂMERA: Throughput {camera_throughput_mbps:.1f}Mbps em "
                f"[{CAMERA_THROUGHPUT_MIN_MBPS:.0f}-{CAMERA_THROUGHPUT_WARNING_MBPS:.0f}Mbps] - CONDITIONAL\033[0m"
            )

        # REGRA: Latência na faixa de guarda → CONDITIONAL
        elif active_cameras > 0 and camera_latency_ms >= CAMERA_LATENCY_WARNING_MS:
            decision['energy_saver'] = 'CONDITIONAL'
            decision['action'] = 'FULL_POWER_GUARD'
            decision['reason'] = (
                f'CÂMERA: Latência {camera_latency_ms:.1f}ms em '
                f'[{CAMERA_LATENCY_WARNING_MS:.0f}-{CAMERA_LATENCY_BLOCK_MS:.0f}ms] - margem protegida'
            )
            decision['confidence'] = 0.7
            decision['priority_violation'] = 'LATENCY_WARNING'
            print(
                f"\033[1;33m[rApp] CÂMERA: Latência {camera_latency_ms:.1f}ms em "
                f"[{CAMERA_LATENCY_WARNING_MS:.0f}-{CAMERA_LATENCY_BLOCK_MS:.0f}ms] - CONDITIONAL\033[0m"
            )
        
        # Câmera tem prioridade máxima. BLOCKED é violação direta; CONDITIONAL é
        # faixa de guarda e também não pode ser relaxada por CVaR/ML/DRL.
        camera_sla_violated = (
            decision.get('energy_saver') in ['BLOCKED']
            and decision.get('priority_violation') in ['THROUGHPUT', 'LATENCY']
        )
        camera_guard_active = (
            decision.get('energy_saver') == 'CONDITIONAL'
            and decision.get('priority_violation') in ['THROUGHPUT_WARNING', 'LATENCY_WARNING', 'THROUGHPUT_WARMUP']
        )
        camera_priority_active = camera_sla_violated or camera_guard_active

        vehicle_metrics = self._get_vehicle_metrics()
        decision['vehicle_metrics'] = vehicle_metrics

        vehicle_sla_violated = False
        vehicle_guard_active = False

        if not camera_priority_active and vehicle_metrics.get('available'):
            if vehicle_intent and vehicle_intent.get('STATE'):
                raw_state = str(vehicle_intent.get('STATE', 'UNKNOWN') or 'UNKNOWN').upper()
                severity = {
                    'CRITICAL': 'critical',
                    'WARNING': 'warning',
                    'NORMAL': 'normal',
                    'IDLE': 'none',
                }.get(raw_state, 'none')
                vehicle_policy = {
                    'available': str(vehicle_intent.get('AVAILABLE', 'true')).lower() == 'true',
                    'severity': severity,
                    'violation': vehicle_intent.get('VIOLATION', '') or '',
                    'action': vehicle_intent.get('ACTION', '') or '',
                    'reason': vehicle_intent.get('REASON', '') or '',
                    'confidence': float(vehicle_intent.get('CONFIDENCE', 0.0) or 0.0),
                    'sla_violated': severity == 'critical',
                    'guard_active': severity == 'warning',
                }
            else:
                vehicle_policy = evaluate_vehicle_policy(vehicle_metrics)
            if vehicle_policy['severity'] == 'critical':
                decision['energy_saver'] = 'BLOCKED'
                decision['action'] = vehicle_policy['action']
                decision['reason'] = f"VEHICLE SAFETY: {vehicle_policy['reason']}"
                decision['confidence'] = vehicle_policy['confidence']
                decision['priority_violation'] = vehicle_policy['violation']
                vehicle_sla_violated = True
                self.stats['sla_violations'] += 1
                print(f"\033[1;31m[rApp] VEHICLE SAFETY: {vehicle_policy['reason']} - BLOCKED\033[0m")
            elif vehicle_policy['severity'] == 'warning':
                decision['energy_saver'] = 'CONDITIONAL'
                decision['action'] = vehicle_policy['action']
                decision['reason'] = f"VEHICLE SAFETY: {vehicle_policy['reason']} - margem protegida"
                decision['confidence'] = vehicle_policy['confidence']
                decision['priority_violation'] = vehicle_policy['violation']
                vehicle_guard_active = True
                print(f"\033[1;33m[rApp] VEHICLE SAFETY: {vehicle_policy['reason']} - CONDITIONAL\033[0m")

        vehicle_priority_active = vehicle_sla_violated or vehicle_guard_active

        app2_metrics = self._get_app2_gateway_metrics()
        decision['app2_metrics'] = app2_metrics

        app2_sla_violated = False
        app2_guard_active = False

        if not camera_priority_active and not vehicle_priority_active and app2_metrics.get('available'):
            connected_ratio = float(app2_metrics.get('connected_ratio', 1.0) or 0)
            packet_loss = float(app2_metrics.get('packet_loss_percent', 0) or 0)
            delivery_success = float(app2_metrics.get('delivery_success_percent', 100) or 0)
            app2_latency_ms = float(app2_metrics.get('avg_latency_ms', 0) or 0)
            avg_battery = float(app2_metrics.get('avg_battery_percent', 100) or 0)
            error_ratio = float(app2_metrics.get('error_ratio', 0) or 0)
            low_battery = int(app2_metrics.get('low_battery_sensors', 0) or 0)
            error_sensors = int(app2_metrics.get('error_sensors', 0) or 0)

            critical_reasons = []
            warning_reasons = []

            self.app2_connectivity_history.append(connected_ratio)
            app2_low_connectivity_samples = sum(
                1 for ratio in self.app2_connectivity_history if ratio < 0.95
            )

            if app2_metrics.get('stale'):
                age = app2_metrics.get('age_seconds')
                warning_reasons.append(f"snapshot antigo ({age:.0f}s)" if age is not None else "snapshot antigo")
            if connected_ratio < 0.85:
                critical_reasons.append(f"sensores conectados {connected_ratio:.0%} < 85%")
            elif connected_ratio < 0.90:
                warning_reasons.append(f"sensores conectados {connected_ratio:.0%} < 90%")
            elif connected_ratio < 0.95 and app2_low_connectivity_samples >= 2:
                warning_reasons.append(
                    f"sensores conectados {connected_ratio:.0%} < 95% persistente "
                    f"({app2_low_connectivity_samples}/{len(self.app2_connectivity_history)} amostras)"
                )
            if packet_loss >= 10:
                critical_reasons.append(f"packet loss {packet_loss:.1f}% >= 10%")
            elif packet_loss >= 5:
                warning_reasons.append(f"packet loss {packet_loss:.1f}% >= 5%")
            if delivery_success < 90:
                critical_reasons.append(f"entrega {delivery_success:.1f}% < 90%")
            elif delivery_success < 95:
                warning_reasons.append(f"entrega {delivery_success:.1f}% < 95%")
            if app2_latency_ms >= 1000:
                critical_reasons.append(f"latência média {app2_latency_ms:.0f}ms >= 1000ms")
            elif app2_latency_ms >= 500:
                warning_reasons.append(f"latência média {app2_latency_ms:.0f}ms >= 500ms")
            if avg_battery < 15:
                critical_reasons.append(f"bateria média {avg_battery:.1f}% < 15%")
            elif avg_battery < 25 or low_battery > 0:
                warning_reasons.append(f"bateria média {avg_battery:.1f}% / baixa={low_battery}")
            if error_ratio >= 0.20:
                critical_reasons.append(f"sensores em erro {error_ratio:.0%} >= 20%")
            elif error_sensors >= 2:
                warning_reasons.append(f"sensores em erro={error_sensors}")
            elif error_sensors == 1 and app2_low_connectivity_samples >= 2:
                warning_reasons.append(
                    f"sensores em erro=1 persistente "
                    f"({app2_low_connectivity_samples}/{len(self.app2_connectivity_history)} amostras)"
                )

            if critical_reasons:
                decision['energy_saver'] = 'BLOCKED'
                decision['action'] = 'FULL_POWER'
                decision['reason'] = f"APP2 mMTC SLA: {critical_reasons[0]}"
                decision['confidence'] = 0.9
                decision['priority_violation'] = 'APP2_MTC_CRITICAL'
                app2_sla_violated = True
                self.stats['sla_violations'] += 1
                print(f"\033[1;31m[rApp] APP2 mMTC SLA: {critical_reasons[0]} - BLOCKED\033[0m")
            elif warning_reasons:
                decision['energy_saver'] = 'CONDITIONAL'
                decision['action'] = 'FULL_POWER_GUARD'
                decision['reason'] = f"APP2 mMTC: {warning_reasons[0]} - margem protegida"
                decision['confidence'] = 0.7
                decision['priority_violation'] = 'APP2_MTC_WARNING'
                app2_guard_active = True
                print(f"\033[1;33m[rApp] APP2 mMTC: {warning_reasons[0]} - CONDITIONAL\033[0m")

        app2_priority_active = app2_sla_violated or app2_guard_active
        service_priority_active = camera_priority_active or vehicle_priority_active or app2_priority_active
        
        # ========================================
        # ETAPA 0: TREND ANALYSIS (SLOPE) - PREDITIVA
        # O rApp detecta se latência está SUBINDO antes de bater crítico
        # ========================================
        network_health_override = _load_network_health_override()

        trend_info = self.trend_analysis.calculate_latency_slope(window_minutes=5)
        if network_health_override:
            trend_info = dict(network_health_override)
        trend_decision = self.trend_analysis.should_preempt_energy(trend_info)
        if network_health_override:
            trend_decision = {
                'preventive': False,
                'reason': 'scenario_control override',
                'confidence': 1.0,
            }
        decision['trend_analysis'] = {
            'slope_ms_per_sec': trend_info.get('slope_ms_per_sec', 0),
            'trend': trend_info.get('trend', 'unknown'),
            'confidence': trend_info.get('confidence', 0),
            'time_to_critical': trend_info.get('time_to_critical_ms'),
            'current_latency_ms': trend_info.get('current_latency_ms', 0)
        }
        
        # Decisão preventiva baseada em tendência
        # Se serviço prioritário já violou/entrou em guarda, não aplicar tendência.
        if not service_priority_active and trend_decision['preventive']:
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
        
        # Se serviço prioritário violou SLA, pular decisões preditivas que relaxam energia.
        if camera_sla_violated:
            print(f"\033[1;31m[rApp] PULANDO ML - Câmera violou SLA primeiro\033[0m")
        elif camera_guard_active:
            print(f"\033[1;33m[rApp] PULANDO ML - Câmera em faixa de guarda\033[0m")
        elif app2_sla_violated:
            print(f"\033[1;31m[rApp] PULANDO ML - App2 mMTC violou SLA\033[0m")
        elif app2_guard_active:
            print(f"\033[1;33m[rApp] PULANDO ML - App2 mMTC em faixa de guarda\033[0m")
        
        pattern_analysis = self.pattern_engine.analyze_current()
        decision['pattern_analysis'] = pattern_analysis
        
        ml_decision = self.pattern_engine.should_allow_energy_saving()
        decision['ml_decision'] = ml_decision
        
        # Só aplica se não houve bloco preventivo por tendência e serviços prioritários OK.
        if not decision['preventive_block'] and not service_priority_active:
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
        network_health_source = 'data_lake'
        if network_health_override:
            network_health = {
                'cvar_us': float(network_health_override.get('cvar_us', 0.0) or 0.0),
                'latest_cvar_us': float(network_health_override.get('latest_cvar_us', network_health_override.get('cvar_us', 0.0)) or 0.0),
                'p95_us': float(network_health_override.get('p95_us', 0.0) or 0.0),
                'median_us': float(network_health_override.get('current_latency_ms', 0.0) or 0.0) * 1000.0,
                'variance_us2': float(network_health_override.get('variance_us2', 0.0) or 0.0),
                'stability_score': float(network_health_override.get('stability_score', 100.0) or 100.0),
            }
            network_health_source = 'scenario_control_override'
        
        # Thresholds recalibrados para o novo CVaR do coletor
        CVAR_ECO_US = 40000          # 40ms - rede muito saudável
        CVAR_NORMAL_US = 120000      # 120ms - faixa normal do novo CVaR de cauda
        CVAR_CRITICAL_US = 250000    # 250ms - cauda crítica real
        P95_WARNING_US = 60000       # 60ms - alerta de degradação distribuída
        P95_CRITICAL_US = 120000     # 120ms - degradação distribuída severa
        SLOPE_PREVENTION = 2.0       # 2ms/s - Prevenção
        STABILITY_THRESHOLD = 50     # Score mínimo de estabilidade
        
        if network_health:
            cvar_us = float(network_health.get('cvar_us', 0) or 0)
            variance_us2 = float(network_health.get('variance_us2', 0) or 0)
            median_us = float(network_health.get('median_us', 0) or 0)
            p95_us = float(network_health.get('p95_us', 0) or 0)
            stability_score = network_health.get('stability_score', 100)
            latest_cvar_us = float(network_health.get('latest_cvar_us', cvar_us) or cvar_us)
            
            # DEBUG: Log do CVaR calculado
            print(f"[rApp DEBUG] network_health.cvar_us = {cvar_us}us = {cvar_us/1000:.1f}ms | latest={latest_cvar_us/1000:.1f}ms | p95={p95_us/1000:.1f}ms")
            
            # Armazenar no decision para debugging
            decision['network_health'] = {
                'median_us': median_us,
                'p95_us': p95_us,
                'cvar_us': cvar_us,
                'latest_cvar_us': latest_cvar_us,
                'variance_us2': variance_us2,
                'stability_score': stability_score
            }
        else:
            # Fallback para mediana se network_health não disponível
            cvar_us = float(self.calculate_median_latency(minutes=5) or 0)
            variance_us2 = 0.0
            stability_score = 100
            decision['network_health'] = {'fallback': True, 'cvar_us': cvar_us}
            network_health_source = 'fallback_median'
            if cvar_us > 0:
                print(f"[rApp DEBUG] Fallback CVaR = {cvar_us}us = {cvar_us/1000:.1f}ms")
            else:
                print(f"[rApp DEBUG] Sem dados de CVaR disponíveis")

        decision['network_health'].update(
            build_network_improvement(
                decision.get('network_health'),
                source=network_health_source,
            )
        )

        resource_allocation = compute_shared_resource_snapshot(
            camera_metrics=decision.get('camera_metrics'),
            app2_metrics=decision.get('app2_metrics'),
            vehicle_metrics=decision.get('vehicle_metrics'),
            network_health=decision.get('network_health'),
            shared_resource_config=RUNTIME_CONFIG.get('shared_resources', {}),
            previous_allocation=self._resource_allocation_prev,
        )
        if (
            self.rl_policy is not None
            and not self.rl_policy.metadata.legacy_runtime_compatible
            and self.rl_policy.is_available()
        ):
            try:
                rl_resource_result = self.rl_policy.predict(
                    {
                        'resource_allocation_baseline': resource_allocation,
                        'previous_allocation': self._resource_allocation_prev,
                        'camera_metrics': decision.get('camera_metrics'),
                        'app2_metrics': decision.get('app2_metrics'),
                        'vehicle_metrics': decision.get('vehicle_metrics'),
                        'network_health': decision.get('network_health'),
                        'shared_resource_config': RUNTIME_CONFIG.get('shared_resources', {}),
                    }
                )
                resource_candidate = rl_resource_result.get('resource_allocation') or {}
                if resource_candidate:
                    resource_allocation = resource_candidate
                    decision['rl_policy_runtime']['resource_policy_applied'] = (
                        rl_resource_result.get('final_decision') == 'RESOURCE_REALLOCATED'
                    )
                    decision['rl_policy_runtime']['resource_policy_reason'] = rl_resource_result.get('reason', '')
                    decision['rl_policy_runtime']['resource_policy_confidence'] = float(
                        rl_resource_result.get('confidence', 0.0) or 0.0
                    )
                    print(
                        f"[rApp RL-RESOURCE] {self.rl_policy.metadata.algorithm}: "
                        f"{rl_resource_result.get('reason', 'no reason')}"
                    )
                else:
                    decision['rl_policy_runtime']['resource_policy_applied'] = False
                    decision['rl_policy_runtime']['resource_policy_reason'] = (
                        rl_resource_result.get('reason', 'empty resource allocation result')
                    )
            except Exception as exc:
                decision['rl_policy_runtime']['resource_policy_applied'] = False
                decision['rl_policy_runtime']['resource_policy_reason'] = (
                    f"resource policy predict failed: {exc}"
                )
                print(f"[rApp RL-RESOURCE] fallback to heuristic allocator: {exc}")
        marl_shadow = self.marl_shadow_evaluator.evaluate(
            (resource_allocation or {}).get('article_marl_state'),
            resource_snapshot=resource_allocation,
        )
        tasam_advisor = marl_shadow.get('advisor', {}) if isinstance(marl_shadow.get('advisor'), dict) else {}
        resource_allocation['marl_shadow'] = marl_shadow
        resource_allocation['tasam_advisor'] = tasam_advisor
        decision['rl_policy_runtime']['marl_shadow'] = marl_shadow
        decision['tasam_advisor'] = tasam_advisor
        decision['tasam_enabled'] = bool(tasam_advisor.get('enabled', marl_shadow.get('enabled', False)))
        decision['tasam_mode'] = tasam_advisor.get('mode', marl_shadow.get('advisory_mode', 'shadow'))
        decision['tasam_policy_id'] = tasam_advisor.get('policy_id', marl_shadow.get('policy_id', ''))
        decision['tasam_source'] = tasam_advisor.get('source', marl_shadow.get('source', ''))
        decision['tasam_confidence'] = float(tasam_advisor.get('confidence', marl_shadow.get('confidence', 0.0)) or 0.0)
        decision['tasam_valid'] = bool(tasam_advisor.get('valid', marl_shadow.get('valid', False)))
        decision['tasam_would_influence'] = bool(tasam_advisor.get('would_influence', marl_shadow.get('would_influence', False)))
        decision['tasam_energy_decision'] = ((tasam_advisor.get('energy_advice') or {}).get('decision', ''))
        decision['tasam_energy_action'] = ((tasam_advisor.get('energy_advice') or {}).get('action', ''))
        decision['resource_allocation'] = resource_allocation
        self._resource_allocation_prev = {
            'r_ran': float(resource_allocation.get('r_ran', 0.5) or 0.5),
            'r_ai': float(resource_allocation.get('r_ai', 0.5) or 0.5),
        }
        
        # Só aplica CVaR se não houve bloco preventivo, não houve violação/guarda
        # prioritária e temos dados. Caso contrário, a decisão prioritária prevalece.
        if not decision['preventive_block'] and not service_priority_active and cvar_us is not None and cvar_us > 0:
            
            # Obter slope para decisões de prevenção
            slope = trend_info.get('slope_ms_per_sec', 0) if trend_info.get('valid') else 0
            
            # ===== LÓGICA DE COORDENAÇÃO rApp-xApps =====
            # REGRAS (refinadas com slope negativo vs zero):
            # 1. CÂMERAS SÃO PRIORIDADE MÁXIMA (se Slicer CRITICAL → BLOCKED)
            # 2. CVaR/UE ≥ 250ms ou P95/UE ≥ 120ms → BLOCKED
            # 3. Slope > 2ms/s → BLOCKED (prevenção)
            # 4a. CVaR < 40ms + slope ≤ 0 → ECO (25% potência)
            # 4b. CVaR < 120ms + slope < -0.01 → ALLOWED (50%) - melhorando
            # 4c. CVaR < 120ms + abs(slope) < 0.01 → ALLOWED (60%) - estável
            # 4d. 120-250ms + slope < -0.01 → ALLOWED (70%) - melhorando
            # 4e. 120-250ms + abs(slope) < 0.01 → ALLOWED (80%) - estável
            # 5. CVaR < 120ms + slope > 0.01 → CONDITIONAL (70%) - piorando
            # 6. 120-250ms + slope > 0.01 → CONDITIONAL (90%) - piorando
            
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
            
            # REGRA 2: cauda crítica ou degradação distribuída
            elif cvar_us >= CVAR_CRITICAL_US or p95_us >= P95_CRITICAL_US:
                decision['energy_saver'] = 'BLOCKED'
                decision['action'] = 'FULL_POWER'
                if p95_us >= P95_CRITICAL_US and cvar_us < CVAR_CRITICAL_US:
                    decision['reason'] = f'CRÍTICO: P95/UE={p95_us/1000:.1f}ms ≥ 120ms - degradação distribuída'
                else:
                    decision['reason'] = f'CRÍTICO: CVaR={cvar_us/1000:.1f}ms ≥ 250ms - SLA em risco!'
                decision['confidence'] = 1.0
                self.stats['sla_violations'] += 1
                print(f"\033[1;31m[rApp] REGRA 2: cauda crítica - BLOCKED\033[0m")
            
            # REGRA 3: Slope > 2ms/s → PREVENÇÃO
            elif slope > SLOPE_PREVENTION:
                decision['energy_saver'] = 'BLOCKED'
                decision['action'] = 'FULL_POWER'
                decision['reason'] = f'PREVENÇÃO: CVaR={cvar_us/1000:.1f}ms + slope=+{slope:.1f}ms/s → Bloqueio preventivo'
                decision['confidence'] = 0.8
                decision['preventive_block'] = True
                self.stats['preventive_blocks'] = self.stats.get('preventive_blocks', 0) + 1
                print(f"\033[1;33m[rApp] REGRA 3: Slope > 2ms/s - BLOCKED\033[0m")
            
            # REGRA 4a: CVaR < 40ms + slope estável/melhorando → ECO MODE (25% potência)
            elif cvar_us < CVAR_ECO_US and p95_us < P95_WARNING_US and slope <= SLOPE_TOLERANCE:
                decision['energy_saver'] = 'ALLOWED'
                decision['action'] = 'POWER_DOWN_ECO'
                decision['eco_mode'] = True
                decision['reason'] = f'ECO MODE: CVaR={cvar_us/1000:.1f}ms em zona saudável + P95={p95_us/1000:.1f}ms + slope={slope:.2f}ms/s ({slope_state}) → Economia extrema (25%)'
                decision['confidence'] = 0.95
                print(f"\033[1;32m[rApp] REGRA 4a: CVaR em zona saudável + Slope estável/melhorando - ECO MODE\033[0m")
            
            # REGRA 4b: CVaR < 60ms + slope < -0.01 → ALLOWED (50%) - melhorando
            elif cvar_us < CVAR_NORMAL_US and slope < -SLOPE_TOLERANCE:
                decision['energy_saver'] = 'ALLOWED'
                decision['action'] = 'POWER_DOWN'
                decision['reason'] = f'MELHORANDO: CVaR={cvar_us/1000:.1f}ms em zona normal + slope={slope:.2f}ms/s (descendo) → Economia (50%)'
                decision['confidence'] = 0.9
                print(f"\033[1;32m[rApp] REGRA 4b: CVaR em zona normal + Slope < 0 - ALLOWED 50%\033[0m")
            
            # REGRA 4c: CVaR < 60ms + slope ≈ 0 → ALLOWED (60%) - estável
            elif cvar_us < CVAR_NORMAL_US and abs(slope) < SLOPE_TOLERANCE:
                decision['energy_saver'] = 'ALLOWED'
                decision['action'] = 'POWER_DOWN'
                decision['reason'] = f'ESTÁVEL: CVaR={cvar_us/1000:.1f}ms em zona normal + slope={slope:.2f}ms/s (zero) → Economia moderada (60%)'
                decision['confidence'] = 0.85
                print(f"\033[1;32m[rApp] REGRA 4c: CVaR em zona normal + Slope ≈ 0 - ALLOWED 60%\033[0m")
            
            # REGRA 4d: 60-80ms + slope < -0.01 → ALLOWED (70%) - melhorando
            elif cvar_us < CVAR_CRITICAL_US and slope < -SLOPE_TOLERANCE:
                decision['energy_saver'] = 'ALLOWED'
                decision['action'] = 'POWER_DOWN'
                decision['reason'] = f'MELHORANDO: CVaR={cvar_us/1000:.1f}ms (120-250ms) + slope={slope:.2f}ms/s (descendo) → Economia (70%)'
                decision['confidence'] = 0.8
                print(f"\033[1;33m[rApp] REGRA 4d: 120ms ≤ CVaR < 250ms + Slope < 0 - ALLOWED 70%\033[0m")
            
            # REGRA 4e: 60-80ms + slope ≈ 0 → ALLOWED (80%) - estável
            elif cvar_us < CVAR_CRITICAL_US and abs(slope) < SLOPE_TOLERANCE:
                decision['energy_saver'] = 'ALLOWED'
                decision['action'] = 'POWER_DOWN'
                decision['reason'] = f'ESTÁVEL: CVaR={cvar_us/1000:.1f}ms (120-250ms) + slope={slope:.2f}ms/s (zero) → Economia moderada (80%)'
                decision['confidence'] = 0.75
                print(f"\033[1;33m[rApp] REGRA 4e: 120ms ≤ CVaR < 250ms + Slope ≈ 0 - ALLOWED 80%\033[0m")
            
            # REGRA 5: CVaR < 60ms + slope > 0.01 → CONDITIONAL (70%) - piorando
            elif cvar_us < CVAR_NORMAL_US and slope > SLOPE_TOLERANCE:
                decision['energy_saver'] = 'CONDITIONAL'
                decision['action'] = 'MONITOR'
                decision['reason'] = f'PIORANDO: CVaR={cvar_us/1000:.1f}ms em zona normal + slope=+{slope:.2f}ms/s (subindo) → Monitorar (70%)'
                decision['confidence'] = 0.6
                print(f"\033[1;36m[rApp] REGRA 5: CVaR em zona normal + Slope > 0 - CONDITIONAL 70%\033[0m")
            
            # REGRA 6: 60-80ms + slope > 0.01 → CONDITIONAL (90%) - piorando
            elif cvar_us < CVAR_CRITICAL_US and slope > SLOPE_TOLERANCE:
                decision['energy_saver'] = 'CONDITIONAL'
                decision['action'] = 'MONITOR'
                decision['reason'] = f'PIORANDO: CVaR={cvar_us/1000:.1f}ms (120-250ms) + slope=+{slope:.2f}ms/s (subindo) → Monitorar (90%)'
                decision['confidence'] = 0.4
                print(f"\033[1;31m[rApp] REGRA 6: 120ms ≤ CVaR < 250ms + Slope > 0 - CONDITIONAL 90%\033[0m")
            
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

        if pattern_analysis and not decision['preventive_block'] and not service_priority_active:
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

        # Features compartilhadas por ML e DRL. Elas ficam fora do bloco de ML
        # para a DRL continuar segura mesmo se o modelo RF não estiver carregado.
        latest_extended = None
        row = {}
        if self.data_lake:
            latest_extended = self.data_lake.get_latest_extended_metrics(limit=1)
            if latest_extended:
                row = latest_extended[0]

        live_extended = _safe_read_json_file(EXTENDED_METRICS_PATH)
        live_global_metrics = live_extended.get('global_metrics', {}) or {}

        throughput_kbps = float(
            live_global_metrics.get(
                'pdcp_delta_throughput_kbps',
                live_global_metrics.get('throughput_kbps', row.get('throughput_kbps', 0)),
            ) or 0
        )
        packet_loss_rate = float(
            live_global_metrics.get('global_packet_loss_rate', row.get('global_packet_loss_rate', 0)) or 0
        )
        jitter_ms = float(
            live_global_metrics.get('global_jitter_us', row.get('global_jitter_us', 0)) or 0
        ) / 1000.0
        tx_bytes = int(live_global_metrics.get('total_tx_bytes', row.get('total_tx_bytes', 0)) or 0)
        rx_bytes = int(live_global_metrics.get('total_rx_bytes', row.get('total_rx_bytes', 0)) or 0)
        tx_rx_ratio = tx_bytes / max(rx_bytes, 1) if rx_bytes > 0 else 0

        energy_history = 0
        if self.data_lake:
            recent_decisions = self.data_lake.get_recent_decisions(minutes=5, limit=10)
            if recent_decisions:
                blocked_count = sum(1 for d in recent_decisions if d.get('decision') == 'BLOCKED')
                energy_history = blocked_count / len(recent_decisions)

        # P95 = pior 5% dos UEs (é o valor crítico que as regras usam).
        p95_value = network_health.get('p95_us', 0) if network_health else 0
        vehicle_metrics = decision.get('vehicle_metrics') or {}

        ml_loaded = self.ml_enabled and self.ml_predictor is not None and self.ml_predictor.is_loaded()
        print(f"[rApp DEBUG] ML enabled: {self.ml_enabled}, loaded: {ml_loaded}, preventive_block: {decision['preventive_block']}")
        
        if ml_loaded and not decision['preventive_block'] and not service_priority_active:
            scenario_stage = (
                (((decision.get('resource_allocation') or {}).get('marl_shadow') or {}).get('scenario_stage'))
                or (((decision.get('rl_policy_runtime') or {}).get('marl_shadow') or {}).get('scenario_stage'))
                or ''
            )
            ml_metrics = {
                'cvar_per_ue_us': cvar_us if cvar_us else 0,
                'cvar_p95_us': p95_value,
                'latency_p95_per_ue_us': p95_value,
                'global_avg_latency_us': float(row.get('global_avg_latency_us', 0) or 0) if latest_extended else 0,
                'variance_per_ue_us2': variance_us2 if variance_us2 else 0,
                'total_active_cameras': slicer_intent.get('ACTIVE_CAMERAS', FIXED_ACTIVE_CAMERAS) if slicer_intent else FIXED_ACTIVE_CAMERAS,
                'total_active_ues': slicer_intent.get('ACTIVE_UES', FIXED_TOTAL_UES) if slicer_intent else FIXED_TOTAL_UES,
                'total_critical_ues': slicer_intent.get('CRITICAL_UES', 0) if slicer_intent else 0,
                'sim_time_s': float(row.get('sim_time_s', 0) or 0) if latest_extended else 0,
                # Novas features
                'throughput_kbps': throughput_kbps,
                'packet_loss_rate': packet_loss_rate,
                'jitter_ms': jitter_ms,
                'tx_rx_ratio': tx_rx_ratio,
                'energy_history': energy_history,
                'total_active_vehicles': vehicle_metrics.get('total_vehicles', 0),
                'vehicle_high_risk': vehicle_metrics.get('high_risk_vehicles', 0),
                'vehicle_medium_risk': vehicle_metrics.get('medium_risk_vehicles', 0),
                'vehicle_degraded_autonomy': vehicle_metrics.get('degraded_autonomy_vehicles', 0),
                'vehicle_max_latency_ms': vehicle_metrics.get('max_latency_ms', 0.0),
                'vehicle_max_packet_loss_percent': vehicle_metrics.get('max_packet_loss_percent', 0.0),
                'scenario_stage': scenario_stage,
            }
            
            # DEBUG: Log do P95 que está sendo enviado para ML
            print(f"[rApp DEBUG] ML cvar_p95 = {p95_value}us = {p95_value/1000:.1f}ms")

            # Usar predição com contexto do banco de dados
            ml_result = self.ml_predictor.predict_with_db_context(ml_metrics)
            decision['ml_rf_prediction'] = ml_result
            
            # DEBUG: Log do resultado ML
            print(f"[rApp DEBUG] ml_result = {ml_result}")

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
                    # Se a rede está claramente saudável, previsões na faixa 70-80ms
                    # também são incoerentes e devem ser descartadas.
                    elif ml_cvar_prev >= 70 and cvar_ms < 10 and (p95_value / 1000.0) < 10:
                        print(
                            f"\033[1;33m[rApp] AVISO: ML superestimou rede saudável "
                            f"({ml_cvar_prev:.1f}ms vs real={cvar_ms:.1f}ms, P95={(p95_value / 1000.0):.1f}ms)\033[0m"
                        )
                        ml_prediction_valid = False

                ml_result['valid'] = ml_prediction_valid
                if ml_prediction_valid:
                    self.ml_invalid_streak = 0
                else:
                    self.ml_invalid_streak += 1
                    if self.ml_invalid_streak >= 3:
                        print(
                            f"\033[1;33m[rApp ML] Resetando histórico interno após "
                            f"{self.ml_invalid_streak} predições inválidas consecutivas\033[0m"
                        )
                        self.ml_predictor.reset_history()
                        self.ml_invalid_streak = 0
                
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
        # ETAPA 2B: LEGACY DRL PREDICTOR PATH
        # Mantido apenas como guarda morta; a linha oficial usa
        # heuristica ao vivo com TA-SAM em shadow/control gate.
        # ========================================
        
        prev_cvar_for_drl = getattr(self, '_prev_cvar_us', 0) or 0
        if self.drl_predictor is not None and not decision.get('preventive_block', False) and not service_priority_active:
            try:
                # Safe float conversion
                cvar_val = float(cvar_us) if cvar_us else 50.0
                prev_val = prev_cvar_for_drl
                drl_cvar_trend = (cvar_val - prev_val) / 1000.0
                
                drl_state = {
                    'cvar_ms': cvar_val / 1000.0,
                    'cvar_trend': drl_cvar_trend,
                    'cvar_acceleration': 0,
                    'latency_p95_ms': float(p95_value) / 1000.0 if p95_value else 60,
                    'jitter_ms': float(jitter_ms) if jitter_ms else 2,
                    'packet_loss_pct': float(packet_loss_rate) * 100 if packet_loss_rate else 0.1,
                    'throughput_mbps': float(throughput_kbps) / 1000.0 if throughput_kbps else 100,
                    'active_ues': int(slicer_intent.get('ACTIVE_UES', FIXED_TOTAL_UES)) if slicer_intent else FIXED_TOTAL_UES,
                    'active_cameras': int(slicer_intent.get('ACTIVE_CAMERAS', FIXED_ACTIVE_CAMERAS)) if slicer_intent else FIXED_ACTIVE_CAMERAS,
                    'critical_ues': int(slicer_intent.get('CRITICAL_UES', 0)) if slicer_intent else 0,
                    'camera_ratio': float(slicer_intent.get('ACTIVE_CAMERAS', FIXED_ACTIVE_CAMERAS)) / max(float(slicer_intent.get('ACTIVE_UES', FIXED_TOTAL_UES)), 1) if slicer_intent else float(FIXED_ACTIVE_CAMERAS) / max(float(FIXED_TOTAL_UES), 1.0),
                    'critical_ue_ratio': float(slicer_intent.get('CRITICAL_UES', 0)) / max(float(slicer_intent.get('ACTIVE_UES', FIXED_TOTAL_UES)), 1) if slicer_intent else 0,
                    'allocated_rbs': int(slicer_intent.get('ACTIVE_UES', FIXED_TOTAL_UES)) * 10 if slicer_intent else FIXED_TOTAL_UES * 10,
                    'current_power': 20,
                    'power_budget': 30,
                    'hour_sin': 0,
                    'hour_cos': 1,
                    'variance_ms2': float(variance_us2) / 1_000_000.0 if variance_us2 else 10,
                    'active_vehicles': int(vehicle_metrics.get('total_vehicles', 0) or 0),
                    'vehicle_high_risk': int(vehicle_metrics.get('high_risk_vehicles', 0) or 0),
                    'vehicle_medium_risk': int(vehicle_metrics.get('medium_risk_vehicles', 0) or 0),
                    'vehicle_degraded_autonomy': int(vehicle_metrics.get('degraded_autonomy_vehicles', 0) or 0),
                    'vehicle_max_latency_ms': float(vehicle_metrics.get('max_latency_ms', 0.0) or 0.0),
                    'vehicle_max_packet_loss_percent': float(vehicle_metrics.get('max_packet_loss_percent', 0.0) or 0.0),
                }
                
                drl_result = self.drl_predictor.predict(drl_state)
                decision['drl_prediction'] = drl_result
                
                drl_cvar = drl_result.get('predicted_cvar_ms', 0)
                drl_raw_cvar = drl_result.get('raw_predicted_cvar_ms', drl_cvar)
                drl_decision = drl_result.get('final_decision', 'UNKNOWN')
                drl_confidence = drl_result.get('confidence', 0)
                
                calibration_note = ''
                if drl_result.get('calibrated'):
                    calibration_note = f", raw={drl_raw_cvar:.2f}ms, calib={drl_result.get('calibration_reason', '')}"
                print(
                    f"[rApp DRL] CVaR predicted: {drl_cvar:.2f}ms, Decision: {drl_decision}, "
                    f"Power: {drl_result.get('power', 'N/A')}, "
                    f"Policy: {drl_result.get('policy_action', 'N/A')}, "
                    f"seq={drl_result.get('state_sequence_len', 0)}"
                    f"{calibration_note}"
                )

                real_cvar_ms = cvar_val / 1000.0 if cvar_val else 0
                real_p95_ms = float(p95_value) / 1000.0 if p95_value else 0
                drl_prediction_valid = True
                if (
                    drl_decision == 'BLOCKED'
                    and real_cvar_ms < 40
                    and real_p95_ms < 60
                    and drl_cvar > 60
                ):
                    drl_prediction_valid = False
                    decision['drl_rejected_reason'] = 'DRL_SUPERESTIMATED_HEALTHY_NETWORK'
                    print(
                        "\033[1;33m[rApp DRL] Ignorada: previu BLOCKED/CVaR="
                        f"{drl_cvar:.1f}ms, mas rede real está saudável "
                        f"(CVaR={real_cvar_ms:.1f}ms, P95={real_p95_ms:.1f}ms)\033[0m"
                    )
                
                if drl_prediction_valid and drl_decision != 'UNKNOWN' and decision['energy_saver'] == 'ALLOWED':
                    ml_risk = decision.get('ml_rf_prediction') or {}
                    ml_risk_ok = ml_risk.get('decision') in (None, 'ALLOWED')
                    ml_predicted_cvar = float(ml_risk.get('predicted_cvar_ms', 0) or 0)
                    if ml_predicted_cvar and ml_predicted_cvar >= 55:
                        ml_risk_ok = False

                    drl_policy_action = drl_result.get('policy_action')
                    if ml_risk_ok and drl_confidence >= 0.40 and drl_policy_action in {
                        'POWER_DOWN_ECO', 'POWER_DOWN', 'CONDITIONAL_REDUCE'
                    }:
                        decision['action'] = drl_policy_action
                        decision['eco_mode'] = drl_policy_action == 'POWER_DOWN_ECO'
                        decision['drl_policy_action'] = drl_policy_action
                        decision['drl_policy_applied'] = True
                        decision['drl_influenced'] = True
                        decision['reason'] = (
                            f"DRL_POLICY: {drl_result.get('power', 'N/A')} -> {drl_policy_action}; "
                            f"CVaR_real={real_cvar_ms:.1f}ms, CVaR_pred={drl_cvar:.1f}ms, "
                            f"conf={drl_confidence:.0%}"
                        )
                        print(
                            f"\033[1;36m[rApp DRL] POLICY: {drl_result.get('power', 'N/A')} "
                            f"→ {drl_policy_action} (CVaR={drl_cvar:.1f}ms, conf={drl_confidence:.0%})\033[0m"
                        )

                # DRL só altera a decisão de alto nível em caso conservador forte.
                if drl_prediction_valid and drl_confidence > 0.6 and drl_decision != 'UNKNOWN':
                    # Se DRL BLOCKED mas ML/RF Allow → seguir DRL (mais seguro)
                    if drl_decision == 'BLOCKED' and decision['energy_saver'] in ['ALLOWED', 'CONDITIONAL']:
                        decision['energy_saver'] = 'BLOCKED'
                        decision['action'] = 'FULL_POWER'
                        reason = decision.get('reason', '')
                        decision['reason'] = f'DRL OVERRIDE: {reason} + DRL={drl_decision} (CVaR={drl_cvar:.1f}ms)'
                        decision['confidence'] = max(decision.get('confidence', 0), drl_confidence)
                        decision['drl_influenced'] = True
                        print(f"\033[1;31m[rApp DRL] OVERRIDE: BLOCKED (conf={drl_confidence:.0%})\033[0m")
                    
                    # Se DRL Allow mas ML Blocked → manter ML (mais seguro)
                    elif drl_decision == 'ALLOWED' and decision['energy_saver'] == 'BLOCKED':
                        print(f"\033[0;33m[rApp DRL] DRL Allow but ML Blocked - keeping ML\033[0m")
                
            except Exception as e:
                print(f"\033[0;90m[rApp DRL] Error: {e}\033[0m")

        # Se a DRL não teve confiança suficiente para escolher a potência, a ML
        # ainda atua como freio de segurança para evitar ECO agressivo com CVaR
        # intermediário. App1/App2 e bloqueios preventivos continuam soberanos.
        if (
            self.ml_enabled
            and
            not service_priority_active
            and not decision.get('preventive_block', False)
            and decision.get('energy_saver') == 'ALLOWED'
            and not decision.get('drl_policy_applied', False)
        ):
            ml_risk = decision.get('ml_rf_prediction') or {}
            ml_risk_valid = bool(ml_risk.get('valid', True))
            try:
                ml_predicted_cvar_ms = float(ml_risk.get('predicted_cvar_ms', 0) or 0)
            except (TypeError, ValueError):
                ml_predicted_cvar_ms = 0.0

            real_cvar_ms = float(cvar_us) / 1000.0 if cvar_us else 0.0
            risk_cvar_ms = max(real_cvar_ms, ml_predicted_cvar_ms) if ml_risk_valid else real_cvar_ms

            if risk_cvar_ms >= 45 and decision.get('action') in ['POWER_DOWN_ECO', 'POWER_DOWN']:
                decision['action'] = 'CONDITIONAL_REDUCE'
                decision['eco_mode'] = False
                decision['ml_risk_cap_applied'] = True
                decision['reason'] = (
                    f"ML_RISK_CAP: CVaR_risco={risk_cvar_ms:.1f}ms "
                    f"(real={real_cvar_ms:.1f}ms, ML={ml_predicted_cvar_ms:.1f}ms); "
                    "DRL sem política aplicada -> CONDITIONAL_REDUCE"
                )
                print(
                    f"\033[1;33m[rApp ML] RISK CAP: CVaR risco={risk_cvar_ms:.1f}ms "
                    "→ CONDITIONAL_REDUCE\033[0m"
                )
            elif risk_cvar_ms >= 30 and decision.get('action') == 'POWER_DOWN_ECO':
                decision['action'] = 'POWER_DOWN'
                decision['eco_mode'] = False
                decision['ml_risk_cap_applied'] = True
                decision['reason'] = (
                    f"ML_RISK_CAP: CVaR_risco={risk_cvar_ms:.1f}ms "
                    f"(real={real_cvar_ms:.1f}ms, ML={ml_predicted_cvar_ms:.1f}ms); "
                    "DRL sem política aplicada -> POWER_DOWN"
                )
                print(
                    f"\033[1;33m[rApp ML] RISK CAP: CVaR risco={risk_cvar_ms:.1f}ms "
                    "→ POWER_DOWN\033[0m"
                )

        # Armazenar CVaR para o próximo ciclo somente depois da DRL usar o anterior.
        self._prev_cvar_us = float(cvar_us) if cvar_us else 0
        
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
        if not service_priority_active and trend_info.get('valid') and trend_info['slope_ms_per_sec'] > 5 and trend_info['current_latency_ms'] > 50:
            decision['energy_saver'] = 'BLOCKED'
            decision['action'] = 'PREVENTIVE_BLOCK'
            decision['reason'] = f"ARBITER_TREND: Slope +{trend_info['slope_ms_per_sec']:.1f}ms/s > 5ms/s - Prevenção total"
            decision['confidence'] = min(0.9, trend_info['confidence'] + 0.1)
            decision['preventive_block'] = True
            self.stats['sla_violations'] += 1

        # ========================================
        # ETAPA 5: ARMD-GreenRAN Runtime Advisor
        # Camada validada offline que só pode aumentar proteção.
        # ========================================
        armd_advice = self.armd_runtime.advise(
            decision=decision,
            camera_metrics=decision.get('camera_metrics'),
            vehicle_metrics=decision.get('vehicle_metrics'),
            app2_metrics=decision.get('app2_metrics'),
            network_health=decision.get('network_health'),
        )
        decision['armd_analysis'] = armd_advice
        decision = self.armd_runtime.apply(decision, armd_advice)
        decision['advisor_arbitration'] = self._build_advisor_arbitration(decision)
        
        return decision

    def _build_advisor_arbitration(self, decision):
        """Build a shadow-only scoreboard between ARMD and TA-SAM advisors."""
        severity_rank = {
            'UNKNOWN': 0,
            'ALLOWED': 1,
            'CONDITIONAL': 2,
            'BLOCKED': 3,
        }
        armd = decision.get('armd_analysis') or {}
        tasam = decision.get('tasam_advisor') or {}

        armd_valid = bool(armd.get('available')) and float(armd.get('confidence', 0.0) or 0.0) >= float(self.armd_runtime.min_confidence)
        tasam_valid = bool(tasam.get('valid', False))

        armd_target = str(armd.get('expected_energy_saver', '') or decision.get('energy_saver', 'UNKNOWN')).upper()
        armd_evidence_count = len(armd.get('evidence', []) or [])
        armd_score = round(
            (0.65 * float(armd.get('confidence', 0.0) or 0.0))
            + (0.20 * (severity_rank.get(armd_target, 0) / 3.0))
            + (0.15 * min(armd_evidence_count / 3.0, 1.0)),
            4,
        )

        tasam_target = str(((tasam.get('energy_advice') or {}).get('decision', 'UNKNOWN')) or 'UNKNOWN').upper()
        tasam_evidence_flags = tasam.get('evidence_flags') or {}
        tasam_evidence_score = sum(1 for value in tasam_evidence_flags.values() if value)
        tasam_score = round(
            (0.50 * float(tasam.get('confidence', 0.0) or 0.0))
            + (0.30 * float(tasam.get('arbitration_score', 0.0) or 0.0))
            + (0.20 * min(tasam_evidence_score / max(len(tasam_evidence_flags), 1), 1.0)),
            4,
        )

        if not armd_valid and not tasam_valid:
            winner = 'none'
        elif armd_valid and not tasam_valid:
            winner = 'armd'
        elif tasam_valid and not armd_valid:
            winner = 'ta_sam'
        elif abs(armd_score - tasam_score) <= 0.02:
            winner = 'tie'
        elif armd_score > tasam_score:
            winner = 'armd'
        else:
            winner = 'ta_sam'

        return {
            'mode': 'shadow_scoreboard',
            'winner': winner,
            'armd_valid': armd_valid,
            'tasam_valid': tasam_valid,
            'agreement': armd_target == tasam_target and armd_target != 'UNKNOWN',
            'armd_score': armd_score,
            'tasam_score': tasam_score,
            'armd_target': armd_target,
            'tasam_target': tasam_target,
            'would_apply': False,
        }
    
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
                    if nh.get('improvement_valid'):
                        f.write(f"|  Melhora rede: {nh.get('network_improvement_pct', 0):.1f}%{' '*36}|\n")
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

                if decision.get('app2_metrics') and decision['app2_metrics'].get('available'):
                    app2 = decision['app2_metrics']
                    f.write("+---------------- APP2 MTC HEALTH --------------------+\n")
                    f.write(f"|  Sensores: {app2.get('connected_sensors', 0)}/{app2.get('total_sensors', 0)} conectados{' '*22}|\n")
                    f.write(f"|  Packet loss: {app2.get('packet_loss_percent', 0):.1f}%{' '*39}|\n")
                    f.write(f"|  Entrega: {app2.get('delivery_success_percent', 0):.1f}%{' '*43}|\n")
                    f.write(f"|  Latencia media: {app2.get('avg_latency_ms', 0):.0f}ms{' '*35}|\n")
                    f.write(f"|  Bateria media: {app2.get('avg_battery_percent', 0):.1f}%{' '*35}|\n")
                    f.write("+----------------------------------------------------+\n")
                    f.write("\n")

                if decision.get('vehicle_metrics') and decision['vehicle_metrics'].get('available'):
                    veh = decision['vehicle_metrics']
                    f.write("+--------------- VEHICLE SAFETY ----------------------+\n")
                    f.write(f"|  Veiculos: {veh.get('total_vehicles', 0)} | Ego: {str(veh.get('ego_present', False)).lower():<5}{' '*31}|\n")
                    f.write(f"|  Risco alto: {veh.get('high_risk_vehicles', 0)} | Risco medio: {veh.get('medium_risk_vehicles', 0)}{' '*20}|\n")
                    f.write(f"|  Latencia max: {veh.get('max_latency_ms', 0):.0f}ms{' '*34}|\n")
                    f.write(f"|  Packet loss: {veh.get('max_packet_loss_percent', 0):.1f}%{' '*37}|\n")
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

                if decision.get('armd_scenario'):
                    f.write(f"|  ARMD: {decision.get('armd_scenario', ''):<49}|\n")
                    f.write(f"|  ARMD source: {decision.get('armd_source', ''):<42}|\n")
                    f.write(f"|  ARMD conf: {float(decision.get('armd_confidence', 0) or 0)*100:.0f}%{' '*47}|\n")
                    if decision.get('armd_override_applied'):
                        f.write("|  ARMD override: escalou protecao                       |\n")
                if decision.get('tasam_enabled'):
                    f.write(f"|  TA-SAM: {decision.get('tasam_mode', 'shadow'):<47}|\n")
                    f.write(f"|  TA-SAM source: {decision.get('tasam_source', ''):<40}|\n")
                    f.write(f"|  TA-SAM conf: {float(decision.get('tasam_confidence', 0) or 0)*100:.0f}%{' '*45}|\n")
                    f.write(f"|  TA-SAM energy: {decision.get('tasam_energy_decision', ''):<39}|\n")
                    if decision.get('advisor_arbitration'):
                        f.write(f"|  Advisor winner: {decision['advisor_arbitration'].get('winner', 'none'):<38}|\n")
                
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
                f.write(f"VEHICLE_STATE={decision.get('vehicle_state', 'UNKNOWN')}\n")
                f.write(f"AGENT_OVERRIDE={str(decision['agent_override']).lower()}\n")
                f.write(f"PATTERN={decision['pattern'] or 'none'}\n")
                f.write(f"PREVENTIVE_BLOCK={str(decision.get('preventive_block', False)).lower()}\n")
                f.write(f"ARMD_ENABLED={str(decision.get('armd_enabled', False)).lower()}\n")
                f.write(f"ARMD_MODE={decision.get('armd_mode', 'unknown')}\n")
                f.write(f"ARMD_SCENARIO={decision.get('armd_scenario', '')}\n")
                f.write(f"ARMD_SOURCE={decision.get('armd_source', '')}\n")
                f.write(f"ARMD_CONFIDENCE={float(decision.get('armd_confidence', 0) or 0):.4f}\n")
                f.write(f"ARMD_OVERRIDE_APPLIED={str(decision.get('armd_override_applied', False)).lower()}\n")
                f.write(f"TASAM_ENABLED={str(decision.get('tasam_enabled', False)).lower()}\n")
                f.write(f"TASAM_MODE={decision.get('tasam_mode', 'shadow')}\n")
                f.write(f"TASAM_SOURCE={decision.get('tasam_source', '')}\n")
                f.write(f"TASAM_CONFIDENCE={float(decision.get('tasam_confidence', 0) or 0):.4f}\n")
                f.write(f"TASAM_VALID={str(decision.get('tasam_valid', False)).lower()}\n")
                f.write(f"TASAM_ENERGY_DECISION={decision.get('tasam_energy_decision', '')}\n")
                f.write(f"TASAM_ENERGY_ACTION={decision.get('tasam_energy_action', '')}\n")
                f.write(f"ADVISOR_WINNER={(decision.get('advisor_arbitration') or {}).get('winner', 'none')}\n")
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

        armd_info = None
        if decision.get('armd_scenario'):
            armd_info = {
                'enabled': decision.get('armd_enabled', False),
                'mode': decision.get('armd_mode', ''),
                'scenario': decision.get('armd_scenario', ''),
                'domain': decision.get('armd_domain', ''),
                'source': decision.get('armd_source', ''),
                'confidence': decision.get('armd_confidence', 0.0),
                'override_applied': decision.get('armd_override_applied', False),
                'reason': decision.get('armd_reason', ''),
            }
        
        self.a1.send_energy_policy(decision, pattern_info)
        self.a1.send_slice_policy(
            decision['slicer_state'],
            {'active': int(decision['pattern_analysis']['current_cameras'])} if decision.get('pattern_analysis') else None,
            armd_info=armd_info,
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
            action = decision.get('action', '')
            # Quando a hierarquia liberou a economia, a DRL pode escolher o
            # nível de redução. A ML permanece como validação de risco.
            if action == 'POWER_DOWN_ECO' or decision.get('eco_mode'):
                # ECO MODE → POWER_DOWN_ECO (10% potência)
                self.energy_cmd.send_power_down_eco(reason=f"ALLOWED (ECO): {reason}")
            elif action == 'POWER_DOWN':
                self.energy_cmd.send_power_down(reason=f"ALLOWED: {reason}")
            elif action in ['CONDITIONAL_REDUCE', 'REDUCE_POWER']:
                self.energy_cmd.send_conditional_reduce(reason=f"ALLOWED (DRL cautela): {reason}")
            else:
                # Permitido → POWER_DOWN (economizar)
                # Verificar nível de confiança para escolher nível
                confidence = decision.get('confidence', 0)
                if confidence >= 0.8:
                    self.energy_cmd.send_power_down(reason=f"ALLOWED: {reason}")
                else:
                    self.energy_cmd.send_reduce_power(reason=f"ALLOWED (cautela): {reason}")
                
        elif energy_state == 'CONDITIONAL':
            if decision.get('priority_violation') in ['THROUGHPUT_WARNING', 'LATENCY_WARNING', 'APP2_MTC_WARNING', 'VEHICLE_WARNING']:
                # Faixa de guarda prioritária: manter potência para não cruzar o SLA mínimo.
                if decision.get('priority_violation') == 'APP2_MTC_WARNING':
                    guard_prefix = 'APP2_GUARD'
                elif decision.get('priority_violation') == 'VEHICLE_WARNING':
                    guard_prefix = 'VEHICLE_GUARD'
                else:
                    guard_prefix = 'CAMERA_GUARD'
                self.energy_cmd.send_full_power(reason=f"{guard_prefix}: {reason}")
            else:
                # Condicional genérico → economia moderada ativa
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
            drl_prediction = decision.get('drl_prediction', {})
            log_entry = {
                'timestamp': decision.get('timestamp', int(time.time())),
                'datetime': datetime.now().isoformat(),
                'cycle': self.cycle,
                'decision': decision.get('energy_saver', 'UNKNOWN'),
                'action': decision.get('action', 'NONE'),
                'reason': decision.get('reason', ''),
                'confidence': decision.get('confidence', 0),
                'cvar_ms': decision.get('network_health', {}).get('cvar_us', 0) / 1000,
                'network_improvement_pct': decision.get('network_health', {}).get('network_improvement_pct', 0),
                'cvar_improvement_pct': decision.get('network_health', {}).get('cvar_improvement_pct', 0),
                'p95_improvement_pct': decision.get('network_health', {}).get('p95_improvement_pct', 0),
                'baseline_cvar_ms': decision.get('network_health', {}).get('baseline_cvar_us', 0) / 1000,
                'baseline_p95_ms': decision.get('network_health', {}).get('baseline_p95_us', 0) / 1000,
                'improvement_source': decision.get('network_health', {}).get('improvement_source', ''),
                'improvement_valid': decision.get('network_health', {}).get('improvement_valid', False),
                'slope_ms_per_sec': decision.get('trend_analysis', {}).get('slope_ms_per_sec', 0),
                'pattern': decision.get('pattern', 'unknown'),
                'ml_influenced': decision.get('ml_influenced', False),
                'ml_source': ml_prediction.get('source', 'unknown'),
                'ml_confidence': ml_prediction.get('confidence', 0),
                'ml_predicted_cvar_ms': ml_prediction.get('predicted_cvar_ms', 0),
                'drl_decision': drl_prediction.get('final_decision', 'UNKNOWN'),
                'drl_confidence': drl_prediction.get('confidence', 0),
                'drl_predicted_cvar_ms': drl_prediction.get('predicted_cvar_ms', 0),
                'drl_raw_predicted_cvar_ms': drl_prediction.get('raw_predicted_cvar_ms', drl_prediction.get('predicted_cvar_ms', 0)),
                'drl_policy_action': decision.get('drl_policy_action') or drl_prediction.get('policy_action', ''),
                'drl_policy_applied': decision.get('drl_policy_applied', False),
                'drl_calibrated': drl_prediction.get('calibrated', False),
                'drl_calibration_reason': drl_prediction.get('calibration_reason', ''),
                'drl_influenced': decision.get('drl_influenced', False),
                'drl_rejected_reason': decision.get('drl_rejected_reason', ''),
                'ml_risk_cap_applied': decision.get('ml_risk_cap_applied', False),
                'preventive_block': decision.get('preventive_block', False),
                'eco_mode': decision.get('eco_mode', False),
                'slicer_state': decision.get('slicer_state', 'UNKNOWN'),
                'priority_violation': decision.get('priority_violation', ''),
                'vehicle_total': (decision.get('vehicle_metrics') or {}).get('total_vehicles'),
                'vehicle_high_risk': (decision.get('vehicle_metrics') or {}).get('high_risk_vehicles'),
                'vehicle_medium_risk': (decision.get('vehicle_metrics') or {}).get('medium_risk_vehicles'),
                'vehicle_max_latency_ms': (decision.get('vehicle_metrics') or {}).get('max_latency_ms'),
                'app2_connected_ratio': (decision.get('app2_metrics') or {}).get('connected_ratio'),
                'app2_packet_loss_percent': (decision.get('app2_metrics') or {}).get('packet_loss_percent'),
                'app2_delivery_success_percent': (decision.get('app2_metrics') or {}).get('delivery_success_percent'),
                'app2_avg_latency_ms': (decision.get('app2_metrics') or {}).get('avg_latency_ms'),
                'armd_enabled': decision.get('armd_enabled', False),
                'armd_mode': decision.get('armd_mode', ''),
                'armd_scenario': decision.get('armd_scenario', ''),
                'armd_domain': decision.get('armd_domain', ''),
                'armd_source': decision.get('armd_source', ''),
                'armd_confidence': decision.get('armd_confidence', 0),
                'armd_override_applied': decision.get('armd_override_applied', False),
                'armd_expected_energy_saver': decision.get('armd_expected_energy_saver', ''),
                'armd_expected_action': decision.get('armd_expected_action', ''),
                'tasam_enabled': decision.get('tasam_enabled', False),
                'tasam_mode': decision.get('tasam_mode', 'shadow'),
                'tasam_policy_id': decision.get('tasam_policy_id', ''),
                'tasam_source': decision.get('tasam_source', ''),
                'tasam_confidence': decision.get('tasam_confidence', 0),
                'tasam_valid': decision.get('tasam_valid', False),
                'tasam_would_influence': decision.get('tasam_would_influence', False),
                'tasam_energy_decision': decision.get('tasam_energy_decision', ''),
                'tasam_energy_action': decision.get('tasam_energy_action', ''),
                'advisor_arbitration_mode': (decision.get('advisor_arbitration') or {}).get('mode', ''),
                'advisor_arbitration_winner': (decision.get('advisor_arbitration') or {}).get('winner', ''),
                'advisor_arbitration_armd_score': (decision.get('advisor_arbitration') or {}).get('armd_score', 0),
                'advisor_arbitration_tasam_score': (decision.get('advisor_arbitration') or {}).get('tasam_score', 0),
                'rl_policy_id': (decision.get('rl_policy_runtime') or {}).get('policy_id', ''),
                'rl_policy_algorithm': (decision.get('rl_policy_runtime') or {}).get('algorithm', ''),
                'resource_controller_id': (decision.get('resource_allocation') or {}).get('controller_id', ''),
                'resource_budget': (decision.get('resource_allocation') or {}).get('resource_budget'),
                'usable_budget': (decision.get('resource_allocation') or {}).get('usable_budget'),
                'd_ran': (decision.get('resource_allocation') or {}).get('d_ran'),
                'd_ai': (decision.get('resource_allocation') or {}).get('d_ai'),
                'r_ran': (decision.get('resource_allocation') or {}).get('r_ran'),
                'r_ai': (decision.get('resource_allocation') or {}).get('r_ai'),
                'ran_completion_ratio': (decision.get('resource_allocation') or {}).get('ran_completion_ratio'),
                'ai_completion_ratio': (decision.get('resource_allocation') or {}).get('ai_completion_ratio'),
                'utilization_ratio': (decision.get('resource_allocation') or {}).get('utilization_ratio'),
                'marl_shadow_policy_id': (((decision.get('resource_allocation') or {}).get('marl_shadow') or {}).get('policy_id', '')),
                'marl_shadow_available': (((decision.get('resource_allocation') or {}).get('marl_shadow') or {}).get('available', False)),
                'marl_shadow_delta_r_ran': (((decision.get('resource_allocation') or {}).get('marl_shadow') or {}).get('delta_r_ran_vs_live')),
                'marl_shadow_delta_r_ai': (((decision.get('resource_allocation') or {}).get('marl_shadow') or {}).get('delta_r_ai_vs_live'))
            }

            with open(RAPP_DECISIONS_LOG_PATH, 'a') as f:
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
            vehicle_active = xapp_status.get('vehicle_active', False)
            slicer_status = "\033[1;32m[ATIVO]\033[0m" if slicer_active else "\033[1;31m[PARADO]\033[0m"
            energy_status = "\033[1;32m[ATIVO]\033[0m" if energy_active else "\033[1;33m[PARADO]\033[0m"
            vehicle_status = "\033[1;32m[ATIVO]\033[0m" if vehicle_active else "\033[1;31m[PARADO]\033[0m"
            print(f"  xApp SLICER:      {slicer_status} (prioridade - sempre ativo)")
            print(f"  xApp ENERGY:      {energy_status} (controlado pelo rApp)")
            print(f"  xApp VEHICLE:     {vehicle_status} (App3 / segurança veicular)")
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
            print(f"  VEHICLE STATE:    [{decision.get('vehicle_state', 'UNKNOWN')}]")
            
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
            if decision.get('armd_scenario'):
                armd_override = " [override]" if decision.get('armd_override_applied') else ""
                print(
                    f"  ARMD:             {decision.get('armd_scenario')} "
                    f"(src={decision.get('armd_source')}, conf={float(decision.get('armd_confidence', 0) or 0):.2f})"
                    f"{armd_override}"
                )
            
            if decision['agent_override']:
                print(f"\033[1;35m  >>> AGENT-AL OVERRIDE: {decision['agent_policy']}\033[0m")

            print("=" * 70)

    def check_ml_retrain(self):
        """Verifica se é hora de retreinar o ML."""
        if not self.ml_retrain_enabled:
            return

        now = time.time()
        elapsed = now - self.ml_last_retrain

        if elapsed >= self.ml_retrain_interval:
            print(f"\033[1;35m[rApp ML] Retreinamento automático (a cada 12h)...\033[0m")
            self.retrain_ml()
            self.ml_last_retrain = now
            self.ml_retrain_count += 1

    def retrain_ml(self):
        """Retreina o modelo ML com dados recentes do banco."""
        if not self.ml_retrain_enabled or self.ml_predictor is None:
            return

        try:
            import subprocess
            import sys

            command = build_online_retrain_command(
                python_bin=sys.executable,
                state_dir=STATE_DIR,
                models_dir=MODELS_DIR,
            )
            manifest_path = online_retrain_manifest_path(STATE_DIR)
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=600,
                cwd=as_str(PROJECT_ROOT)
            )

            if result.returncode != 0:
                print(f"\033[1;31m[rApp ML] Erro no retreinamento (código {result.returncode}):\033[0m")
                print(f"\033[1;31m[rApp ML] STDERR: {result.stderr[-500:]}\033[0m")
                if result.stdout:
                    print(f"\033[1;31m[rApp ML] STDOUT: {result.stdout[-500:]}\033[0m")
                return

            manifest = load_online_retrain_manifest(manifest_path)
            summary = summarize_online_retrain_manifest(manifest)
            status = summary.get('status', 'missing')
            reasons = summary.get('reasons', [])

            if status == 'promoted':
                print(f"\033[1;32m[rApp ML] Promoção online concluída (#{self.ml_retrain_count + 1})\033[0m")
                self.ml_predictor._load_models()
                accuracy = summary.get('rf_accuracy')
                regressor_r2 = summary.get('regressor_r2')
                classes = summary.get('classes', [])
                feature_profile = summary.get('feature_profile', '')
                accuracy_text = "n/a" if accuracy is None else f"{float(accuracy):.2%}"
                r2_text = "n/a" if regressor_r2 is None else f"{float(regressor_r2):.4f}"
                print(
                    f"\033[1;32m[rApp ML] Modelo ativo: profile={feature_profile}, "
                    f"accuracy={accuracy_text}, r2={r2_text}, classes={classes}\033[0m"
                )
            else:
                print(f"\033[1;33m[rApp ML] Retreinamento online não promoveu modelo: status={status}\033[0m")
                if reasons:
                    print(f"\033[1;33m[rApp ML] Motivos: {' | '.join(str(reason) for reason in reasons)}\033[0m")
                quality_status = summary.get('collection_quality_status')
                quality_reasons = summary.get('collection_quality_reasons', [])
                if quality_status and quality_status != 'ready':
                    print(f"\033[1;33m[rApp ML] Gate da coleta: {quality_status}\033[0m")
                    if quality_reasons:
                        print(f"\033[1;33m[rApp ML] Coleta: {' | '.join(str(reason) for reason in quality_reasons)}\033[0m")

            if result.stdout:
                print(f"\033[1;34m[rApp ML] Log do retreino online: {result.stdout[-500:]}\033[0m")

        except subprocess.TimeoutExpired:
            print(f"\033[1;31m[rApp ML] Timeout no retreinamento online (>10min)\033[0m")
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
        total_safe = total if total > 0 else 1
        
        print("")
        print("=" * 70)
        print("              rApp-ResourceOptimizer - ESTATÍSTICAS FINAIS")
        print("=" * 70)
        print(f"  Ciclos totais: {total}")
        print(f"  Decisões BLOCKED: {self.stats['blocked']} ({self.stats['blocked']/total_safe*100:.1f}%)")
        print(f"  Decisões ALLOWED: {self.stats['allowed']} ({self.stats['allowed']/total_safe*100:.1f}%)")
        print(f"  Decisões CONDITIONAL: {self.stats['conditional']} ({self.stats['conditional']/total_safe*100:.1f}%)")
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
        from greenran_paths import RAPP_DB_PATH, RAPP_POLICIES_DIR
        print(f"  Data Lake: {RAPP_DB_PATH}")
        print(f"  A1 Policies: {RAPP_POLICIES_DIR}/")
        print("=" * 70)
        
        # Timeout para detectar quando dados param (watchdog).
        # Um ciclo sem avanço é comum enquanto o coletor espera novos CSVs; só
        # considerar offline após ciclos consecutivos sem avanço do sim_time.
        SIM_STALE_CYCLES = 3
        
        # Status da conexão (para exibir no dashboard)
        self.data_fresh = True
        self.last_sim_time = 0
        self.stale_sim_cycles = 0
        self.vehicle_only_mode = False
        
        # Para detecção de transição de simulação
        self.last_sim_time_for_reset = None
        
        while self.running:
            self.cycle += 1
            
            # ========================================
            # 0. WATCHDOG: Verificar se dados estão chegando
            # Usa sim_time (tempo de simulação) ao invés de timestamp do sistema
            # Se sim_time não avanza, a simulação parou
            # ========================================
            
            # Buscar sim_time mais recente.
            # Preferimos o DB, mas recuperamos via JSON vivo se o DB perder um ciclo.
            new_sim_time = self._get_latest_sim_time()
            
            # Verificar watchdog usando SIM_TIME do DB (não timestamp do sistema)
            # Comparar sim_time atual com sim_time do ciclo anterior
            if self.last_sim_time > 0 and new_sim_time > 0:
                sim_time_diff = new_sim_time - self.last_sim_time

                # Diff negativo significa que a simulação resetou para um novo run.
                if sim_time_diff < -1.0:
                    if not self.data_fresh:
                        print(f"\n✅  [rApp] Nova simulação detectada! sim_time={new_sim_time}s (reset)")
                        self.data_fresh = True
                    self.vehicle_only_mode = False
                    self.stale_sim_cycles = 0
                    self.ml_invalid_streak = 0
                    if self.ml_predictor is not None:
                        self.ml_predictor.reset_history()
                    if self.drl_predictor is not None and hasattr(self.drl_predictor, 'reset_history'):
                        self.drl_predictor.reset_history()
                    print(f"    → Histórico de runtime resetado")
                else:
                    # Se sim_time não avançou (diff ~= 0), simulação terminou
                    if sim_time_diff <= 0.1:
                        self.stale_sim_cycles += 1
                        if self.data_fresh and self.stale_sim_cycles >= SIM_STALE_CYCLES:
                            print(f"\n⚠️  [rApp] SIMULAÇÃO PARADA! sim_time={new_sim_time}s (último: {self.last_sim_time}s)")
                            print(f"    → Entrando em modo OFFLINE - aguardando novos dados...")
                            self.data_fresh = False
                        if not self.data_fresh:
                            if self._should_keep_vehicle_collection_active():
                                if not self.vehicle_only_mode:
                                    print(
                                        f"\n⚠️  [rApp] SIM_TIME parado em {new_sim_time}s, "
                                        "mas App3 segue ativo; mantendo coleta veicular."
                                    )
                                    self.vehicle_only_mode = True
                            else:
                                self.vehicle_only_mode = False
                                time.sleep(self.interval)
                                self.last_sim_time = new_sim_time
                                continue
                    else:
                        self.stale_sim_cycles = 0
                        self.vehicle_only_mode = False
                        if not self.data_fresh:
                            print(f"\n✅  [rApp] Simulação ativa! sim_time={new_sim_time}s")
                            self.data_fresh = True
            
            # Update last_sim_time
            if new_sim_time > 0:
                self.last_sim_time = new_sim_time
            
            # 1. Lê intenções dos xApps
            slicer_intent = self.read_slicer_intent()
            energy_intent = self.read_energy_intent()
            vehicle_intent = self.read_vehicle_intent()
            
            # 2. Registra métricas no Data Lake
            self.record_current_metrics(slicer_intent, energy_intent)
            
            # 3. Toma decisão estratégica
            decision = self.make_decision(slicer_intent, energy_intent, vehicle_intent)
            
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
            if self.ml_enabled:
                self._record_ml_history(decision)
            
            # 8.6. Exibe histórico de predições ML periodicamente
            if self.ml_enabled and self.cycle % self.history_display_interval == 0 and len(self.ml_history) > 0:
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
        default=int(RUNTIME_CONFIG["orchestrator"]["synthetic_days"]),
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
