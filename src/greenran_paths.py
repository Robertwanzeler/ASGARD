#!/usr/bin/env python3
"""
Centraliza caminhos do GreenRAN Core.

Mantém compatibilidade com o comportamento atual, mas concentra
os paths em um único ponto para facilitar portabilidade,
reprodutibilidade e futura configuração por ambiente.
"""

from pathlib import Path
import os


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

FLEXRIC_DIR = PROJECT_ROOT / "flexric"
FLEXRIC_LIB_DIR = PROJECT_ROOT / "flexric_lib"
FLEXRIC_BUILD_DIR = FLEXRIC_DIR / "build_e2ap_v1"
NS3_DIR = PROJECT_ROOT / "ns-O-RAN-flexric" / "mmwave-LENA-oran"
DRLEXP_DIR = PROJECT_ROOT / "drlexp"
DRL_VENV_SITE_PACKAGES = DRLEXP_DIR / ".venv" / "lib" / "python3.12" / "site-packages"

XAPP_INTENTS_DIR = STATE_DIR / "xapp_intents"
XAPP_METRICS_DIR = STATE_DIR / "xapp_metrics"
RAPP_POLICIES_DIR = STATE_DIR / "rapp_policies"
CARLA_STATE_DIR = STATE_DIR / "carla_state"

RAPP_DB_PATH = STATE_DIR / "rapp_data_lake.db"
RAPP_LOG_PATH = STATE_DIR / "rapp.log"
XAPP_HEALTH_PATH = STATE_DIR / "xapp_health.json"
AGENT_INTENT_PATH = STATE_DIR / "agent_intent.json"
AGENT_STATUS_PATH = STATE_DIR / "rapp_agent_status.json"
ARTICLE00_SCENARIO_CONTROL_PATH = STATE_DIR / "article00_scenario_control.json"

SLICER_INTENT_PATH = XAPP_INTENTS_DIR / "slicer.txt"
ENERGY_INTENT_PATH = XAPP_INTENTS_DIR / "energy_saver.txt"
RAPP_DECISION_PATH = XAPP_INTENTS_DIR / "rapp_decision.txt"
ENERGY_COMMAND_PATH = XAPP_INTENTS_DIR / "energy_command.json"

METRICS_JSON_PATH = XAPP_METRICS_DIR / "metrics.json"
EXTENDED_METRICS_JSON_PATH = XAPP_METRICS_DIR / "extended_metrics.json"
CARLA_VEHICLES_PATH = CARLA_STATE_DIR / "vehicles.json"
CARLA_VEHICLE_MAP_PATH = CARLA_STATE_DIR / "vehicle_network_map.json"

XAPP_SLICER_LOG_PATH = STATE_DIR / "xapp_slicer.log"
XAPP_ENERGY_LOG_PATH = STATE_DIR / "xapp_energy.log"
XAPP_SLICER_PID_PATH = STATE_DIR / "xapp_slicer.pid"
XAPP_ENERGY_PID_PATH = STATE_DIR / "xapp_energy.pid"


def ensure_runtime_dirs() -> None:
    """Garante os diretórios de runtime usados pelo core."""
    for path in (STATE_DIR, RUNS_DIR, XAPP_INTENTS_DIR, XAPP_METRICS_DIR, RAPP_POLICIES_DIR, CARLA_STATE_DIR):
        path.mkdir(parents=True, exist_ok=True)


def as_str(path: Path) -> str:
    """Converte Path para string normalizada."""
    return str(path)
