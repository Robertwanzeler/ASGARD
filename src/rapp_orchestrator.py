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
import socket
import hashlib
import math
from pathlib import Path
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
    SLICER_SOCKET_PATH,
    ENERGY_SOCKET_PATH,
    TASAM_CONTROL_SOCKET_PATH,
    ensure_runtime_dirs,
    as_str,
    get_fixed_active_cameras,
    get_fixed_total_ues,
)
from greenran_runtime import load_runtime_config, get_xapp_transport_mode

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
from rapp_sac_resource_model import apply_baseline_resource_band, apply_per_ue_allocation, compute_shared_resource_snapshot, enforce_resource_state, enforce_tasam_headroom_envelope, infer_allocation_state
from rapp_marl_shadow import MARLShadowRuntimeEvaluator, build_shadow_comparison
from rapp_network_improvement import build_network_improvement
from rapp_judge import RAppJudge, expected_verdict_for_stage
from rapp_policy_source import RAppPolicySource

# from rapp_synthetic_generator import SyntheticDataGenerator  # Removed - not available
from rapp_xapp_manager import XAppManager
from rapp_trend_analysis import TrendAnalysis
from energy_command_protocol import EnergyCommand
from energy_calibration import (
    load_calibration,
    state_power_w,
    observed_radio_power_w,
    sleep_state_power_by_cell_w,
    sleep_state_power_w,
)
from greenran_control_bundle import (
    ControlBundleClient,
    ControlBundleError,
    SCHEMA as CONTROL_BUNDLE_SCHEMA,
    V3_SCHEMA as CONTROL_BUNDLE_V3_SCHEMA,
    V4_SCHEMA as CONTROL_BUNDLE_V4_SCHEMA,
    failsafe_bundle,
    quantize_power_percent,
)
from tasam_economic_v3 import (
    CONTRACT as ECONOMIC_ACTION_V3_CONTRACT,
    ENERGY_STAIRCASE_CONTRACT,
    DU_CELL_IDS,
    EconomicActionV3Error,
    normalize_power_by_cell,
    project_total_budget_fraction,
    realized_economic_reward,
    staircase_candidate,
)
from tasam_dynamic_floor import (
    DYNAMIC_FLOOR_CONTRACT,
    DynamicFloorError,
    atomic_write_state as atomic_write_dynamic_floor_state,
    dynamic_floor_candidate,
    load_baseline_signature,
    load_dynamic_floor_ledger,
    project_asgard_power,
    project_discretionary_symbol_budget,
    sha256_file as dynamic_floor_sha256,
)
from greenran_infra_budget import CgroupV2Controller, InfraBudgetError, build_physical_budget
from tasam_safety_shield import (
    build_runtime_ue_inputs,
    evaluate_sla_window,
    project_safe_action,
    real_pdcp_window_is_mature,
)
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


def _load_collection_event_context():
    """Capture the event stage used for this decision.

    The alternator is the source of truth for collection labels.  Persisting
    the stage beside the decision avoids reconstructing it later from a
    free-form reason, which can mention a secondary priority (for example a
    vehicle guard during a camera event).
    """
    control = _safe_read_json_file(ARTICLE00_SCENARIO_CONTROL_PATH)
    if not isinstance(control, dict):
        return {}
    stage = str(control.get('collection_event_stage_name') or '').strip()
    if not stage:
        return {}
    try:
        cycle = int(control.get('collection_event_cycle', 0) or 0)
    except (TypeError, ValueError):
        cycle = 0
    try:
        stage_index = int(control.get('collection_event_stage_index', 0) or 0)
    except (TypeError, ValueError):
        stage_index = 0
    try:
        generated_at = int(control.get('generated_at', 0) or 0)
    except (TypeError, ValueError):
        generated_at = 0
    return {
        'collection_event_stage_name': stage,
        'collection_event_target_domain': str(
            control.get('collection_event_target_domain') or ''
        ).strip(),
        'collection_event_cycle': cycle,
        'collection_event_stage_index': stage_index,
        'collection_event_generated_at': generated_at,
        'collection_event_stage_authoritative': True,
        'collection_event_transition_key': f'{cycle}:{stage_index}:{generated_at}',
        'pairing_schedule_id': str(control.get('pairing_schedule_id') or '').strip(),
        'pairing_stage_key': str(control.get('pairing_stage_key') or '').strip(),
    }


def _is_real_pdcp_snapshot(snapshot):
    """Return whether a live snapshot is safe to use as a real observation."""
    if not isinstance(snapshot, dict) or not snapshot:
        return False
    collector_mode = str(snapshot.get('collector_mode', '') or '').strip().lower()
    try:
        proxy_samples = int(snapshot.get('proxy_latency_sample_count', 0) or 0)
    except (TypeError, ValueError):
        proxy_samples = 0
    return (
        collector_mode == 'pdcp_real'
        and proxy_samples == 0
        and not bool(snapshot.get('pdcp_stale', False))
    )


