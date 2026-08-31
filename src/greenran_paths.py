#!/usr/bin/env python3
"""
Centraliza caminhos do GreenRAN Core.

Mantém compatibilidade com o comportamento atual, mas concentra
os paths em um único ponto para facilitar portabilidade,
reprodutibilidade e futura configuração por ambiente.
"""

import json
import os
import sys
from functools import lru_cache
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SRC_DIR.parent

# Diretório de estado em tempo de execução.
# Mantido em /tmp por compatibilidade, mas agora configurável.
STATE_DIR = Path(os.environ.get("GREENRAN_STATE_DIR", "/tmp")).resolve()

RUNS_DIR = Path(os.environ.get("GREENRAN_RUNS_DIR", PROJECT_ROOT / "runs")).resolve()
MODELS_DIR = PROJECT_ROOT / "models"
CONFIG_DIR = PROJECT_ROOT / "config"
TEMPLATES_DIR = PROJECT_ROOT / "templates"
DOCS_DIR = PROJECT_ROOT / "docs"
FIXED_SCENARIO_CONFIG_PATH = Path(
    os.environ.get("GREENRAN_FIXED_SCENARIO_CONFIG", CONFIG_DIR / "greenran_fixed_scenario.json")
).resolve()

FLEXRIC_DIR = PROJECT_ROOT / "flexric"
FLEXRIC_LIB_DIR = PROJECT_ROOT / "flexric_lib"
FLEXRIC_BUILD_DIR = FLEXRIC_DIR / "build_e2ap_v1"
NS3_DIR = PROJECT_ROOT / "ns-O-RAN-flexric" / "mmwave-LENA-oran"
DRLEXP_DIR = PROJECT_ROOT / "drlexp"
DRL_VENV_SITE_PACKAGES = (
    DRLEXP_DIR
    / ".venv"
    / "lib"
    / f"python{sys.version_info.major}.{sys.version_info.minor}"
    / "site-packages"
)

XAPP_INTENTS_DIR = STATE_DIR / "xapp_intents"
XAPP_METRICS_DIR = STATE_DIR / "xapp_metrics"
XAPP_SOCKET_DIR = STATE_DIR / "sockets"
RAPP_POLICIES_DIR = STATE_DIR / "rapp_policies"
CARLA_STATE_DIR = STATE_DIR / "carla_state"

RAPP_DB_PATH = Path(os.environ.get("GREENRAN_DB_PATH", STATE_DIR / "rapp_data_lake.db")).resolve()
RAPP_LOG_PATH = STATE_DIR / "rapp.log"
XAPP_HEALTH_PATH = STATE_DIR / "xapp_health.json"
AGENT_INTENT_PATH = STATE_DIR / "agent_intent.json"
AGENT_STATUS_PATH = STATE_DIR / "rapp_agent_status.json"
ARTICLE00_SCENARIO_CONTROL_PATH = STATE_DIR / "article00_scenario_control.json"

SLICER_INTENT_PATH = XAPP_INTENTS_DIR / "slicer.txt"
ENERGY_INTENT_PATH = XAPP_INTENTS_DIR / "energy_saver.txt"
VEHICLE_INTENT_PATH = XAPP_INTENTS_DIR / "vehicle_control.txt"
RAPP_DECISION_PATH = XAPP_INTENTS_DIR / "rapp_decision.txt"
ENERGY_COMMAND_PATH = XAPP_INTENTS_DIR / "energy_command.json"
SLICER_SOCKET_PATH = XAPP_SOCKET_DIR / "slicer.sock"
ENERGY_SOCKET_PATH = XAPP_SOCKET_DIR / "energy_saver.sock"

METRICS_JSON_PATH = XAPP_METRICS_DIR / "metrics.json"
EXTENDED_METRICS_JSON_PATH = XAPP_METRICS_DIR / "extended_metrics.json"
CARLA_VEHICLES_PATH = CARLA_STATE_DIR / "vehicles.json"
CARLA_VEHICLE_MAP_PATH = CARLA_STATE_DIR / "vehicle_network_map.json"

XAPP_SLICER_LOG_PATH = STATE_DIR / "xapp_slicer.log"
XAPP_ENERGY_LOG_PATH = STATE_DIR / "xapp_energy.log"
XAPP_VEHICLE_LOG_PATH = STATE_DIR / "xapp_vehicle.log"
XAPP_SLICER_PID_PATH = STATE_DIR / "xapp_slicer.pid"
XAPP_ENERGY_PID_PATH = STATE_DIR / "xapp_energy.pid"
XAPP_VEHICLE_PID_PATH = STATE_DIR / "xapp_vehicle.pid"


def ensure_runtime_dirs() -> None:
    """Garante os diretórios de runtime usados pelo core."""
    for path in (STATE_DIR, RUNS_DIR, XAPP_INTENTS_DIR, XAPP_METRICS_DIR, XAPP_SOCKET_DIR, RAPP_POLICIES_DIR, CARLA_STATE_DIR):
        path.mkdir(parents=True, exist_ok=True)


def as_str(path: Path) -> str:
    """Converte Path para string normalizada."""
    return str(path)


@lru_cache(maxsize=1)
def load_fixed_scenario_config() -> dict:
    """Carrega o manifesto do cenario fixo do GreenRAN."""
    if not FIXED_SCENARIO_CONFIG_PATH.exists():
        return {}
    try:
        with open(FIXED_SCENARIO_CONFIG_PATH, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _scenario_int(path: tuple[str, ...], default: int) -> int:
    payload = load_fixed_scenario_config()
    current = payload
    for key in path:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
    try:
        return int(current)
    except (TypeError, ValueError):
        return default


def get_fixed_total_ues(default: int = 12) -> int:
    return _scenario_int(("ns3", "total_ues"), default)


def get_fixed_active_cameras(default: int = 3) -> int:
    return _scenario_int(("apps", "app1", "active_cameras"), default)


def get_fixed_vehicle_base_imsi(default: int = 16) -> int:
    return _scenario_int(("apps", "app3", "base_imsi"), default)


def get_fixed_max_vehicles(default: int = 5) -> int:
    return _scenario_int(("apps", "app3", "max_vehicles"), default)


def get_fixed_camera_imsis(default: tuple[int, ...] = (1, 2, 3)) -> tuple[int, ...]:
    payload = load_fixed_scenario_config()
    values = (((payload.get("ns3") or {}).get("camera_imsis")) or list(default))
    try:
        return tuple(int(v) for v in values)
    except (TypeError, ValueError):
        return tuple(default)


def get_fixed_background_imsi_range(default: tuple[int, int] = (4, 12)) -> tuple[int, int]:
    payload = load_fixed_scenario_config()
    values = (((payload.get("ns3") or {}).get("background_imsi_range")) or list(default))
    try:
        if len(values) != 2:
            raise ValueError
        return (int(values[0]), int(values[1]))
    except (TypeError, ValueError):
        return default


def get_fixed_marl_topology() -> dict:
    payload = load_fixed_scenario_config()
    topology = payload.get("marl") if isinstance(payload, dict) else {}
    return topology if isinstance(topology, dict) else {}