def _is_real_pdcp_metric_row(row):
    """Apply the real-PDCP gate to a row read back from SQLite."""
    if row is None:
        return False
    if hasattr(row, 'keys'):
        payload = {key: row[key] for key in row.keys()}
    else:
        payload = dict(row)
    return _is_real_pdcp_snapshot(payload)


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
        # Isolated stress/A-B runs can provide a decision target.  Enforce it
        # inside the rApp loop as well as in the external watcher: signaling
        # the process from another thread/process has a small race window in
        # which several cycles could otherwise be persisted after the target.
        try:
            self.decision_target = max(0, int(os.environ.get('GREENRAN_DECISION_TARGET', '0') or 0))
        except (TypeError, ValueError):
            self.decision_target = 0
        
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
        self.stats['pdcp_warmup_skips'] = 0
        self.stats['duplicate_metric_skips'] = 0
        self.stats['native_evidence_imported'] = 0
        self.stats['native_evidence_errors'] = 0
        self._last_native_evidence = {}
        
        # Histórico de predições ML para exibição
        self.ml_history = deque(maxlen=50)  # Mantém últimas 50 predições
        self.history_display_interval = 1  # Exibir a cada ciclo (mais frequente)
        self.app2_connectivity_history = deque(maxlen=3)
        self._resource_allocation_prev = {
            'r_ran': 0.5,
            'r_ai': 0.5,
            'allocation_state': 'ALLOWED',
            'healthy_streak': 0,
            'floor_total_ran': 0.0,
            'floor_total_ai': 0.0,
        }
        self._energy_staircase_state = {
            'contract': ENERGY_STAIRCASE_CONTRACT,
            'healthy_streak': 0,
            'healthy_required': int(os.environ.get(
                'GREENRAN_TASAM_ENERGY_STAIRCASE_HEALTHY_REQUIRED', '3'
            ) or 3),
            'rung_by_cell': {},
            'last_confirmed_by_cell': {},
            'last_observation_healthy': False,
            'last_observation_critical': False,
        }
        self._dynamic_floor_config = {}
        self._dynamic_floor_signature = set()
        self._dynamic_floor_state = {}
        self._dynamic_floor_state_path = None
        self._initialize_dynamic_floor()
        self.marl_shadow_evaluator = MARLShadowRuntimeEvaluator(RUNTIME_CONFIG.get('tasam_advisor', {}))
        if self.marl_shadow_evaluator.require_checkpoint and not self.marl_shadow_evaluator.checkpoint_loaded:
            raise RuntimeError(
                'TA-SAM checkpoint required but not loaded: '
                f'{self.marl_shadow_evaluator.checkpoint_error or "unknown checkpoint error"}'
            )
        if self.marl_shadow_evaluator.checkpoint_loaded:
            print(
                '[rApp] TA-SAM checkpoint active: '
                f'{self.marl_shadow_evaluator.checkpoint_run_dir} '
                f'(readiness={self.marl_shadow_evaluator.checkpoint_readiness})'
            )
        self.rapp_policy_source = RAppPolicySource()
        self.rapp_judge = RAppJudge({
            'enabled': True,
            'production': True,
            # Opt-in so the active v7 collection keeps its frozen arbitration
            # contract. Future operational runs can enable the cooperative
            # hierarchy with GREENRAN_ASSISTANT_DECISION_MODE.
            'composition_mode': os.environ.get(
                'GREENRAN_ASSISTANT_DECISION_MODE', 'competitive'
            ),
        })
        # The judge receives delayed real observations on the next cycle so
        # an assistant is penalized for an overreaction in a healthy network.
        # Delayed native confirmations can arrive out of order.  Keep every
        # decision pending until its own native transaction is confirmed or
        # expires; the legacy single-slot alias is retained for shutdown
        # compatibility only.
        self._pending_judge_decisions = []
        self._pending_judge_decision = None
        self._last_real_metric_snapshot_id = None
        self._last_decision_sim_time = None
        self._feedback_drained = True
        
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
        self.data_lake.ensure_initial_energy_observation()
        self._latest_energy_observation = self.data_lake.latest_energy_observation()
        self.tasam_control = ControlBundleClient(socket_path=TASAM_CONTROL_SOCKET_PATH)
        self._tasam_control_sequence = 0
        self.cgroup_controller = (
            CgroupV2Controller()
            if os.environ.get('GREENRAN_CGROUP_ENFORCE', '0').strip().lower()
            in {'1', 'true', 'yes', 'on'} else None
        )
        
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
            print("[rApp] Legacy ML runtime disabled by config; TA-SAM checkpoint path remains active")

        # Runtime RL compatibility hook. The effective live resource decision
        # is produced later by the ARMD envelope + TA-SAM Judge path.
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
                print("[rApp] RL compatibility hook loaded; ARMD + TA-SAM owns effective resource control")
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
        self.xapp_transport_mode = get_xapp_transport_mode(RUNTIME_CONFIG)
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
        self._tasam_actuator_disabled = False
        if self.xapp_transport_mode == 'socket':
            if (
                self._tasam_full_control_enabled()
                or os.environ.get('GREENRAN_TASAM_E2_CONTROL', '0').strip().lower()
                in {'1', 'true', 'yes', 'on'}
            ):
                self._start_tasam_actuator()
            else:
                self._start_slicer()
                self._start_vehicle_control()
        else:
            print('[rApp] xApps em modo file/shadow: sockets não são obrigatórios')

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

    def _start_tasam_actuator(self):
        """Start the sole actuator for every native-E2 control decision."""
        if self._tasam_actuator_disabled:
            return False
        for legacy in ("slicer", "energy_saver", "vehicle_control"):
            if self.xapp_manager.is_running(legacy):
                self.xapp_manager.stop(legacy)
        if not self.xapp_manager.is_available("tasam_actuator"):
            self._tasam_actuator_disabled = True
            print("[rApp] ERRO: atuador E2 exclusivo TA-SAM indisponível")
            return False
        if not self.xapp_manager.start("tasam_actuator"):
            return False
        ready = self.xapp_manager.wait_for_ready("tasam_actuator", timeout=10)
        print("[rApp] atuador E2 TA-SAM exclusivo ativo" if ready else
              "[rApp] AVISO: atuador E2 TA-SAM iniciou sem confirmação de prontidão")
        return True

    def _native_e2_actuation_enabled(self):
        """Whether this arm owns radio control through the dedicated E2 xApp.

        The ENERGY Saver socket and the TA-SAM actuator are two incompatible
        control planes.  Starting the latter and then sending a non-selected
        rApp/safety decision to the former creates an unobservable command
        failure (and used to produce ``Connection refused`` during shadow).
        Keep the decision here rather than duplicating environment parsing at
        every send site.
        """
        return (
            getattr(self, 'xapp_transport_mode', 'socket') == 'socket'
            and os.environ.get('GREENRAN_TASAM_E2_CONTROL', '0').strip().lower()
            in {'1', 'true', 'yes', 'on'}
        )
    
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

        # Native-E2 arms have a single actuator.  rApp-live, ARMD safety and
        # a selected TA-SAM proposal all use that same control plane.  The
        # legacy xApps must stay stopped; their sockets are not a fallback in
        # this mode because they cannot provide the v5 native confirmation.
        if self._native_e2_actuation_enabled():
            for legacy in ("slicer", "energy_saver", "vehicle_control"):
                if self.xapp_manager.is_running(legacy):
                    self.xapp_manager.stop(legacy)
            self._energy_active = False
            if not self.xapp_manager.is_running('tasam_actuator'):
                self._start_tasam_actuator()
            return {
                'slicer_active': False,
                'energy_active': False,
                'vehicle_active': False,
                'tasam_actuator_active': self.xapp_manager.is_running("tasam_actuator"),
                'exclusive_actuator': True,
                'control_plane': 'native_e2',
                'legacy_energy_socket_required': False,
            }

        if self._tasam_full_control_enabled():
            for legacy in ("slicer", "energy_saver", "vehicle_control"):
                if self.xapp_manager.is_running(legacy):
                    self.xapp_manager.stop(legacy)
            if not self.xapp_manager.is_running("tasam_actuator"):
                self._start_tasam_actuator()
            return {
                'slicer_active': False,
                'energy_active': False,
                'vehicle_active': False,
                'tasam_actuator_active': self.xapp_manager.is_running("tasam_actuator"),
                'exclusive_actuator': True,
            }

        if getattr(self, 'xapp_transport_mode', 'socket') == 'file':
            return {
                'slicer_active': False,
                'energy_active': False,
                'vehicle_active': False,
                'transport_mode': 'file',
                'socket_required': False,
            }
        
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
    
    def _check_socket(self, socket_path):
        """Verifica se um Unix Domain Socket está acessível."""
        if not os.path.exists(socket_path):
            return "MISSING"
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(0.1)
                sock.connect(socket_path)
            return "OK"
        except:
            return "UNRESPONSIVE"

    def get_xapp_status(self):
        """Retorna status dos xApps e salva em arquivo."""
        status = {}
        native_e2 = self._native_e2_actuation_enabled()
        
        # Status do SLICER
        slicer_pid = self.xapp_manager.get_pid('slicer')
        slicer_running = self.xapp_manager.is_running('slicer')
        status['SLICER'] = {
            'status': ('NOT_USED_NATIVE_E2' if native_e2 else ('RUNNING' if slicer_running else 'STOPPED')) if getattr(self, 'xapp_transport_mode', 'socket') == 'socket' else 'FILE_ONLY',
            'socket': 'NOT_REQUIRED' if native_e2 else (self._check_socket(as_str(SLICER_SOCKET_PATH)) if getattr(self, 'xapp_transport_mode', 'socket') == 'socket' else 'NOT_REQUIRED'),
            'socket_path': as_str(SLICER_SOCKET_PATH),
            'transport_mode': getattr(self, 'xapp_transport_mode', 'socket'),
            'socket_required': getattr(self, 'xapp_transport_mode', 'socket') == 'socket' and not native_e2,
            'pid': slicer_pid,
            'last_cycle': getattr(self, 'cycle', 0),
            'total_restarts': 0,
            'last_heartbeat': datetime.now().isoformat()
        }
        
        # Status do ENERGY SAVER
        energy_pid = self.xapp_manager.get_pid('energy_saver')
        energy_running = self.xapp_manager.is_running('energy_saver')
        status['ENERGY'] = {
            'status': ('NOT_USED_NATIVE_E2' if native_e2 else ('RUNNING' if energy_running else 'STOPPED')) if getattr(self, 'xapp_transport_mode', 'socket') == 'socket' else 'FILE_ONLY',
            'socket': 'NOT_REQUIRED' if native_e2 else (self._check_socket(as_str(ENERGY_SOCKET_PATH)) if getattr(self, 'xapp_transport_mode', 'socket') == 'socket' else 'NOT_REQUIRED'),
            'socket_path': as_str(ENERGY_SOCKET_PATH),
            'transport_mode': getattr(self, 'xapp_transport_mode', 'socket'),
            'socket_required': getattr(self, 'xapp_transport_mode', 'socket') == 'socket' and not native_e2,
            'pid': energy_pid,
            'last_cycle': getattr(self, 'cycle', 0),
            'total_restarts': 0,
            'last_heartbeat': datetime.now().isoformat()
        }

        vehicle_pid = self.xapp_manager.get_pid('vehicle_control')
        vehicle_running = self.xapp_manager.is_running('vehicle_control')
        status['VEHICLE'] = {
            'status': 'NOT_USED_NATIVE_E2' if native_e2 else ('RUNNING' if vehicle_running else 'STOPPED'),
            'pid': vehicle_pid,
            'last_cycle': getattr(self, 'cycle', 0),
            'total_restarts': 0,
            'last_heartbeat': datetime.now().isoformat()
        }
        actuator_pid = self.xapp_manager.get_pid('tasam_actuator')
        actuator_running = self.xapp_manager.is_running('tasam_actuator')
        status['TASAM_ACTUATOR'] = {
            'status': ('RUNNING' if actuator_running else 'STOPPED') if native_e2 else 'NOT_REQUIRED',
            'pid': actuator_pid,
            'control_plane': 'native_e2' if native_e2 else 'legacy',
            'required': native_e2,
            'last_cycle': getattr(self, 'cycle', 0),
            'last_heartbeat': datetime.now().isoformat(),
        }
        
        # Salvar em arquivo para dashboard
        try:
            with open(XAPP_HEALTH_FILE, 'w') as f:
                json.dump(status, f, indent=2)
        except Exception as e:
            pass
        
        return status
    
    def read_slicer_intent(self):
        """Lê intenção do SLICER via Socket com fallback para arquivo."""
        # Socket só faz parte do contrato de integração.
        try:
            if getattr(self, 'xapp_transport_mode', 'socket') != 'socket':
                raise FileNotFoundError('file/shadow mode')
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(0.1) # 100ms
            with sock:
                sock.connect(as_str(SLICER_SOCKET_PATH))
                data = sock.recv(1024)
            return json.loads(data.decode('utf-8'))
        except Exception as e:
            # Fallback para leitura de arquivo
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
            except Exception as e2:
                print(f"[rApp] ERRO ao ler SLICER (Socket e Fallback): {e}, {e2}")
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

        strict_pdcp = os.environ.get('GREENRAN_REQUIRE_REAL_PDCP', '0') == '1'
        override = _load_app1_camera_override()
        if override and not strict_pdcp:
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
        if camera_mode == "simulated" and not strict_pdcp:
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
            strict_pdcp = os.environ.get('GREENRAN_REQUIRE_REAL_PDCP', '0') == '1'
            override = _load_app2_sensor_override()
            if override and not strict_pdcp:
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
        strict_pdcp = os.environ.get('GREENRAN_REQUIRE_REAL_PDCP', '0') == '1'
        app3_is_real = (
            isinstance(app3_snapshot, dict)
            and app3_snapshot.get('schema') == 'greenran.app3.pdcp_real_snapshot.v1'
            and app3_snapshot.get('pdcp_provenance') == 'pdcp_real'
            and bool(app3_snapshot.get('valid'))
        )
        if isinstance(app3_snapshot, dict) and app3_snapshot and (not strict_pdcp or app3_is_real):
            self.data_lake.record_app3_snapshot(snapshot=app3_snapshot)

        metric_snapshot_id = None
        extended_metrics = self.read_extended_metrics()
        if extended_metrics:
            metric_snapshot_id = self.data_lake.record_extended_from_json(
                extended_metrics,
                energy_state=energy_state,
                slicer_state=slicer_state
            )
        return metric_snapshot_id

    def _real_metric_snapshot_id_is_valid(self, metric_snapshot_id):
        """Verify the exact just-recorded SQLite snapshot is real PDCP."""
        try:
            cursor = self.data_lake.conn.cursor()
            cursor.execute(
                'SELECT * FROM extended_metrics WHERE id = ?',
                (int(metric_snapshot_id),),
            )
            return _is_real_pdcp_metric_row(cursor.fetchone())
        except Exception:
            return False
    
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

    @staticmethod
    def _tasam_full_control_enabled() -> bool:
        mode = os.environ.get('GREENRAN_TASAM_ADVISOR_MODE', '').strip().lower()
        return mode in {'tasam_full_control', 'tasam-full-control'}

    @staticmethod
    def _assistant_judge_enabled() -> bool:
        mode = os.environ.get('GREENRAN_ASSISTANT_DECISION_MODE', '').strip().lower()
        tasam_mode = os.environ.get('GREENRAN_TASAM_ADVISOR_MODE', '').strip().lower()
        return mode in {
            'assistant_judge', 'judge', 'production', 'cooperative_hierarchy',
            'hierarchical', 'cooperative',
        } or tasam_mode in {'assistant_only_control', 'tasam_full_control', 'tasam-full-control'}

    @staticmethod
    def _online_rollout_fraction(decision) -> float:
        """Read the online promotion fraction without restarting the rApp."""
        if RappResourceOptimizer._tasam_full_control_enabled():
            return 1.0
        default = os.environ.get('GREENRAN_TASAM_ONLINE_ROLLOUT_FRACTION', '1.0')
        try:
            fraction = float(default)
        except (TypeError, ValueError):
            fraction = 1.0
        manifest_path = os.environ.get('GREENRAN_TASAM_ONLINE_ROLLOUT_MANIFEST', '').strip()
        if manifest_path:
            try:
                payload = json.loads(open(manifest_path, encoding='utf-8').read())
                rollout = payload.get('rollout') if isinstance(payload, dict) else {}
                if isinstance(rollout, dict) and rollout.get('fraction') is not None:
                    fraction = float(rollout.get('fraction'))
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                pass
        return min(max(fraction, 0.0), 1.0)

    @classmethod
    def _online_rollout_allows(cls, decision, fraction: float) -> bool:
        if cls._tasam_full_control_enabled():
            return True
        if fraction >= 1.0:
            return True
        if fraction <= 0.0:
            return False
        # The bounded native-actuation smoke must demonstrate one real,
        # material application.  Keep its declared rollout at 10%, but use a
        # deterministic probe schedule so a short smoke cannot fail merely
        # because a random hash placed every sample outside the canary.  This
        # switch is set only by ``combined_actuation_smoke`` and is never
        # enabled by the online adaptation or causal arms.
        if os.environ.get('GREENRAN_TASAM_ACTUATION_SMOKE_PROBE') == '1':
            probe_count = int(getattr(cls, '_actuation_smoke_probe_count', 0))
            setattr(cls, '_actuation_smoke_probe_count', probe_count + 1)
            return probe_count % 10 == 0
        identity = f"{decision.get('timestamp', '')}:{decision.get('cycle', '')}:{decision.get('collection_event_cycle', '')}"
        bucket = int(hashlib.sha256(identity.encode('utf-8')).hexdigest()[:8], 16) / 0xFFFFFFFF
        return bucket < fraction

    @staticmethod
    def _economic_safety_isolation(decision: dict) -> tuple[bool, str]:
        """Decide whether the economic actor must be isolated before rollout."""
        allocation = decision.get('resource_allocation') or {}
        armd = decision.get('armd_analysis') or {}
        reasons: list[str] = []
        safety_level = str(
            decision.get('armd_safety_level')
            or armd.get('safety_level')
            or ''
        ).upper()
        if safety_level == 'HARD_VETO':
            reasons.append('armd_hard_veto')
        if allocation.get('failsafe_required'):
            reasons.append('resource_failsafe')
        if allocation.get('floor_feasible') is False or allocation.get('per_ue_floor_feasible') is False:
            reasons.append('sla_floor_infeasible')
        if allocation.get('floor_verified') is False and safety_level == 'HARD_VETO':
            reasons.append('sla_floor_unverified')
        shield = decision.get('tasam_safety_shield') or {}
        if shield.get('failsafe') and safety_level == 'HARD_VETO':
            reasons.append('safety_shield_failsafe')
        return bool(reasons), ';'.join(dict.fromkeys(reasons))

    @staticmethod
    def _classify_armd_safety_level(decision: dict, advice: dict) -> str:
        """Return ARMD authority, independent of the rApp energy category."""
        advice = advice or {}
        explicit = str(advice.get('safety_level', '') or '').upper()
        if explicit in {'CLEAR', 'ADVISORY', 'HARD_VETO', 'UNKNOWN'}:
            return explicit
        scenario = str(advice.get('scenario', '') or '').lower()
        violation = str(
            advice.get('priority_violation')
            or decision.get('priority_violation')
            or ''
        ).upper()
        if advice.get('critical_violation') or scenario in {
            'app1_throughput', 'app1_latencia', 'app2_degradado_critico',
            'vehicle_critical',
        } or violation in {
            'THROUGHPUT', 'LATENCY', 'APP2_MTC_CRITICAL',
            'VEHICLE_CRITICAL', 'CVAR_CRITICAL', 'P95_CRITICAL',
        }:
            return 'HARD_VETO'
        verdict = str(advice.get('expected_energy_saver', '') or '').upper()
        if verdict == 'ALLOWED':
            return 'CLEAR'
        if verdict == 'CONDITIONAL':
            return 'ADVISORY'
        if advice.get('available') and advice.get('proposal_valid'):
            return 'ADVISORY'
        return 'UNKNOWN'

    @staticmethod
    def _economic_action_contract_enabled() -> bool:
        """Whether this decision must use the applied-action economic contract."""
        return os.environ.get(
            'GREENRAN_TASAM_ECONOMIC_ACTION_CONTRACT', ''
        ).strip().lower() in {'applied_action_v2', ECONOMIC_ACTION_V3_CONTRACT}

    @staticmethod
    def _energy_staircase_enabled() -> bool:
        return os.environ.get(
            'GREENRAN_TASAM_ENERGY_STAIRCASE_CONTRACT', ''
        ).strip() == ENERGY_STAIRCASE_CONTRACT

    @staticmethod
    def _dynamic_floor_enabled() -> bool:
        return os.environ.get(
            'GREENRAN_TASAM_DYNAMIC_FLOOR_CONTRACT', ''
        ).strip() == DYNAMIC_FLOOR_CONTRACT

    def _initialize_dynamic_floor(self) -> None:
        """Load the immutable ledger/signature and create resumable state."""
        if not self._dynamic_floor_enabled():
            return
        ledger_path = Path(os.environ.get(
            'GREENRAN_TASAM_DYNAMIC_FLOOR_LEDGER', ''
        ).strip())
        signature_path = Path(os.environ.get(
            'GREENRAN_TASAM_BASELINE_SIGNATURE', ''
        ).strip())
        state_path = Path(os.environ.get(
            'GREENRAN_TASAM_DYNAMIC_FLOOR_STATE',
            str(Path(STATE_DIR) / 'dynamic_floor_state.json'),
        ))
        if not ledger_path.is_file() or not signature_path.is_file():
            raise DynamicFloorError(
                'dynamic floor requires ledger v2 and r26 baseline signature'
            )
        try:
            expected_seed = int(os.environ.get(
                'GREENRAN_TASAM_TRUE_ONLINE_SEED', '43'
            ) or 43)
        except (TypeError, ValueError) as exc:
            raise DynamicFloorError('dynamic floor seed is invalid') from exc
        expected_profile = str(os.environ.get(
            'GREENRAN_COLLECTION_EVENT_PROFILE', ''
        ) or '')
        ledger = load_dynamic_floor_ledger(
            ledger_path,
            expected_seed=expected_seed,
            expected_profile=expected_profile or None,
            baseline_signature_path=signature_path,
        )
        signature = load_baseline_signature(
            signature_path,
            expected_seed=expected_seed,
            expected_profile=expected_profile or None,
            strict_contract=True,
        )
        state = {}
        if state_path.is_file():
            try:
                state = json.loads(state_path.read_text(encoding='utf-8'))
            except (OSError, json.JSONDecodeError) as exc:
                raise DynamicFloorError('dynamic-floor state is unreadable') from exc
            if not isinstance(state, dict):
                raise DynamicFloorError('dynamic-floor state must be an object')
            if state.get('contract') != DYNAMIC_FLOOR_CONTRACT:
                raise DynamicFloorError('dynamic-floor state contract mismatch')
            if state.get('ledger_sha256') != ledger['ledger_sha256']:
                raise DynamicFloorError('dynamic-floor state ledger mismatch')
            if state.get('baseline_signature_sha256') != dynamic_floor_sha256(signature_path):
                raise DynamicFloorError('dynamic-floor state baseline mismatch')
            if int(state.get('seed', -1)) != expected_seed:
                raise DynamicFloorError('dynamic-floor state seed mismatch')
            if str(state.get('profile') or '') != expected_profile:
                raise DynamicFloorError('dynamic-floor state profile mismatch')
        if not state:
            state = {
                'contract': DYNAMIC_FLOOR_CONTRACT,
                'seed': expected_seed,
                'profile': expected_profile,
                'ledger_sha256': ledger['ledger_sha256'],
                'baseline_signature_sha256': dynamic_floor_sha256(signature_path),
                'floor_percent_by_cell': dict(ledger['initial_floor_percent_by_cell']),
                'minimum_floor_percent_by_cell': dict(ledger['minimum_floor_percent_by_cell']),
                'previous_validated_floor_percent_by_cell': dict(
                    ledger['previous_validated_floor_percent_by_cell']
                ),
                'descend_streak': 0,
                'stable_streak': 0,
                'retreats': 0,
                'descent_enabled': True,
                'projection_count': 0,
                'applied_projection_count': 0,
                'actor_influenced_decisions': 0,
                'resource_influenced_decisions': 0,
                'sleep_influenced_decisions': 0,
                'native_authority_events': [],
                'observed_windows': 0,
                'window_sequence_id': 0,
                'processed_window_ids': [],
                'seen_attributable_violation_keys': [],
                'sleep_transition': {},
                'resource_budget_projection_count': 0,
            }
        self._dynamic_floor_config = ledger
        self._dynamic_floor_signature = signature
        self._dynamic_floor_state = state
        self._dynamic_floor_state_path = state_path
        self._persist_dynamic_floor_state()

    def _persist_dynamic_floor_state(self) -> None:
        if self._dynamic_floor_state_path is None:
            return
        atomic_write_dynamic_floor_state(
            self._dynamic_floor_state_path, self._dynamic_floor_state
        )

    def _apply_adaptive_resource_budget(
        self,
        decision: dict,
        allocation: dict,
        power_by_cell: dict[int, int],
    ) -> dict[int, int]:
        """Turn the learned global allocation action into a native DL cap.

        A missing/malformed economic head never invents a cut: the legacy
        unconstrained value is retained and recorded.  A valid ASGARD action
        is represented by an explicit per-DU cap in the v4 control bundle.
        """
        active_cells = tuple(cell for cell in DU_CELL_IDS if power_by_cell.get(cell, 100) != 0)
        if not self._dynamic_floor_enabled():
            return {cell: 10_000 for cell in active_cells}
        advisor = decision.get('tasam_advisor') or {}
        energy = advisor.get('energy_advice') or {}
        proposal = decision.get('tasam_proposal') or {}
        proposed_allocation = proposal.get('resource_allocation') or {}
        total = (
            energy.get('total_budget_fraction')
            if energy.get('total_budget_fraction') is not None else
            proposed_allocation.get('total_budget_fraction', allocation.get('total_budget_fraction'))
        )
        ran = (
            energy.get('ran_share')
            if energy.get('ran_share') is not None else
            proposed_allocation.get('ran_share', allocation.get('ran_share'))
        )
        try:
            caps, evidence = project_discretionary_symbol_budget(
                total, ran, active_cells=active_cells,
            )
        except DynamicFloorError as exc:
            decision['adaptive_resource_budget'] = {
                'valid': False,
                'reason': str(exc),
                'applied_discretionary_dl_symbols_bp_by_cell': {
                    str(cell): 10_000 for cell in active_cells
                },
            }
            return {cell: 10_000 for cell in active_cells}
        evidence.update({
            'valid': True,
            'applied_discretionary_dl_symbols_bp_by_cell': {
                str(cell): int(value) for cell, value in caps.items()
            },
        })
        decision['adaptive_resource_budget'] = evidence
        state = dict(self._dynamic_floor_state)
        state['resource_budget_projection_count'] = int(
            state.get('resource_budget_projection_count', 0) or 0
        ) + 1
        state['last_resource_budget_projection'] = dict(evidence)
        self._dynamic_floor_state = state
        self._persist_dynamic_floor_state()
        return caps

    def _adaptive_sleep_transition(
        self,
        decision: dict,
        power_by_cell: dict[int, int],
        association_cells: dict[int, set[int]],
        *,
        sla_valid: bool,
        pdcp_mature: bool,
        sim_time_s: float,
    ) -> tuple[dict[int, int], dict | None, bool]:
        """Advance one fail-closed drain/commit/wake sleep transaction."""
        if not self._dynamic_floor_enabled():
            return power_by_cell, None, False
        projection = decision.get('dynamic_floor_projection') or {}
        requested = projection.get('requested_power_percent_by_cell') or {}
        requested_sleep = []
        for cell in DU_CELL_IDS:
            raw_power = requested.get(str(cell), requested.get(cell, 100))
            try:
                requested_power = int(100 if raw_power is None else raw_power)
            except (TypeError, ValueError):
                requested_power = 100
            if requested_power == 0:
                requested_sleep.append(cell)
        state = dict(self._dynamic_floor_state)
        transition_state = dict(state.get('sleep_transition') or {})
        phase = str(transition_state.get('phase') or '').lower()
        source = int(transition_state.get('source_cell_id', 0) or 0)

        def persist(next_state: dict) -> None:
            state['sleep_transition'] = next_state
            self._dynamic_floor_state = state
            self._persist_dynamic_floor_state()

        if phase == 'drain':
            source_ues = {int(item) for item in transition_state.get('source_imsis', [])}
            current_source = set(association_cells.get(source, set()))
            all_present = set().union(*(association_cells.get(cell, set()) for cell in DU_CELL_IDS)) == set(range(1, 21))
            if not sla_valid or not pdcp_mature:
                persist({
                    **transition_state, 'phase': 'wake', 'reason': 'drain_sla_or_pdcp_invalid',
                })
                decision['adaptive_sleep'] = dict(state['sleep_transition'])
                return {cell: 100 for cell in DU_CELL_IDS}, {
                    'phase': 'wake',
                    'sleep_transaction_id': transition_state.get('sleep_transaction_id'),
                    'source_cell_id': source,
                }, True
            if current_source:
                # Handover still converging: source remains active, targets
                # temporarily get the full safe envelope.
                draining = {
                    cell: (max(25, int((self._dynamic_floor_state.get('floor_percent_by_cell') or {}).get(str(cell), 25)))
                           if cell == source else 100)
                    for cell in DU_CELL_IDS
                }
                payload = {
                    'phase': 'drain',
                    'sleep_transaction_id': transition_state['sleep_transaction_id'],
                    'source_cell_id': source,
                    'handover_plan': list(transition_state.get('handover_plan') or []),
                }
                decision['adaptive_sleep'] = {**transition_state, 'native_source_empty': False}
                return draining, payload, False
            # The drain only enters its 10-second health window after the
            # source snapshot is explicitly empty and all IMSIs remain
            # observable on the remaining multi-connectivity legs.
            if not all_present:
                persist({
                    **transition_state, 'phase': 'wake', 'reason': 'drain_empty_source_association_invalid',
                })
                decision['adaptive_sleep'] = dict(state['sleep_transition'])
                return {cell: 100 for cell in DU_CELL_IDS}, {
                    'phase': 'wake',
                    'sleep_transaction_id': transition_state.get('sleep_transaction_id'),
                    'source_cell_id': source,
                }, True
            empty_since = float(transition_state.get('source_empty_since_s', 0.0) or 0.0)
            if empty_since <= 0.0:
                transition_state['source_empty_since_s'] = float(sim_time_s)
                persist(transition_state)
                empty_since = float(sim_time_s)
            if float(sim_time_s) - empty_since < 10.0:
                draining = {cell: (25 if cell == source else 100) for cell in DU_CELL_IDS}
                payload = {
                    'phase': 'drain', 'sleep_transaction_id': transition_state['sleep_transaction_id'],
                    'source_cell_id': source, 'handover_plan': list(transition_state.get('handover_plan') or []),
                }
                decision['adaptive_sleep'] = {**transition_state, 'native_source_empty': True}
                return draining, payload, False
            committed = {cell: (0 if cell == source else int(power_by_cell.get(cell, 100))) for cell in DU_CELL_IDS}
            transition_state.update({
                'phase': 'commit', 'committed_at_s': float(sim_time_s),
                'handover_confirmed': True, 'association_valid': True,
                'pdcp_window_valid': True,
                'pdcp_window_s': float(sim_time_s) - empty_since,
            })
            persist(transition_state)
            payload = {
                'phase': 'commit', 'sleep_transaction_id': transition_state['sleep_transaction_id'],
                'source_cell_id': source, 'handover_plan': list(transition_state.get('handover_plan') or []),
                'handover_confirmed': True, 'association_valid': True,
                'pdcp_window_valid': True, 'pdcp_window_s': float(sim_time_s) - empty_since,
            }
            decision['adaptive_sleep'] = dict(transition_state)
            return committed, payload, False

        if phase == 'commit':
            current_source = set(association_cells.get(source, set()))
            all_present = set().union(
                *(association_cells.get(cell, set()) for cell in DU_CELL_IDS)
            ) == set(range(1, 21))
            if current_source or not all_present or not sla_valid or not pdcp_mature:
                persist({
                    **transition_state, 'phase': 'wake',
                    'reason': 'sleep_commit_health_or_association_invalid',
                })
                decision['adaptive_sleep'] = dict(state['sleep_transition'])
                return {cell: 100 for cell in DU_CELL_IDS}, {
                    'phase': 'wake',
                    'sleep_transaction_id': transition_state.get('sleep_transaction_id'),
                    'source_cell_id': source,
                }, True
            committed = {
                cell: (0 if cell == source else int(power_by_cell.get(cell, 100)))
                for cell in DU_CELL_IDS
            }
            payload = {
                'phase': 'commit',
                'sleep_transaction_id': transition_state['sleep_transaction_id'],
                'source_cell_id': source,
                'handover_plan': list(transition_state.get('handover_plan') or []),
                'handover_confirmed': True, 'association_valid': True,
                'pdcp_window_valid': True,
                'pdcp_window_s': max(10.0, float(transition_state.get('pdcp_window_s', 10.0) or 10.0)),
            }
            decision['adaptive_sleep'] = dict(transition_state)
            return committed, payload, False

        if phase == 'wake':
            if requested_sleep:
                decision['adaptive_sleep_rejected'] = 'sleep_wake_pending_actor_request'
                return {cell: 100 for cell in DU_CELL_IDS}, {
                    'phase': 'wake',
                    'sleep_transaction_id': transition_state.get('sleep_transaction_id'),
                    'source_cell_id': source,
                }, True
            persist({})
            return power_by_cell, None, False

        if not requested_sleep:
            return power_by_cell, None, False
        if len(requested_sleep) != 1:
            decision['adaptive_sleep_rejected'] = 'multiple_sleep_requests'
            return {cell: 100 for cell in DU_CELL_IDS}, None, True
        source = requested_sleep[0]
        source_ues = set(association_cells.get(source, set()))
        if not source_ues:
            decision['adaptive_sleep_rejected'] = 'source_association_unavailable'
            return power_by_cell, None, False
        plan = []
        target_load = {cell: len(association_cells.get(cell, set())) for cell in DU_CELL_IDS if cell != source}
        for imsi in sorted(source_ues):
            candidates = [cell for cell in target_load if imsi in association_cells.get(cell, set())]
            if not candidates:
                decision['adaptive_sleep_rejected'] = f'imsi_{imsi}_has_no_mc_destination'
                return power_by_cell, None, False
            target = min(candidates, key=lambda cell: (target_load[cell], cell))
            target_load[target] += 1
            plan.append({'imsi': imsi, 'target_cell_id': target})
        sleep_id = f"sleep:{decision.get('tasam_control_sequence', 0)}:{source}:{int(sim_time_s * 1000)}"
        next_state = {
            'phase': 'drain', 'sleep_transaction_id': sleep_id,
            'source_cell_id': source, 'source_imsis': sorted(source_ues),
            'handover_plan': plan, 'requested_at_s': float(sim_time_s),
        }
        persist(next_state)
        draining = {cell: (max(25, int(power_by_cell.get(cell, 25))) if cell == source else 100)
                    for cell in DU_CELL_IDS}
        payload = {
            'phase': 'drain', 'sleep_transaction_id': sleep_id,
            'source_cell_id': source, 'handover_plan': plan,
        }
        decision['adaptive_sleep'] = dict(next_state)
        return draining, payload, False

    def _apply_dynamic_floor(
        self,
        decision: dict,
        requested_power_by_cell: dict[int, int],
    ) -> tuple[dict[int, int], bool]:
        """Project ASGARD's active proposal onto the current safety floor."""
        if not self._dynamic_floor_enabled():
            return requested_power_by_cell, False
        selected_assistant = str(decision.get('selected_assistant') or '').lower()
        if selected_assistant not in {'ta_sam', 'joint'}:
            decision['dynamic_floor_applied'] = False
            decision['dynamic_floor_reason'] = 'non_asgard_action'
            return requested_power_by_cell, False
        try:
            selected, evidence = project_asgard_power(
                requested_power_by_cell,
                self._dynamic_floor_state.get('floor_percent_by_cell') or {},
                isolated=False,
            )
        except DynamicFloorError as exc:
            decision['dynamic_floor_applied'] = False
            decision['dynamic_floor_reason'] = str(exc)
            decision['tasam_v3_action_error'] = str(exc)
            return {cell: 100 for cell in DU_CELL_IDS}, True
        decision['dynamic_floor_projected'] = True
        decision['dynamic_floor_applied'] = False
        decision['dynamic_floor_contract'] = DYNAMIC_FLOOR_CONTRACT
        decision['dynamic_floor_projection'] = evidence
        sleep_candidates = [
            cell for cell, power in requested_power_by_cell.items()
            if int(power) == 0
        ]
        decision['dynamic_sleep_requested_cells'] = sorted(sleep_candidates)
        decision['dynamic_floor_state_before'] = dict(self._dynamic_floor_state)
        decision['dynamic_floor_reason'] = 'asgard_proposal_clamped_to_dynamic_floor'
        return selected, False

    def _record_dynamic_floor_application(
        self,
        decision: dict,
        bundle: dict,
        *,
        accepted: bool,
        failsafe: bool,
    ) -> None:
        if not self._dynamic_floor_enabled() or not decision.get('dynamic_floor_projected'):
            return
        projection = dict(decision.get('dynamic_floor_projection') or {})
        applied_by_cell = {
            str(cell.get('cell_id')): int(cell.get('tx_power_percent', 100))
            for cell in bundle.get('cells') or []
            if isinstance(cell, dict) and cell.get('cell_id') is not None
        }
        dynamically_applied = bool(accepted and not failsafe and bundle.get('mode') != 'failsafe')
        requested_by_cell = projection.get('requested_power_percent_by_cell') or {}
        effective_actor_cells = [
            int(cell) for cell in projection.get('actor_influenced_cells') or []
            if applied_by_cell.get(str(cell)) == requested_by_cell.get(str(cell))
        ] if dynamically_applied else []
        resource_budget = dict(decision.get('adaptive_resource_budget') or {})
        requested_caps = resource_budget.get(
            'applied_discretionary_dl_symbols_bp_by_cell'
        ) or {}
        effective_resource_cells = []
        if dynamically_applied:
            for cell in bundle.get('cells') or []:
                if not isinstance(cell, dict):
                    continue
                cell_id = str(cell.get('cell_id'))
                raw_cap = cell.get('max_discretionary_dl_symbols_bp')
                try:
                    applied_cap = 10_000 if raw_cap is None else int(raw_cap)
                    requested_cap = int(requested_caps[cell_id])
                except (KeyError, TypeError, ValueError):
                    continue
                if applied_cap < 10_000 and applied_cap == requested_cap:
                    effective_resource_cells.append(int(cell_id))
        sleep_transition = dict(bundle.get('sleep_transition') or {})
        sleep_source = int(sleep_transition.get('source_cell_id', 0) or 0)
        sleep_influenced = bool(
            dynamically_applied
            and str(sleep_transition.get('phase') or '').lower() == 'commit'
            and sleep_source in DU_CELL_IDS
            and applied_by_cell.get(str(sleep_source)) == 0
        )
        actor_influenced = bool(
            effective_actor_cells or effective_resource_cells or sleep_influenced
        )
        decision['dynamic_floor_applied'] = dynamically_applied
        decision['dynamic_floor_overridden_by_isolation'] = bool(failsafe)
        decision['dynamic_floor_applied_power_percent_by_cell'] = applied_by_cell
        decision['dynamic_floor_actor_influenced_cells'] = effective_actor_cells
        decision['dynamic_floor_resource_influenced_cells'] = effective_resource_cells
        decision['dynamic_floor_sleep_influenced'] = sleep_influenced
        decision['dynamic_floor_actor_influenced'] = actor_influenced
        state = dict(self._dynamic_floor_state)
        state['projection_count'] = int(state.get('projection_count', 0) or 0) + 1
        if dynamically_applied:
            state['applied_projection_count'] = int(
                state.get('applied_projection_count', 0) or 0
            ) + 1
            if actor_influenced:
                state['actor_influenced_decisions'] = int(
                    state.get('actor_influenced_decisions', 0) or 0
                ) + 1
            if effective_resource_cells:
                state['resource_influenced_decisions'] = int(
                    state.get('resource_influenced_decisions', 0) or 0
                ) + 1
            if sleep_influenced:
                state['sleep_influenced_decisions'] = int(
                    state.get('sleep_influenced_decisions', 0) or 0
                ) + 1
            if actor_influenced:
                events = list(state.get('native_authority_events') or [])
                events.append({
                    'native_control_sequence': int(
                        decision.get('tasam_control_sequence', 0) or 0
                    ),
                    'power_cells': effective_actor_cells,
                    'resource_cells': effective_resource_cells,
                    'sleep_source_cell': sleep_source if sleep_influenced else 0,
                    'sleep_transaction_id': str(
                        sleep_transition.get('sleep_transaction_id') or ''
                    ),
                    'applied_power_percent_by_cell': applied_by_cell,
                })
                state['native_authority_events'] = events[-128:]
        state['last_projection'] = {
            'snapshot_sequence_id': decision.get('snapshot_sequence_id'),
            'power_transaction_id': decision.get('tasam_control_sequence'),
            'requested_power_percent_by_cell': projection.get(
                'requested_power_percent_by_cell', {}
            ),
            'floor_percent_by_cell': projection.get('floor_percent_by_cell', {}),
            'selected_power_percent_by_cell': projection.get(
                'selected_power_percent_by_cell', {}
            ),
            'applied_power_percent_by_cell': applied_by_cell,
            'actor_proposal_influenced_cells': list(
                projection.get('actor_influenced_cells') or []
            ),
            'actor_influenced_cells': effective_actor_cells,
            'actor_influenced': actor_influenced,
            'resource_influenced_cells': effective_resource_cells,
            'sleep_influenced': sleep_influenced,
            'sleep_transaction_id': str(sleep_transition.get('sleep_transaction_id') or ''),
            'accepted': bool(accepted),
            'safety_isolated': bool(failsafe),
            'isolation_reason': str(
                decision.get('economic_safety_isolation_reason')
                or bundle.get('reason') or ''
            ),
        }
        self._dynamic_floor_state = state
        self._persist_dynamic_floor_state()

    def _advance_dynamic_floor_from_observation(
        self,
        previous: dict,
        current_decision: dict,
        *,
        economic_valid: bool,
        actuation_confirmed: bool,
    ) -> None:
        """Bind the next real PDCP window to the preceding E2 transaction."""
        if not self._dynamic_floor_enabled() or not previous.get('dynamic_floor_applied'):
            return
        snapshot = self.data_lake.sla_violation_keys(
            current_decision.get('metric_snapshot_id')
        )
        violations = {
            (int(window), int(imsi), str(reason))
            for window, imsi, reason in snapshot.get('violation_keys', [])
        }
        prior_association = (previous.get('tasam_association_evidence') or {}).get('mapping') or {}
        affected_cells = {
            int(prior_association.get(str(imsi), prior_association.get(imsi)))
            for _window, imsi, _reason in violations
            if prior_association.get(str(imsi), prior_association.get(imsi)) is not None
        }
        healthy = bool(
            snapshot.get('valid')
            and not violations
            and economic_valid
            and actuation_confirmed
        )
        prior_window_sequence = int(
            self._dynamic_floor_state.get('window_sequence_id', 0) or 0
        )
        floor, state = dynamic_floor_candidate(
            self._dynamic_floor_state,
            current_violations=violations,
            baseline_signature=self._dynamic_floor_signature,
            start_percent=self._dynamic_floor_config['initial_floor_percent_by_cell'],
            minimum_percent=self._dynamic_floor_config['minimum_floor_percent_by_cell'],
            window_id=(
                current_decision.get('snapshot_sequence_id')
                or current_decision.get('metric_snapshot_id')
            ),
            power_transaction_id=(
                (previous.get('economic_action') or {}).get('native_control_sequence')
                or previous.get('tasam_control_sequence')
            ),
            window_healthy=healthy,
            isolation_reason=str(
                current_decision.get('economic_safety_isolation_reason') or ''
            ),
            affected_cells=affected_cells or DU_CELL_IDS,
        )
        if int(state.get('window_sequence_id', 0) or 0) == prior_window_sequence:
            current_decision['dynamic_floor_state'] = dict(state)
            return
        state['observed_windows'] = int(state.get('window_sequence_id', 0) or 0)
        observation = {
            'window_sequence_id': state.get('window_sequence_id'),
            'snapshot_sequence_id': current_decision.get('snapshot_sequence_id'),
            'metric_snapshot_id': current_decision.get('metric_snapshot_id'),
            'window_s': snapshot.get('window_s'),
            'power_transaction_id': state.get('last_power_transaction_id'),
            'healthy': healthy,
            'sla_evidence_valid': bool(snapshot.get('valid')),
            'violation_keys': sorted(violations),
            'attributable_violation_keys': state.get('attributable_last_window', []),
            'new_attributable_violation_keys': state.get(
                'new_attributable_last_window', []
            ),
            'affected_cells': state.get('affected_cells_last_window', []),
            'floor_percent_by_cell': {str(k): int(v) for k, v in floor.items()},
            'safety_isolated': bool(current_decision.get('economic_safety_isolated')),
            'isolation_reason': str(
                current_decision.get('economic_safety_isolation_reason') or ''
            ),
        }
        state['last_observation'] = observation
        previous['dynamic_floor_observation'] = observation
        current_decision['dynamic_floor_state'] = dict(state)
        self._dynamic_floor_state = state
        self._persist_dynamic_floor_state()

    @staticmethod
    def _safe_power_floor_by_cell(decision: dict, allocation: dict, contract: dict) -> dict | None:
        """Read only an explicit native/checkpoint safe power-floor ledger.

        The staircase must never infer RF safety from the actor request or
        from scheduler shares.  Campaigns that do not expose this ledger are
        therefore rejected into the existing full-power safety path.
        """
        proposal = decision.get('tasam_proposal') or {}
        advisor = decision.get('tasam_advisor') or {}
        ledger_candidate = None
        ledger_path = os.environ.get('GREENRAN_TASAM_SAFE_POWER_FLOOR_LEDGER', '').strip()
        if ledger_path:
            try:
                with open(ledger_path, encoding='utf-8') as handle:
                    ledger = json.load(handle)
                if (
                    isinstance(ledger, dict)
                    and ledger.get('status') == 'validated'
                    and ledger.get('schema') == 'greenran.tasam.v2x.safe_power_floor.v1'
                ):
                    ledger_candidate = ledger.get('safe_floor_percent_by_cell')
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                ledger_candidate = None
        candidates = (
            ledger_candidate,
            decision.get('safe_power_floor_percent_by_cell'),
            decision.get('native_safe_power_floor_percent_by_cell'),
            allocation.get('safe_power_floor_percent_by_cell'),
            allocation.get('native_safe_power_floor_percent_by_cell'),
            proposal.get('safe_power_floor_percent_by_cell'),
            advisor.get('safe_power_floor_percent_by_cell'),
            (contract.get('live_candidate') or {}).get('safe_power_floor_percent_by_cell'),
        )
        for candidate in candidates:
            if isinstance(candidate, dict) and all(
                str(cell) in candidate or cell in candidate for cell in DU_CELL_IDS
            ):
                return candidate
        return None

    def _apply_energy_staircase(
        self,
        decision: dict,
        allocation: dict,
        contract: dict,
        requested_power_by_cell: dict[int, int],
    ) -> tuple[dict[int, int], bool]:
        """Project only TA-SAM's economic action onto the safe ladder."""
        if self._dynamic_floor_enabled():
            return self._apply_dynamic_floor(decision, requested_power_by_cell)
        if not self._energy_staircase_enabled():
            return requested_power_by_cell, False
        selected_assistant = str(decision.get('selected_assistant') or '').lower()
        if selected_assistant not in {'ta_sam', 'joint'}:
            decision['energy_staircase_applied'] = False
            decision['energy_staircase_reason'] = 'rapp_reference_is_unrestricted'
            return requested_power_by_cell, False
        floor = self._safe_power_floor_by_cell(decision, allocation, contract)
        if floor is None:
            decision['energy_staircase_applied'] = False
            decision['energy_staircase_reason'] = 'safe_power_floor_telemetry_missing'
            decision['tasam_v3_action_error'] = 'energy_staircase_requires_native_safe_power_floor'
            return {cell: 100 for cell in DU_CELL_IDS}, True
        state = dict(self._energy_staircase_state)
        try:
            selected, next_state = staircase_candidate(
                floor,
                requested_power_by_cell,
                allow_sleep=os.environ.get('GREENRAN_TASAM_ALLOW_DU_SLEEP', '0') == '1',
                state=state,
                healthy=bool(state.get('last_observation_healthy')),
                healthy_required=int(state.get('healthy_required', 3) or 3),
                critical=bool(state.get('last_observation_critical')),
            )
        except EconomicActionV3Error as exc:
            decision['energy_staircase_applied'] = False
            decision['energy_staircase_reason'] = str(exc)
            decision['tasam_v3_action_error'] = str(exc)
            return {cell: 100 for cell in DU_CELL_IDS}, True
        self._energy_staircase_state = next_state
        self._energy_staircase_state['last_observation_healthy'] = False
        self._energy_staircase_state['last_observation_critical'] = False
        decision['energy_staircase_applied'] = True
        decision['energy_staircase_contract'] = ENERGY_STAIRCASE_CONTRACT
        decision['energy_staircase_state'] = dict(next_state)
        decision['energy_staircase_safe_floor_percent_by_cell'] = {
            str(cell): float(floor.get(cell, floor.get(str(cell))))
            for cell in DU_CELL_IDS
        }
        decision['energy_staircase_requested_power_percent_by_cell'] = {
            str(cell): int(requested_power_by_cell[cell]) for cell in DU_CELL_IDS
        }
        decision['energy_staircase_selected_power_percent_by_cell'] = {
            str(cell): int(selected[cell]) for cell in DU_CELL_IDS
        }
        return selected, False

    @staticmethod
    def _is_economic_contract(contract: dict | None) -> bool:
        return str((contract or {}).get('contract') or '') in {
            'applied_action_v2', ECONOMIC_ACTION_V3_CONTRACT,
        }

    @staticmethod
    def _economic_contract_name() -> str:
        return (
            ECONOMIC_ACTION_V3_CONTRACT
            if os.environ.get('GREENRAN_TASAM_ECONOMIC_ACTION_CONTRACT', '').strip().lower()
            == ECONOMIC_ACTION_V3_CONTRACT
            else 'applied_action_v2'
        )

    @staticmethod
    def _economic_power_candidate(decision: dict) -> float:
        """Return the rApp-only power candidate for this exact snapshot.

        This deliberately does not inspect TA-SAM or ARMD output.  It is the
        counterfactual candidate used to score the intervention made on the
        same rApp snapshot, not a previous observed command.
        """
        state = str(decision.get('energy_saver', 'UNKNOWN') or 'UNKNOWN').upper()
        action = str(decision.get('action', '') or '').upper()
        if state == 'BLOCKED' or action in {'PREVENTIVE_BLOCK', 'FULL_POWER'}:
            return 100.0
        if state == 'CONDITIONAL' or action in {'POWER_DOWN', 'CONDITIONAL_REDUCE', 'REDUCE_POWER'}:
            return 60.0
        if state == 'ALLOWED' and action == 'POWER_DOWN_ECO':
            return 25.0
        return 100.0

    @staticmethod
    def _economic_du_actions(resource_allocation: dict) -> list[list[float]]:
        """Serialize the actual three-DU allocation action for SAC replay."""
        article = (resource_allocation or {}).get('article_marl_state') or {}
        du_states = article.get('du_states') or []
        actions: list[list[float]] = []
        for row in du_states:
            mix = (row or {}).get('slice_mix') or {}
            values = [
                max(0.0, float(mix.get(slice_id, 0.0) or 0.0))
                for slice_id in ('eMBB', 'mMTC', 'URLLC')
            ]
            total = sum(values)
            if total <= 1e-9:
                return []
            actions.append([round(value / total, 8) for value in values])
        return actions if len(actions) == 3 else []

    def _economic_action_snapshot(
        self,
        resource_allocation: dict,
        power_percent: float | None,
        *,
        ru_count: object = None,
        mmwave_count: object = None,
        power_percent_by_cell: dict | None = None,
    ) -> dict:
        """Create a bounded, auditable economic action snapshot."""
        allocation = resource_allocation or {}
        usable = float(allocation.get('usable_budget', allocation.get('resource_budget', 0.0)) or 0.0)
        ran = max(0.0, float(allocation.get('r_ran', 0.0) or 0.0))
        ai = max(0.0, float(allocation.get('r_ai', 0.0) or 0.0))
        try:
            power = float(power_percent)
        except (TypeError, ValueError):
            power = -1.0
        try:
            ru = int(ru_count)
            mmwave = int(mmwave_count)
        except (TypeError, ValueError):
            ru = mmwave = -1
        snapshot = {
            'power_percent': round(power, 6),
            'ran_allocation': round(ran, 8),
            'ai_allocation': round(ai, 8),
            'total_allocation': round(ran + ai, 8),
            'usable_budget': round(usable, 8),
            'du_actions': self._economic_du_actions(allocation),
            'ru_count': ru,
            'mmwave_count': mmwave,
        }
        if power_percent_by_cell:
            try:
                snapshot['power_percent_by_cell'] = {
                    str(cell): int(value) for cell, value in normalize_power_by_cell(power_percent_by_cell).items()
                }
            except (EconomicActionV3Error, TypeError, ValueError):
                snapshot['power_percent_by_cell'] = None
        return snapshot

    @staticmethod
    def _canonical_energy_command_power(value: object) -> float:
        """Return the level the energy actuator can actually apply."""
        try:
            requested = float(value)
        except (TypeError, ValueError):
            requested = 100.0
        return float(min((25.0, 60.0, 100.0), key=lambda level: abs(level - requested)))

    def _begin_economic_action_contract(self, decision: dict) -> None:
        """Capture the side-effect-free rApp candidate before assistants act."""
        if not self._economic_action_contract_enabled():
            return
        allocation = dict(decision.get('resource_allocation') or {})
        observation = allocation.get('live_energy_observation') or self.data_lake.latest_energy_observation()
        candidate_power = self._economic_power_candidate(decision)
        candidate = self._economic_action_snapshot(
            allocation,
            candidate_power,
            ru_count=observation.get('ru_count'),
            mmwave_count=observation.get('mmwave_count'),
            power_percent_by_cell=(
                decision.get('rapp_live_power_percent_by_cell')
                or ({cell: candidate_power for cell in DU_CELL_IDS}
                    if self._economic_contract_name() == ECONOMIC_ACTION_V3_CONTRACT else None)
            ),
        )
        try:
            calibration = load_calibration()
            if candidate['ru_count'] < 0 or candidate['mmwave_count'] < 0:
                raise ValueError('missing live radio counts')
            candidate['power_w'] = round(
                state_power_w(
                    calibration,
                    candidate['ru_count'],
                    candidate['mmwave_count'],
                    candidate_power,
                ),
                8,
            )
            # The v2 calibration contract names this field explicitly. Keep
            # older aliases only as compatibility fallbacks.
            energy_model_version = str(
                calibration.get('calibration_version')
                or calibration.get('version')
                or calibration.get('calibration_id')
                or ''
            )
            candidate['energy_model_version'] = energy_model_version
            decision['energy_model_version'] = energy_model_version
        except Exception as exc:
            # Failing closed here only invalidates the economic transition;
            # it never disables ARMD's safety intervention.
            candidate['power_w'] = None
            candidate['energy_model_error'] = str(exc)
        decision['economic_action_contract'] = self._economic_contract_name()
        decision['economic_execution_mode'] = 'diagnostic'
        decision['economic_safety_isolated'] = False
        decision['economic_safety_isolation_reason'] = ''
        decision['economic_isolation_source'] = ''
        decision['armd_safety_level'] = 'UNKNOWN'
        decision['armd_role'] = 'advisory'
        decision['armd_advisory_only'] = True
        decision['armd_hard_veto'] = False
        decision['tasam_operating_permission'] = False
        decision['economic_training_eligible'] = False
        decision['economic_promotion_eligible'] = False
        decision['economic_application_status'] = 'pending'
        decision['economic_rejection_reason'] = ''
        decision['economic_transition_eligible'] = False
        decision['economic_action'] = {
            'contract': self._economic_contract_name(),
            'correlation_id': '',
            'live_candidate': candidate,
            'proposed': {},
            'projected': {},
            'applied': {},
            'application_status': 'pending',
            'command_sent': False,
            'actuation_confirmed': False,
            'actuation_confirmation_source': '',
            'observed_power_percent': None,
            'observed_power_w': None,
            'observed_ru_count': None,
            'observed_mmwave_count': None,
            'confirmation_decision_id': None,
            'native_control_sequence': None,
            'rejection_reason': '',
            'safety_override': False,
            'economic_execution_mode': 'diagnostic',
            'economic_safety_isolated': False,
            'economic_safety_isolation_reason': '',
            'economic_isolation_source': '',
            'armd_safety_level': 'UNKNOWN',
            'armd_role': 'advisory',
            'armd_advisory_only': True,
            'armd_hard_veto': False,
            'tasam_operating_permission': False,
            'economic_training_eligible': False,
            'economic_promotion_eligible': False,
            'energy_model_version': candidate.get('energy_model_version', ''),
        }

    def _update_economic_application(
        self,
        decision: dict,
        *,
        status: str,
        reason: str = '',
        projected_power: float | None = None,
    ) -> None:
        """Keep flat decision columns and nested v2 contract in sync."""
        contract = decision.get('economic_action')
        if not isinstance(contract, dict) or not self._is_economic_contract(contract):
            return
        self._sync_economic_contract_provenance(decision, contract)
        if projected_power is not None:
            observation = (decision.get('resource_allocation') or {}).get('live_energy_observation') or {}
            contract['projected'] = self._economic_action_snapshot(
                decision.get('resource_allocation') or {},
                self._canonical_energy_command_power(projected_power),
                ru_count=observation.get('ru_count'),
                mmwave_count=observation.get('mmwave_count'),
            )
            contract['projected']['energy_model_version'] = str(
                contract.get('energy_model_version')
                or (contract.get('live_candidate') or {}).get('energy_model_version')
                or decision.get('energy_model_version', '')
                or ''
            )
        contract['application_status'] = status
        contract['rejection_reason'] = reason
        decision['economic_application_status'] = status
        decision['economic_rejection_reason'] = reason

    @staticmethod
    def _sync_economic_contract_provenance(decision: dict, contract: dict | None = None) -> dict:
        """Copy final ARMD/permission provenance into the v2 action contract.

        The contract is created before ARMD and the Judge finish their
        decision.  Keeping these fields synchronized at every actuator and
        feedback boundary prevents a real TA-SAM action from being persisted
        as ``UNKNOWN``/diagnostic while its flat decision fields say CLEAR or
        ADVISORY.
        """
        contract = contract if isinstance(contract, dict) else decision.get('economic_action')
        if not isinstance(contract, dict) or not RappResourceOptimizer._is_economic_contract(contract):
            return contract or {}
        for field in (
            'armd_safety_level', 'armd_role', 'armd_advisory_only', 'armd_hard_veto',
            'tasam_operating_permission', 'economic_execution_mode',
            'economic_safety_isolated', 'economic_safety_isolation_reason',
            'economic_isolation_source',
        ):
            if field in decision:
                contract[field] = decision.get(field)
        return contract

    def _write_native_control_context(self, decision: dict, contract: dict) -> bool:
        """Publish the decision identity before the ns-3 control is sent.

        The E2 callback runs independently from the Python orchestrator and
        the native trace is emitted later on the ns-3 thread.  This small
        sidecar is the bridge for that asynchronous boundary.  It contains
        identifiers only; it is never treated as proof that a command was
        applied.  The v5 importer still requires the subsequent PHY and
        active-policy observations.
        """
        context_path = str(
            os.environ.get('GREENRAN_NS3_NATIVE_CONTROL_CONTEXT_PATH', '') or ''
        ).strip()
        if not context_path:
            return False
        path = Path(context_path)
        if os.environ.get('GREENRAN_LOCAL_ONLY', '0').strip().lower() in {
            '1', 'true', 'yes', 'on'
        }:
            state_dir = Path(os.environ.get('GREENRAN_STATE_DIR', '') or '').resolve()
            try:
                path.resolve().relative_to(state_dir)
            except (OSError, ValueError):
                decision['native_control_context_error'] = 'context_path_outside_local_state_dir'
                return False
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            needs_header = not path.exists() or path.stat().st_size == 0
            sequence = int(
                contract.get('native_control_sequence')
                or decision.get('tasam_control_sequence')
                or 0
            )
            if sequence <= 0:
                decision['native_control_context_error'] = 'native_control_sequence_missing'
                return False
            decision_id = int(
                decision.get('decision_id')
                or self.data_lake.peek_next_decision_id()
                or 0
            )
            correlation = str(contract.get('correlation_id') or '').strip()
            campaign = str(os.environ.get('GREENRAN_CAMPAIGN_ID', '') or '').strip()
            generation = str(
                os.environ.get('GREENRAN_NATIVE_SOURCE_GENERATION', '') or ''
            ).strip()
            sleep_transaction_id = str(
                ((contract.get('sleep_transition') or {}).get('sleep_transaction_id'))
                or ((decision.get('adaptive_sleep') or {}).get('sleep_transaction_id'))
                or ''
            ).strip()
            sim_time = decision.get('sim_time_s')
            if sim_time is None:
                sim_time = ((decision.get('resource_allocation') or {}).get('sim_time_s'))
            with path.open('a', encoding='utf-8') as handle:
                if needs_header:
                    handle.write(
                        'NativeControlSequence,DecisionId,ActionCorrelationId,'
                        'CampaignId,SourceGeneration,SimTime,SleepTransactionId\n'
                    )
                handle.write(
                    f'{sequence},{decision_id},{correlation},{campaign},{generation},'
                    f'{"" if sim_time is None else sim_time},{sleep_transaction_id}\n'
                )
                handle.flush()
                os.fsync(handle.fileno())
            decision['native_control_context'] = {
                'path': str(path),
                'native_control_sequence': sequence,
                'decision_id': decision_id,
                'action_correlation_id': correlation,
                'campaign_id': campaign,
                'source_generation': generation,
                'sleep_transaction_id': sleep_transaction_id,
            }
            return True
        except (OSError, TypeError, ValueError) as exc:
            decision['native_control_context_error'] = (
                f'{type(exc).__name__}: {exc}'
            )[:512]
            return False

    def _build_armd_proposal(self, decision, advice, resource_snapshot):
        """Build ARMD's complete proposal without using the live allocator."""
        cfg = RUNTIME_CONFIG.get('shared_resources', {}) or {}
        advice = advice or {}
        snapshot = resource_snapshot or {}
        usable_budget = float(snapshot.get('usable_budget', snapshot.get('resource_budget', 1.0)) or 1.0)
        resource_budget = float(snapshot.get('resource_budget', usable_budget) or usable_budget)
        ran_demand = float(snapshot.get('d_ran', 0.0) or 0.0)
        ai_demand = float(snapshot.get('d_ai', 0.0) or 0.0)
        domain = str(advice.get('domain', '') or '').lower()
        scenario = str(advice.get('scenario', '') or '')
        # ARMD uses app1/app3 vocabulary while the network policy uses
        # camera/vehicle. Normalize once so safety and allocation priorities
        # cannot diverge.
        priority_domain = domain
        if domain in {'camera', 'app1', 'video'} or 'camera' in scenario or 'app1' in scenario:
            priority_domain = 'camera'
        elif domain in {'vehicle', 'app3'} or 'vehicle' in scenario:
            priority_domain = 'vehicle'
        elif domain in {'app2', 'sensor', 'sensor_critical'}:
            priority_domain = 'sensor'
        else:
            priority_domain = 'global'
        if priority_domain in {'camera', 'vehicle'}:
            ran_share = 0.75 if priority_domain == 'camera' else 0.70
        elif priority_domain == 'sensor':
            ran_share = 0.55
        else:
            ran_share = 0.50
        ran_min = float(cfg.get('ran_min_share', 0.35) or 0.35)
        ai_min = float(cfg.get('ai_min_share', 0.15) or 0.15)
        ran_share = max(ran_min, min(1.0 - ai_min, ran_share))
        allocation = dict(snapshot)
        allocation.update({
            'resource_budget': resource_budget,
            'usable_budget': usable_budget,
            'd_ran': ran_demand,
            'd_ai': ai_demand,
            'r_ran': round(usable_budget * ran_share, 6),
            'r_ai': round(usable_budget * (1.0 - ran_share), 6),
            'controller_id': 'armd_greenran_assistant',
            'source': 'armd_proposal',
            'proposal_priority': priority_domain,
        })
        allocation['ran_completion_ratio'] = min(1.0, allocation['r_ran'] / ran_demand) if ran_demand > 0 else 1.0
        allocation['ai_completion_ratio'] = min(1.0, allocation['r_ai'] / ai_demand) if ai_demand > 0 else 1.0
        allocation['utilization_ratio'] = min(1.0, (allocation['r_ran'] + allocation['r_ai']) / usable_budget) if usable_budget > 0 else 0.0
        allocation = enforce_resource_state(allocation)
        verdict = str(advice.get('expected_energy_saver') or decision.get('energy_saver') or 'CONDITIONAL').upper()
        action = str(advice.get('expected_action') or decision.get('action') or 'FULL_POWER_GUARD')
        priority_violation = str(
            advice.get('priority_violation')
            or decision.get('priority_violation', '')
            or ''
        ).upper()
        hard_priority_violation = priority_violation in {
            'THROUGHPUT', 'LATENCY', 'APP2_MTC_CRITICAL',
            'VEHICLE_CRITICAL', 'CVAR_CRITICAL', 'P95_CRITICAL',
        }
        guarded_priority_violation = priority_violation in {
            'THROUGHPUT_WARNING', 'LATENCY_WARNING', 'THROUGHPUT_WARMUP',
            'VEHICLE_WARNING',
        }
        # A warning keeps the service in a protected energy state, but it is
        # not an absolute resource veto while the hard SLA is still met. This
        # lets TA-SAM optimize the shared budget inside the ARMD envelope.
        # Only a confirmed critical violation may create an absolute veto.
        # Predictive warnings and proactive guards remain proposals that the
        # rApp judge can compare with TA-SAM and later score against reality.
        explicit_critical_veto = bool(
            advice.get('critical_violation', False)
            or (advice.get('safety_veto', False) and hard_priority_violation)
        )
        if hard_priority_violation or explicit_critical_veto:
            priority_score = 1.0 if priority_domain in {'camera', 'vehicle'} else 0.75 if priority_domain == 'sensor' else 0.50
        elif guarded_priority_violation:
            priority_score = 0.25
        else:
            priority_score = 0.50
        if priority_domain in {'camera', 'vehicle'} and hard_priority_violation:
            # The safety advocate must carry the hard decision into its
            # complete proposal; otherwise selecting ARMD would still apply a
            # weaker CONDITIONAL state and the veto would be cosmetic.
            verdict = 'BLOCKED'
            action = 'FULL_POWER'
        elif priority_domain in {'camera', 'vehicle'} and guarded_priority_violation:
            verdict = 'CONDITIONAL'
            action = 'FULL_POWER_GUARD'
        # A camera/vehicle guard is an absolute safety envelope. TA-SAM may
        # propose the allocation, but cannot relax this live protection.
        safety_veto = bool(
            hard_priority_violation
            or explicit_critical_veto
            or (
                priority_domain in {'camera', 'vehicle'}
                and hard_priority_violation
            )
        )
        # ARMD and TA-SAM must share the same per-UE minimum. A safety
        # proposal may request reinforcement only for a genuinely
        # BLOCKED/critical state; ALLOWED and CONDITIONAL proposals are
        # deliberately reduced to the common SLA floor so the safety advisor
        # cannot consume extra energy merely by being conservative.
        safety_level = self._classify_armd_safety_level(decision, advice)
        safety_veto = safety_level == 'HARD_VETO'
        # The energy category can be BLOCKED for a non-safety rApp/ML reason.
        # ARMD's resource authority follows the explicit safety level instead
        # of inheriting that category.
        allocation_state = 'BLOCKED' if safety_veto else verdict
        if allocation_state == 'BLOCKED' and not safety_veto:
            allocation_state = 'CONDITIONAL'
        allocation_state = (
            allocation_state
            if allocation_state in {'ALLOWED', 'CONDITIONAL', 'BLOCKED'}
            else 'CONDITIONAL'
        )
        allocation['allocation_state'] = allocation_state
        if allocation_state in {'ALLOWED', 'CONDITIONAL'}:
            allocation['r_ran'] = float(allocation.get('floor_total_ran', 0.0) or 0.0)
            allocation['r_ai'] = float(allocation.get('floor_total_ai', 0.0) or 0.0)
            allocation['reinforcement_scale'] = 0.0
            allocation['reinforcement_scale_state'] = allocation_state
            allocation['reinforcement_ran'] = 0.0
            allocation['reinforcement_ai'] = 0.0
        allocation = enforce_resource_state(allocation)
        allocation['armd_resource_mode'] = (
            'floor_only'
            if allocation_state in {'ALLOWED', 'CONDITIONAL'}
            else 'critical_reinforcement'
        )
        return {
            'proposal_id': f"armd:{decision.get('timestamp', int(time.time()))}:{self.cycle}",
            'source': 'armd',
            'available': bool(advice.get('proposal_present', advice.get('available', False))),
            'valid': bool(advice.get('proposal_valid', False)),
            'feasible': True,
            'verdict': verdict,
            'action': action,
            'expected_energy_saver': verdict,
            'expected_action': action,
            'confidence': float(advice.get('confidence', 0.0) or 0.0),
            'proposal_score': round((0.55 * priority_score) + (0.45 * float(advice.get('confidence', 0.0) or 0.0)), 6),
            'priority_score': priority_score,
            'resource_score': priority_score,
            'resource_allocation': allocation,
            'resource_advice': dict(allocation),
            'resource_mode': allocation.get('armd_resource_mode', 'floor_only'),
            'sla_protection': {
                'camera_throughput_mbps': float(cfg.get('camera_throughput_target_mbps', 25.0) or 25.0),
                'camera_latency_ms': float(cfg.get('camera_latency_target_ms', 100.0) or 100.0),
                'vehicle_latency_ms': float(cfg.get('vehicle_latency_target_ms', 20.0) or 20.0),
                'priority_domain': priority_domain,
            },
            'priority_constraints': cfg,
            'scenario': scenario or 'greenran_global_noop',
            'proposal_kind': str(advice.get('proposal_kind', '') or ''),
            'reason': str(advice.get('reason', '') or 'ARMD proposal'),
            'evidence': advice.get('evidence', []) or [],
            'safety_veto': safety_veto,
            'critical_violation': safety_veto,
            'armd_safety_level': safety_level,
            'armd_role': 'safety_enforcer' if safety_veto else 'advisory',
            'armd_advisory_only': not safety_veto,
            'priority_violation': priority_violation,
            'proactive_sla_guard': bool(advice.get('proactive_sla_guard', False)),
        }

    @staticmethod
    def _safe_float(value, default=0.0):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _build_proactive_sla_guard_advice(self, decision, armd_advice, tasam_proposal):
        """Turn a risky TA-SAM RAN reduction into a contextual ARMD proposal.

        TA-SAM is allowed to optimize the shared RAN/AI budget, but the
        assistant contract cannot let a resource reduction silently remove
        the safety margin of active camera or autonomous-vehicle services.
        The rApp remains the final judge; this method only makes the ARMD
        safety proposal explicit so the judge can select it deterministically.
        """
        base = dict(armd_advice or {})
        tasam = tasam_proposal if isinstance(tasam_proposal, dict) else {}
        if not tasam.get('valid') or not tasam.get('feasible'):
            return base

        # A global CVaR regression is a valid assistant-level safety signal,
        # but it must be relative to the previous real window.  The historic
        # 484.7 ms comparison baseline is for reporting only and must not be
        # used as a live guard reference.  ARMD intervenes after two
        # consecutive relative regressions, or immediately at the configured
        # absolute critical limit. The paired campaign accepts at most a 5%
        # tail increase, so the live guard uses the same strict margin.
        network = decision.get('network_health') or {}
        current_cvar_us = self._safe_float(network.get('cvar_us'), 0.0)
        previous_cvar_us = self._safe_float(getattr(self, '_prev_cvar_us', 0.0), 0.0)
        reference_cvar_us = self._safe_float(network.get('reference_cvar_us'), 0.0) or previous_cvar_us
        cfg = RUNTIME_CONFIG.get('shared_resources', {}) or {}
        cvar_regression_pct = self._safe_float(cfg.get('cvar_guard_relative_regression_pct'), 5.0)
        cvar_required_windows = max(1, int(self._safe_float(cfg.get('cvar_guard_required_windows'), 2.0)))
        cvar_regression = False
        cvar_ratio = 0.0
        cvar_streak = int(getattr(self, '_cvar_risk_streak', 0) or 0)
        if current_cvar_us > 0.0 and reference_cvar_us > 0.0:
            cvar_ratio = current_cvar_us / reference_cvar_us
            cvar_regression = current_cvar_us > reference_cvar_us * (1.0 + cvar_regression_pct / 100.0)
        if cvar_regression:
            cvar_streak += 1
        else:
            cvar_streak = 0
        self._cvar_risk_streak = cvar_streak
        cvar_target_ms = self._safe_float((RUNTIME_CONFIG.get('shared_resources', {}) or {}).get('cvar_target_ms'), 120.0)
        cvar_hard_risk = (
            current_cvar_us >= max(1.0, cvar_target_ms) * 1000.0
            or cvar_streak >= cvar_required_windows
        )

        resource_advice = tasam.get('resource_advice') or {}
        delta_ran = self._safe_float(resource_advice.get('delta_r_ran_vs_live'), 0.0)

        def floor_proposal(reason):
            """Make ARMD's healthy-cycle floor contract explicit.

            A healthy cycle is not a missing ARMD decision: the safety
            assistant still recommends the common per-UE SLA floor.  Keeping
            this as a proposal (instead of mutating the live decision) lets
            the rApp judge compare it with TA-SAM while guaranteeing that an
            ARMD selection cannot request extra resources in a normal state.
            """
            verdict = str(
                base.get('expected_energy_saver')
                or decision.get('energy_saver')
                or 'ALLOWED'
            ).upper()
            if verdict not in {'ALLOWED', 'CONDITIONAL', 'BLOCKED'}:
                verdict = 'ALLOWED'
            if verdict == 'BLOCKED':
                # A genuinely blocked state is handled by the critical
                # reinforcement path in _build_armd_proposal.  The healthy
                # floor conversion only applies to non-critical cycles.
                return base
            base.update({
                'available': True,
                'proposal_present': True,
                'proposal_valid': True,
                'proposal_kind': 'resource_floor',
                'scenario': 'greenran_floor_minimum',
                'domain': 'global',
                'expected_energy_saver': verdict,
                'expected_action': (
                    'REDUCE_POWER' if verdict == 'ALLOWED' else 'FULL_POWER_GUARD'
                ),
                'reason': reason,
                'source': 'runtime_floor',
                'safety_veto': False,
                'critical_violation': False,
                'proactive_sla_guard': False,
                'priority_violation': str(base.get('priority_violation', '') or ''),
                'evidence': list(base.get('evidence') or []) + [
                    'armd_floor_only',
                    'shared_per_ue_minimum',
                ],
            })
            return base

        # A CVaR guard is independent of the proposed RAN delta: the model can
        # keep the same nominal allocation and still move the tail into a
        # worse contention stage. Skip the contextual guard only when both
        # allocation and tail risk are healthy.
        if delta_ran >= -0.01 and not cvar_hard_risk:
            if str(base.get('scenario', '') or '') == 'greenran_global_noop':
                return floor_proposal(
                    'ARMD floor proposal: estado sem risco crítico; aplicar somente o mínimo por UE'
                )
            return base

        camera = decision.get('camera_metrics') or {}
        vehicle = decision.get('vehicle_metrics') or {}
        active_cameras = self._safe_float(camera.get('active_cameras'), 0.0)
        vehicles_available = bool(vehicle.get('available'))
        active_vehicles = self._safe_float(vehicle.get('total_vehicles'), 0.0)
        camera_active = active_cameras > 0.0
        vehicle_active = vehicles_available and active_vehicles > 0.0
        if not camera_active and not vehicle_active and not cvar_hard_risk:
            if str(base.get('scenario', '') or '') == 'greenran_global_noop':
                return floor_proposal(
                    'ARMD floor proposal: nenhum serviço prioritário em risco; aplicar somente o mínimo por UE'
                )
            return base

        camera_target = self._safe_float(cfg.get('camera_throughput_target_mbps'), 25.0)
        camera_guard = self._safe_float(cfg.get('camera_throughput_guard_mbps'), 30.0)
        camera_latency_warning = self._safe_float(cfg.get('camera_latency_warning_ms'), 80.0)
        camera_latency_target = self._safe_float(cfg.get('camera_latency_target_ms'), 100.0)
        vehicle_latency_warning = self._safe_float(cfg.get('vehicle_latency_warning_ms'), 10.0)
        vehicle_latency_target = self._safe_float(cfg.get('vehicle_latency_target_ms'), 20.0)
        vehicle_loss_warning = self._safe_float(cfg.get('vehicle_loss_warning_pct'), 0.5)
        vehicle_loss_target = self._safe_float(cfg.get('vehicle_loss_target_pct'), 1.0)

        camera_throughput = self._safe_float(camera.get('throughput_mbps'), 0.0)
        camera_latency = self._safe_float(camera.get('latency_ms'), 0.0)
        vehicle_latency = self._safe_float(vehicle.get('max_latency_ms'), 0.0)
        vehicle_loss = self._safe_float(vehicle.get('max_packet_loss_percent'), 0.0)
        camera_hard_risk = camera_active and (
            not camera.get('throughput_ready', False)
            or camera_throughput < camera_target
            or camera_latency >= camera_latency_target
        )
        vehicle_hard_risk = vehicle_active and (
            vehicle_latency >= vehicle_latency_target
            or vehicle_loss >= vehicle_loss_target
            or self._safe_float(vehicle.get('high_risk_vehicles'), 0.0) > 0.0
            or self._safe_float(vehicle.get('degraded_autonomy_vehicles'), 0.0) > 0.0
        )
        # Warning margins remain visible in the rApp energy/SLA decision, but
        # they must not turn every healthy-under-target cycle into an ARMD
        # resource veto. Only a hard SLA risk creates the contextual guard.
        if not camera_hard_risk and not vehicle_hard_risk and not cvar_hard_risk:
            if str(base.get('scenario', '') or '') == 'greenran_global_noop':
                return floor_proposal(
                    'ARMD floor proposal: métricas dentro da proteção; aplicar somente o mínimo por UE'
                )
            return base

        evidence = list(base.get('evidence') or [])
        reasons = []
        if camera_active:
            throughput = self._safe_float(camera.get('throughput_mbps'), 0.0)
            latency = self._safe_float(camera.get('latency_ms'), 0.0)
            if not camera.get('throughput_ready', False):
                reasons.append('camera throughput ainda sem janela validada')
            elif throughput < camera_target:
                reasons.append(f'camera throughput {throughput:.1f} < {camera_target:.1f} Mbps')
            elif throughput < camera_guard:
                reasons.append(f'camera throughput {throughput:.1f} < margem {camera_guard:.1f} Mbps')
            if latency >= camera_latency_target:
                reasons.append(f'camera latência {latency:.1f} >= {camera_latency_target:.1f} ms')
            elif latency >= camera_latency_warning:
                reasons.append(f'camera latência {latency:.1f} >= guarda {camera_latency_warning:.1f} ms')
            evidence.append(f'camera_active={active_cameras:.0f}')
            evidence.append(f'camera_sla={camera_target:.1f}Mbps/{camera_latency_target:.1f}ms')

        if vehicle_active:
            latency = self._safe_float(vehicle.get('max_latency_ms'), 0.0)
            loss = self._safe_float(vehicle.get('max_packet_loss_percent'), 0.0)
            if latency >= vehicle_latency_target:
                reasons.append(f'vehicle latência {latency:.1f} >= {vehicle_latency_target:.1f} ms')
            elif latency >= vehicle_latency_warning:
                reasons.append(f'vehicle latência {latency:.1f} >= guarda {vehicle_latency_warning:.1f} ms')
            if loss >= vehicle_loss_target:
                reasons.append(f'vehicle loss {loss:.2f} >= {vehicle_loss_target:.2f}%')
            elif loss >= vehicle_loss_warning:
                reasons.append(f'vehicle loss {loss:.2f} >= guarda {vehicle_loss_warning:.2f}%')
            evidence.append(f'vehicle_active={active_vehicles:.0f}')
            evidence.append(f'vehicle_sla={vehicle_latency_target:.1f}ms/{vehicle_loss_target:.2f}%')

        if cvar_hard_risk:
            if current_cvar_us >= cvar_target_ms * 1000.0:
                reasons.append(f'CVaR {current_cvar_us / 1000.0:.2f} ms >= limite crítico {cvar_target_ms:.2f} ms')
            else:
                reasons.append(
                    f'CVaR subiu para {current_cvar_us / 1000.0:.2f} ms '
                    f'({cvar_ratio:.2f}x da referência, {cvar_streak} janelas)'
                )
            evidence.extend([
                f'cvar_current_ms={current_cvar_us / 1000.0:.3f}',
                f'cvar_reference_ms={reference_cvar_us / 1000.0:.3f}',
                f'cvar_ratio={cvar_ratio:.3f}',
                f'cvar_risk_streak={cvar_streak}',
            ])

        # Camera/eMBB and vehicle safety share the top priority.  Camera is
        # selected when both are active because its proposal reserves the
        # larger RAN share; the vehicle SLA still remains in the evidence and
        # is never relaxed by the TA-SAM proposal.
        domain = 'camera' if camera_active else 'vehicle' if vehicle_active else 'network'
        original_verdict = str(base.get('expected_energy_saver', '') or 'CONDITIONAL').upper()
        guarded_verdict = 'BLOCKED' if original_verdict == 'BLOCKED' else 'CONDITIONAL'
        guard_reason = (
            'SLA guard preventivo: TA-SAM propôs reduzir RAN em '
            f'{abs(delta_ran):.4f}; proteção preservada para '
            f"{'câmera e veículo' if camera_active and vehicle_active else domain}"
        )
        if reasons:
            guard_reason += ' (' + '; '.join(reasons) + ')'
        critical_violation = bool(camera_hard_risk or vehicle_hard_risk or cvar_hard_risk)
        original_violation = str(base.get('priority_violation', '') or '').upper()
        if cvar_hard_risk and not (camera_hard_risk or vehicle_hard_risk):
            guard_violation = 'CVAR_CRITICAL'
        elif original_violation in {
            'THROUGHPUT', 'LATENCY', 'APP2_MTC_CRITICAL', 'VEHICLE_CRITICAL',
            'CVAR_CRITICAL', 'P95_CRITICAL',
        }:
            guard_violation = original_violation
        else:
            guard_violation = 'PROACTIVE_SLA_PROTECTION'

        base.update({
            'available': True,
            'proposal_present': True,
            'proposal_valid': True,
            'proposal_kind': 'contextual_sla_guard',
            'scenario': 'proactive_sla_guard',
            'domain': domain,
            'expected_energy_saver': guarded_verdict,
            'expected_action': 'FULL_POWER' if guarded_verdict == 'BLOCKED' else 'FULL_POWER_GUARD',
            'confidence': max(self._safe_float(base.get('confidence'), 0.0), 0.99),
            'reason': guard_reason,
            'evidence': evidence + [f'tasam_delta_r_ran={delta_ran:.4f}'],
            'priority_violation': guard_violation,
            'safety_veto': critical_violation,
            'critical_violation': critical_violation,
            'proactive_sla_guard': True,
            'guard_thresholds': {
                'camera_throughput_target_mbps': camera_target,
                'camera_latency_target_ms': camera_latency_target,
                'vehicle_latency_target_ms': vehicle_latency_target,
                'vehicle_loss_target_pct': vehicle_loss_target,
                'cvar_target_ms': cvar_target_ms,
                'cvar_relative_regression_pct': cvar_regression_pct,
                'cvar_required_consecutive_windows': cvar_required_windows,
            },
        })
        return base

    def _build_tasam_proposal(self, decision, tasam_advisor, resource_snapshot):
        """Normalize TA-SAM's checkpoint output into the complete proposal contract."""
        tasam_advisor = tasam_advisor or {}
        shadow = (resource_snapshot or {}).get('marl_shadow') or {}
        resource_advice = tasam_advisor.get('resource_advice') or {}
        energy = tasam_advisor.get('energy_advice') or {}
        verdict = str(energy.get('decision', 'CONDITIONAL') or 'CONDITIONAL').upper()
        action = str(energy.get('action', 'MONITOR') or 'MONITOR')
        confidence = float(tasam_advisor.get('confidence', 0.0) or 0.0)
        shadow_ran = shadow.get('shadow_r_ran')
        shadow_ai = shadow.get('shadow_r_ai')
        allocation = dict(resource_snapshot or {})
        if shadow_ran is not None and shadow_ai is not None:
            allocation.update({
                'r_ran': float(shadow_ran),
                'r_ai': float(shadow_ai),
                'controller_id': 'ta_sam_marl_assistant',
                'source': 'tasam_proposal',
            })
        allocation['allocation_state'] = verdict
        power_by_cell = energy.get('power_percent_by_cell') or {}
        if power_by_cell:
            try:
                allocation['power_percent_by_cell'] = {
                    str(cell): int(value) for cell, value in normalize_power_by_cell(power_by_cell).items()
                }
            except (EconomicActionV3Error, TypeError, ValueError):
                allocation['power_percent_by_cell'] = None
        if energy.get('total_budget_fraction') is not None:
            allocation['total_budget_fraction'] = max(
                0.0, min(1.0, float(energy.get('total_budget_fraction')))
            )
        if not self._tasam_full_control_enabled():
            allocation = enforce_resource_state(allocation, preserve_allocation=True)
        allocation = enforce_tasam_headroom_envelope(
            allocation, headroom_ratio=0.15
        )
        structural_valid = bool(
            tasam_advisor.get('enabled')
            and tasam_advisor.get('source') in {'checkpoint', 'mixed'}
            and resource_advice.get('enabled')
            and shadow_ran is not None
            and shadow_ai is not None
        )
        return {
            'proposal_id': f"tasam:{decision.get('timestamp', int(time.time()))}:{self.cycle}",
            'source': 'ta_sam',
            'available': structural_valid,
            'valid': structural_valid,
            'feasible': bool(resource_advice.get('enabled', False)),
            'verdict': verdict,
            'action': action,
            'confidence': confidence,
            'proposal_score': round(
                (0.55 * float(tasam_advisor.get('arbitration_score', 0.0) or 0.0))
                + (0.45 * confidence), 6
            ),
            'priority_score': float(tasam_advisor.get('arbitration_score', 0.0) or 0.0),
            'resource_score': float(resource_advice.get('score_delta', 0.0) or 0.0),
            'resource_allocation': allocation,
            'resource_advice': resource_advice,
            'sla_protection': {
                'priority_domain': resource_advice.get('priority', 'mixed'),
                'evidence_flags': tasam_advisor.get('evidence_flags', {}),
            },
            'priority_constraints': {'source': 'tasam_checkpoint'},
            'scenario': shadow.get('scenario_stage', 'greenran_runtime'),
            'reason': str(tasam_advisor.get('reason', '') or energy.get('reason', '') or 'TA-SAM proposal'),
            'evidence': [key for key, value in (tasam_advisor.get('evidence_flags') or {}).items() if value],
            'safety_veto': False,
            'power_percent_by_cell': allocation.get('power_percent_by_cell'),
            'total_budget_fraction': allocation.get('total_budget_fraction'),
        }

    def _apply_selected_assistant_proposal(self, decision, judge_result, armd_proposal, tasam_proposal):
        selected = judge_result.get('selected_proposal') or {}
        source = str(selected.get('source', '') or '')
        decision['rapp_judge_result'] = judge_result
        decision['selected_assistant'] = source
        decision['selected_proposal_id'] = selected.get('proposal_id', '')
        decision['external_last_resort_used'] = bool(judge_result.get('external_last_resort_used', False))
        decision['rapp_judge_conflict_type'] = judge_result.get('conflict_type', '')
        decision['rapp_judge_reason'] = judge_result.get('reason', '')
        decision['proactive_sla_guard'] = bool(judge_result.get('proactive_sla_guard', False))
        decision['proactive_sla_guard_reason'] = judge_result.get('proactive_sla_guard_reason', '')
        if source not in {'armd', 'ta_sam', 'joint'} or not selected.get('valid'):
            decision['assistant_only_failure'] = True
            decision['proposal_applied_exactly'] = False
            decision['ta_sam_actuation_applied'] = False
            decision['tasam_actuation_applied'] = False
            if self._tasam_full_control_enabled():
                decision['training_run_invalid'] = True
                decision['invalid_reason'] = 'invalid_or_missing_tasam_proposal'
                decision['effective_policy_algorithm'] = 'TA-SAM-MARL-INVALID'
                decision['effective_policy_source'] = 'invalid_tasam_proposal'
                decision['energy_saver'] = 'UNKNOWN'
                decision['action'] = 'NONE'
                decision['reason'] = 'TA-SAM proposal invalid; no heuristic/live fallback applied'
                decision['resource_allocation'] = {}
            decision['control_trial_mode'] = 'assistant_judge_invalid'
            decision['control_trial_reason'] = 'no valid assistant proposal selected'
            self._update_economic_application(
                decision,
                status='invalid',
                reason='invalid_or_missing_assistant_proposal',
            )
            return decision
        allocation = selected.get('resource_allocation') or selected.get('resource_advice') or {}
        decision['energy_saver'] = selected.get('verdict', 'CONDITIONAL')
        decision['action'] = selected.get('action', 'FULL_POWER_GUARD')
        decision['reason'] = f"rApp Judge: {selected.get('reason', '')} | vencedor={source}"
        decision['confidence'] = float(selected.get('confidence', 0.0) or 0.0)
        decision['resource_allocation'] = dict(allocation)
        # In cooperative mode the Judge returns the composed package; keeping
        # that package in the audit record is essential because neither raw
        # proposal alone describes what was actually applied.
        decision['selected_assistant_proposal'] = dict(
            selected if source == 'joint'
            else armd_proposal if source == 'armd' else tasam_proposal
        )
        decision['proposal_applied_exactly'] = True
        decision['assistant_only_failure'] = False
        decision['control_trial_mode'] = 'assistant_judge'
        decision['control_trial_reason'] = f"rApp Judge selected {source} proposal"
        decision['effective_policy_algorithm'] = 'ARMD-GreenRAN' if source == 'armd' else 'TA-SAM-MARL'
        decision['effective_policy_source'] = selected.get('source', source)
        decision['ta_sam_actuation_applied'] = source in {'ta_sam', 'joint'}
        decision['tasam_actuation_applied'] = decision['ta_sam_actuation_applied']
        decision['armd_actuation_applied'] = source in {'armd', 'joint'}
        if source == 'joint':
            decision['effective_policy_algorithm'] = 'ARMD+TA-SAM-HIERARCHICAL'
            decision['effective_policy_source'] = 'cooperative_hierarchy'
        if source in {'ta_sam', 'joint'}:
            self._update_economic_application(
                decision,
                status='projected',
                projected_power=decision.get('tasam_power_percent'),
            )
        else:
            self._update_economic_application(
                decision,
                status='rejected',
                reason='rapp_judge_selected_armd',
                projected_power=self._economic_power_candidate(decision),
            )
        return decision
    
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
            'tasam_proposal_present': False,
            'tasam_proposal_valid': False,
            'tasam_proposal_kind': 'missing',
            'advisor_arbitration': {},
            'rapp_judge_result': {},
            'selected_assistant': '',
            'selected_proposal_id': '',
            'selected_assistant_proposal': {},
            'proposal_applied_exactly': False,
            'external_last_resort_used': False,
            'armd_actuation_applied': False,
            'assistant_only_failure': False,
            'ta_sam_actuation_applied': False,
            'control_trial_mode': 'baseline',
            'effective_policy_algorithm': 'live_allocator',
            'effective_policy_source': 'heuristic_baseline',
            'control_trial_reason': 'assistant control disabled',
        }
        decision.update(_load_collection_event_context())

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

        # Feed the allocator and the E2 safety shield from the same real-PDCP
        # snapshot.  Proxy/missing UEs are intentionally omitted from the
        # allocator's verified ledger and will force full power at actuation.
        safety_sla_rows, safety_demand_rows = build_runtime_ue_inputs(
            self.read_extended_metrics()
        )
        safety_demand_by_imsi = {
            int(row['imsi']): row for row in safety_demand_rows
        }
        verified_rows = [
            {**row, **safety_demand_by_imsi.get(int(row['imsi']), {})}
            for row in safety_sla_rows if row.get('observed')
        ]
        decision['camera_metrics']['per_ue'] = [
            row for row in verified_rows if 1 <= int(row['imsi']) <= 3
        ]
        decision['app2_metrics']['per_ue'] = [
            row for row in verified_rows if 4 <= int(row['imsi']) <= 15
        ]
        decision['vehicle_metrics']['per_ue'] = [
            row for row in verified_rows if 16 <= int(row['imsi']) <= 20
        ]
        decision['per_ue_sla_report'] = evaluate_sla_window(safety_sla_rows)

        resource_allocation = compute_shared_resource_snapshot(
            camera_metrics=decision.get('camera_metrics'),
            app2_metrics=decision.get('app2_metrics'),
            vehicle_metrics=decision.get('vehicle_metrics'),
            network_health=decision.get('network_health'),
            shared_resource_config=RUNTIME_CONFIG.get('shared_resources', {}),
            previous_allocation=self._resource_allocation_prev,
            allocation_state=decision.get('energy_saver'),
            state_context=infer_allocation_state(
                decision.get('camera_metrics'),
                decision.get('app2_metrics'),
                decision.get('vehicle_metrics'),
                decision.get('network_health'),
                RUNTIME_CONFIG.get('shared_resources', {}),
            ),
        )
        # Causal shadow evidence must compare against the power state observed
        # before this decision is actuated.  The persisted energy command is
        # the source of truth; missing fields remain invalid downstream.
        live_energy = self.data_lake.latest_energy_observation()
        resource_allocation['live_energy_observation'] = live_energy
        resource_allocation['live_power_percent'] = live_energy.get('applied_power_percent', live_energy.get('power_percent'))
        resource_allocation['live_ru_count'] = live_energy.get('ru_count')
        resource_allocation['live_mmwave_count'] = live_energy.get('mmwave_count')
        resource_allocation['live_power_w'] = live_energy.get('power_w')
        resource_allocation['live_allocator_algorithm'] = str(
            (decision.get('rl_policy_runtime') or {}).get('algorithm', 'HEURISTIC') or 'HEURISTIC'
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
                    # The learned policy is allowed to propose a different
                    # split, but it must inherit the audited SLA-floor ledger
                    # from the baseline snapshot.  Replacing the whole dict
                    # here would silently discard the per-UE guarantees.
                    resource_allocation = dict(resource_allocation)
                    resource_allocation.update(resource_candidate)
                    resource_allocation = enforce_resource_state(resource_allocation, preserve_allocation=True)
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
        decision['tasam_proposal_present'] = bool(
            tasam_advisor and tasam_advisor.get('enabled', marl_shadow.get('enabled', False))
        )
        decision['tasam_proposal_valid'] = bool(
            decision['tasam_proposal_present'] and tasam_advisor.get('valid', False)
        )
        decision['tasam_proposal_kind'] = (
            'checkpoint' if decision['tasam_proposal_present'] and decision['tasam_source'] else
            ('unavailable' if decision['tasam_proposal_present'] else 'missing')
        )
        decision['tasam_would_influence'] = bool(tasam_advisor.get('would_influence', marl_shadow.get('would_influence', False)))
        decision['tasam_energy_decision'] = ((tasam_advisor.get('energy_advice') or {}).get('decision', ''))
        decision['tasam_energy_action'] = ((tasam_advisor.get('energy_advice') or {}).get('action', ''))
        energy_advice = tasam_advisor.get('energy_advice') or {}
        global_budget_advice = marl_shadow.get('global_budget_actor') or {}
        if global_budget_advice.get('enabled'):
            decision['tasam_infra_compute_budget'] = float(
                global_budget_advice.get('compute_budget', 1.0) or 1.0
            )
            decision['tasam_infra_io_budget'] = float(
                global_budget_advice.get('io_budget', 1.0) or 1.0
            )
        if energy_advice.get('power_percent') is not None:
            try:
                decision['tasam_power_percent'] = float(energy_advice.get('power_percent'))
            except (TypeError, ValueError):
                decision['tasam_power_percent'] = 100.0
        decision['tasam_energy_action'] = energy_advice.get('intent', decision.get('tasam_energy_action', ''))
        marl_shadow['shadow_power_percent'] = energy_advice.get('power_percent')
        marl_shadow['shadow_power_source'] = energy_advice.get('source', '')
        # Recompute after the global power actor has produced its proposal.
        marl_shadow['comparison'] = build_shadow_comparison(resource_allocation, marl_shadow)
        decision['tasam_checkpoint_valid'] = bool(marl_shadow.get('tasam_checkpoint_valid', False))
        decision['tasam_fallback_used'] = bool(marl_shadow.get('tasam_fallback_used', False))
        decision['tasam_evidence_valid'] = bool(
            marl_shadow.get('tasam_evidence_valid', False)
            and marl_shadow['comparison'].get('energy_valid')
            and marl_shadow['comparison'].get('resource_valid')
        )
        decision['live_allocator_algorithm'] = resource_allocation['live_allocator_algorithm']
        for field in (
            'live_power_percent', 'shadow_power_percent', 'live_power_w', 'shadow_power_w',
            'energy_saving_fraction', 'resource_saving_fraction', 'causal_score_delta',
            'energy_model_version',
        ):
            decision[field] = marl_shadow['comparison'].get(field)

        # In assistant-only evaluation mode the rApp applies the checkpoint
        # backed TA-SAM resource vector.  ARMD remains the safety assistant;
        # this block only replaces the live heuristic resource split when a
        # real checkpoint proposal is available, so a silent fallback cannot
        # be counted as TA-SAM actuation.
        control_mode = os.environ.get('GREENRAN_TASAM_ADVISOR_MODE', '').strip().lower()
        if control_mode == 'assistant_only_control':
            # In judge mode TA-SAM is a proposer. Its vector is copied into
            # the final decision only after the rApp judge selects TA-SAM.
            if self._assistant_judge_enabled():
                decision['control_trial_mode'] = 'assistant_judge_pending'
            else:
                decision['control_trial_mode'] = 'assistant_only_control'
            shadow_source = str(tasam_advisor.get('source', marl_shadow.get('source', '')) or '')
            shadow_r_ran = marl_shadow.get('shadow_r_ran')
            shadow_r_ai = marl_shadow.get('shadow_r_ai')
            if (
                not self._assistant_judge_enabled()
                and marl_shadow.get('available')
                and shadow_source in {'checkpoint', 'mixed'}
                and shadow_r_ran is not None
                and shadow_r_ai is not None
            ):
                resource_allocation['r_ran'] = float(shadow_r_ran)
                resource_allocation['r_ai'] = float(shadow_r_ai)
                resource_allocation['controller_id'] = 'ta_sam_marl_assistant'
                resource_allocation['source'] = 'ta_sam_marl_assistant'
                budget = float(resource_allocation.get('usable_budget', resource_allocation.get('resource_budget', 1.0)) or 1.0)
                d_ran = float(resource_allocation.get('d_ran', 0.0) or 0.0)
                d_ai = float(resource_allocation.get('d_ai', 0.0) or 0.0)
                resource_allocation['ran_completion_ratio'] = min(1.0, float(shadow_r_ran) / d_ran) if d_ran > 0 else 1.0
                resource_allocation['ai_completion_ratio'] = min(1.0, float(shadow_r_ai) / d_ai) if d_ai > 0 else 1.0
                resource_allocation['utilization_ratio'] = min(1.0, (float(shadow_r_ran) + float(shadow_r_ai)) / budget) if budget > 0 else 0.0
                resource_allocation = enforce_resource_state(resource_allocation, preserve_allocation=True)
                decision['rl_policy_runtime']['algorithm'] = 'TA-SAM-MARL'
                decision['rl_policy_runtime']['policy_id'] = tasam_advisor.get('policy_id', marl_shadow.get('policy_id', 'TA-SAM-MARL'))
                decision['ta_sam_actuation_applied'] = True
                decision['effective_policy_algorithm'] = 'TA-SAM-MARL'
                decision['effective_policy_source'] = shadow_source
                decision['control_trial_reason'] = 'checkpoint proposal applied by rApp arbitration'
            elif not self._assistant_judge_enabled():
                decision['effective_policy_algorithm'] = 'TA-SAM-MARL'
                decision['effective_policy_source'] = 'fallback_after_tasam_error'
                decision['control_trial_reason'] = 'checkpoint proposal unavailable; run invalid'
        decision['resource_allocation'] = resource_allocation
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
        # O relógio de simulação no TOPO da decisão: a observação atrasada
        # do Judge roda no ciclo seguinte e compara current_sim >=
        # issued_sim + ttl (5 s de sim).  Fontes anteriores (camera_metrics
        # fixo em 0.0, send_tasam_bundle que só roda DEPOIS da observação)
        # deixavam current_sim=None e os pendentes nunca fechavam (r29/r30:
        # 3 outcomes, seleção 20/90).
        decision['sim_time_s'] = float(
            (row.get('sim_time_s') if isinstance(row, dict) else 0) or 0
        )

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
        # Mantido apenas como compatibilidade; a linha oficial usa
        # ARMD + TA-SAM no caminho de decisão efetiva.
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
        self._begin_economic_action_contract(decision)
        armd_advice = self.armd_runtime.advise(
            decision=decision,
            camera_metrics=decision.get('camera_metrics'),
            vehicle_metrics=decision.get('vehicle_metrics'),
            app2_metrics=decision.get('app2_metrics'),
            network_health=decision.get('network_health'),
        )
        decision['armd_analysis'] = armd_advice
        full_control_mode = self._tasam_full_control_enabled()
        assistant_judge_mode = self._assistant_judge_enabled()
        decision = self.armd_runtime.apply(
            decision,
            armd_advice,
            mutate=not assistant_judge_mode,
        )
        if assistant_judge_mode:
            tasam_proposal = self._build_tasam_proposal(
                decision,
                decision.get('tasam_advisor') or {},
                decision.get('resource_allocation') or {},
            )
            armd_advice_for_proposal = self._build_proactive_sla_guard_advice(
                decision,
                armd_advice,
                tasam_proposal,
            )
            # Keep the original runtime diagnosis available for audit while
            # exposing the contextual safety proposal to the rApp judge.
            decision['armd_base_analysis'] = armd_advice
            decision['armd_analysis'] = armd_advice_for_proposal
            # The persisted ARMD fields must describe the proposal that the
            # judge actually receives.  Otherwise the decision log would say
            # ``neutral_noop`` while the applied proposal was a safety guard.
            decision['armd_scenario'] = armd_advice_for_proposal.get('scenario', '')
            decision['armd_domain'] = armd_advice_for_proposal.get('domain', '')
            decision['armd_source'] = armd_advice_for_proposal.get('source', 'armd')
            decision['armd_confidence'] = armd_advice_for_proposal.get('confidence', 0.0)
            decision['armd_reason'] = armd_advice_for_proposal.get('reason', '')
            decision['armd_expected_energy_saver'] = armd_advice_for_proposal.get('expected_energy_saver', '')
            decision['armd_expected_action'] = armd_advice_for_proposal.get('expected_action', '')
            armd_proposal = self._build_armd_proposal(
                decision,
                armd_advice_for_proposal,
                decision.get('resource_allocation') or {},
            )
            armd_safety_level = str(
                armd_proposal.get('armd_safety_level')
                or self._classify_armd_safety_level(decision, armd_advice_for_proposal)
            ).upper()
            decision['armd_safety_level'] = armd_safety_level
            decision['armd_role'] = (
                'safety_enforcer' if armd_safety_level == 'HARD_VETO' else 'advisory'
            )
            decision['armd_advisory_only'] = armd_safety_level != 'HARD_VETO'
            decision['armd_hard_veto'] = armd_safety_level == 'HARD_VETO'
            self._sync_economic_contract_provenance(
                decision, decision.get('economic_action') or {}
            )
            if full_control_mode and armd_safety_level != 'HARD_VETO':
                # Integral online control deliberately bypasses the ARMD
                # advisory envelope: the raw TA-SAM proposal is the action
                # applied to the simulator in non-critical states.
                tasam_shadow = dict(
                    decision.get('rl_policy_runtime', {}).get('marl_shadow') or {}
                )
                tasam_shadow.pop('armd_policy_envelope', None)
                tasam_shadow['armd_policy_envelope'] = {
                    'applied': False,
                    'source': 'disabled_tasam_full_control',
                    'reason': 'full-control training mode owns the complete action',
                }
            elif armd_safety_level == 'HARD_VETO':
                # A confirmed critical event retains ARMD's hard envelope,
                # including when the caller requested full-control training.
                tasam_shadow = self.marl_shadow_evaluator.apply_armd_policy_envelope(
                    decision.get('rl_policy_runtime', {}).get('marl_shadow') or {},
                    armd_proposal,
                    decision.get('resource_allocation') or {},
                )
            else:
                # In CLEAR/ADVISORY, ARMD only publishes constraints.  Do not
                # rewrite TA-SAM's allocation or label the action as an ARMD
                # composition; the later permission check enforces floors.
                tasam_shadow = dict(
                    decision.get('rl_policy_runtime', {}).get('marl_shadow') or {}
                )
                tasam_shadow['armd_policy_envelope'] = {
                    'applied': False,
                    'authority': 'ARMD-GreenRAN',
                    'policy_id': armd_proposal.get('proposal_id', ''),
                    'verdict': armd_proposal.get('verdict', ''),
                    'safety_veto': False,
                    'safety_level': armd_safety_level,
                    'advisory_only': True,
                    'reason': 'ARMD advisory only; TA-SAM may optimize within validated floors',
                }
            tasam_advisor = tasam_shadow.get('advisor', {}) if isinstance(tasam_shadow, dict) else {}
            decision['tasam_advisor'] = tasam_advisor
            decision['rl_policy_runtime']['marl_shadow'] = tasam_shadow
            decision['resource_allocation']['marl_shadow'] = tasam_shadow
            decision['resource_allocation']['tasam_advisor'] = tasam_advisor
            decision['tasam_mode'] = tasam_advisor.get('mode', decision.get('tasam_mode', 'shadow'))
            decision['tasam_source'] = tasam_advisor.get('source', decision.get('tasam_source', ''))
            decision['tasam_confidence'] = float(tasam_advisor.get('confidence', decision.get('tasam_confidence', 0.0)) or 0.0)
            decision['tasam_valid'] = bool(tasam_advisor.get('valid', decision.get('tasam_valid', False)))
            decision['tasam_would_influence'] = bool(tasam_advisor.get('would_influence', decision.get('tasam_would_influence', False)))
            decision['tasam_energy_decision'] = (tasam_advisor.get('energy_advice') or {}).get('decision', decision.get('tasam_energy_decision', ''))
            decision['tasam_energy_action'] = (tasam_advisor.get('energy_advice') or {}).get('action', decision.get('tasam_energy_action', ''))
            tasam_proposal = self._build_tasam_proposal(
                decision,
                tasam_advisor,
                decision.get('resource_allocation') or {},
            )
            decision['armd_proposal'] = armd_proposal
            decision['tasam_proposal'] = tasam_proposal
            economic_contract = decision.get('economic_action') or {}
            if self._is_economic_contract(economic_contract):
                proposed_allocation = dict(
                    tasam_proposal.get('resource_allocation') or {}
                )
                observation = proposed_allocation.get('live_energy_observation') or (
                    (decision.get('resource_allocation') or {}).get('live_energy_observation') or {}
                )
                economic_contract['proposed'] = self._economic_action_snapshot(
                    proposed_allocation,
                    decision.get('tasam_power_percent'),
                    ru_count=observation.get('ru_count'),
                    mmwave_count=observation.get('mmwave_count'),
                    power_percent_by_cell=(
                        proposed_allocation.get('power_percent_by_cell')
                        or (decision.get('tasam_proposal') or {}).get('power_percent_by_cell')
                    ),
                )
            decision['tasam_policy_envelope'] = dict(
                tasam_shadow.get('armd_policy_envelope') or {}
            )
            decision['tasam_policy_envelope_source'] = (
                'disabled_tasam_full_control'
                if full_control_mode and armd_safety_level != 'HARD_VETO'
                else 'armd_safety_enforcer'
                if armd_safety_level == 'HARD_VETO'
                else 'armd_advisory'
            )
            decision['tasam_policy_envelope_applied'] = bool(
                tasam_shadow.get('armd_policy_envelope', {}).get('applied', False)
            )
            decision['armd_proposal_present'] = bool(armd_proposal.get('available'))
            decision['armd_proposal_valid'] = bool(armd_proposal.get('valid'))
            decision['armd_proposal_kind'] = (
                armd_advice_for_proposal.get('proposal_kind')
                or ('contextual' if armd_advice_for_proposal.get('scenario') != 'greenran_global_noop' else 'neutral_noop')
            )
            decision['tasam_proposal_present'] = bool(tasam_proposal.get('available'))
            decision['tasam_proposal_valid'] = bool(tasam_proposal.get('valid'))
            decision['tasam_proposal_kind'] = 'checkpoint' if tasam_proposal.get('available') else 'missing'
            policy_status = self.rapp_policy_source.refresh()
            policy = policy_status.get('policy') or {}
            arbitration_cfg = policy.get('arbitration') or {}
            self.rapp_judge.external_last_resort_winner = str(
                (arbitration_cfg.get('last_resort') or {}).get('energy', '') or ''
            ).lower()
            self.rapp_judge.external_policy_id = policy_status.get('policy_id', '')
            self.rapp_judge.tie_margin = float(arbitration_cfg.get('tie_margin', 0.02) or 0.02)
            live_fallback = {
                'proposal_id': f"rapp:{decision.get('timestamp', int(time.time()))}:{self.cycle}",
                'source': 'rapp_policy',
                'available': True,
                'valid': True,
                'feasible': True,
                'verdict': str(decision.get('energy_saver', 'CONDITIONAL') or 'CONDITIONAL').upper(),
                'action': str(decision.get('action', 'FULL_POWER_GUARD') or 'FULL_POWER_GUARD'),
                'confidence': float(decision.get('confidence', 0.0) or 0.0),
                'resource_allocation': dict(decision.get('resource_allocation') or {}),
                'reason': 'rApp live policy fallback',
            }
            judge_result = self.rapp_judge.decide(
                priorities=(policy.get('network_policy') or {}).get('priorities') or {},
                armd_proposal=armd_proposal,
                tasam_proposal=tasam_proposal,
                fallback_proposal=live_fallback,
            )
            if full_control_mode:
                # Keep the categorical Judge decision for audit/comparison,
                # but force the training action to TA-SAM whenever its
                # proposal is valid.  Invalid proposals remain invalid and
                # are never replaced by a live allocator action.
                decision['rapp_judge_arbitration_result'] = dict(judge_result)
                selected_tasam = dict(tasam_proposal)
                forced_result = dict(judge_result)
                forced_result.update({
                    'selected_advocate': 'ta_sam',
                    'winner': 'ta_sam',
                    'resource_winner': 'ta_sam',
                    'selected_proposal': selected_tasam,
                    'mode': 'tasam_full_control',
                    'composition_mode': 'tasam_full_control',
                    'reason': 'TA-SAM integral online control; Judge retained for audit',
                })
                judge_result = forced_result
            economic_safety_isolated, isolation_reason = self._economic_safety_isolation(decision)
            decision['economic_safety_isolated'] = economic_safety_isolated
            decision['economic_safety_isolation_reason'] = isolation_reason
            decision['economic_isolation_source'] = (
                'armd_hard_veto' if economic_safety_isolated else ''
            )
            decision['economic_execution_mode'] = (
                'safety_isolated' if economic_safety_isolated else 'economic'
            )
            contract = decision.get('economic_action') or {}
            self._sync_economic_contract_provenance(decision, contract)
            if economic_safety_isolated:
                # ARMD/rApp owns the safe action. TA-SAM remains visible in
                # the audit record but can never be selected, applied, or
                # credited to the economic replay in this state.
                safe_proposal = armd_proposal if armd_proposal.get('valid') else {}
                safe_allocation = safe_proposal.get('resource_allocation') or decision.get('resource_allocation') or {}
                decision['rapp_judge_arbitration_result'] = dict(judge_result)
                decision['rapp_judge_result'] = {
                    **dict(judge_result),
                    'selected_advocate': 'armd' if safe_proposal else 'rapp_safety',
                    'winner': 'armd' if safe_proposal else 'rapp_safety',
                    'resource_winner': 'armd' if safe_proposal else 'rapp_safety',
                    'selected_proposal': safe_proposal,
                    'mode': 'safety_isolated',
                    'reason': f'economic isolation: {isolation_reason}',
                }
                decision['selected_assistant'] = 'armd' if safe_proposal else 'rapp_safety'
                decision['selected_proposal_id'] = safe_proposal.get('proposal_id', '') if safe_proposal else ''
                decision['selected_assistant_proposal'] = dict(safe_proposal)
                decision['resource_allocation'] = dict(safe_allocation)
                decision['proposal_applied_exactly'] = False
                decision['assistant_only_failure'] = False
                decision['control_trial_mode'] = 'safety_isolated'
                decision['control_trial_reason'] = isolation_reason
                decision['effective_policy_algorithm'] = 'ARMD/rApp-SAFETY'
                decision['effective_policy_source'] = 'critical_economic_isolation'
                decision['ta_sam_actuation_applied'] = False
                decision['tasam_actuation_applied'] = False
                decision['armd_actuation_applied'] = bool(safe_proposal)
                decision['assistant_rollout_fraction'] = self._online_rollout_fraction(decision)
                decision['assistant_rollout_allowed'] = False
                decision['assistant_rollout_applied'] = False
                decision['economic_training_eligible'] = False
                decision['economic_promotion_eligible'] = False
                self._update_economic_application(
                    decision,
                    status='safety_isolated',
                    reason='critical_state_economic_isolation',
                    projected_power=100.0,
                )
                contract['economic_execution_mode'] = 'safety_isolated'
                contract['economic_safety_isolated'] = True
                contract['economic_safety_isolation_reason'] = isolation_reason
            else:
                decision['tasam_operating_permission'] = bool(
                    armd_safety_level in {'CLEAR', 'ADVISORY'}
                    and tasam_proposal.get('valid')
                    and decision.get('resource_allocation', {}).get('floor_feasible', True) is not False
                    and decision.get('resource_allocation', {}).get('per_ue_floor_feasible', True) is not False
                    and decision.get('resource_allocation', {}).get('floor_verified', True) is not False
                )
                self._sync_economic_contract_provenance(decision, contract)
                rollout_fraction = self._online_rollout_fraction(decision)
                rollout_allowed = bool(
                    decision['tasam_operating_permission']
                    and self._online_rollout_allows(decision, rollout_fraction)
                )
                decision['assistant_rollout_fraction'] = rollout_fraction
                decision['assistant_rollout_allowed'] = rollout_allowed
                if rollout_allowed:
                    decision = self._apply_selected_assistant_proposal(
                        decision,
                        judge_result,
                        armd_proposal,
                        tasam_proposal,
                    )
                    decision['assistant_rollout_applied'] = bool(decision.get('proposal_applied_exactly', False))
                    decision['control_trial_mode'] = (
                        ('tasam_full_control' if decision.get('proposal_applied_exactly', False)
                         else 'tasam_full_control_invalid') if full_control_mode
                        else decision.get('control_trial_mode', 'assistant_judge')
                    )
                    decision['control_trial_reason'] = (
                        'TA-SAM proposal applied directly; no ARMD envelope, fallback, or rollback'
                        if full_control_mode else decision.get('control_trial_reason', '')
                    )
                else:
                    # Shadow/canary hold: keep the live rApp allocation in force,
                    # but retain both proposals and the Judge verdict for scoring.
                    selected = judge_result.get('selected_proposal') or {}
                    decision['rapp_judge_result'] = judge_result
                    decision['selected_assistant'] = str(selected.get('source', '') or '')
                    decision['selected_proposal_id'] = selected.get('proposal_id', '')
                    decision['external_last_resort_used'] = bool(judge_result.get('external_last_resort_used', False))
                    decision['rapp_judge_conflict_type'] = judge_result.get('conflict_type', '')
                    decision['rapp_judge_reason'] = judge_result.get('reason', '')
                    decision['proposal_applied_exactly'] = False
                    decision['assistant_only_failure'] = False
                    decision['control_trial_mode'] = 'shadow' if rollout_fraction <= 0.0 else 'canary_hold'
                    decision['control_trial_reason'] = (
                        f'assistant proposals scored but not applied; rollout={rollout_fraction:.3f}'
                    )
                    decision['effective_policy_algorithm'] = 'rApp-live'
                    decision['effective_policy_source'] = 'live_allocator'
                    decision['ta_sam_actuation_applied'] = False
                    decision['armd_actuation_applied'] = False
                    decision['assistant_rollout_applied'] = False
                    selected_source = str(selected.get('source', '') or '')
                    self._update_economic_application(
                        decision,
                        status='not_applied',
                        reason='rollout_hold',
                        projected_power=(
                            decision.get('tasam_power_percent')
                            if selected_source in {'ta_sam', 'joint'}
                            else self._economic_power_candidate(decision)
                        ),
                    )
                    contract = decision.get('economic_action') or {}
                    if self._is_economic_contract(contract):
                        projected_allocation = selected.get('resource_allocation') or decision.get('resource_allocation') or {}
                        observation = projected_allocation.get('live_energy_observation') or {}
                        contract['projected'] = self._economic_action_snapshot(
                            projected_allocation,
                            decision.get('tasam_power_percent') if selected_source in {'ta_sam', 'joint'} else self._economic_power_candidate(decision),
                            ru_count=observation.get('ru_count'),
                            mmwave_count=observation.get('mmwave_count'),
                        )
            decision['advisor_arbitration'] = self._build_advisor_arbitration(decision)
            decision['advisor_arbitration'].update({
                'mode': 'tasam_full_control' if full_control_mode else 'rapp_judge_v1',
                'judge': 'rapp',
                'selected_assistant': decision.get('selected_assistant', ''),
                'selected_proposal_id': decision.get('selected_proposal_id', ''),
                'proposal_applied_exactly': decision.get('proposal_applied_exactly', False),
                'external_last_resort_used': decision.get('external_last_resort_used', False),
                'winner': decision.get('selected_assistant', 'none'),
                'rapp_final_decision': decision.get('energy_saver', 'UNKNOWN'),
                'rapp_final_action': decision.get('action', ''),
                'judge_result': judge_result,
                'categorical_judge_result': decision.get('rapp_judge_arbitration_result', {}),
            })
        else:
            decision['advisor_arbitration'] = self._build_advisor_arbitration(decision)

        # Persist the state machine after the rApp judge has produced the
        # final verdict.  The selected proposal already carries the SLA floor;
        # this audit state drives the two-cycle recovery on the next cycle.
        resource_allocation = dict(decision.get('resource_allocation') or {})
        final_state = str(decision.get('energy_saver', 'ALLOWED') or 'ALLOWED').upper()
        resource_allocation['allocation_state'] = final_state
        assistant_runtime_active = bool(
            self._assistant_judge_enabled()
            or decision.get('tasam_enabled')
            or decision.get('armd_enabled')
            or os.environ.get('GREENRAN_AB_MODE', '').strip().lower() == 'joint'
        )
        if assistant_runtime_active:
            resource_allocation = enforce_resource_state(
                resource_allocation,
                preserve_allocation=decision.get('selected_assistant') in {'ta_sam', 'joint'},
            )
            if decision.get('selected_assistant') in {'ta_sam', 'joint'}:
                resource_allocation = enforce_tasam_headroom_envelope(
                    resource_allocation, headroom_ratio=0.15
                )
            resource_allocation = apply_per_ue_allocation(resource_allocation)
        else:
            resource_allocation = apply_baseline_resource_band(
                resource_allocation,
                final_state,
                RUNTIME_CONFIG.get('shared_resources', {}) or {},
            )
        final_shadow = resource_allocation.get('marl_shadow') or {}
        if final_shadow:
            final_shadow['comparison'] = build_shadow_comparison(resource_allocation, final_shadow)
            resource_allocation['marl_shadow'] = final_shadow
            # Keep checkpoint provenance monotonic across the final resource
            # projection.  ``enforce_resource_state`` may rebuild the
            # resource snapshot without copying the evaluator's top-level
            # provenance flags, even though the nested DU recommendations
            # still all come from the loaded checkpoint.  A missing/stale
            # flag must not turn a valid checkpoint-backed proposal into a
            # false negative after it has already been selected.  Conversely,
            # a heuristic/mixed source or any fallback remains invalid.
            shadow_sources = [
                str(item.get('source', '') or '').strip().lower()
                for item in (final_shadow.get('du_recommendations') or [])
                if isinstance(item, dict)
            ]
            checkpoint_provenance = bool(
                str(final_shadow.get('source', '') or '').strip().lower() == 'checkpoint'
                and not bool(final_shadow.get('tasam_fallback_used', False))
                and len(shadow_sources) == 3
                and all(source == 'checkpoint' for source in shadow_sources)
                and str(final_shadow.get('checkpoint_readiness', '') or '')
                in {'shadow_ready', 'control_candidate'}
            )
            decision['tasam_checkpoint_valid'] = bool(
                final_shadow.get('tasam_checkpoint_valid') is True
                or checkpoint_provenance
                or decision.get('tasam_checkpoint_valid', False)
            )
            decision['tasam_fallback_used'] = bool(
                final_shadow.get('tasam_fallback_used', decision.get('tasam_fallback_used', False))
            )
            decision['tasam_evidence_valid'] = bool(
                decision.get('tasam_checkpoint_valid', False)
                and not decision.get('tasam_fallback_used', False)
                and final_shadow['comparison'].get('energy_valid')
                and final_shadow['comparison'].get('resource_valid')
            )
            for field in (
                'live_power_percent', 'shadow_power_percent', 'live_power_w', 'shadow_power_w',
                'energy_saving_fraction', 'resource_saving_fraction', 'causal_score_delta',
                'energy_model_version',
            ):
                decision[field] = final_shadow['comparison'].get(field)
        decision['resource_allocation'] = resource_allocation
        if (
            decision.get('economic_action_contract') in {'applied_action_v2', ECONOMIC_ACTION_V3_CONTRACT}
            and decision.get('selected_assistant') in {'ta_sam', 'joint'}
            and decision.get('proposal_applied_exactly', False)
        ):
            # Floors/per-UE projection can alter the Judge's package.  The
            # projected action stored for replay must therefore be the final
            # post-projection allocation, before its energy command is sent.
            self._update_economic_application(
                decision,
                status='projected',
                projected_power=decision.get('tasam_power_percent'),
            )
        decision['allocation_state'] = final_state
        decision['resource_floor_policy'] = resource_allocation.get('floor_policy', 'sla_per_ue_v1')
        self._resource_allocation_prev = {
            'r_ran': float(resource_allocation.get('r_ran', 0.5) or 0.5),
            'r_ai': float(resource_allocation.get('r_ai', 0.5) or 0.5),
            'allocation_state': final_state,
            'healthy_streak': int(resource_allocation.get('healthy_streak', 0) or 0),
            'floor_total_ran': float(resource_allocation.get('floor_total_ran', 0.0) or 0.0),
            'floor_total_ai': float(resource_allocation.get('floor_total_ai', 0.0) or 0.0),
        }
        
        return decision

    def _build_advisor_arbitration(self, decision):
        """Build the auditable ARMD/TA-SAM scoreboard judged by the rApp."""
        severity_rank = {
            'UNKNOWN': 0,
            'ALLOWED': 1,
            'CONDITIONAL': 2,
            'BLOCKED': 3,
        }
        armd = decision.get('armd_analysis') or {}
        tasam = decision.get('tasam_advisor') or {}

        armd_proposal_present = bool(
            decision.get('armd_proposal_present', armd.get('proposal_present', False))
        )
        tasam_proposal_present = bool(
            decision.get('tasam_proposal_present', tasam.get('proposal_present', False))
        )

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

        # Keep legacy policy-source fields in the audit record. They are
        # diagnostic only; the rApp decision above remains the final result.
        legacy_external_available = False
        legacy_external_used = False
        legacy_energy_winner = winner if winner in {'armd', 'ta_sam'} else 'none'
        if hasattr(self, 'rapp_policy_source'):
            try:
                policy_status = self.rapp_policy_source.refresh()
                legacy_external_available = bool(policy_status.get('available', False))
                policy = policy_status.get('policy') or {}
                arbitration = policy.get('arbitration') or {}
                tie_margin = float(arbitration.get('tie_margin', 0.02) or 0.02)
                same_target = armd_target == tasam_target and armd_target != 'UNKNOWN'
                score_tie = abs(armd_score - tasam_score) <= tie_margin
                if armd_valid and tasam_valid and (same_target or score_tie):
                    legacy_energy_winner = str(
                        (arbitration.get('last_resort') or {}).get('energy', legacy_energy_winner)
                    )
                    legacy_external_used = legacy_energy_winner in {'armd', 'ta_sam'}
            except Exception:
                legacy_external_available = False

        return {
            'mode': 'shadow_scoreboard',
            'judge': 'rapp',
            'rapp_final_decision': str(decision.get('energy_saver', 'UNKNOWN') or 'UNKNOWN').upper(),
            'rapp_final_action': decision.get('action', ''),
            'proposal_pair_complete': armd_proposal_present and tasam_proposal_present,
            'arbitration_present': True,
            'armd_proposal_present': armd_proposal_present,
            'armd_proposal_valid': bool(decision.get('armd_proposal_valid', armd.get('proposal_valid', armd_valid))),
            'armd_proposal_kind': decision.get('armd_proposal_kind', armd.get('proposal_kind', 'missing')),
            'tasam_proposal_present': tasam_proposal_present,
            'tasam_proposal_valid': bool(decision.get('tasam_proposal_valid', tasam.get('valid', tasam_valid))),
            'tasam_proposal_kind': decision.get('tasam_proposal_kind', tasam.get('proposal_kind', 'missing')),
            'winner': winner,
            'armd_valid': armd_valid,
            'tasam_valid': tasam_valid,
            'agreement': armd_target == tasam_target and armd_target != 'UNKNOWN',
            'armd_score': armd_score,
            'tasam_score': tasam_score,
            'armd_target': armd_target,
            'tasam_target': tasam_target,
            'would_apply': False,
            'energy_winner': legacy_energy_winner,
            'resource_winner': 'ta_sam' if tasam_valid else ('armd' if armd_valid else 'none'),
            'external_policy_available': legacy_external_available,
            'energy_external_used': legacy_external_used,
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
        slice_policy = self.a1.send_slice_policy(
            decision['slicer_state'],
            {'active': int(decision['pattern_analysis']['current_cameras'])} if decision.get('pattern_analysis') else None,
            armd_info=armd_info,
            per_ue_allocation=(decision.get('resource_allocation') or {}).get('per_ue_allocation') or None,
            per_ue_context=decision.get('resource_allocation') or {},
        )
        per_ue = (decision.get('resource_allocation') or {}).get('per_ue_allocation') or []
        if per_ue and isinstance(slice_policy, dict):
            per_ue_policy = slice_policy.get('per_ue_resource_policy') or {}
            decision['resource_allocation']['per_ue_policy_id'] = str(
                per_ue_policy.get('policy_id') or slice_policy.get('policy_id') or ''
            )
            decision['resource_allocation']['per_ue_application_status'] = str(
                per_ue_policy.get('application_status')
                or 'pending_ack'
            )

    @staticmethod
    def _control_cell_for_imsi(imsi):
        """Map the canonical 20-UE topology onto its three logical DUs."""
        if 1 <= int(imsi) <= 6:
            return 2
        if 7 <= int(imsi) <= 9:
            return 3
        return 4

    def _native_association_map(self, sim_time_s):
        """Return the latest native IMSI-to-cell map at a simulation time.

        The static topology map remains useful for model features, but it is
        not safe for an E2 control bundle: the ns-3 RRC may attach a UE to a
        different DU.  Control is fail-closed when the native association
        trace does not cover all 20 canonical IMSIs.
        """
        try:
            self.data_lake.ingest_native_association_observations()
            conn = getattr(self.data_lake, 'conn', None)
            if conn is None:
                return {}, 'association_database_unavailable'
            cutoff = float(sim_time_s or 0.0)
            campaign_id = str(os.environ.get('GREENRAN_CAMPAIGN_ID', '') or '').strip()
            source_generation = str(os.environ.get('GREENRAN_NATIVE_SOURCE_GENERATION', '') or '').strip()
            evidence_version = str(os.environ.get('GREENRAN_NATIVE_EVIDENCE_VERSION', '') or '').strip()
            filters = ""
            params = [cutoff]
            if campaign_id:
                filters += " AND campaign_id=?"
                params.append(campaign_id)
            if source_generation:
                filters += " AND source_generation=?"
                params.append(source_generation)
            if evidence_version:
                filters += " AND evidence_version=?"
                params.append(evidence_version)
            rows = conn.execute(
                f"""
                SELECT imsi, cell_id, sim_time_s, association_epoch, evidence_version
                  FROM tasam_native_associations
                 WHERE imsi BETWEEN 1 AND 20
                   AND sim_time_s <= ?
                   {filters}
                 ORDER BY imsi ASC, sim_time_s DESC, id DESC
                """,
                tuple(params),
            ).fetchall()
        except Exception:
            return {}, 'association_query_failed'
        mapping = {}
        evidence = {}
        for row in rows:
            try:
                imsi = int(row[0])
                cell_id = int(row[1])
                row_time = float(row[2])
            except (TypeError, ValueError):
                continue
            if imsi in mapping or cell_id not in {2, 3, 4}:
                continue
            mapping[imsi] = cell_id
            evidence[imsi] = {
                'cell_id': cell_id,
                'sim_time_s': row_time,
                'association_epoch': str(row[3] or ''),
                'evidence_version': str(row[4] or ''),
            }
        expected = set(range(1, 21))
        if set(mapping) != expected:
            missing = sorted(expected - set(mapping))
            return mapping, f'association_incomplete_missing_imsis:{missing}'
        observed_cells = set(mapping.values())
        if observed_cells != {2, 3, 4}:
            return mapping, (
                'association_incomplete_expected_du_cells:{'
                f'{sorted(observed_cells)}'
                '}'
            )
        return mapping, evidence

    def _native_association_cells(
        self,
        sim_time_s,
        *,
        allow_empty_cell=None,
        allow_empty_sleep_transaction=None,
    ):
        """Return the native RRC IMSI set currently visible on each DU.

        In MC runs an IMSI can legitimately be present in more than one
        mmWave RRC map while the association converges.  The old helper
        collapsed those rows independently per IMSI, which could create a
        bundle with an empty DU or with a policy sent to a DU that did not
        contain that RNTI when ns-3 processed the request.  Use the latest
        complete per-cell snapshot instead and preserve native multi-cell
        membership for scheduler preparation.
        """
        try:
            self.data_lake.ingest_native_association_observations()
            conn = getattr(self.data_lake, 'conn', None)
            if conn is None:
                return {}, 'association_database_unavailable'
            cutoff = float(sim_time_s or 0.0)
            campaign_id = str(os.environ.get('GREENRAN_CAMPAIGN_ID', '') or '').strip()
            source_generation = str(os.environ.get('GREENRAN_NATIVE_SOURCE_GENERATION', '') or '').strip()
            evidence_version = str(os.environ.get('GREENRAN_NATIVE_EVIDENCE_VERSION', '') or '').strip()
            filters = ""
            params = [cutoff]
            if campaign_id:
                filters += " AND campaign_id=?"
                params.append(campaign_id)
            if source_generation:
                filters += " AND source_generation=?"
                params.append(source_generation)
            if evidence_version:
                filters += " AND evidence_version=?"
                params.append(evidence_version)
            rows = conn.execute(
                f"""
                SELECT id, cell_id, imsi, rnti, sim_time_s, association_epoch
                  FROM tasam_native_associations
                 WHERE imsi BETWEEN 1 AND 20
                   AND cell_id IN (2, 3, 4)
                   AND sim_time_s <= ?
                   {filters}
                 ORDER BY cell_id ASC, sim_time_s ASC, id ASC
                """,
                tuple(params),
            ).fetchall()
            snapshot_rows = conn.execute(
                f"""
                SELECT cell_id, sim_time_s, association_epoch, attached_ue_count,
                       sleep_transaction_id
                  FROM tasam_native_association_snapshots
                 WHERE cell_id IN (2, 3, 4)
                   AND sim_time_s <= ?
                   {filters}
                 ORDER BY cell_id ASC, sim_time_s ASC, id ASC
                """,
                tuple(params),
            ).fetchall()
        except Exception:
            return {}, 'association_query_failed'

        # The trace is change-based per cell.  Select one complete event for
        # each DU, rather than mixing the latest row of every IMSI from
        # different epochs.  This preserves removals and native MC overlap.
        events: dict[int, dict[tuple[float, str], list[tuple[int, int, int, int]]]] = {}
        for row in rows:
            try:
                row_id = int(row[0])
                cell_id = int(row[1])
                imsi = int(row[2])
                rnti = int(row[3])
                row_time = float(row[4])
                epoch = str(row[5] or '')
            except (TypeError, ValueError):
                continue
            if cell_id not in {2, 3, 4} or imsi not in range(1, 21):
                continue
            events.setdefault(cell_id, {}).setdefault((row_time, epoch), []).append(
                (row_id, imsi, rnti, cell_id)
            )

        selected: dict[int, tuple[float, str, list[tuple[int, int, int, int]]]] = {}
        for cell_id, cell_events in events.items():
            if not cell_events:
                continue
            key = max(
                cell_events,
                key=lambda item: (item[0], max(row[0] for row in cell_events[item])),
            )
            selected[cell_id] = (key[0], key[1], cell_events[key])

        # v7 snapshots are authoritative for a changed cell and, unlike the
        # historical row stream, can explicitly represent zero attached UEs.
        snapshots = {}
        for row in snapshot_rows:
            try:
                cell_id, row_time, epoch, count, sleep_id = (
                    int(row[0]), float(row[1]), str(row[2] or ''), int(row[3]), str(row[4] or ''),
                )
            except (TypeError, ValueError):
                continue
            if cell_id in DU_CELL_IDS and count >= 0:
                snapshots[cell_id] = (row_time, epoch, count, sleep_id)
        for cell_id, (row_time, epoch, count, snapshot_sleep_id) in snapshots.items():
            cell_rows = events.get(cell_id, {}).get((row_time, epoch), [])
            if count == 0:
                if (
                    cell_id != allow_empty_cell
                    or not allow_empty_sleep_transaction
                    or snapshot_sleep_id != str(allow_empty_sleep_transaction)
                ):
                    return {}, 'association_empty_du_without_matching_sleep_transaction'
                cell_rows = []
            elif len(cell_rows) != count:
                # A partially flushed trace is never used for a handover
                # decision; wait for the next complete snapshot instead.
                continue
            selected[cell_id] = (row_time, epoch, cell_rows)

        missing_cells = sorted({2, 3, 4} - set(selected))
        if missing_cells:
            return {}, f'association_incomplete_missing_cells:{missing_cells}'

        association_cells: dict[int, set[int]] = {2: set(), 3: set(), 4: set()}
        coverage: dict[int, set[int]] = {imsi: set() for imsi in range(1, 21)}
        evidence: dict[int, dict[str, object]] = {}
        for cell_id, (row_time, epoch, cell_rows) in selected.items():
            for _row_id, imsi, rnti, _cell_id in cell_rows:
                association_cells[cell_id].add(imsi)
                coverage[imsi].add(cell_id)
            evidence[cell_id] = {
                'cell_id': cell_id,
                'sim_time_s': row_time,
                'association_epoch': epoch,
                'imsis': sorted(association_cells[cell_id]),
            }

        missing_imsis = sorted(imsi for imsi, cells in coverage.items() if not cells)
        if missing_imsis:
            return {}, f'association_incomplete_missing_imsis:{missing_imsis}'
        allowed_empty = None if allow_empty_cell is None else int(allow_empty_cell)
        if any(
            not association_cells[cell_id] and cell_id != allowed_empty
            for cell_id in (2, 3, 4)
        ):
            return {}, 'association_incomplete_empty_du'
        return association_cells, evidence

    def _ingest_native_evidence_cycle(self, sim_time_s):
        """Import native traces even when the current decision is shadow.

        Importing is deliberately decoupled from E2 actuation.  This keeps
        the canonical association/PHY evidence warm before the first canary
        bundle while preventing a stale native row from being interpreted as
        a confirmation of an action that was never selected.
        """
        try:
            result = self.data_lake.ingest_native_evidence_cycle(
                campaign_id=os.environ.get('GREENRAN_CAMPAIGN_ID', ''),
                source_generation=os.environ.get('GREENRAN_NATIVE_SOURCE_GENERATION', ''),
            )
        except Exception as exc:
            result = {
                'schema': 'greenran.tasam.native_evidence_cycle.v1',
                'valid': False,
                'sim_time_s': sim_time_s,
                'error': f'{type(exc).__name__}: {exc}',
                'sources': [],
            }
        result['sim_time_s'] = sim_time_s
        self._last_native_evidence = result
        self.stats['native_evidence_imported'] += int(
            result.get('imported_control_rows', 0) or 0
        ) + int(result.get('imported_association_rows', 0) or 0)
        self.stats['native_evidence_errors'] += sum(
            1 for source in result.get('sources', [])
            if isinstance(source, dict) and source.get('error')
        )
        return result

    def _rapp_live_power_percent(self, decision):
        """Return the same-snapshot rApp candidate power for native shadow.

        A TA-SAM proposal is deliberately *not* a fallback value here.  In a
        shadow/hold decision the live candidate is the operational baseline;
        using ``tasam_power_percent`` would silently turn an observation into
        an unselected TA-SAM intervention.
        """
        fixed_native_power = os.environ.get('GREENRAN_TASAM_FIXED_NATIVE_POWER_PERCENT', '').strip()
        if fixed_native_power:
            try:
                requested_fixed = float(fixed_native_power)
            except (TypeError, ValueError):
                # A malformed baseline must not quietly become an economic
                # cut; use the only safe fixed reference.
                return 100
            if not math.isfinite(requested_fixed):
                return 100
            # Calibration directive (fixed-native arm): honor the exact
            # requested percent on the envelope's 5% grid instead of the
            # legacy {25, 60, 100} ladder, so physics calibration runs at
            # 45/70 keep their identity on the wire and in the readbacks.
            return float(min(100.0, max(25.0, 5.0 * round(requested_fixed / 5.0))))
        contract = decision.get('economic_action') or {}
        live = contract.get('live_candidate') or {}
        candidates = (
            live.get('power_percent'),
            decision.get('live_power_percent'),
            decision.get('rapp_live_power_percent'),
        )
        for value in candidates:
            try:
                return self._canonical_energy_command_power(value)
            except (TypeError, ValueError):
                continue
        energy_state = str(decision.get('energy_saver', 'UNKNOWN') or 'UNKNOWN').upper()
        return self._canonical_energy_command_power({
            'ALLOWED': 25.0,
            'CONDITIONAL': 60.0,
            'BLOCKED': 100.0,
        }.get(energy_state, 100.0))

    def send_tasam_control_bundle(self, decision, *, action_origin='ta_sam'):
        """Apply one policy through the dedicated E2 xApp.

        ``action_origin`` is intentionally explicit.  The dedicated actuator
        is also the operational path for rApp-live and ARMD+rApp safe actions
        in native-E2 campaigns, but only ``ta_sam`` may become an economic
        transition after independent ns-3 confirmation.
        """
        action_origin = str(action_origin or 'ta_sam').strip().lower()
        if action_origin not in {'ta_sam', 'rapp_live', 'armd_rapp_safety'}:
            raise ValueError(f'origem E2 desconhecida: {action_origin}')
        economic_candidate = action_origin == 'ta_sam'
        allocation = decision.get('resource_allocation') or {}
        self._tasam_control_sequence += 1
        decision['tasam_control_sequence'] = self._tasam_control_sequence
        contract = decision.get('economic_action') or {}
        if self._is_economic_contract(contract) and economic_candidate:
            contract['native_control_sequence'] = self._tasam_control_sequence
        v3_contract = contract.get('contract') == ECONOMIC_ACTION_V3_CONTRACT
        requested = (
            quantize_power_percent(
                decision.get('tasam_power_percent', decision.get('tasam_power_applied_percent', 100))
            )
            if economic_candidate else
            self._rapp_live_power_percent(decision)
        )
        if action_origin == 'armd_rapp_safety':
            requested = 100
        # The energetic rApp reference is also a native V3 producer.  It is
        # operational evidence, not TA-SAM economic replay, but it must use
        # the same per-DU bundle/readback contract so the two arms are
        # comparable.  Safety bundles remain v2 failsafe traffic.
        rapp_v3_candidate = (
            self._economic_contract_name() == ECONOMIC_ACTION_V3_CONTRACT
            and action_origin == 'rapp_live'
        )
        power_by_cell: dict[int, int] | None = None
        if (v3_contract and economic_candidate or rapp_v3_candidate) and action_origin != 'armd_rapp_safety':
            raw_power_by_cell = (
                decision.get('tasam_power_percent_by_cell')
                or (decision.get('tasam_proposal') or {}).get('power_percent_by_cell')
                or (allocation.get('power_percent_by_cell') if isinstance(allocation, dict) else None)
                or contract.get('proposed', {}).get('power_percent_by_cell')
                or contract.get('projected', {}).get('power_percent_by_cell')
                or contract.get('live_candidate', {}).get('power_percent_by_cell')
                or {cell_id: requested for cell_id in DU_CELL_IDS}
            )
            try:
                power_by_cell = normalize_power_by_cell(raw_power_by_cell)
            except (EconomicActionV3Error, TypeError, ValueError) as exc:
                # A malformed per-DU action is never repaired into an
                # economic action.  The existing ARMD+rApp failsafe remains
                # the only recovery path.
                decision['tasam_v3_action_error'] = str(exc)
                action_origin = 'armd_rapp_safety'
                economic_candidate = False
                requested = 100
        if action_origin == 'armd_rapp_safety':
            power_by_cell = {cell_id: 100 for cell_id in DU_CELL_IDS}
        staircase_failsafe = False
        if power_by_cell and (v3_contract or rapp_v3_candidate):
            power_by_cell, staircase_failsafe = self._apply_energy_staircase(
                decision,
                allocation,
                contract,
                power_by_cell,
            )
            if staircase_failsafe:
                action_origin = 'armd_rapp_safety'
                economic_candidate = False
        # V3 zero-power requests need legacy ``du_sleep`` evidence here.
        # V4 obtains that evidence from the explicit drain transaction below;
        # rejecting the raw ASGARD request before the drain starts would make
        # the adaptive sleep path unreachable.
        if (
            power_by_cell
            and any(power == 0 for power in power_by_cell.values())
            and not self._dynamic_floor_enabled()
        ):
            sleep_evidence = decision.get('du_sleep') or contract.get('projected', {}).get('du_sleep') or {}
            sleep_ready = (
                isinstance(sleep_evidence, dict)
                and bool(sleep_evidence.get('handover_confirmed'))
                and bool(sleep_evidence.get('pdcp_window_valid'))
                and bool(sleep_evidence.get('association_valid'))
                and float(sleep_evidence.get('pdcp_window_s', 0.0) or 0.0) >= 10.0
            )
            if not sleep_ready:
                decision['tasam_v3_action_error'] = 'du_sleep_requires_native_handover_and_10s_pdcp_window'
                action_origin = 'armd_rapp_safety'
                economic_candidate = False
                power_by_cell = {cell_id: 100 for cell_id in DU_CELL_IDS}
            requested = max(power_by_cell.values())
        snapshot = self.read_extended_metrics()
        sla_rows, demand_rows = build_runtime_ue_inputs(snapshot)
        sla_report = evaluate_sla_window(sla_rows)
        try:
            sim_time_s = float(
                ((snapshot or {}).get('sim_time_range') or {}).get('end', 0) or 0
            )
        except (TypeError, ValueError):
            sim_time_s = 0.0
        # O relógio de simulação alimenta o TTL das transições do Judge
        # (r29: sem este campo _sim_time() retornava None, o gate
        # current_sim >= issued_sim + ttl nunca disparava e TODOS os
        # pendentes econômicos acumulavam até o shutdown — 45/193
        # outcomes, seleção 20/90, not_promotable).
        decision['sim_time_s'] = sim_time_s
        sleep_state = dict(self._dynamic_floor_state.get('sleep_transition') or {})
        allow_empty_cell = (
            sleep_state.get('source_cell_id')
            if str(sleep_state.get('phase') or '').lower() in {'drain', 'commit'}
            else None
        )
        association_cells, association_evidence = self._native_association_cells(
            sim_time_s,
            allow_empty_cell=allow_empty_cell,
            allow_empty_sleep_transaction=(
                sleep_state.get('sleep_transaction_id') if allow_empty_cell is not None else None
            ),
        )
        # Cobertura parcial é mobilidade, não quebra de identidade: exige-se
        # apenas que PELO MENOS um UE tenha associação observada.  DUs vazios
        # ficam em 100% no bundle e IMSIs sem associação seguem no scheduler
        # stock; o gap fica registrado na evidência (r23/r24: exigir 3 DUs +
        # 20 IMSIs perfeitos mantendria a campanha em failsafe eterno).
        covered_imsis = (
            set().union(*(association_cells.get(cell, set()) for cell in (2, 3, 4)))
            if association_cells else set()
        )
        association_valid = bool(covered_imsis) and bool(association_cells)
        # Keep a primary cell only for the SLA/floor projector.  The E2
        # bundle below uses every native cell membership, including a real
        # MC overlap, so scheduler preparation is sent to the same DUs that
        # exposed the RNTI in the native trace.
        association_map = {}
        if association_valid:
            for imsi in sorted(covered_imsis):
                candidates = [
                    cell for cell in (2, 3, 4)
                    if imsi in association_cells.get(cell, set())
                ]
                association_map[imsi] = min(candidates)
        decision['tasam_association_evidence'] = {
            'valid': association_valid,
            'reason': '' if association_valid else str(association_evidence),
            'sim_time_s': sim_time_s,
            'mapping': association_map if association_valid else {},
            'cells': {
                str(cell): sorted(association_cells.get(cell, set()))
                for cell in (2, 3, 4)
            } if association_valid else {},
            'multi_cell_imsis': sorted(
                imsi for imsi in range(1, 21)
                if sum(imsi in association_cells.get(cell, set()) for cell in (2, 3, 4)) > 1
            ) if association_valid else [],
        }
        unassociated_imsis = []
        if association_valid:
            # Replace only the serving-cell field used by the E2 shield and
            # projection.  SLA/service classification remains canonical by
            # IMSI; radio control follows the observed RRC association.  Um
            # IMSI sem associação nesta janela mantém a célula original da
            # linha (scheduler stock) e é registrado como gap observável.
            for row in sla_rows + demand_rows:
                try:
                    row['cell_id'] = int(
                        association_map[int(row['imsi'])]
                    )
                except (KeyError, TypeError, ValueError):
                    try:
                        unassociated_imsis.append(int(row['imsi']))
                    except (TypeError, ValueError):
                        pass
        if unassociated_imsis:
            decision['tasam_association_evidence']['skipped_imsis'] = (
                sorted(set(unassociated_imsis))
            )
        # The simulator may advance much more slowly than the controller.  A
        # fixed 30-second sim-time warm-up would keep sending the emergency
        # bundle even after the collector has a complete real-PDCP window.
        # Readiness remains fail-closed: all 20 canonical UEs must have a
        # sufficiently long, connected, real-PDCP observation with traffic.
        pdcp_mature = real_pdcp_window_is_mature(sla_rows)
        warmup = sim_time_s < 30.0 and not pdcp_mature
        proposal_policies = []
        weight_by_service = {'camera': 5000, 'sensor': 5000, 'vehicle': 7500}
        # Resource proposals persist their verified per-UE envelope as
        # ``per_ue_floor``; the post-application view may additionally expose
        # ``per_ue_allocation``.  Accept both representations, but never
        # synthesize a missing floor or infer one from the command itself.
        for item in (
            allocation.get('per_ue_allocation')
            or allocation.get('per_ue_floor')
            or []
        ):
            imsi = int(item.get('imsi', 0) or 0)
            if not 1 <= imsi <= 20:
                continue
            proposal_policies.append({
                'imsi': imsi,
                'min_dl_share_bp': int(round(float(item.get('floor_share', 0.0) or 0.0) * 10000)),
                'min_ul_share_bp': 0,
                'surplus_weight_bp': weight_by_service.get(str(item.get('service', '')), 5000),
            })
        shield_sla = dict(sla_report)
        if warmup:
            shield_sla['pass'] = False
            shield_sla['warmup'] = True
        projected = project_safe_action(
            {'tx_power_percent': requested, 'ue_policies': proposal_policies},
            demand_rows,
            shield_sla,
        )
        sleep_transition = None
        sleep_failsafe = False
        if power_by_cell and self._dynamic_floor_enabled() and association_valid:
            power_by_cell, sleep_transition, sleep_failsafe = self._adaptive_sleep_transition(
                decision,
                power_by_cell,
                association_cells,
                sla_valid=bool(sla_report.get('pass')) and not warmup,
                pdcp_mature=pdcp_mature,
                sim_time_s=sim_time_s,
            )
            if sleep_transition is not None:
                decision['sleep_transition'] = dict(sleep_transition)
        # ``energy_saver=BLOCKED`` is the rApp's categorical advisory, not an
        # ARMD hard veto.  In assistant-only mode it must not prevent a valid
        # TA-SAM proposal from reaching E2; only the central safety detector,
        # an infeasible floor, or a failed shield may force the safe bundle.
        preexisting_failsafe = bool(
            action_origin == 'armd_rapp_safety'
            or decision.get('economic_safety_isolated')
            or decision.get('armd_hard_veto')
            or str(decision.get('armd_safety_level', '') or '').upper() == 'HARD_VETO'
            or allocation.get('failsafe_required')
            # Missing floor fields are not a veto by themselves.  The
            # projector below recomputes and verifies the floors from the
            # current real-PDCP snapshot.  Only an explicit prior failure is
            # allowed to force the safe bundle here.
            or allocation.get('per_ue_floor_feasible') is False
            or allocation.get('floor_verified') is False
            or not association_valid
        )
        failsafe = preexisting_failsafe or bool(projected.get('failsafe')) or sleep_failsafe
        if failsafe:
            # A failed native precondition is recovered by the same safe
            # origin as an ARMD hard veto.  It must never be attributed to a
            # shadow rApp command or to TA-SAM.
            action_origin = 'armd_rapp_safety'
            decision['native_operational_action_origin'] = action_origin
        decision['tasam_safety_shield'] = {
            **projected,
            'sla_report': sla_report,
            'warmup': warmup,
            'sim_time_s': sim_time_s,
        }
        cells = {2: [], 3: [], 4: []}
        if not failsafe:
            requested = int(projected['tx_power_percent'])
            for policy in projected.get('ue_policies') or []:
                imsi = int(policy['imsi'])
                native_cells = [
                    cell for cell in (2, 3, 4)
                    if imsi in association_cells.get(cell, set())
                ]
                if not native_cells:
                    # Sem associação nativa para este IMSI nesta janela:
                    # pular a política por-UE (scheduler stock serve a UE) e
                    # registrar o gap; não degrada o bundle inteiro.
                    skipped = decision['tasam_association_evidence'].setdefault(
                        'skipped_policy_imsis', []
                    )
                    if imsi not in skipped:
                        skipped.append(imsi)
                    continue
                for cell_id in native_cells:
                    cells[cell_id].append({
                        key: value for key, value in policy.items() if key != 'cell_id'
                    })
        if not failsafe:
            # Um DU sem UEs associados neste instante não é um gap de
            # identidade: é mobilidade.  Cortar a célula vazia não é
            # exigido pela política de energia e manter 100% nela é o
            # padrão seguro; degradar TODO o bundle para failsafe por
            # causa de uma célula vazia transformou mobilidade normal
            # em bloqueio econômico permanente (r23, 47/47 failsafe).
            empty_managed_cells = [
                cell_id for cell_id in (2, 3, 4) if not cells[cell_id]
            ]
            if len(empty_managed_cells) == 3:
                decision['tasam_association_evidence']['reason'] = (
                    'native_association_policy_coverage_incomplete'
                )
                decision['tasam_association_evidence']['valid'] = False
                decision['tasam_association_evidence']['mapping'] = {}
                failsafe = True
            else:
                decision['tasam_association_evidence']['empty_managed_cells'] = (
                    empty_managed_cells
                )
            allocation['per_ue_floor_feasible'] = True
            allocation['floor_verified'] = True
            allocation['floor_by_cell_bp'] = projected.get('floor_by_cell_bp', {})
            allocation['floor_estimator'] = 'offered_load_cqi_mcs_backlog_v3'
        else:
            requested = 100
        resource_caps = (
            self._apply_adaptive_resource_budget(decision, allocation, power_by_cell)
            if not failsafe and power_by_cell and self._dynamic_floor_enabled()
            else {cell: 10_000 for cell in DU_CELL_IDS}
        )
        now_ns = time.time_ns()
        if failsafe:
            reason = (
                'native_association_missing' if not association_valid else
                ('warmup' if warmup else
                projected.get('reason') or allocation.get('failsafe_reason') or 'blocked'
                )
            )
            bundle = failsafe_bundle(
                sequence=self._tasam_control_sequence,
                cell_ids=(2, 3, 4),
                reason=reason,
                sim_time_s=sim_time_s,
            )
        else:
            bundle = {
                'schema': CONTROL_BUNDLE_SCHEMA,
                'policy_id': str(decision.get('selected_proposal_id') or f"tasam-{now_ns}"),
                'sequence': self._tasam_control_sequence,
                'issued_at_ns': now_ns,
                'ttl_ms': 5000,
                'mode': 'combined',
                'sim_time_s': sim_time_s,
                'cells': [
                    {
                        'cell_id': cell_id,
                        # DU vazio (sem UEs associados) permanece em 100%:
                        # não há demanda a servir e o padrão seguro é não
                        # cortar célula sem readback de identidade.
                        'tx_power_percent': (
                            int((power_by_cell or {}).get(cell_id, requested))
                            if policies or (
                                isinstance(sleep_transition, dict)
                                and str(sleep_transition.get('phase') or '') == 'commit'
                                and int(sleep_transition.get('source_cell_id', -1)) == cell_id
                            )
                            else 100
                        ),
                        'ue_policies': policies,
                        'max_discretionary_dl_symbols_bp': int(resource_caps.get(cell_id, 0)),
                    }
                    for cell_id, policies in sorted(cells.items())
                ],
                'infra': build_physical_budget(
                    allocation.get('r_ai', 1.0),
                    compute_fraction=decision.get('tasam_infra_compute_budget'),
                    io_fraction=decision.get('tasam_infra_io_budget'),
                ),
            }
            # Dispositivos MC aparecem em dois DUs; sem a flag o cliente
            # rejeita o bundle como 'duplicate IMSI' (r24/r25).
            if any(
                sum(
                    1 for cell_policies in cells.values()
                    if any(int(item.get('imsi', 0)) == imsi for item in cell_policies)
                ) > 1
                for imsi in {int(p.get('imsi', 0)) for policies in cells.values() for p in policies}
            ):
                bundle['association_mode'] = 'native_rrc_mc_overlap'
            if (v3_contract or rapp_v3_candidate) and power_by_cell:
                bundle['schema'] = (
                    CONTROL_BUNDLE_V4_SCHEMA if self._dynamic_floor_enabled()
                    else CONTROL_BUNDLE_V3_SCHEMA
                )
                bundle['economic_action_contract'] = ECONOMIC_ACTION_V3_CONTRACT
                bundle['power_percent_by_cell'] = {
                    str(cell_id): (
                        int(power)
                        if cells.get(cell_id) or (
                            isinstance(sleep_transition, dict)
                            and str(sleep_transition.get('phase') or '') == 'commit'
                            and int(sleep_transition.get('source_cell_id', -1)) == cell_id
                        )
                        else 100
                    )
                    for cell_id, power in power_by_cell.items()
                }
                if any(
                    sum(
                        1 for cell_policies in cells.values()
                        if any(int(item.get('imsi', 0)) == imsi for item in cell_policies)
                    ) > 1
                    for imsi in range(1, 21)
                ):
                    bundle['association_mode'] = 'native_rrc_mc_overlap'
                bundle['mode'] = ECONOMIC_ACTION_V3_CONTRACT
                if self._dynamic_floor_enabled() and isinstance(sleep_transition, dict):
                    bundle['sleep_transition'] = dict(sleep_transition)
                elif any(power == 0 for power in power_by_cell.values()):
                    bundle['du_sleep'] = dict(decision.get('du_sleep') or {})
        if failsafe:
            # A safe bundle may be emitted after the Judge selected TA-SAM
            # (for example while the native association trace is still in
            # warm-up).  It is an ARMD+rApp safety action, not a TA-SAM
            # application.  Clear the optimistic pre-send flags before the
            # decision is persisted so ACKs and delayed feedback cannot make
            # this event eligible for the economic replay.
            decision['proposal_applied_exactly'] = False
            decision['assistant_rollout_applied'] = False
            decision['ta_sam_actuation_applied'] = False
            decision['tasam_actuation_applied'] = False
            decision['armd_actuation_applied'] = True
            decision['economic_execution_mode'] = 'safety_isolated'
            decision['economic_safety_isolated'] = True
            if economic_candidate:
                contract['economic_execution_mode'] = 'safety_isolated'
                contract['economic_safety_isolated'] = True
                contract['action_origin'] = 'armd_rapp_safety'
        # ``project_safe_action`` is the final Python-side projection that
        # becomes the E2 bundle.  Keep the economic contract synchronized with
        # that exact value before the actuator records the command; otherwise
        # the delayed confirmation path can compare the native readback with
        # an earlier categorical default (for example 50% vs 60%).
        if (
            self._is_economic_contract(contract)
            and economic_candidate
            and not failsafe
        ):
            projected_contract = dict(contract.get('projected') or {})
            projected_contract['power_percent'] = float(requested)
            contract['projected'] = projected_contract
        integration = os.environ.get('GREENRAN_TASAM_E2_CONTROL', '0').strip().lower() in {
            '1', 'true', 'yes', 'on'
        }
        trace_contract = contract
        if integration:
            # Every native bundle receives a distinct context.  The economic
            # contract is used only for a selected TA-SAM action; rApp-live
            # and safety bundles carry an operational token so they cannot be
            # mistaken for replay evidence later.
            if economic_candidate and self._is_economic_contract(contract):
                correlation_id = str(contract.get('correlation_id') or '')
                if not correlation_id:
                    correlation_id = (
                        f"economic:{decision.get('timestamp', int(time.time()))}:"
                        f"{self.cycle}:e2:{self._tasam_control_sequence}:"
                        f"{decision.get('snapshot_sequence_id', '')}"
                    )
                    contract['correlation_id'] = correlation_id
                    decision['economic_action_correlation_id'] = correlation_id
                trace_contract = contract
            else:
                correlation_id = (
                    f"operational:{action_origin}:{decision.get('timestamp', int(time.time()))}:"
                    f"{self.cycle}:e2:{self._tasam_control_sequence}:"
                    f"{decision.get('snapshot_sequence_id', '')}"
                )
                trace_contract = {
                    'native_control_sequence': self._tasam_control_sequence,
                    'correlation_id': correlation_id,
                }
                decision['native_operational_correlation_id'] = correlation_id
                decision['native_operational_action_origin'] = action_origin
            native_required = os.environ.get(
                'GREENRAN_NATIVE_EVIDENCE_VERSION', 'v3'
            ).strip() in {'v5', 'v6'}
            context_ok = self._write_native_control_context(decision, trace_contract)
            if native_required and not context_ok and not failsafe:
                # An optimization without the native identity bridge is not
                # measurable. Replace it with the existing safe package.
                failsafe = True
                action_origin = 'armd_rapp_safety'
                requested = 100
                decision['tasam_context_required'] = True
                decision['tasam_context_failure'] = (
                    decision.get('native_control_context_error')
                    or 'native_control_context_not_written'
                )
                bundle = failsafe_bundle(
                    sequence=self._tasam_control_sequence,
                    cell_ids=(2, 3, 4),
                    reason='native_control_context_unavailable',
                    sim_time_s=sim_time_s,
                )
        if self.cgroup_controller is not None:
            try:
                self.cgroup_controller.apply(bundle['infra'])
            except InfraBudgetError as exc:
                decision['tasam_infra_error'] = str(exc)
                allocation['failsafe_required'] = True
                allocation['failsafe_reason'] = 'physical_infra_budget_failed'
                if bundle.get('mode') != 'failsafe':
                    failsafe = True
                    action_origin = 'armd_rapp_safety'
                    decision['energy_saver'] = 'BLOCKED'
                    decision['tasam_power_applied_percent'] = 100
                    bundle = failsafe_bundle(
                        sequence=self._tasam_control_sequence,
                        cell_ids=(2, 3, 4),
                        reason='physical_infra_budget_failed',
                        sim_time_s=sim_time_s,
                    )
                    try:
                        self.cgroup_controller.apply(bundle['infra'])
                    except InfraBudgetError as recovery_exc:
                        decision['tasam_infra_recovery_error'] = str(recovery_exc)
        try:
            ack = self.tasam_control.send(bundle, integration=integration)
            decision['tasam_control_bundle'] = bundle
            decision['tasam_control_ack'] = ack
            decision['tasam_native_bundle_mode'] = str(bundle.get('mode') or '')
            decision['native_ack_cell_ack_complete'] = bool(ack.get('cell_ack_complete'))
            decision['native_ack_cell_results_count'] = (
                len(ack.get('cell_results') or [])
                if isinstance(ack, dict) else 0
            )
            decision['requested_power_by_cell'] = {
                int(cell.get('cell_id')): int(cell.get('tx_power_percent', 0))
                for cell in bundle.get('cells') or []
                if isinstance(cell, dict)
            }
            policy_applied = bool(ack.get('applied')) and not bool(ack.get('fallback'))
            decision['tasam_transport_ack'] = policy_applied
            cell_results = ack.get('cell_results') if isinstance(ack, dict) else None
            if isinstance(cell_results, list):
                confirmed_cells = {
                    int(item.get('cell_id')) for item in cell_results
                    if isinstance(item, dict)
                    and item.get('scheduler_ack') is True
                    and item.get('power_ack') is True
                    and item.get('cell_id') is not None
                }
                decision['e2_cell_ack_complete'] = confirmed_cells >= {2, 3, 4}
                decision['e2_cell_ack_cells'] = sorted(confirmed_cells)
            else:
                decision['e2_cell_ack_complete'] = False if os.environ.get(
                    'GREENRAN_NATIVE_EVIDENCE_VERSION', 'v3'
                ).strip() in {'v5', 'v6'} else None
            # Transport ACK is not native application evidence. The flag used
            # by replay and promotion becomes true only in the delayed
            # feedback path after the ns-3 observation is correlated.
            decision['ta_sam_actuation_applied'] = False
            if (
                self._is_economic_contract(contract)
                and integration
                and economic_candidate
            ):
                # The E2 ACK proves transport only.  Persist a pending command
                # so the later feedback cycle can bind the independent ns-3
                # observation to this exact control sequence and correlation.
                correlation_id = str(contract.get('correlation_id') or '')
                if not correlation_id:
                    correlation_id = (
                        f"economic:{decision.get('timestamp', int(time.time()))}:"
                        f"{self.cycle}:e2:{self._tasam_control_sequence}:"
                        f"{decision.get('snapshot_sequence_id', '')}"
                    )
                    contract['correlation_id'] = correlation_id
                    decision['economic_action_correlation_id'] = correlation_id
                live_observation = allocation.get('live_energy_observation') or {}
                try:
                    ru_count = int(live_observation.get('ru_count'))
                except (TypeError, ValueError):
                    ru_count = -1
                try:
                    mmwave_count = int(live_observation.get('mmwave_count'))
                except (TypeError, ValueError):
                    mmwave_count = -1
                application_status = (
                    'safety_isolated' if failsafe else
                    ('pending_confirmation' if policy_applied else 'rejected')
                )
                command_id = self.data_lake.record_energy_command(
                    command='ARMD_RAPP_SAFETY_BUNDLE' if failsafe else 'TA_SAM_E2_BUNDLE',
                    power_percent=requested,
                    ru_count=ru_count,
                    mmwave_count=mmwave_count,
                    reason=str(bundle.get('reason') or 'TA-SAM E2 control bundle'),
                    timestamp_ns=now_ns,
                    requested_power_percent=requested,
                    power_safety_override_reason=(
                        str(bundle.get('reason') or '') if failsafe else ''
                    ),
                    decision_id=decision.get('decision_id'),
                    action_correlation_id=correlation_id,
                    native_control_sequence=self._tasam_control_sequence,
                    action_origin='armd_rapp_safety' if failsafe else action_origin,
                    application_status=application_status,
                )
                contract['command_id'] = command_id
                contract['command_sent'] = bool(command_id)
                # Do not copy the requested command into ``applied``.  The
                # applied action is populated only after the independent
                # native PHY/scheduler evidence is correlated in the delayed
                # feedback path.
                contract['applied'] = {}
                contract['application_status'] = application_status
                decision['economic_application_status'] = application_status
                decision['economic_rejection_reason'] = (
                    '' if application_status == 'pending_confirmation'
                    else str(bundle.get('reason') or 'e2_control_not_applied')
                )
            elif integration:
                # Operational native E2 traffic is deliberately auditable but
                # never enters the TA-SAM economic contract.  It lets shadow
                # and hard-veto decisions reach ns-3 without creating a
                # phantom applied TA-SAM action or a pending replay row.
                live_observation = allocation.get('live_energy_observation') or {}
                try:
                    ru_count = int(live_observation.get('ru_count'))
                except (TypeError, ValueError):
                    ru_count = -1
                try:
                    mmwave_count = int(live_observation.get('mmwave_count'))
                except (TypeError, ValueError):
                    mmwave_count = -1
                operational_status = (
                    'safety_isolated' if action_origin == 'armd_rapp_safety' else
                    ('operational_transport_ack' if policy_applied else 'rejected')
                )
                command_id = self.data_lake.record_energy_command(
                    command=(
                        'ARMD_RAPP_SAFETY_BUNDLE'
                        if action_origin == 'armd_rapp_safety' else 'RAPP_LIVE_E2_BUNDLE'
                    ),
                    power_percent=requested,
                    ru_count=ru_count,
                    mmwave_count=mmwave_count,
                    reason=str(bundle.get('reason') or 'native operational E2 bundle'),
                    timestamp_ns=now_ns,
                    requested_power_percent=requested,
                    power_safety_override_reason=(
                        str(bundle.get('reason') or '')
                        if action_origin == 'armd_rapp_safety' else ''
                    ),
                    decision_id=decision.get('decision_id'),
                    action_correlation_id=str(trace_contract.get('correlation_id') or ''),
                    native_control_sequence=self._tasam_control_sequence,
                    action_origin=action_origin,
                    application_status=operational_status,
                )
                decision['native_operational_command_id'] = command_id
                decision['native_operational_application_status'] = operational_status
                decision['native_operational_action_origin'] = action_origin
                if action_origin == 'armd_rapp_safety':
                    decision['economic_execution_mode'] = 'safety_isolated'
                    decision['economic_safety_isolated'] = True
                    decision['ta_sam_actuation_applied'] = False
                    decision['tasam_actuation_applied'] = False
            allocation['per_ue_application_status'] = (
                'failsafe_stock_scheduler' if ack.get('fallback') else
                ('e2_ack_pending_confirmation' if ack.get('applied') else 'shadow_only')
            )
            self._record_dynamic_floor_application(
                decision,
                bundle,
                accepted=policy_applied,
                failsafe=bool(failsafe or ack.get('fallback')),
            )
            return policy_applied or not integration
        except ControlBundleError as exc:
            decision['tasam_control_error'] = str(exc)
            decision['native_ack_cell_ack_complete'] = False
            decision['ta_sam_actuation_applied'] = False
            # Um failsafe rejeitado não muda a categoria: ele JÁ É o estado
            # seguro (100%).  Flipar BLOCKED aqui apagava o verdict do
            # policy-engine sem tocar o motivo, produzindo o par
            # reason=ECO MODE / decision=BLOCKED que destruiu o r23.
            if str(bundle.get('mode') or '') != 'failsafe':
                decision['energy_saver'] = 'BLOCKED'
                decision['tasam_power_applied_percent'] = 100
            allocation['per_ue_application_status'] = 'e2_failed_failsafe'
            allocation['failsafe_required'] = True
            self._record_dynamic_floor_application(
                decision, bundle, accepted=False, failsafe=True,
            )
            if integration and bundle.get('mode') != 'failsafe':
                self._tasam_control_sequence += 1
                recovery = failsafe_bundle(
                    sequence=self._tasam_control_sequence,
                    cell_ids=(2, 3, 4),
                    reason='partial_or_timed_out_e2_transaction',
                    sim_time_s=sim_time_s,
                )
                decision['tasam_recovery_bundle'] = recovery
                try:
                    decision['tasam_recovery_ack'] = self.tasam_control.send(
                        recovery, integration=True
                    )
                except ControlBundleError as recovery_exc:
                    decision['tasam_recovery_error'] = str(recovery_exc)
            return False

    def send_native_operational_bundle(self, decision):
        """Route a non-economic rApp/ARMD action through native E2.

        This method is used for rollout shadow, Judge holds and hard-veto
        recovery.  It intentionally does not promote the action to TA-SAM
        evidence: only a selected TA-SAM proposal is eligible for that path.
        """
        hard_veto = bool(
            decision.get('economic_safety_isolated')
            or decision.get('armd_hard_veto')
            or str(decision.get('armd_safety_level', '') or '').upper() == 'HARD_VETO'
        )
        origin = 'armd_rapp_safety' if hard_veto else 'rapp_live'
        decision['tasam_e2_route'] = 'native_operational_bundle'
        decision['native_operational_action_origin'] = origin
        if origin == 'rapp_live':
            # Preserve an observational shadow as rApp-live; it must not be
            # labelled as an attempted/rejected TA-SAM command.
            decision.setdefault('economic_application_status', 'not_selected_shadow')
        return self.send_tasam_control_bundle(decision, action_origin=origin)

    def send_energy_command(self, decision):
        """
        Envia comando JSON para xApp Energy Saver.
        
        Converte a decisão do rApp em comando específico para o atuador.
        
        Args:
            decision: Decisão do rApp
        """
        if self._native_e2_actuation_enabled():
            # Never fall through to the legacy ENERGY Saver socket while the
            # dedicated E2 actuator owns this arm.  That socket is deliberately
            # absent in native-fidelity runs and cannot confirm ns-3 action.
            return self.send_native_operational_bundle(decision)

        energy_state = str(decision.get('energy_saver', 'UNKNOWN') or 'UNKNOWN').upper()
        reason = decision.get('reason', '')
        requested = decision.get('tasam_power_percent')
        try:
            requested = max(25.0, min(100.0, float(requested)))
        except (TypeError, ValueError):
            requested = {'ALLOWED': 25.0, 'CONDITIONAL': 60.0, 'BLOCKED': 100.0}.get(
                energy_state, 100.0
            )
        if os.environ.get('GREENRAN_FEASIBILITY_LOCK_POWER', '0').strip().lower() in {'1', 'true', 'yes', 'on'}:
            requested = 100.0
            decision['feasibility_power_lock'] = True
            decision['feasibility_power_lock_percent'] = 100.0
        requested = self._canonical_energy_command_power(requested)

        critical_safety = bool(
            decision.get('economic_safety_isolated')
            or decision.get('armd_hard_veto')
            or str(decision.get('armd_safety_level', '') or '').upper() == 'HARD_VETO'
        )
        contract = decision.get('economic_action') or {}
        selected_tasam = (
            decision.get('selected_assistant') in {'ta_sam', 'joint'}
            and bool(decision.get('proposal_applied_exactly', False))
            and bool(decision.get('tasam_operating_permission', False))
            and str(decision.get('armd_safety_level', '') or '').upper() in {'CLEAR', 'ADVISORY'}
        )
        # Economic safety is a one-sided guard in CLEAR/ADVISORY: TA-SAM may
        # reduce the rApp candidate, but it may never turn an optimization
        # decision into a higher-power command.  The candidate is captured
        # before ARMD/TA-SAM act on this same snapshot, so this does not use a
        # stale command from a previous window.  HARD_VETO remains authoritative
        # and deliberately bypasses this advisory ceiling below.
        if (
            selected_tasam
            and bool(decision.get('tasam_operating_permission', False))
            and not critical_safety
            and str(decision.get('armd_safety_level', '') or '').upper() in {'CLEAR', 'ADVISORY'}
            and self._is_economic_contract(contract)
        ):
            live_candidate = contract.get('live_candidate') or {}
            try:
                live_ceiling = self._canonical_energy_command_power(live_candidate['power_percent'])
            except (KeyError, TypeError, ValueError):
                live_ceiling = None
            if live_ceiling is not None and requested > live_ceiling:
                requested = live_ceiling
                decision['tasam_power_percent'] = requested
                decision['economic_power_ceiling_applied'] = True
                decision['economic_power_ceiling_source'] = 'rapp_live_candidate'
                projected = contract.get('projected') or {}
                projected['power_percent'] = requested
                contract['projected'] = projected
            else:
                decision['economic_power_ceiling_applied'] = False
                decision['economic_power_ceiling_source'] = 'rapp_live_candidate'

        def send_level(percent: float, why: str, override_reason: str = '') -> bool:
            """Send one canonical level and persist requested vs applied power."""
            fixed_native_power = os.environ.get(
                'GREENRAN_TASAM_FIXED_NATIVE_POWER_PERCENT', ''
            ).strip()
            if fixed_native_power:
                # Fixed-native calibration arm: the directive overrides the
                # ladder state machine entirely, on the envelope's 5% grid.
                try:
                    percent = float(
                        min(100.0, max(25.0, 5.0 * round(float(fixed_native_power) / 5.0)))
                    )
                except (TypeError, ValueError):
                    percent = 100.0
                decision['fixed_native_power_forced'] = percent
            else:
                percent = self._canonical_energy_command_power(percent)
            action = {
                25.0: 'POWER_DOWN_ECO',
                60.0: 'CONDITIONAL_REDUCE',
                100.0: 'FULL_POWER',
            }.get(percent, 'CONDITIONAL_REDUCE' if percent < 100.0 else 'FULL_POWER')
            decision['tasam_power_applied_percent'] = percent
            decision['power_safety_override_reason'] = override_reason
            decision['tasam_energy_action'] = action
            contract = decision.get('economic_action') or {}
            correlation_id = ''
            action_origin = (
                str(decision.get('selected_assistant') or 'rapp_live')
                if selected_tasam else 'rapp_live'
            )
            if decision.get('economic_safety_isolated'):
                action_origin = 'armd_rapp_safety'
            initial_status = str(decision.get('economic_application_status') or '')
            if self._is_economic_contract(contract):
                correlation_id = str(contract.get('correlation_id') or '')
                if not correlation_id:
                    correlation_id = (
                        f"economic:{decision.get('timestamp', int(time.time()))}:"
                        f"{self.cycle}:e2:{decision.get('tasam_control_sequence', '')}:"
                        f"{decision.get('snapshot_sequence_id', '')}"
                    )
                    contract['correlation_id'] = correlation_id
                    decision['economic_action_correlation_id'] = correlation_id
                if initial_status in {'projected', 'pending'}:
                    initial_status = 'command_sent'
            command_sent = self.energy_cmd.write_command(
                action,
                power_level=int(percent),
                reason=why,
                requested_power_percent=requested,
                power_safety_override_reason=override_reason,
                action_correlation_id=correlation_id,
                action_origin=action_origin,
                application_status=initial_status,
            )
            if self._is_economic_contract(contract):
                contract['command_sent'] = bool(command_sent)
                observation = (decision.get('resource_allocation') or {}).get('live_energy_observation') or {}
                intended = self._economic_action_snapshot(
                    decision.get('resource_allocation') or {},
                    percent,
                    ru_count=observation.get('ru_count'),
                    mmwave_count=observation.get('mmwave_count'),
                )
                command = self.data_lake.energy_command_for_correlation(correlation_id)
                if command:
                    intended['energy_command_id'] = command.get('id')
                    intended['power_w'] = command.get('power_w')
                    intended['ru_count'] = int(command.get('ru_count', intended['ru_count']) or 0)
                    intended['mmwave_count'] = int(command.get('mmwave_count', intended['mmwave_count']) or 0)
                # A command/ACK is transport evidence only.  The applied
                # snapshot is filled exclusively by the later native
                # observation path.
                contract['proposed'] = intended
                contract['sent'] = {
                    'power_percent': percent,
                    'action_origin': action_origin,
                    'command_sent': bool(command_sent),
                }
                contract['applied'] = {}
                intended['energy_model_version'] = str(
                    contract.get('energy_model_version')
                    or (contract.get('live_candidate') or {}).get('energy_model_version')
                    or decision.get('energy_model_version', '')
                    or ''
                )
                projected = contract.get('projected') or {}
                projected_power = projected.get('power_percent')
                try:
                    aligned = abs(float(projected_power) - percent) <= 5.0
                except (TypeError, ValueError):
                    aligned = False
                if not command_sent:
                    status, rejection = 'invalid', 'energy_command_not_persisted'
                elif decision.get('economic_safety_isolated'):
                    status, rejection = 'safety_isolated', str(
                        decision.get('economic_safety_isolation_reason') or
                        'critical_state_economic_isolation'
                    )
                elif not selected_tasam:
                    status, rejection = 'rejected', 'tasam_not_selected_or_not_applied'
                elif override_reason or not aligned:
                    status, rejection = 'safety_override', (
                        override_reason or 'projected_applied_power_divergence_over_5pp'
                    )
                else:
                    # Sending a command is not yet proof that ns-3 observed
                    # it.  The next real window confirms the exact
                    # correlation id before the transition can become
                    # economically applied.
                    status, rejection = 'pending_confirmation', ''
                self._sync_economic_contract_provenance(decision, contract)
                contract['application_status'] = status
                contract['rejection_reason'] = rejection
                contract['safety_override'] = status == 'safety_override'
                decision['economic_application_status'] = status
                decision['economic_rejection_reason'] = rejection
            return command_sent

        # Economic isolation is decided before rollout and owns the actuator
        # path: the safe ARMD+rApp package must be sent at full power even if
        # an intermediate categorical verdict still says ALLOWED/CONDITIONAL.
        # BLOCKED/critical safety follows the same fail-safe rule.
        if decision.get('economic_safety_isolated'):
            send_level(100.0, f"ECONOMIC SAFETY ISOLATION: {reason}", 'economic_safety_isolation')
        elif critical_safety:
            override_reason = '' if requested == 100.0 else 'critical_safety_envelope'
            send_level(100.0, f"BLOCKED/SAFETY: {reason}", override_reason)
        elif energy_state in {'CONDITIONAL', 'BLOCKED'}:
            if selected_tasam and decision.get('tasam_operating_permission', False):
                send_level(requested, f"CONDITIONAL TA-SAM {requested:.0f}%: {reason}")
            else:
                # No TA-SAM action was selected/applied: preserve the live
                # rApp candidate.  A categorical BLOCKED verdict alone does
                # not authorize ARMD to overwrite it with 100% power.
                # A CONDITIONAL verdict never cuts below the requested live
                # level (floor 60%): the legacy hardcoded 60% ignored the
                # rule-engine monitor levels (70/90%) and turned the vehicle
                # "margem protegida" guard into a power cut, breaking
                # cell-edge vehicle SLA in the 2026-09-17 V2X feasibility
                # runs (loss 2.7% -> 7% after the reduce).
                if energy_state == 'BLOCKED' or decision.get('priority_violation'):
                    fallback_power = max(float(requested or 100.0), 90.0)
                else:
                    fallback_power = max(60.0, min(float(requested or 100.0), 100.0))
                send_level(fallback_power, f"{energy_state} rApp fallback: {reason}")
        elif energy_state == 'ALLOWED':
            send_level(requested, f"ALLOWED TA-SAM {requested:.0f}%: {reason}")
        else:
            send_level(100.0, f"UNKNOWN: {reason}", 'unknown_state_fail_safe')
    
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

    def _observe_pending_judge_outcome(self, current_decision):
        """Process all delayed decisions against the newest real snapshot."""
        pending = list(self._pending_judge_decisions)
        if not pending and isinstance(self._pending_judge_decision, dict):
            pending = [self._pending_judge_decision]
        remaining = []
        for previous in pending:
            self._pending_judge_decision = previous
            self._observe_one_pending_judge_outcome(current_decision)
            if isinstance(self._pending_judge_decision, dict):
                remaining.append(self._pending_judge_decision)
        self._pending_judge_decisions = remaining
        self._pending_judge_decision = remaining[-1] if remaining else None

    def _observe_one_pending_judge_outcome(self, current_decision):
        """Score the previous assistant pair using the next real snapshot."""
        previous = self._pending_judge_decision
        if not isinstance(previous, dict) or not isinstance(current_decision, dict):
            return
        judge_result = previous.get('rapp_judge_result') or {}
        if not judge_result:
            self._pending_judge_decision = None
            return

        # Do not close an economic transition merely because the next
        # controller loop has arrived.  ns-3 confirmation is asynchronous and
        # can lag several wall-clock cycles behind the E2 ACK.
        pending_contract = previous.get('economic_action') or {}
        operational_only = bool(
            previous.get('native_operational_action_origin')
            or previous.get('tasam_e2_route') == 'native_operational_bundle'
        )
        pending_bundle = previous.get('tasam_control_bundle') or {}
        pending_cells_payload = pending_bundle.get('cells') or []
        pending_native_correlation = str(
            pending_contract.get('correlation_id')
            or previous.get('native_operational_correlation_id')
            or ''
        )
        if (
            (operational_only or self._is_economic_contract(pending_contract))
            and pending_cells_payload
        ):
            pending_cells = [cell.get('cell_id') for cell in pending_cells_payload]
            pending_command = self.data_lake.energy_command_for_correlation(
                pending_native_correlation
            )
            pending_power = (
                (pending_contract.get('applied') or {}).get('power_percent_by_cell')
                or (pending_contract.get('applied') or {}).get('power_percent')
                or pending_command.get('applied_power_percent')
                or pending_command.get('requested_power_percent')
            )
            if operational_only:
                pending_power_by_cell = {
                    str(cell.get('cell_id')): float(cell.get('tx_power_percent'))
                    for cell in pending_cells_payload
                    if cell.get('cell_id') is not None and cell.get('tx_power_percent') is not None
                }
                if pending_power_by_cell:
                    pending_power = pending_power_by_cell
            pending_sequence = (
                pending_contract.get('native_control_sequence')
                or previous.get('tasam_control_sequence')
            )
            native_probe = self.data_lake.confirm_native_control_observation(
                pending_sequence,
                pending_power,
                pending_cells,
                sim_time_s=pending_bundle.get('sim_time_s'),
                ttl_s=float((pending_bundle.get('ttl_ms') or 5000) / 1000.0),
                require_evidence_version=os.environ.get('GREENRAN_NATIVE_EVIDENCE_VERSION', 'v3'),
                source_generation=os.environ.get('GREENRAN_NATIVE_SOURCE_GENERATION') or None,
                campaign_id=os.environ.get('GREENRAN_CAMPAIGN_ID') or None,
                action_correlation_id=pending_native_correlation or None,
            )
            def _sim_time(payload):
                for value in (
                    payload.get('sim_time_s'),
                    (payload.get('resource_allocation') or {}).get('sim_time_s'),
                    ((payload.get('resource_allocation') or {}).get('live_energy_observation') or {}).get('sim_time_s'),
                    (payload.get('tasam_control_bundle') or {}).get('sim_time_s'),
                ):
                    try:
                        value = float(value)
                    except (TypeError, ValueError):
                        continue
                    if value == value and abs(value) != float('inf'):
                        return value
                return None
            issued_sim = _sim_time(previous)
            current_sim = _sim_time(current_decision)
            ttl = float((pending_bundle.get('ttl_ms') or 5000) / 1000.0)
            if not native_probe.get('valid') and not (
                issued_sim is not None and current_sim is not None and current_sim >= issued_sim + ttl
            ):
                # Keep it pending; the next loop may import the native row.
                return

        outcome = RAppJudge.derive_observed_outcome(current_decision, RUNTIME_CONFIG)
        if outcome.get('correct_verdict') == 'UNKNOWN':
            # Keep the decision pending through warm-up/no-data cycles.
            return
        feedback = self.rapp_judge.evaluate_outcome(judge_result, outcome)
        continuous = RAppJudge.compute_observed_error(previous, current_decision, RUNTIME_CONFIG)
        if self._tasam_full_control_enabled() and not (
            previous.get('tasam_proposal_valid') and previous.get('ta_sam_actuation_applied')
        ):
            # A missing/invalid TA-SAM action is an experimental failure.  It
            # is never converted into a heuristic action and receives the
            # maximum training penalty even if the next snapshot is healthy.
            continuous = {
                **continuous,
                'tasam_observed_error': 1.0,
                'tasam_continuous_reward': -1.0,
                'tasam_reward_source': 'invalid_tasam_proposal_max_penalty',
            }
        feedback.update(continuous)
        feedback['observed_metric_id'] = current_decision.get('metric_snapshot_id')
        # The v2 economic learner is scored exclusively from the applied
        # command.  Shadow savings stay available for diagnostics, but are
        # deliberately unable to create a positive replay reward.
        previous_resource = previous.get('resource_allocation') or {}
        contract = previous.get('economic_action') or {}
        contract_v2 = self._is_economic_contract(contract)
        bundle_present = bool((previous.get('tasam_control_bundle') or {}).get('cells'))
        shadow_observational = bool(
            operational_only
            or (
                str(previous.get('control_trial_mode') or '').strip().lower() == 'shadow'
                and not bundle_present
                and previous.get('tasam_e2_route') != 'native_bundle'
            )
        )
        correlation_id = str(
            contract.get('correlation_id')
            or previous.get('native_operational_correlation_id')
            or ''
        )
        command = self.data_lake.energy_command_for_correlation(correlation_id)
        live = contract.get('live_candidate') or {}
        applied = contract.get('applied') or {}
        application_status = str(
            contract.get('application_status')
            or previous.get('economic_application_status') or ''
        )
        # A command row and an E2 ACK prove transport only.  Economic replay
        # requires the independent ns-3 control trace to show every cell for
        # this exact transaction, power and simulation-time window.
        observed_energy = {}
        native_confirmation = {}
        if bundle_present and (contract_v2 or operational_only):
            bundle = previous.get('tasam_control_bundle') or {}
            expected_cells = [cell.get('cell_id') for cell in bundle.get('cells') or []]
            expected_power_by_cell = (
                applied.get('power_percent_by_cell')
                or (command or {}).get('applied_power_percent')
            )
            if operational_only:
                operational_power_by_cell = {
                    str(cell.get('cell_id')): float(cell.get('tx_power_percent'))
                    for cell in bundle.get('cells') or []
                    if cell.get('cell_id') is not None and cell.get('tx_power_percent') is not None
                }
                if operational_power_by_cell:
                    expected_power_by_cell = operational_power_by_cell
            native_confirmation = self.data_lake.confirm_native_control_observation(
                contract.get('native_control_sequence') or previous.get('tasam_control_sequence'),
                # The registered wire payload (bundle per-DU map) is the
                # authoritative expectation; the global summary may have
                # diverged from what actually went to E2 (r6g evidence).
                self.data_lake.pending_native_expected_power(correlation_id)
                or expected_power_by_cell
                or applied.get('power_percent', (command or {}).get('applied_power_percent')),
                expected_cells,
                sim_time_s=bundle.get('sim_time_s'),
                ttl_s=float((bundle.get('ttl_ms') or 5000) / 1000.0),
                require_evidence_version=os.environ.get('GREENRAN_NATIVE_EVIDENCE_VERSION', 'v3'),
                source_generation=os.environ.get('GREENRAN_NATIVE_SOURCE_GENERATION') or None,
                campaign_id=os.environ.get('GREENRAN_CAMPAIGN_ID') or None,
                action_correlation_id=correlation_id or None,
            )
            if native_confirmation.get('valid'):
                observed_energy = dict(native_confirmation)
                observed_energy['action_correlation_id'] = correlation_id
                observed_energy['applied_power_percent'] = native_confirmation.get('power_percent')
                observed_energy['ru_count'] = None
                observed_energy['mmwave_count'] = len(
                    native_confirmation.get('cell_ids') or []
                )
                try:
                    calibration = load_calibration()
                    if contract.get('contract') == ECONOMIC_ACTION_V3_CONTRACT:
                        observed_energy['power_w'] = sleep_state_power_by_cell_w(
                            calibration,
                            native_confirmation.get('power_percent_by_cell') or {},
                        )
                    else:
                        observed_energy['power_w'] = observed_radio_power_w(
                            calibration,
                            float(native_confirmation['power_percent']),
                        )
                except (TypeError, ValueError, KeyError):
                    observed_energy['power_w'] = None
                observed_energy['total_allocation'] = native_confirmation.get(
                    'native_allocation_fraction'
                )
                applied = dict(applied)
                projected_action = contract.get('projected') or {}
                try:
                    projected_ran = float(projected_action.get('ran_allocation'))
                    projected_ai = float(projected_action.get('ai_allocation'))
                    projected_total = float(projected_action.get('total_allocation'))
                    allocation_snapshot_valid = all(
                        math.isfinite(value) and value >= 0.0
                        for value in (projected_ran, projected_ai, projected_total)
                    )
                except (TypeError, ValueError):
                    projected_ran = projected_ai = projected_total = None
                    allocation_snapshot_valid = False
                applied.update({
                    'power_percent': native_confirmation.get('power_percent'),
                    'power_percent_by_cell': native_confirmation.get('power_percent_by_cell'),
                    'power_w': observed_energy.get('power_w'),
                    'ru_count': None,
                    'mmwave_count': observed_energy.get('mmwave_count'),
                    'total_allocation': native_confirmation.get('native_allocation_fraction'),
                    'native_cell_ids': native_confirmation.get('cell_ids'),
                    'native_active_ues': native_confirmation.get('active_ues'),
                    'native_active_dl_symbols': native_confirmation.get('active_dl_symbols'),
                    'native_dl_symbol_capacity': native_confirmation.get('active_dl_symbol_capacity'),
                    'native_allocation_fraction': native_confirmation.get('native_allocation_fraction'),
                    'native_evidence_version': native_confirmation.get('evidence_version'),
                    # The ns-3 trace's symbol fraction is a radio-utilization
                    # metric, not the RAN+AI budget used by the economic
                    # learner.  Once the native policy is active, the
                    # projected RAN/AI package is the applied consumer-side
                    # allocation; preserve the radio fraction separately for
                    # audit and never use it as the economic total.
                    'ran_allocation': projected_ran if allocation_snapshot_valid else None,
                    'ai_allocation': projected_ai if allocation_snapshot_valid else None,
                    'total_allocation': projected_total if allocation_snapshot_valid else None,
                    'allocation_observation_source': (
                        'native_policy_active' if allocation_snapshot_valid else ''
                    ),
                    'native_radio_allocation_fraction': native_confirmation.get(
                        'native_allocation_fraction'
                    ),
                    'application_observed': True,
                })
                contract['applied'] = applied
        actuation_confirmed = bool(contract_v2 and native_confirmation.get('valid'))
        # V2X adaptive reward is scored only after the correlated native
        # observation is available.  The first call to compute_observed_error
        # above intentionally has no energy evidence because the async E2/ns-3
        # readback has not arrived yet.  Recompose here so the trainer and the
        # persisted transition consume the same immutable reward snapshot.
        if (
            os.environ.get('GREENRAN_TASAM_REWARD_CONTRACT', '').strip()
            == 'greenran.tasam.v2x.reward_adaptive.v1'
            and os.environ.get('GREENRAN_TASAM_REWARD_ENERGY_ENABLED', '0').strip().lower()
            in {'1', 'true', 'yes', 'on'}
        ):
            observed_power_w = observed_energy.get('power_w')
            reference_power_w = None
            try:
                calibration = load_calibration()
                by_cell = native_confirmation.get('power_percent_by_cell') or {}
                active_cells = sum(
                    1 for cell in (2, 3, 4)
                    if float(by_cell.get(str(cell), by_cell.get(cell, 0.0)) or 0.0) > 0.0
                )
                if active_cells in {2, 3} and calibration.get('sleep_states'):
                    reference_power_w = sleep_state_power_w(
                        calibration, active_cells=active_cells, power_percent=100.0
                    )
                elif calibration.get('combined_model'):
                    combined = calibration['combined_model']
                    reference_power_w = float(combined.get('idle_w', 0.0)) + float(
                        combined.get('dynamic_w', 0.0)
                    )
                if reference_power_w is None or reference_power_w <= 0.0:
                    reference_power_w = observed_radio_power_w(calibration, 100.0)
            except (TypeError, ValueError, KeyError):
                reference_power_w = None
            energy_evidence = {
                'native': bool(native_confirmation.get('valid')),
                'e2_ack': bool(actuation_confirmed),
                'evidence_version': native_confirmation.get('evidence_version', ''),
                'action_correlation_id': correlation_id,
                'power_w': observed_power_w,
                'reference_power_w': reference_power_w,
                'cost': (
                    max(0.0, min(1.0, float(observed_power_w) / float(reference_power_w)))
                    if observed_power_w is not None and reference_power_w and reference_power_w > 0.0
                    else None
                ),
                'energy_model_version': str(
                    contract.get('energy_model_version') or previous.get('energy_model_version') or ''
                ),
            }
            previous['energy_evidence'] = energy_evidence
            current_decision['energy_evidence'] = energy_evidence
            refreshed = RAppJudge.compute_observed_error(previous, current_decision, RUNTIME_CONFIG)
            feedback.update(refreshed)
            feedback['energy_evidence'] = energy_evidence
        previous['ta_sam_actuation_applied'] = bool(
            actuation_confirmed
            and previous.get('selected_assistant') in {'ta_sam', 'joint'}
            and previous.get('proposal_applied_exactly', False)
            and previous.get('tasam_operating_permission', False)
        )
        observed_power_percent = observed_energy.get('applied_power_percent')
        if application_status == 'pending_confirmation' and actuation_confirmed:
            application_status = 'applied'
        components = feedback.get('tasam_error_components') or {}
        sla_penalty = max(
            float(components.get('service_error', 0.0) or 0.0),
            float(components.get('completion_shortfall_penalty', 0.0) or 0.0),
            float(components.get('underallocation_penalty', 0.0) or 0.0),
            float(components.get('tail_latency_error', 0.0) or 0.0),
            float(components.get('packet_loss_error', 0.0) or 0.0),
        )
        pdcp_metric_snapshot_id = current_decision.get('metric_snapshot_id')
        pdcp_coverage = self.data_lake.real_pdcp_loss_coverage(pdcp_metric_snapshot_id)
        latest_extended = self.data_lake.get_latest_extended_metrics(limit=1)
        pdcp_real = bool(
            pdcp_coverage.get('valid', False)
            and latest_extended
            and _is_real_pdcp_metric_row(latest_extended[0])
        )
        try:
            live_power_w = float(live.get('power_w'))
            # Economic reward is based on the power reconstructed from the
            # correlated ns-3 observation. A command row is only transport
            # provenance and cannot substitute for observed power.
            applied_power_w = float(observed_energy.get('power_w'))
            energy_reward = max(-1.0, min(1.0, (live_power_w - applied_power_w) / live_power_w))
            energy_valid = (
                actuation_confirmed
                and live_power_w > 0.0
                and applied_power_w > 0.0
            )
        except (TypeError, ValueError, ZeroDivisionError):
            live_power_w = applied_power_w = None
            energy_reward = 0.0
            energy_valid = False
        try:
            live_total = float(live.get('total_allocation'))
            # Do not use the native OFDM-symbol utilization as a proxy for
            # the economic RAN+AI allocation.  The two quantities have
            # different units; only the applied consumer-side package is
            # comparable with the same-snapshot rApp candidate.
            applied_total = float(applied.get('total_allocation'))
            allocation_reward = max(
                -1.0, min(1.0, (live_total - applied_total) / live_total)
            )
            allocation_valid = (
                live_total > 0.0
                and applied_total >= 0.0
                and math.isfinite(applied_total)
                and str(applied.get('allocation_observation_source') or '')
                == 'native_policy_active'
            )
        except (TypeError, ValueError, ZeroDivisionError):
            live_total = applied_total = None
            allocation_reward = 0.0
            allocation_valid = False
        projected = contract.get('projected') or {}
        topology_valid = bool(
            previous_resource.get('topology_valid', False)
            and pdcp_coverage.get('topology_valid', False)
        )
        try:
            projected_power = float(projected.get('power_percent'))
            applied_power = float(applied.get('power_percent', (command or {}).get('applied_power_percent')))
            aligned = abs(projected_power - applied_power) <= 5.0
        except (TypeError, ValueError):
            aligned = False
        invalid_reason = ''
        if shadow_observational:
            # A shadow decision intentionally has no economic bundle.  It is
            # an observational sample, not a failed application and must not
            # become an invalid economic transition or a learner regression.
            invalid_reason = 'rollout_shadow_observational'
            application_status = 'not_selected_shadow'
        elif not contract_v2:
            invalid_reason = 'economic_action_contract_missing'
        elif not bundle_present:
            invalid_reason = 'economic_control_bundle_missing'
        elif not actuation_confirmed:
            invalid_reason = 'actuation_not_confirmed_by_correlated_observation'
        elif application_status != 'applied':
            invalid_reason = application_status or 'economic_action_not_applied'
        elif not previous.get('ta_sam_actuation_applied', False):
            invalid_reason = 'tasam_actuation_not_applied'
        elif previous.get('tasam_fallback_used', False):
            invalid_reason = 'tasam_fallback_used'
        elif not previous.get('tasam_checkpoint_valid', False):
            invalid_reason = 'tasam_checkpoint_invalid'
        elif not command or not correlation_id:
            invalid_reason = 'energy_command_not_correlated'
        elif not energy_valid:
            invalid_reason = 'applied_energy_invalid'
        elif not allocation_valid:
            invalid_reason = 'applied_allocation_invalid'
        elif not pdcp_real:
            invalid_reason = str(pdcp_coverage.get('reason') or 'real_pdcp_loss_missing')
        elif not topology_valid:
            invalid_reason = 'topology_invalid'
        elif not previous_resource.get('floor_feasible', True):
            invalid_reason = 'sla_floor_infeasible'
        elif outcome.get('critical_violation'):
            invalid_reason = 'critical_sla_violation'
        elif not aligned:
            invalid_reason = 'projected_applied_power_divergence_over_5pp'
        economic_valid = not invalid_reason
        self._advance_dynamic_floor_from_observation(
            previous,
            current_decision,
            economic_valid=economic_valid,
            actuation_confirmed=actuation_confirmed,
        )
        if (
            self._energy_staircase_enabled()
            and not self._dynamic_floor_enabled()
            and previous.get('energy_staircase_applied')
        ):
            staircase_healthy = bool(
                economic_valid
                and not outcome.get('critical_violation')
                and sla_penalty <= 0.0
                and actuation_confirmed
            )
            self._energy_staircase_state['last_observation_healthy'] = staircase_healthy
            self._energy_staircase_state['last_observation_critical'] = bool(
                outcome.get('critical_violation') or not economic_valid
            )
            previous['energy_staircase_observation'] = {
                'healthy': staircase_healthy,
                'critical': bool(outcome.get('critical_violation') or not economic_valid),
                'economic_transition_eligible': economic_valid,
            }
        if outcome.get('critical_violation') or not previous_resource.get('floor_feasible', True):
            sla_penalty = 1.0
        energy_delta = float(energy_reward)
        allocation_delta = float(allocation_reward)
        economic_neutral_noop = bool(
            economic_valid
            and abs(energy_delta) <= 0.005
            and abs(allocation_delta) <= 0.001
        )
        economic_training_eligible = bool(
            economic_valid
            and application_status == 'applied'
            and not previous.get('economic_safety_isolated', False)
            and not economic_neutral_noop
        )
        economic_promotion_eligible = bool(
            economic_training_eligible
            and energy_delta > 0.005
            and allocation_delta >= -0.001
            and sla_penalty <= 0.0
        )
        if shadow_observational:
            economic_execution_mode = 'shadow_observational'
        elif previous.get('economic_safety_isolated', False):
            economic_execution_mode = 'safety_isolated'
        elif economic_neutral_noop:
            economic_execution_mode = 'economic_neutral_noop'
        else:
            economic_execution_mode = str(
                contract.get('economic_execution_mode') or 'economic'
            )
        reward_detail = realized_economic_reward(
            energy_reward if economic_valid else 0.0,
            allocation_reward if economic_valid else 0.0,
            soft_sla_shortfall_penalty=(sla_penalty if not outcome.get('critical_violation') else 0.0),
            hard_safety_penalty=(1.0 if outcome.get('critical_violation') else 0.0),
        )
        online_reward = reward_detail['reward'] if economic_valid else 0.0
        contract['economic_transition_eligible'] = economic_valid
        contract['actuation_confirmed'] = actuation_confirmed
        contract['actuation_confirmation_source'] = (
            'correlated_energy_observation' if actuation_confirmed else ''
        )
        contract['observed_power_percent'] = observed_power_percent
        contract['observed_power_w'] = observed_energy.get('power_w')
        contract['observed_ru_count'] = observed_energy.get('ru_count')
        contract['observed_mmwave_count'] = observed_energy.get('mmwave_count')
        contract['native_observation'] = native_confirmation
        # The command belongs to the decision that issued the bundle.  The
        # following snapshot is the PDCP/effect observation and is tracked
        # separately as pdcp_metric_snapshot_id; it must not overwrite the
        # decision provenance of the native confirmation.
        contract['confirmation_decision_id'] = previous.get('decision_id')
        self._sync_economic_contract_provenance(previous, contract)
        contract['application_status'] = application_status
        contract['economic_execution_mode'] = economic_execution_mode
        contract['economic_safety_isolated'] = bool(previous.get('economic_safety_isolated', False))
        contract['economic_safety_isolation_reason'] = str(
            previous.get('economic_safety_isolation_reason') or
            contract.get('economic_safety_isolation_reason') or ''
        )
        contract['economic_training_eligible'] = economic_training_eligible
        contract['economic_promotion_eligible'] = economic_promotion_eligible
        contract['outcome_invalid_reason'] = invalid_reason
        contract['realized_energy_saving_fraction'] = round(energy_reward, 8)
        contract['realized_allocation_saving_fraction'] = round(allocation_reward, 8)
        contract['pdcp_loss_coverage'] = pdcp_coverage
        contract['topology_valid'] = topology_valid
        contract['pdcp_metric_snapshot_id'] = pdcp_metric_snapshot_id
        previous['economic_transition_eligible'] = economic_valid
        previous['economic_application_status'] = application_status
        previous['economic_execution_mode'] = economic_execution_mode
        previous['economic_training_eligible'] = economic_training_eligible
        previous['economic_promotion_eligible'] = economic_promotion_eligible
        previous['economic_action'] = contract
        previous['economic_outcome_invalid_reason'] = invalid_reason
        previous['pdcp_loss_coverage'] = pdcp_coverage
        previous['topology_valid'] = topology_valid
        previous['pdcp_metric_snapshot_id'] = pdcp_metric_snapshot_id
        if correlation_id:
            self.data_lake.update_energy_command_application(
                correlation_id,
                'applied' if actuation_confirmed else 'invalid',
                actuation_confirmed=actuation_confirmed,
                confirmation_source=(
                    'correlated_energy_observation' if actuation_confirmed else ''
                ),
                observed_power_percent=observed_power_percent,
                observed_power_w=observed_energy.get('power_w'),
                observed_ru_count=observed_energy.get('ru_count'),
                observed_mmwave_count=observed_energy.get('mmwave_count'),
                confirmation_decision_id=previous.get('decision_id'),
                observed_allocation_fraction=observed_energy.get('native_allocation_fraction'),
                native_active_dl_symbols=observed_energy.get('active_dl_symbols'),
                native_dl_symbol_capacity=observed_energy.get('active_dl_symbol_capacity'),
                native_cell_ids=observed_energy.get('cell_ids'),
                native_observation_version=observed_energy.get('evidence_version'),
            )
        feedback.update({
            'tasam_online_reward': round(online_reward, 6),
            'tasam_energy_reward': round(energy_reward if economic_valid else 0.0, 6),
            'tasam_allocation_reward': round(allocation_reward if economic_valid else 0.0, 6),
            'tasam_sla_penalty': round(max(0.0, min(1.0, sla_penalty)), 6),
            'tasam_soft_sla_shortfall_penalty': reward_detail['soft_sla_shortfall_penalty'],
            'tasam_hard_safety_penalty': reward_detail['hard_safety_penalty'],
            'economic_reward_contract': 'realized_bilateral_v3',
            'tasam_economic_feedback_valid': economic_valid,
            'economic_action_contract': contract.get('contract', ''),
            'economic_transition_eligible': economic_valid,
            'economic_execution_mode': economic_execution_mode,
            'economic_safety_isolated': bool(previous.get('economic_safety_isolated', False)),
            'economic_safety_isolation_reason': contract.get('economic_safety_isolation_reason', ''),
            'economic_training_eligible': economic_training_eligible,
            'economic_promotion_eligible': economic_promotion_eligible,
            'economic_invalid_reason': invalid_reason,
            'economic_outcome_invalid_reason': invalid_reason,
            'economic_application_status': application_status,
            'actuation_confirmed': actuation_confirmed,
            'actuation_confirmation_source': (
                'correlated_energy_observation' if actuation_confirmed else ''
            ),
            'observed_power_percent': observed_power_percent,
            'observed_power_w': observed_energy.get('power_w'),
            'observed_ru_count': observed_energy.get('ru_count'),
            'observed_mmwave_count': observed_energy.get('mmwave_count'),
            'confirmation_decision_id': previous.get('decision_id'),
            'economic_action': contract,
            'energy_model_version': str(
                contract.get('energy_model_version')
                or (contract.get('live_candidate') or {}).get('energy_model_version')
                or previous.get('energy_model_version', '')
                or ''
            ),
            'energy_command_id': (command or {}).get('id'),
            'live_power_percent': live.get('power_percent'),
            'live_power_w': live_power_w,
            'live_total_allocation': live_total,
            'applied_power_percent': applied.get('power_percent', (command or {}).get('applied_power_percent')),
            'applied_power_w': applied_power_w,
            'applied_ran_allocation': applied.get('ran_allocation'),
            'applied_ai_allocation': applied.get('ai_allocation'),
            'applied_total_allocation': applied_total,
            'realized_energy_saving_fraction': round(energy_reward, 6),
            'realized_allocation_saving_fraction': round(allocation_reward, 6),
            'economic_action_alignment_valid': aligned,
            'pdcp_loss_coverage': pdcp_coverage,
            'topology_valid': topology_valid,
            'pdcp_metric_snapshot_id': pdcp_metric_snapshot_id,
        })
        # Keep the strong categorical training signal separate from the
        # canonical audit credit.  A healthy resource observation cannot
        # compensate for a wrong ordinal state.
        strong_category_credit = float(
            feedback.get(
                'tasam_training_category_credit',
                feedback.get('tasam_category_credit', -2.0),
            )
            or 0.0
        )
        continuous_reward = max(
            -1.0,
            min(1.0, float(feedback.get('tasam_continuous_reward', 0.0) or 0.0)),
        )
        feedback['tasam_training_reward'] = round(
            min(continuous_reward, strong_category_credit), 6
        )
        feedback['tasam_action_applied'] = bool(previous.get('ta_sam_actuation_applied', False))
        decision_stage_name = str(
            previous.get('collection_event_stage_name') or ''
        ).strip()
        observed_stage_name = str(
            current_decision.get('collection_event_stage_name') or ''
        ).strip()
        stage_boundary_feedback = bool(
            previous.get('collection_event_transition_key')
            and current_decision.get('collection_event_transition_key')
            and previous.get('collection_event_transition_key')
            != current_decision.get('collection_event_transition_key')
        )
        if not stage_boundary_feedback:
            # Legacy/live contexts without a transition key retain the old
            # name-based boundary semantics.
            stage_boundary_feedback = bool(
                decision_stage_name
                and observed_stage_name
                and decision_stage_name != observed_stage_name
            )
        feedback.update({
            'decision_stage_name': decision_stage_name,
            'observed_stage_name': observed_stage_name,
            'stage_boundary_feedback': stage_boundary_feedback,
            'decision_stage_transition_key': previous.get('collection_event_transition_key', ''),
            'observed_stage_transition_key': current_decision.get('collection_event_transition_key', ''),
            'nominal_expected_verdict': expected_verdict_for_stage(decision_stage_name),
        })
        outcome.update({
            'observed_metric_id': current_decision.get('metric_snapshot_id'),
            'metric_snapshot_id': current_decision.get('metric_snapshot_id'),
            'decision_stage_name': decision_stage_name,
            'observed_stage_name': observed_stage_name,
            'stage_boundary_feedback': stage_boundary_feedback,
            'decision_stage_transition_key': previous.get('collection_event_transition_key', ''),
            'observed_stage_transition_key': current_decision.get('collection_event_transition_key', ''),
            'nominal_expected_verdict': expected_verdict_for_stage(decision_stage_name),
        })
        previous['rapp_judge_feedback'] = feedback
        self.data_lake.record_judge_outcome(
            previous,
            feedback,
            observation=outcome,
            observed_timestamp=current_decision.get('timestamp'),
            # The complete pair is passed only in memory.  DataLake writes
            # its compact economic representation to the canonical replay
            # table and never duplicates it inside judge_outcome_history.
            transition={
                'decision': previous,
                'next_decision': current_decision,
            },
        )
        if contract_v2 and correlation_id:
            self.data_lake.resolve_pending_native_action(
                correlation_id,
                'applied' if actuation_confirmed else 'expired',
                confirmation=native_confirmation,
                reason='' if actuation_confirmed else invalid_reason,
            )
        print(
            '[rApp Judge] feedback: '
            f"correto={feedback.get('correct_verdict')} "
            f"ARMD={feedback.get('armd_credit', 0):+.2f} "
            f"TA-SAM={feedback.get('tasam_credit', 0):+.2f} "
            f"({outcome.get('reason', '')})"
        )
        self._pending_judge_decision = None

    def _build_shutdown_observation(self, metric_snapshot):
        """Build a real next-observation record for the final pending decision."""
        snapshot = metric_snapshot if isinstance(metric_snapshot, dict) else {}
        slicer_intent = self.read_slicer_intent()
        return {
            'timestamp': int(time.time()),
            'metric_snapshot_id': snapshot.get('metric_snapshot_id') or snapshot.get('id'),
            'observed_metric_id': snapshot.get('metric_snapshot_id') or snapshot.get('id'),
            'camera_metrics': self._get_camera_sla_metrics(slicer_intent),
            'vehicle_metrics': self._get_vehicle_metrics(),
            'app2_metrics': self._get_app2_gateway_metrics(),
            'network_health': self.data_lake.get_network_health(window_minutes=5),
            'slice_state': {'background': {'completion_ratio': 1.0}},
            'collection_event_stage_name': (
                self._pending_judge_decision or {}
            ).get('collection_event_stage_name', ''),
            'collector_mode': snapshot.get('collector_mode', ''),
        }

    def _drain_pending_judge_feedback(self, timeout_seconds=5.0):
        """Wait briefly for a fresh real snapshot, otherwise persist a gap."""
        pending = list(self._pending_judge_decisions)
        if not pending and isinstance(self._pending_judge_decision, dict):
            pending = [self._pending_judge_decision]
        if not pending:
            return True
        previous_metric_id = pending[0].get('metric_snapshot_id')
        deadline = time.time() + max(0.0, float(timeout_seconds))
        while time.time() < deadline:
            # The collector publishes JSON, while the Data Lake is normally
            # imported by the decision loop.  During shutdown there is no
            # next decision loop, so explicitly import the live snapshot
            # before checking for a fresh metric.
            try:
                self.record_current_metrics(
                    self.read_slicer_intent(), self.read_energy_intent()
                )
            except Exception:
                pass
            latest = self.data_lake.get_latest_extended_metrics(limit=1)
            if latest:
                current = latest[0]
                current_id = current.get('metric_snapshot_id') or current.get('id')
                is_real = _is_real_pdcp_snapshot(current)
                if current_id and current_id != previous_metric_id and is_real:
                    self._observe_pending_judge_outcome(
                        self._build_shutdown_observation(current)
                    )
                    return not self._pending_judge_decisions and self._pending_judge_decision is None
            time.sleep(min(0.25, max(0.01, self.interval / 4.0)))

        for pending_decision in pending:
            self.data_lake.record_missing_judge_outcome(
                pending_decision, 'shutdown_before_next_real_snapshot'
            )
        self._pending_judge_decision = None
        self._pending_judge_decisions = []
        return False

    def record_decision(self, decision):
        """Registra decisão no Data Lake e log estruturado."""
        decision_id = self.data_lake.record_decision(decision)
        if decision_id is not None:
            decision['decision_id'] = decision_id

        # Structured logging to JSONL file
        try:
            ml_prediction = decision.get('ml_rf_prediction', {})
            drl_prediction = decision.get('drl_prediction', {})
            log_entry = {
                'decision_id': decision.get('decision_id'),
                'snapshot_sequence_id': decision.get('snapshot_sequence_id'),
                'pairing_schedule_id': decision.get('pairing_schedule_id', ''),
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
                'ta_sam_actuation_applied': decision.get('ta_sam_actuation_applied', False),
                # Evidência da cadeia de permissão/rollout (ausentes do log até
                # 2026-09-25: o diagnóstico do r19 leu None para um valor que
                # nunca foi gravado).  Sem estes campos é impossível auditar
                # por que uma decisão não atuou economicamente.
                'armd_safety_level': decision.get('armd_safety_level'),
                'tasam_operating_permission': decision.get('tasam_operating_permission'),
                'native_bundle_mode': decision.get('tasam_native_bundle_mode', ''),
                'native_ack_cell_ack_complete': decision.get(
                    'native_ack_cell_ack_complete'
                ),
                'native_ack_cell_results_count': decision.get(
                    'native_ack_cell_results_count'
                ),
                'native_requested_power_by_cell': decision.get(
                    'requested_power_by_cell'
                ) or {},
                'tasam_control_error': str(decision.get('tasam_control_error') or ''),
                'tasam_infra_error': str(decision.get('tasam_infra_error') or ''),
                'assoc_valid': bool(
                    (decision.get('tasam_association_evidence') or {}).get('valid')
                ),
                'assoc_reason': str(
                    (decision.get('tasam_association_evidence') or {}).get('reason') or ''
                ),
                'assoc_empty_cells': (
                    decision.get('tasam_association_evidence') or {}
                ).get('empty_managed_cells') or [],
                'economic_rejection_reason': str(
                    decision.get('economic_rejection_reason') or ''
                ),
                'assistant_rollout_fraction': decision.get('assistant_rollout_fraction'),
                'assistant_rollout_allowed': decision.get('assistant_rollout_allowed'),
                'assistant_rollout_applied': decision.get('assistant_rollout_applied'),
                'proposal_applied_exactly': decision.get('proposal_applied_exactly', False),
                'economic_execution_mode': decision.get('economic_execution_mode', ''),
                'economic_safety_isolated': decision.get('economic_safety_isolated', False),
                'control_trial_mode': decision.get('control_trial_mode', ''),
                'effective_policy_algorithm': decision.get('effective_policy_algorithm', ''),
                'effective_policy_source': decision.get('effective_policy_source', ''),
                'control_trial_reason': decision.get('control_trial_reason', ''),
                'collection_event_stage_name': decision.get('collection_event_stage_name', ''),
                'collection_event_target_domain': decision.get('collection_event_target_domain', ''),
                'collection_event_cycle': decision.get('collection_event_cycle', 0),
                'collection_event_stage_index': decision.get('collection_event_stage_index', 0),
                'collection_event_generated_at': decision.get('collection_event_generated_at', 0),
                'collection_event_stage_authoritative': decision.get('collection_event_stage_authoritative', False),
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
            if self.decision_target > 0:
                try:
                    decision_count = int(
                        self.data_lake.get_database_stats().get('decisions_count', 0) or 0
                    )
                except Exception:
                    decision_count = 0
                if decision_count >= self.decision_target:
                    print(
                        f"[rApp] Alvo de decisões atingido internamente: "
                        f"{decision_count}/{self.decision_target}"
                    )
                    self.running = False
                    break
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
                    self._last_decision_sim_time = None
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

            # Keep native ns-3 evidence synchronized during shadow as well as
            # during actuation.  This is an import-only operation; confirmation
            # remains gated by a pending, selected TA-SAM action below.
            native_evidence = self._ingest_native_evidence_cycle(new_sim_time)
            
            # 1. Lê intenções dos xApps
            slicer_intent = self.read_slicer_intent()
            energy_intent = self.read_energy_intent()
            vehicle_intent = self.read_vehicle_intent()
            
            # 2. Registra métricas no Data Lake
            metric_snapshot_id = self.record_current_metrics(slicer_intent, energy_intent)

            # Do not create a decision until a fresh real-PDCP snapshot exists.
            # Re-reading the same JSON snapshot must not create duplicate
            # decisions or accidentally pair multiple actions with one metric.
            if (
                metric_snapshot_id is None
                or not self._real_metric_snapshot_id_is_valid(metric_snapshot_id)
            ):
                self.stats['pdcp_warmup_skips'] += 1
                time.sleep(self.interval)
                continue
            if metric_snapshot_id == self._last_real_metric_snapshot_id:
                self.stats['duplicate_metric_skips'] += 1
                time.sleep(self.interval)
                continue
            min_sim_advance = float(os.environ.get('GREENRAN_TASAM_MIN_SIM_ADVANCE', '0.5') or 0.5)
            if (
                new_sim_time > 0.0
                and self._last_decision_sim_time is not None
                and new_sim_time - self._last_decision_sim_time < min_sim_advance
            ):
                self.stats['duplicate_metric_skips'] += 1
                time.sleep(self.interval)
                continue
            self._last_real_metric_snapshot_id = metric_snapshot_id
            if new_sim_time > 0.0:
                self._last_decision_sim_time = new_sim_time
            
            # 3. Toma decisão estratégica
            decision = self.make_decision(slicer_intent, energy_intent, vehicle_intent)
            decision['native_evidence_ingest'] = native_evidence
            if metric_snapshot_id is not None:
                decision['metric_snapshot_id'] = metric_snapshot_id
                # One control decision is keyed to exactly one fresh real
                # PDCP snapshot. This is independent of wall-clock seconds.
                decision['snapshot_sequence_id'] = f"pdcp:{int(metric_snapshot_id)}"
                pairing_schedule_id = str(
                    os.environ.get('GREENRAN_PAIRING_SCHEDULE_ID', '') or ''
                ).strip()
                if pairing_schedule_id:
                    try:
                        decision_ordinal = int(
                            self.data_lake.get_database_stats().get('decisions_count', 0) or 0
                        ) + 1
                    except Exception:
                        decision_ordinal = self.cycle
                    # The ordinal is local to this isolated arm, while the
                    # schedule id is shared by both arms. Together they make
                    # a stable cross-arm snapshot key without changing the
                    # legacy pdcp:<metric_id> contract outside paired runs.
                    decision['pairing_schedule_id'] = pairing_schedule_id
                    decision['snapshot_sequence_id'] = (
                        f"{pairing_schedule_id}:snapshot:{int(decision_ordinal):06d}"
                    )

            # A decisão anterior é avaliada somente agora, com a próxima
            # observação real disponível. Isso evita premiar um ARMD
            # conservador apenas por ter imposto um veto preventivo.
            self._observe_pending_judge_outcome(decision)
            
            # 3.1 Controla xApps baseado na decisão
            xapp_status = self.decide_xapp_activation(decision)
            
            # 4. Resolve the actuator path before persisting the decision.
            # Native-E2 campaigns have exactly one actuator: a selected
            # TA-SAM policy is economic, while rApp-live/shadow and ARMD
            # safety are operational bundles on that same E2 path.  The
            # latter are audit-only and can never become TA-SAM replay data.
            joint_e2_control = (
                self._tasam_full_control_enabled()
                or os.environ.get('GREENRAN_TASAM_E2_CONTROL', '0').strip().lower()
                in {'1', 'true', 'yes', 'on'}
            )
            # The Judge is the source of truth for which proposal won.  Keep
            # the explicit decision field as the primary value, but tolerate
            # older Judge/advisor payloads that expose the winner only in
            # their nested result.  This fallback is deliberately narrow:
            # exact application and the TA-SAM permission are still required,
            # so a shadow, hold, rejection or safety action cannot reach E2.
            judge_result = decision.get('rapp_judge_result') or {}
            arbitration = decision.get('advisor_arbitration') or {}
            selected_source = str(
                decision.get('selected_assistant')
                or judge_result.get('selected_advocate')
                or judge_result.get('winner')
                or arbitration.get('winner')
                or ''
            ).strip().lower()
            if selected_source in {'tasam', 'ta-sam'}:
                selected_source = 'ta_sam'
            decision['tasam_e2_selection_source'] = selected_source
            selected_tasam_for_e2 = (
                selected_source in {'ta_sam', 'joint'}
                and bool(decision.get('proposal_applied_exactly', False))
                and bool(decision.get('tasam_operating_permission', False))
            )
            decision['tasam_e2_route'] = (
                'native_bundle'
                if joint_e2_control and selected_tasam_for_e2
                else ('native_operational_bundle' if joint_e2_control else 'legacy_operational_or_safety')
            )
            if joint_e2_control and selected_tasam_for_e2:
                self.send_tasam_control_bundle(decision)
            elif joint_e2_control:
                self.send_native_operational_bundle(decision)
            else:
                self.send_energy_command(decision)

            # 5. Escreve decisão
            self.write_decision(decision)

            # 5.1 Envia políticas A1
            if not joint_e2_control:
                self.send_a1_policies(decision)
            
            # 5.2 Verifica ACKs pendentes
            self.check_and_handle_acks()
            
            # 6. Registra decisão no Data Lake
            self.record_decision(decision)
            if metric_snapshot_id is not None and decision.get('decision_id') is not None:
                self.data_lake.associate_extended_metric_decision(
                    metric_snapshot_id, decision['decision_id']
                )
            if decision.get('rapp_judge_result'):
                self._pending_judge_decisions.append(decision)
                self._pending_judge_decision = decision
                contract = decision.get('economic_action') or {}
                bundle = decision.get('tasam_control_bundle') or {}
                if (
                    self._is_economic_contract(contract)
                    and contract.get('native_control_sequence')
                    and contract.get('correlation_id')
                    and bundle.get('cells')
                    and decision.get('decision_id')
                ):
                    # The wire truth lives in the bundle: the global
                    # ``tasam_power_percent`` summary can diverge from the
                    # per-DU candidate that actually went to E2 (r6g: book
                    # 100% vs wire 70% made 191 confirmations mismatch).
                    registered_power_by_cell = (
                        bundle.get('power_percent_by_cell')
                        or {
                            int(cell.get('cell_id')): float(cell.get('tx_power_percent'))
                            for cell in bundle.get('cells') or []
                            if cell.get('cell_id') is not None
                            and cell.get('tx_power_percent') is not None
                        }
                        or None
                    )
                    self.data_lake.register_pending_native_action(
                        os.environ.get('GREENRAN_CAMPAIGN_ID', ''),
                        decision.get('decision_id'),
                        contract.get('correlation_id'),
                        contract.get('native_control_sequence'),
                        bundle.get('sim_time_s'),
                        float(bundle.get('ttl_ms') or 5000) / 1000.0,
                        [cell.get('cell_id') for cell in bundle.get('cells') or []],
                        registered_power_by_cell
                        if registered_power_by_cell
                        else decision.get('tasam_power_percent'),
                    )
                    self.data_lake.associate_energy_command_decision(
                        contract.get('correlation_id'), decision.get('decision_id')
                    )
                elif decision.get('native_operational_correlation_id') and decision.get('decision_id'):
                    # Keep the rApp/ARMD operational command attached to the
                    # decision for audit without inserting it into the
                    # economic pending-action/replay state machine.
                    self.data_lake.associate_energy_command_decision(
                        decision['native_operational_correlation_id'],
                        decision.get('decision_id'),
                    )
            
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
        feedback_drained = self._drain_pending_judge_feedback()
        self._feedback_drained = bool(feedback_drained)
        try:
            feedback_status_path = os.path.join(
                os.environ.get('GREENRAN_STATE_DIR', str(STATE_DIR)),
                'feedback_integrity.json',
            )
            with open(feedback_status_path, 'w', encoding='utf-8') as handle:
                json.dump({
                    'schema': 'greenran.feedback_integrity.v1',
                    'feedback_drained': self._feedback_drained,
                    'pending_after_shutdown': bool(
                        self._pending_judge_decisions or self._pending_judge_decision
                    ),
                    'invalid_for_analysis': not self._feedback_drained,
                    'reason': '' if self._feedback_drained else 'missing_real_pdcp_feedback',
                }, handle, indent=2, ensure_ascii=False)
                handle.write('\n')
        except OSError as exc:
            print(f'[rApp] Não foi possível persistir feedback_integrity.json: {exc}')
        if not feedback_drained:
            print('[rApp] Feedback final ausente: execução marcada para invalidação analítica')
        
        # Para Energy Saver (rApp controla)
        self._stop_energy_saver()
        
        self.xapp_manager.stop_all()
        
        self.print_final_stats()
        
        # Cleanup
        self.data_lake.close()
        self.agent.clear_intent()
        
        print("[rApp] Encerrado; xApps e PID files finalizados")


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
