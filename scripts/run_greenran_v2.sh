#!/bin/bash
# GreenRAN Integrated Execution Script - V2 (LONG RUN MODE)
# ==========================================
# Este script inicia a coleta de longa duração (infinito)
# 
# ARQUITETURA:
#   - nearRT-RIC: gerencia conexões E2
#   - ns-3: simulador de rede
#   - csv_to_metrics: coleta métricas do simulador
#   - rApp: orquestrador que controla xApps
#     └── xApp SLICER: iniciado automaticamente pelo rApp (prioridade)
#     └── xApp ENERGY: iniciado pelo rApp quando permitido
# ==========================================

. "$(dirname "$0")/core_runtime.sh"
load_greenran_runtime
create_greenran_run "greenran_v2" >/dev/null

GREENRAN_CLEAN_SCOPE="${GREENRAN_CLEAN_SCOPE:-global}"
GREENRAN_ENABLE_MONITORING_STACK="${GREENRAN_ENABLE_MONITORING_STACK:-1}"
GREENRAN_REQUIRE_REAL_APP2_SENSORS="${GREENRAN_REQUIRE_REAL_APP2_SENSORS:-1}"
GREENRAN_APP2_REAL_SENSOR_WAIT_SECONDS="${GREENRAN_APP2_REAL_SENSOR_WAIT_SECONDS:-60}"
GREENRAN_COLLECTION_EVENT_PROFILE="${GREENRAN_COLLECTION_EVENT_PROFILE:-none}"
GREENRAN_RAN_PRESSURE_PROFILE="${GREENRAN_RAN_PRESSURE_PROFILE:-$GREENRAN_COLLECTION_EVENT_PROFILE}"
GREENRAN_COLLECTION_EVENT_CYCLES="${GREENRAN_COLLECTION_EVENT_CYCLES:-0}"
GREENRAN_COLLECTION_EVENT_TICK_S="${GREENRAN_COLLECTION_EVENT_TICK_S:-1.0}"
GREENRAN_COLLECTION_EVENT_LOG="${GREENRAN_COLLECTION_EVENT_LOG:-$STATE_DIR/collection_event_alternator.log}"
GREENRAN_COLLECTION_EVENT_PID="${GREENRAN_COLLECTION_EVENT_PID:-$STATE_DIR/collection_event_alternator.pid}"

register_app2_dashboard_when_ready() {
    local attempts=20
    local sleep_s=5
    local i

    for ((i=1; i<=attempts; i++)); do
        if python3 ./push/create_grafana_app2.py > "$STATE_DIR/create_grafana_app2.log" 2>&1; then
            echo -e "${GREEN}    Dashboard App2 registrado no Grafana${NC}"
            return 0
        fi
        sleep "$sleep_s"
    done

    echo -e "${RED}    Aviso: falha ao registrar dashboard App2 no Grafana${NC}"
    echo -e "${RED}    Verifique: $STATE_DIR/create_grafana_app2.log${NC}"
    return 1
}

register_app3_dashboard_when_ready() {
    local attempts=20
    local sleep_s=5
    local i

    for ((i=1; i<=attempts; i++)); do
        if python3 ./push/create_grafana_app3.py > "$STATE_DIR/create_grafana_app3.log" 2>&1; then
            echo -e "${GREEN}    Dashboard App3 registrado no Grafana${NC}"
            return 0
        fi
        sleep "$sleep_s"
    done

    echo -e "${RED}    Aviso: falha ao registrar dashboard App3 no Grafana${NC}"
    echo -e "${RED}    Verifique: $STATE_DIR/create_grafana_app3.log${NC}"
    return 1
}

cd $BASE_DIR

resolve_ric_binary() {
    local candidates=(
        "$BASE_DIR/flexric/build_e2ap_v1/examples/ric/nearRT-RIC"
        "$BASE_DIR/flexric/build/examples/ric/nearRT-RIC"
    )
    local candidate
    for candidate in "${candidates[@]}"; do
        if [ -x "$candidate" ]; then
            echo "$candidate"
            return 0
        fi
    done
    return 1
}

resolve_ns3_binary() {
    local candidates=()
    if [ "$GREENRAN_REQUIRE_REAL_APP2_SENSORS" = "1" ]; then
        candidates=(
            "$BASE_DIR/ns-O-RAN-flexric/mmwave-LENA-oran/build/scratch/ns3.42-Energy_saving_with_cell_utilization_scenario-default"
            "$BASE_DIR/ns-O-RAN-flexric/mmwave-LENA-oran/build/scratch/ns3.42-Energy_Saving_with_load_balancing_scenario-default"
            "$BASE_DIR/ns-O-RAN-flexric/mmwave-LENA-oran/build/scratch/ns3.42-scenario-greenran-optimized"
            "$BASE_DIR/ns-O-RAN-flexric/mmwave-LENA-oran/build/scratch/ns3.42-scenario-greenran-default"
            "$BASE_DIR/ns-O-RAN-flexric/mmwave-LENA-oran/build/scratch/ns3.42-scenario-greenran-debug"
        )
    else
        candidates=(
            "$BASE_DIR/ns-O-RAN-flexric/mmwave-LENA-oran/build/scratch/ns3.42-Energy_saving_with_cell_utilization_scenario-default"
            "$BASE_DIR/ns-O-RAN-flexric/mmwave-LENA-oran/build/scratch/ns3.42-Energy_Saving_with_load_balancing_scenario-default"
            "$BASE_DIR/ns-O-RAN-flexric/mmwave-LENA-oran/build/scratch/ns3.42-scenario-greenran-optimized"
            "$BASE_DIR/ns-O-RAN-flexric/mmwave-LENA-oran/build/scratch/ns3.42-scenario-greenran-default"
            "$BASE_DIR/ns-O-RAN-flexric/mmwave-LENA-oran/build/scratch/ns3.42-scenario-greenran-debug"
            "$BASE_DIR/ns-O-RAN-flexric/mmwave-LENA-oran/build/scratch/ns3.42-scenario-zero-with_parallel_loging-default"
            "$BASE_DIR/ns-O-RAN-flexric/mmwave-LENA-oran/build/scratch/ns3.42-scenario-zero-default"
            "$BASE_DIR/ns-O-RAN-flexric/mmwave-LENA-oran/build/scratch/ns3.42-scenario-zero-debug"
        )
    fi
    local candidate
    for candidate in "${candidates[@]}"; do
        if [ -x "$candidate" ]; then
            echo "$candidate"
            return 0
        fi
    done
    return 1
}

ns3_binary_supports_real_app2_sensors() {
    local binary_name
    binary_name="$(basename "$1")"
    case "$binary_name" in
        ns3.42-Energy_saving_with_cell_utilization_scenario-*|ns3.42-Energy_Saving_with_load_balancing_scenario-*)
            return 0
            ;;
        ns3.42-scenario-greenran-*)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

ns3_binary_max_sim_time() {
    local binary_name
    binary_name="$(basename "$1")"
    case "$binary_name" in
        ns3.42-Energy_saving_with_cell_utilization_scenario-*|ns3.42-Energy_Saving_with_load_balancing_scenario-*)
            echo "100000"
            ;;
        *)
            echo ""
            ;;
    esac
}

has_e2_assoc() {
    ss -nA sctp state established 2>/dev/null | grep -qE '127\.0\.0\.1:36421|127\.0\.0\.1:36422'
}

app2_has_real_sensor_ues() {
    python3 - "$STATE_DIR" <<'PY'
import json
import time
import sys
from pathlib import Path

path = Path(sys.argv[1]) / "xapp_metrics" / "extended_metrics.json"
if not path.exists():
    raise SystemExit(1)

try:
    data = json.loads(path.read_text())
except Exception:
    raise SystemExit(1)

try:
    file_age_s = max(0.0, time.time() - path.stat().st_mtime)
except OSError:
    file_age_s = None

global_metrics = data.get("global_metrics", {}) or {}
ue_metrics = data.get("ue_metrics", {}) or {}
total = int(global_metrics.get("total_active_sensors", 0) or 0)
sensor_ues = sum(1 for ue in ue_metrics.values() if (ue or {}).get("device_type") == "sensor")

if file_age_s is not None and file_age_s > 90:
    raise SystemExit(1)

raise SystemExit(0 if total > 0 or sensor_ues > 0 else 1)
PY
}

wait_for_app2_real_sensor_ues() {
    local attempts="${1:-25}"
    local sleep_s="${2:-1}"
    local i

    for ((i=1; i<=attempts; i++)); do
        if app2_has_real_sensor_ues; then
            return 0
        fi
        sleep "$sleep_s"
    done

    return 1
}

wait_for_armd_artifacts() {
    local attempts="${1:-15}"
    local sleep_s="${2:-2}"
    local i

    for ((i=1; i<=attempts; i++)); do
        if python3 - "$STATE_DIR" <<'PY'
import json
import sys
from pathlib import Path

state_dir = Path(sys.argv[1])
policy_dir = state_dir / "rapp_policies"

for name in ("energy_policy.json", "slice_policy.json"):
    path = policy_dir / name
    if not path.exists():
        continue
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        continue
    armd = payload.get("armd", {}) if isinstance(payload, dict) else {}
    scenario = str(armd.get("scenario", "") or "").strip()
    if scenario:
        raise SystemExit(0)

decision_path = state_dir / "xapp_intents" / "rapp_decision.txt"
if decision_path.exists():
    try:
        text = decision_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        text = ""
    if "ARMD_SCENARIO=" in text:
        raise SystemExit(0)

raise SystemExit(1)
PY
        then
            return 0
        fi
        sleep "$sleep_s"
    done

    return 1
}

print_armd_operational_summary() {
    python3 - "$STATE_DIR" "$GREENRAN_DB_PATH" "$GREENRAN_ARMD_SUMMARY" "$GREENRAN_ARMD_MODE" "$GREENRAN_ARMD_MIN_CONFIDENCE" <<'PY'
import json
import sqlite3
import sys
from pathlib import Path

state_dir = Path(sys.argv[1])
db_path = Path(sys.argv[2])
summary_path = Path(sys.argv[3])
armd_mode = sys.argv[4]
armd_min_conf = sys.argv[5]
policy_dir = state_dir / "rapp_policies"

def load_json(path: Path):
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}

energy = load_json(policy_dir / "energy_policy.json")
slice_policy = load_json(policy_dir / "slice_policy.json")
armd = {}
for payload in (energy, slice_policy):
    block = payload.get("armd", {}) if isinstance(payload, dict) else {}
    if isinstance(block, dict) and str(block.get("scenario", "") or "").strip():
        armd = block
        break

print("\033[0;34m=== [14/14] Verificacao ARMD-GreenRAN ===\033[0m")
print(f"\033[0;32m    Modo: {armd_mode} | min_confidence={armd_min_conf}\033[0m")
print(f"\033[0;32m    Summary: {summary_path}\033[0m")

if armd:
    print(f"\033[0;32m    Politica ARMD: {armd.get('scenario', '')} | domain={armd.get('domain', '')} | source={armd.get('source', '')}\033[0m")
    print(f"\033[0;32m    Confidence: {float(armd.get('confidence', 0.0) or 0.0):.2f} | override={bool(armd.get('override_applied', False))}\033[0m")
else:
    print("\033[0;33m    Aviso: bloco ARMD ainda nao apareceu nas politicas A1\033[0m")

decision_path = state_dir / "xapp_intents" / "rapp_decision.txt"
if decision_path.exists():
    try:
        lines = [
            line.strip()
            for line in decision_path.read_text(encoding="utf-8", errors="ignore").splitlines()
            if line.startswith(("ENERGY_SAVER=", "ACTION=", "ARMD_SCENARIO=", "ARMD_SOURCE=", "ARMD_CONFIDENCE=", "ARMD_OVERRIDE_APPLIED="))
        ]
    except Exception:
        lines = []
    if lines:
        print("\033[0;32m    Ultima decisao:\033[0m")
        for line in lines:
            print(f"\033[0;32m      {line}\033[0m")

if db_path.exists():
    try:
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
        row = cur.execute(
            """
            SELECT datetime, decision, armd_scenario, armd_source, armd_confidence, armd_override_applied
            FROM decisions_history
            ORDER BY timestamp DESC
            LIMIT 1
            """
        ).fetchone()
        conn.close()
    except Exception:
        row = None
    if row:
        dt, decision, scenario, source, confidence, override = row
        print(f"\033[0;32m    DB latest: {dt} | {decision} | scenario={scenario or '-'} | source={source or '-'} | conf={float(confidence or 0.0):.2f} | override={int(override or 0)}\033[0m")
PY
}

probe_carla_port() {
    python3 - "$1" "$2" <<'PY'
import socket
import sys

host = sys.argv[1]
port = int(sys.argv[2])
s = socket.socket()
s.settimeout(1.5)
try:
    s.connect((host, port))
except Exception:
    raise SystemExit(1)
finally:
    s.close()
PY
}

kill_pid_if_running() {
    local pid_file="$1"
    if [ -f "$pid_file" ]; then
        local pid
        pid="$(cat "$pid_file" 2>/dev/null || true)"
        if [ -n "$pid" ] && ps -p "$pid" >/dev/null 2>&1; then
            kill -9 "$pid" 2>/dev/null || true
        fi
        rm -f "$pid_file"
    fi
}

cleanup_instance_processes() {
    echo -e "${BLUE}    Limpando apenas processos desta instância (${GREENRAN_INSTANCE_NAME})...${NC}"
    kill_pid_if_running "$GREENRAN_XAPP_SLICER_PID"
    kill_pid_if_running "$GREENRAN_XAPP_ENERGY_PID"
    kill_pid_if_running "$GREENRAN_XAPP_VEHICLE_PID"
    kill_pid_if_running "$GREENRAN_NS3_PID"
    if [ "${GREENRAN_DISABLE_RIC:-0}" != "1" ]; then
        kill_pid_if_running "$GREENRAN_RIC_PID"
    fi
    kill_pid_if_running "$GREENRAN_CSV_PID"
    kill_pid_if_running "$GREENRAN_RAPP_PID"
    kill_pid_if_running "$GREENRAN_DASHBOARD_PID"
    kill_pid_if_running "$GREENRAN_WATCHDOG_PID"
    kill_pid_if_running "$GREENRAN_APP1_PID"
    kill_pid_if_running "$GREENRAN_APP1_SIMULATOR_PID"
    kill_pid_if_running "$GREENRAN_APP2_PID"
    kill_pid_if_running "$GREENRAN_APP2_SIMULATOR_PID"
    kill_pid_if_running "$GREENRAN_APP3_PID"
    kill_pid_if_running "$GREENRAN_PUSH_APP1_PID"
    kill_pid_if_running "$GREENRAN_PUSH_APP2_PID"
    kill_pid_if_running "$GREENRAN_PUSH_APP3_PID"
    kill_pid_if_running "$GREENRAN_CARLA_BRIDGE_PID"
    kill_pid_if_running "$GREENRAN_CARLA_MAPPER_PID"
    kill_pid_if_running "$GREENRAN_STAGE_CONTROLLER_PID"
    kill_pid_if_running "$GREENRAN_COLLECTION_EVENT_PID"
}

cleanup_ns3_runtime_artifacts() {
    local ns3_runtime_dir="$BASE_DIR/ns-O-RAN-flexric/mmwave-LENA-oran"
    local patterns=(
        "DlPdcpStats.txt"
        "DlMacStats.txt"
        "DlRlcStats.txt"
        "cu-up-cell-*.txt"
        "cu-cp-cell-*.txt"
        "du-cell-*.txt"
    )
    local pattern

    echo -e "${BLUE}    Limpando artefatos de métricas do ns-3 antes da nova rodada...${NC}"
    for pattern in "${patterns[@]}"; do
        rm -f "$ns3_runtime_dir"/$pattern
    done
}

start_runtime_stage_controller() {
    if [ "${GREENRAN_STAGED_CONTROL}" != "1" ]; then
        echo -e "${YELLOW}    Controle por etapas desabilitado (GREENRAN_STAGED_CONTROL=${GREENRAN_STAGED_CONTROL})${NC}"
        rm -f "$GREENRAN_STAGE_CONTROLLER_PID"
        return 0
    fi

    setsid python3 ./scripts/runtime_stage_controller.py \
        --required-streak "$GREENRAN_STAGE_REQUIRED_STREAK" \
        > "$GREENRAN_STAGE_CONTROLLER_LOG" 2>&1 &
    echo $! > "$GREENRAN_STAGE_CONTROLLER_PID"
    sleep 1
    echo -e "${GREEN}    Observador de etapas ativo: ALLOWED natural -> BLOCKED natural${NC}"
    echo -e "${GREEN}    Required streak: ${GREENRAN_STAGE_REQUIRED_STREAK}${NC}"
}

start_app2_simulator() {
    mkdir -p "$STATE_DIR/app2_monitoramento/sensors"
    rm -f \
        "$STATE_DIR/app2_monitoramento/simulator_state.json" \
        "$STATE_DIR/app2_monitoramento/sensors/latest.json" \
        "$STATE_DIR/app2_monitoramento/monitoring_snapshot.json"
    setsid python3 ./apps/app2_monitoramento/backend/simulate_sensors.py --num-sensors 17 --interval 5.0 > "$GREENRAN_APP2_SIMULATOR_LOG" 2>&1 &
    echo $! > "$GREENRAN_APP2_SIMULATOR_PID"
    sleep 2
}

start_carla_support_stack() {
    local effective_mode="${GREENRAN_CARLA_MODE}"

    if [ "${GREENRAN_ENABLE_CARLA_STACK}" != "1" ]; then
        echo -e "${YELLOW}    Stack CARLA desabilitada por GREENRAN_ENABLE_CARLA_STACK=${GREENRAN_ENABLE_CARLA_STACK}${NC}"
        rm -f "$GREENRAN_CARLA_BRIDGE_PID" "$GREENRAN_CARLA_MAPPER_PID"
        return 0
    fi

    if [ "$GREENRAN_CARLA_MODE" = "carla" ]; then
        if ! probe_carla_port "$GREENRAN_CARLA_HOST" "$GREENRAN_CARLA_PORT" >/dev/null 2>&1; then
            if [ "${GREENRAN_CARLA_FALLBACK_TO_MOCK}" = "1" ]; then
                echo -e "${YELLOW}    CARLA ${GREENRAN_CARLA_HOST}:${GREENRAN_CARLA_PORT} indisponivel; fallback automatico para mock${NC}"
                effective_mode="mock"
            else
                echo -e "${RED}    ERRO: CARLA ${GREENRAN_CARLA_HOST}:${GREENRAN_CARLA_PORT} indisponivel e fallback desabilitado${NC}"
                exit 1
            fi
        fi
    fi

    mkdir -p "$STATE_DIR/carla_state"
    setsid python3 ./src/carla_bridge.py \
        --mode "$effective_mode" \
        --host "$GREENRAN_CARLA_HOST" \
        --port "$GREENRAN_CARLA_PORT" \
        --poll-interval "$GREENRAN_CARLA_POLL_INTERVAL" \
        > "$GREENRAN_CARLA_BRIDGE_LOG" 2>&1 &
    echo $! > "$GREENRAN_CARLA_BRIDGE_PID"
    sleep 1

    setsid python3 ./src/carla_ns3_mapper.py \
        --poll-interval "$GREENRAN_CARLA_MAPPER_POLL_INTERVAL" \
        > "$GREENRAN_CARLA_MAPPER_LOG" 2>&1 &
    echo $! > "$GREENRAN_CARLA_MAPPER_PID"
    sleep 1

    echo -e "${GREEN}    Bridge CARLA ativo em modo ${effective_mode}${NC}"
    echo -e "${GREEN}    Mapper CARLA->IMSI ativo${NC}"
}

start_collection_event_alternator() {
    if [ "${GREENRAN_COLLECTION_EVENT_PROFILE}" = "none" ]; then
        rm -f "$GREENRAN_COLLECTION_EVENT_PID"
        echo -e "${YELLOW}    Alternador de eventos de coleta desabilitado${NC}"
        return 0
    fi

    setsid python3 ./scripts/collection_event_alternator.py \
        --profile "$GREENRAN_COLLECTION_EVENT_PROFILE" \
        --cycles "$GREENRAN_COLLECTION_EVENT_CYCLES" \
        --tick-s "$GREENRAN_COLLECTION_EVENT_TICK_S" \
        --time-source sim \
        > "$GREENRAN_COLLECTION_EVENT_LOG" 2>&1 &
    echo $! > "$GREENRAN_COLLECTION_EVENT_PID"
    sleep 1
    echo -e "${GREEN}    Alternador de eventos ativo: ${GREENRAN_COLLECTION_EVENT_PROFILE}${NC}"
    echo -e "${GREEN}    Log: ${GREENRAN_COLLECTION_EVENT_LOG}${NC}"
}

RIC_BIN="$(resolve_ric_binary)" || {
    echo -e "${RED}ERRO: nearRT-RIC não encontrado em build_e2ap_v1 nem em build padrão.${NC}"
    echo -e "${RED}Verifique os binários em flexric/build*/examples/ric/nearRT-RIC${NC}"
    exit 1
}

NS3_BIN="$(resolve_ns3_binary)" || {
    echo -e "${RED}ERRO: binário do cenário ns-3 não encontrado.${NC}"
    if [ "$GREENRAN_REQUIRE_REAL_APP2_SENSORS" = "1" ]; then
        echo -e "${RED}Você pediu App2 com dados reais do ns-3; por isso o launcher exige um binário com mMTC real.${NC}"
        echo -e "${RED}Procure/compile um destes alvos em build/scratch:${NC}"
        echo -e "${RED}  - ns3.42-Energy_saving_with_cell_utilization_scenario-default${NC}"
        echo -e "${RED}  - ns3.42-Energy_Saving_with_load_balancing_scenario-default${NC}"
        echo -e "${RED}  - ou um scenario-greenran-* equivalente${NC}"
        echo -e "${RED}Com o estado atual, o scenario-zero-* só entrega App1 real (câmeras) e força App2 em fallback.${NC}"
    else
        echo -e "${RED}Esperado em build/scratch como scenario-greenran-* ou scenario-zero-*.${NC}"
    fi
    exit 1
}

GREENRAN_DISABLE_RIC="${GREENRAN_DISABLE_RIC:-0}"

# Tempo de simulacao configuravel
SIM_TIME="$GREENRAN_SIM_TIME"
REQUESTED_SIM_TIME="$SIM_TIME"

# Cores para o terminal
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${BLUE}=== [1/6] Limpando processos e arquivos antigos ===${NC}"

if [ "$GREENRAN_CLEAN_SCOPE" = "instance" ]; then
    cleanup_instance_processes
else
    # Matar processos principais
    pkill -9 -f "nearRT-RIC" 2>/dev/null || true
    pkill -9 -f "xapp" 2>/dev/null || true
    pkill -9 -f "ns3.42" 2>/dev/null || true
    pkill -9 -f "python3.*rapp" 2>/dev/null || true
    pkill -9 -f "csv_to_metrics" 2>/dev/null || true
    pkill -9 -f "watchdog_xapps" 2>/dev/null || true
    pkill -9 -f "rapp_dashboard" 2>/dev/null || true
    pkill -9 -f "app1_vigilancia/backend/app.py" 2>/dev/null || true
    pkill -9 -f "app1_vigilancia/backend/simulate_cameras.py" 2>/dev/null || true
    pkill -9 -f "app2_monitoramento/backend/app.py" 2>/dev/null || true
    pkill -9 -f "app3_veicular/backend/app.py" 2>/dev/null || true
    pkill -9 -f "simulate_sensors.py" 2>/dev/null || true
    pkill -9 -f "push_stats_to_influx.py" 2>/dev/null || true
    pkill -9 -f "push_cvar_to_influx.py" 2>/dev/null || true
    pkill -9 -f "push_app1_to_influx.py" 2>/dev/null || true
    pkill -9 -f "push_app2_to_influx.py" 2>/dev/null || true
    pkill -9 -f "push_app3_to_influx.py" 2>/dev/null || true
    pkill -9 -f "python3 ./src/carla_bridge.py" 2>/dev/null || true
    pkill -9 -f "python3 ./src/carla_ns3_mapper.py" 2>/dev/null || true
    pkill -9 -f "python3 ./scripts/runtime_stage_controller.py" 2>/dev/null || true
    pkill -9 -f "python3 ./scripts/collection_event_alternator.py" 2>/dev/null || true
fi

if [ "$GREENRAN_CLEAN_SCOPE" != "instance" ]; then
    echo -e "${BLUE}    Limpando processos zumbis...${NC}"
    ZOMBIES=$(ps aux | grep -E "defunct|zombie" | grep -v grep | awk '{print $2}' | wc -l)
    if [ $ZOMBIES -gt 0 ]; then
        ps aux | grep -E "defunct|zombie" | grep -v grep | awk '{print $2}' | xargs -r kill -9 2>/dev/null || true
        echo -e "${GREEN}    $ZOMBIES processos zumbis eliminados${NC}"
    fi
fi

# Limpar arquivos PID antigos
echo -e "${BLUE}    Limpando arquivos PID antigos...${NC}"
rm -f "$GREENRAN_XAPP_SLICER_PID"
rm -f "$STATE_DIR/xapp_energy_saver.pid"
rm -f "$GREENRAN_XAPP_ENERGY_PID"
rm -f "$GREENRAN_XAPP_VEHICLE_PID"
rm -f "$GREENRAN_NS3_PID"
rm -f "$GREENRAN_RIC_PID"
rm -f "$GREENRAN_CSV_PID"
rm -f "$GREENRAN_RAPP_PID"
rm -f "$GREENRAN_DASHBOARD_PID"
rm -f "$GREENRAN_WATCHDOG_PID"
rm -f "$GREENRAN_APP1_PID"
rm -f "$GREENRAN_APP1_SIMULATOR_PID"
rm -f "$GREENRAN_APP2_PID"
rm -f "$GREENRAN_APP2_SIMULATOR_PID"
rm -f "$GREENRAN_APP3_PID"
rm -f "$GREENRAN_PUSH_APP1_PID"
rm -f "$GREENRAN_PUSH_APP2_PID"
rm -f "$GREENRAN_PUSH_APP3_PID"
rm -f "$GREENRAN_CARLA_BRIDGE_PID"
rm -f "$GREENRAN_CARLA_MAPPER_PID"
rm -f "$GREENRAN_STAGE_CONTROLLER_PID"

if [ "$GREENRAN_CLEAN_SCOPE" != "instance" ]; then
    tmux kill-session -t greenran 2>/dev/null || true
    tmux kill-session -t slicer 2>/dev/null || true
    tmux kill-session -t energy 2>/dev/null || true
fi

# Configura as bibliotecas (CRÍTICO para todos os processos)
export LD_LIBRARY_PATH=$BASE_DIR/flexric/build_e2ap_v1/src/ric:$BASE_DIR/flexric_lib:$BASE_DIR/flexric/build_e2ap_v1/src/xApp:$LD_LIBRARY_PATH

echo -e "${BLUE}=== [2/6] Verificando processos restantes ===${NC}"

sleep 2
if [ "$GREENRAN_CLEAN_SCOPE" = "instance" ]; then
    echo -e "${GREEN}    Limpeza por instância concluída${NC}"
else
    REMAINING=$(ps aux | grep -E "nearRT-RIC|xapp_slicer|xapp_energy|ns3.42|rapp_orchestrator|csv_to_metrics" | grep -v grep | wc -l)
    if [ $REMAINING -gt 0 ]; then
        echo -e "${RED}    AVISO: $REMAINING processos ainda rodando!${NC}"
        ps aux | grep -E "nearRT-RIC|xapp_slicer|xapp_energy|ns3.42|rapp_orchestrator|csv_to_metrics" | grep -v grep
        echo -e "${RED}    Tentando eliminar novamente...${NC}"
        pkill -9 -f "nearRT-RIC" 2>/dev/null || true
        pkill -9 -f "xapp_slicer" 2>/dev/null || true
        pkill -9 -f "xapp_energy" 2>/dev/null || true
        pkill -9 -f "ns3.42" 2>/dev/null || true
        pkill -9 -f "python3.*rapp" 2>/dev/null || true
        pkill -9 -f "csv_to_metrics" 2>/dev/null || true
        pkill -9 -f "app1_vigilancia/backend/app.py" 2>/dev/null || true
        pkill -9 -f "app1_vigilancia/backend/simulate_cameras.py" 2>/dev/null || true
        pkill -9 -f "app2_monitoramento/backend/app.py" 2>/dev/null || true
        pkill -9 -f "app3_veicular/backend/app.py" 2>/dev/null || true
        pkill -9 -f "simulate_sensors.py" 2>/dev/null || true
        pkill -9 -f "push_stats_to_influx.py" 2>/dev/null || true
        pkill -9 -f "push_cvar_to_influx.py" 2>/dev/null || true
        pkill -9 -f "push_app1_to_influx.py" 2>/dev/null || true
        pkill -9 -f "push_app2_to_influx.py" 2>/dev/null || true
        pkill -9 -f "push_app3_to_influx.py" 2>/dev/null || true
        pkill -9 -f "python3 ./src/carla_bridge.py" 2>/dev/null || true
        pkill -9 -f "python3 ./src/carla_ns3_mapper.py" 2>/dev/null || true
        sleep 2
    else
        echo -e "${GREEN}    Todos os processos foram eliminados!${NC}"
    fi
fi

# NÃO removemos mais o banco de dados ($GREENRAN_DB_PATH)
# Apenas limpamos os arquivos de texto temporários
rm -f "$STATE_DIR"/xapp_intents/*.txt
rm -f "$STATE_DIR"/xapp_intents/*.json
rm -f "$STATE_DIR"/xapp_metrics/*.json
rm -f "$STATE_DIR"/article00_scenario_control.json
mkdir -p "$STATE_DIR/xapp_intents" "$STATE_DIR/xapp_metrics" "$STATE_DIR/app1_vigilancia" "$STATE_DIR/app2_monitoramento/sensors" "$STATE_DIR/app3_veicular/vehicles" "$STATE_DIR/app3_veicular/events"
sleep 2

cleanup_ns3_runtime_artifacts

if ns3_binary_supports_real_app2_sensors "$NS3_BIN"; then
    NS3_RUNTIME_PROFILE_MSG="cenário com câmeras + sensores mMTC reais esperados"
else
    NS3_RUNTIME_PROFILE_MSG="cenário sem sensores mMTC reais exportados para App2"
fi

echo -e "${BLUE}=== [3/9] Iniciando nearRT-RIC ===${NC}"
RIC_ACTIVE=1
if [ "$GREENRAN_DISABLE_RIC" = "1" ]; then
    RIC_ACTIVE=0
    echo -e "${YELLOW}    RIC desabilitado por GREENRAN_DISABLE_RIC=1; seguindo em modo coleta sem E2.${NC}"
    : > "$GREENRAN_RIC_LOG"
else
    setsid "$RIC_BIN" -c $BASE_DIR/flexric/flexric.conf -p $BASE_DIR/flexric_lib/ > "$GREENRAN_RIC_LOG" 2>&1 &
    echo $! > "$GREENRAN_RIC_PID"
    sleep 3
    if ! ps -p "$(cat "$GREENRAN_RIC_PID" 2>/dev/null)" >/dev/null 2>&1; then
        RIC_ACTIVE=0
        echo -e "${YELLOW}    nearRT-RIC encerrou cedo; seguindo em modo coleta sem E2.${NC}"
        echo -e "${YELLOW}    Verifique: $GREENRAN_RIC_LOG${NC}"
    fi
fi

echo -e "${BLUE}=== [4/9] Iniciando ns-3 (Scenario GreenRAN - 1 hora) ===${NC}"
NS3_MAX_SIM_TIME="$(ns3_binary_max_sim_time "$NS3_BIN")"
if [ -n "$NS3_MAX_SIM_TIME" ] && [ "$SIM_TIME" -gt "$NS3_MAX_SIM_TIME" ]; then
    echo -e "${YELLOW}    Aviso: binario $(basename "$NS3_BIN") rejeita simTime > $NS3_MAX_SIM_TIME; ajustando ${REQUESTED_SIM_TIME}s -> ${NS3_MAX_SIM_TIME}s${NC}"
    SIM_TIME="$NS3_MAX_SIM_TIME"
fi
echo -e "${GREEN}    Binário ns-3: $NS3_BIN${NC}"
echo -e "${GREEN}    Perfil ns-3: $NS3_RUNTIME_PROFILE_MSG${NC}"
GREENRAN_NS3_ENABLE_TRACES="${GREENRAN_NS3_ENABLE_TRACES:-0}"
echo -e "${GREEN}    Pressao RAN: $GREENRAN_RAN_PRESSURE_PROFILE${NC}"
echo -e "${GREEN}    Traces ns-3: $GREENRAN_NS3_ENABLE_TRACES${NC}"
echo -e "${GREEN}    simTime efetivo: $SIM_TIME s${NC}"
cd $BASE_DIR/ns-O-RAN-flexric/mmwave-LENA-oran
# Forçamos o LD_LIBRARY_PATH aqui também para o ns-3
export LD_LIBRARY_PATH=$LD_LIBRARY_PATH
setsid "$NS3_BIN" --e2TermIp=127.0.0.1 --simTime="$SIM_TIME" --ranPressureProfile="$GREENRAN_RAN_PRESSURE_PROFILE" --enableTraces="$GREENRAN_NS3_ENABLE_TRACES" > "$GREENRAN_NS3_LOG" 2>&1 &
echo $! > "$GREENRAN_NS3_PID"
cd $BASE_DIR
sleep 2

if ! ps -p "$(cat "$GREENRAN_NS3_PID" 2>/dev/null)" >/dev/null 2>&1; then
    echo -e "${RED}    ERRO: o ns-3 encerrou logo após o startup.${NC}"
    echo -e "${RED}    Verifique: $GREENRAN_NS3_LOG${NC}"
    tail -n 20 "$GREENRAN_NS3_LOG" 2>/dev/null || true
    exit 1
fi

if grep -q "Invalid command-line argument" "$GREENRAN_NS3_LOG" 2>/dev/null; then
    echo -e "${RED}    ERRO: o ns-3 rejeitou a linha de comando de inicializacao.${NC}"
    echo -e "${RED}    Verifique: $GREENRAN_NS3_LOG${NC}"
    tail -n 20 "$GREENRAN_NS3_LOG" 2>/dev/null || true
    exit 1
fi

# Aguarda o ns-3 estabelecer conexão E2
if [ "$RIC_ACTIVE" = "1" ]; then
    echo -e "${BLUE}    Aguardando conexão E2...${NC}"
    E2_READY=0
    for i in {1..30}; do
        if grep -q "E2setupResponse" "$GREENRAN_NS3_LOG" 2>/dev/null; then
            echo -e "${GREEN}    Conexão E2 estabelecida!${NC}"
            E2_READY=1
            break
        fi
        if has_e2_assoc; then
            echo -e "${GREEN}    Associação SCTP E2 estabelecida!${NC}"
            E2_READY=1
            break
        fi
        if grep -q "Assertion .*ATS_ALIGEND_BASIC_PER syntax" "$GREENRAN_RIC_LOG" 2>/dev/null; then
            echo -e "${YELLOW}    RIC incompatível com o agente E2 atual; seguindo em modo coleta sem E2.${NC}"
            echo -e "${YELLOW}    Verifique: $GREENRAN_RIC_LOG${NC}"
            RIC_ACTIVE=0
            break
        fi
        if [ -f "$GREENRAN_NS3_PID" ] && ! ps -p "$(cat "$GREENRAN_NS3_PID" 2>/dev/null)" >/dev/null 2>&1; then
            echo -e "${RED}    ERRO: o ns-3 encerrou durante o handshake E2.${NC}"
            echo -e "${RED}    Verifique: $GREENRAN_NS3_LOG${NC}"
            tail -n 20 "$GREENRAN_NS3_LOG" 2>/dev/null || true
            exit 1
        fi
        if [ -f "$GREENRAN_RIC_PID" ] && ! ps -p "$(cat "$GREENRAN_RIC_PID" 2>/dev/null)" >/dev/null 2>&1; then
            echo -e "${YELLOW}    nearRT-RIC encerrou durante o handshake; seguindo em modo coleta sem E2.${NC}"
            echo -e "${YELLOW}    Verifique: $GREENRAN_RIC_LOG${NC}"
            RIC_ACTIVE=0
            break
        fi
        sleep 1
    done
    if [ "$E2_READY" = "0" ] && [ "$RIC_ACTIVE" = "1" ]; then
        echo -e "${YELLOW}    Timeout aguardando E2setupResponse; seguindo em modo coleta sem E2.${NC}"
    fi
else
    echo -e "${YELLOW}    E2 desabilitado; pulando handshake com RIC.${NC}"
fi

echo -e "${BLUE}=== [5/9] Iniciando Leitor de Métricas ===${NC}"
setsid /bin/bash -lc "cd '$BASE_DIR' && while true; do python3 ./src/csv_to_metrics.py --input-dir ./ns-O-RAN-flexric/mmwave-LENA-oran --output '$STATE_DIR/xapp_metrics/metrics.json' --poll-interval '$GREENRAN_COLLECTOR_POLL_INTERVAL'; code=\$?; echo \"[CSV_METRICS_SUPERVISOR] collector exited with code \$code at \$(date -Is); restarting in 2s\"; sleep 2; done" > "$GREENRAN_CSV_LOG" 2>&1 &
echo $! > "$GREENRAN_CSV_PID"
sleep 2

# NÃO INICIAMOS xApps DIRETAMENTE!
# O rApp controla o ciclo de vida dos xApps:
#   - SLICER: iniciado automaticamente pelo rApp (prioridade)
#   - VEHICLE: iniciado automaticamente pelo rApp (App3)
#   - ENERGY: iniciado pelo rApp quando condições permitirem
echo -e "${BLUE}=== [6/9] xApps serao iniciados pelo rApp ===${NC}"
echo -e "${BLUE}    - xApp SLICER: iniciado com rApp (prioridade) ===${NC}"
echo -e "${BLUE}    - xApp VEHICLE: iniciado com rApp (App3) ===${NC}"
echo -e "${BLUE}    - xApp ENERGY: ativado pelo rApp quando permitido ===${NC}"

echo -e "${BLUE}=== [7/9] Initiating rApp Orchestrator (ML TRAINING) ===${NC}"
RAPP_PYTHON_BIN="python3"
case "${GREENRAN_RL_POLICY:-legacy_a3c}" in
    sac|caora_sac|resource_sac|awac|caora_awac|resource_awac)
        if [ -x "$BASE_DIR/drlexp/.venv/bin/python" ]; then
            RAPP_PYTHON_BIN="$BASE_DIR/drlexp/.venv/bin/python"
            echo -e "${GREEN}    rApp usando virtualenv RL: $RAPP_PYTHON_BIN${NC}"
        else
            echo -e "${YELLOW}    Aviso: virtualenv RL nao encontrado; usando python3 do sistema${NC}"
        fi
        ;;
esac
setsid "$RAPP_PYTHON_BIN" ./src/rapp_orchestrator.py --synthetic "$GREENRAN_ORCHESTRATOR_SYNTHETIC_DAYS" --interval "$GREENRAN_ORCHESTRATOR_INTERVAL" > "$GREENRAN_RAPP_LOG" 2>&1 &
echo $! > "$GREENRAN_RAPP_PID"
sleep 3

echo -e "${BLUE}=== [7.1/9] Aplicando controle em etapas ===${NC}"
start_runtime_stage_controller

# Aguarda rApp iniciar e reportar status
echo -e "${BLUE}    Aguardando rApp inicializar...${NC}"
for i in {1..10}; do
    if grep -q "xApp SLICER" "$GREENRAN_RAPP_LOG" 2>/dev/null; then
        echo -e "${GREEN}    rApp iniciou xApp SLICER!${NC}"
        break
    fi
    sleep 1
done

echo -e "${BLUE}=== [8/11] Iniciando Simulador de Câmeras da App1 ===${NC}"
if [ "$GREENRAN_APP1_CAMERA_SOURCE_MODE" = "real" ]; then
    rm -f "$GREENRAN_APP1_SIMULATOR_PID"
    echo -e "${GREEN}    App1 em modo de câmeras reais${NC}"
    echo -e "${GREEN}    Bootstrap: $GREENRAN_APP1_CAMERAS_BOOTSTRAP${NC}"
else
    mkdir -p "$STATE_DIR/app1_vigilancia/camera_sources"
    rm -f "$STATE_DIR/app1_vigilancia/camera_sources/simulator_state.json"
    setsid python3 ./apps/app1_vigilancia/backend/simulate_cameras.py --interval "$GREENRAN_APP1_SIMULATOR_INTERVAL" --duration 2 --resolution 3840x2160 > "$GREENRAN_APP1_SIMULATOR_LOG" 2>&1 &
    echo $! > "$GREENRAN_APP1_SIMULATOR_PID"
    sleep 2
    echo -e "${GREEN}    Simulador App1 iniciado; fontes em $STATE_DIR/app1_vigilancia/camera_sources${NC}"
fi

echo -e "${BLUE}=== [9/11] Iniciando App1-Vigilancia ===${NC}"
setsid env GREENRAN_APP1_CAMERAS_BOOTSTRAP="$GREENRAN_APP1_CAMERAS_BOOTSTRAP" python3 ./apps/app1_vigilancia/backend/app.py --host "$APP1_HOST" --port "$APP1_PORT" > "$GREENRAN_APP1_LOG" 2>&1 &
echo $! > "$GREENRAN_APP1_PID"
sleep 2
echo -e "${GREEN}    App1 disponível em http://localhost:${APP1_PORT}${NC}"

echo -e "${BLUE}=== [10/12] Iniciando App2-Monitoramento ===${NC}"
APP2_SENSOR_SOURCE="${GREENRAN_APP2_SENSOR_SOURCE:-ns3}"
APP2_REAL_SENSOR_UES_DETECTED="0"
if [ "$APP2_SENSOR_SOURCE" = "mock" ]; then
    start_app2_simulator
    echo -e "${GREEN}    App2 usando simulador Python de sensores${NC}"
else
    rm -f "$GREENRAN_APP2_SIMULATOR_PID"
    if ! ns3_binary_supports_real_app2_sensors "$NS3_BIN"; then
        if [ "$GREENRAN_REQUIRE_REAL_APP2_SENSORS" = "1" ]; then
            echo -e "${RED}    ERRO: o binário ns-3 selecionado não exporta sensores mMTC reais para o App2.${NC}"
            echo -e "${RED}    Desative GREENRAN_REQUIRE_REAL_APP2_SENSORS=0 apenas se você aceitar rodar com fallback.${NC}"
            exit 1
        else
            echo -e "${YELLOW}    Binário ns-3 atual não exporta sensores mMTC reais; ativando fallback do simulador App2${NC}"
            start_app2_simulator
            APP2_SENSOR_SOURCE="mock-fallback"
        fi
    elif app2_has_real_sensor_ues; then
        APP2_REAL_SENSOR_UES_DETECTED="1"
        echo -e "${GREEN}    App2 usando sensores reais exportados do ns-3${NC}"
    else
        echo -e "${BLUE}    Aguardando o csv_to_metrics publicar sensores mMTC reais...${NC}"
        if wait_for_app2_real_sensor_ues "$GREENRAN_APP2_REAL_SENSOR_WAIT_SECONDS" 1; then
            APP2_REAL_SENSOR_UES_DETECTED="1"
            echo -e "${GREEN}    App2 usando sensores reais exportados do ns-3${NC}"
        elif [ "$GREENRAN_REQUIRE_REAL_APP2_SENSORS" = "1" ]; then
            echo -e "${RED}    ERRO: o ns-3 subiu, mas não expôs sensores mMTC reais no extended_metrics.${NC}"
            echo -e "${RED}    Tempo de espera configurado: ${GREENRAN_APP2_REAL_SENSOR_WAIT_SECONDS}s.${NC}"
            echo -e "${RED}    Verifique o cenário GreenRAN e a instrumentação dos UEs sensor antes de prosseguir.${NC}"
            exit 1
        else
            echo -e "${YELLOW}    ns-3 não expôs sensores mMTC; ativando fallback do simulador App2${NC}"
            start_app2_simulator
            APP2_SENSOR_SOURCE="mock-fallback"
        fi
    fi
fi
setsid env \
    GREENRAN_APP2_SENSOR_SOURCE="$GREENRAN_APP2_SENSOR_SOURCE" \
    GREENRAN_APP2_SENSOR_SOURCE_EFFECTIVE="$APP2_SENSOR_SOURCE" \
    GREENRAN_APP2_REAL_SENSOR_UES_DETECTED="$APP2_REAL_SENSOR_UES_DETECTED" \
    python3 ./apps/app2_monitoramento/backend/app.py --host "$APP2_HOST" --port "$APP2_PORT" > "$GREENRAN_APP2_LOG" 2>&1 &
echo $! > "$GREENRAN_APP2_PID"
sleep 2
echo -e "${GREEN}    App2 disponível em http://localhost:${APP2_PORT}${NC}"

echo -e "${BLUE}=== [11/13] Iniciando Stack CARLA/App3 ===${NC}"
start_carla_support_stack

echo -e "${BLUE}=== [12/13] Iniciando App3-Veicular ===${NC}"
setsid python3 ./apps/app3_veicular/backend/app.py --host "$APP3_HOST" --port "$APP3_PORT" > "$GREENRAN_APP3_LOG" 2>&1 &
echo $! > "$GREENRAN_APP3_PID"
sleep 2
echo -e "${GREEN}    App3 disponível em http://localhost:${APP3_PORT}${NC}"

echo -e "${BLUE}=== [12.1/13] Alternando eventos da coleta ===${NC}"
start_collection_event_alternator

echo -e "${BLUE}=== [13/13] Iniciando Monitoramento (Grafana + InfluxDB) ===${NC}"
# Verificar se Docker está disponível
if [ "$GREENRAN_ENABLE_MONITORING_STACK" != "1" ]; then
    echo -e "${YELLOW}    Monitoring stack desabilitado para esta instância${NC}"
elif command -v docker-compose &> /dev/null || docker compose version &> /dev/null; then
    # Verificar acesso ao Docker
    # Verificar Docker Compose (V1 usa "docker-compose", V2 usa "docker compose")
    if docker-compose --version &>/dev/null; then
        DOCKER_CMD="docker-compose"
        echo -e "${GREEN}    Docker Compose V1 detectado${NC}"
    elif docker compose version &>/dev/null; then
        DOCKER_CMD="docker compose"
        echo -e "${GREEN}    Docker Compose V2 detectado${NC}"
    else
        echo -e "${RED}    Docker Compose não encontrado!${NC}"
        echo -e "${YELLOW}    Instale com: sudo apt install docker-compose${NC}"
        exit 1
    fi
    
    cd $BASE_DIR/ns-O-RAN-flexric/mmwave-LENA-oran/GUI
    
    echo -e "${BLUE}    diretório: $(pwd)${NC}"
    
    # Parar serviços existentes (sem -v para preservar dados)
    echo -e "${BLUE}    Parando serviços existentes (preservando volumes)...${NC}"
    $DOCKER_CMD down 2>/dev/null || true
    sudo $DOCKER_CMD down 2>/dev/null || true
    
    # Iniciar serviços
    echo -e "${BLUE}    Iniciando serviços Docker...${NC}"
    if $DOCKER_CMD up -d; then
        echo -e "${GREEN}    ✓ Grafana iniciado em http://localhost:${GREENRAN_GRAFANA_PORT}${NC}"
        echo -e "${GREEN}    ✓ InfluxDB iniciado em http://${GREENRAN_INFLUXDB_HOST}:${GREENRAN_INFLUXDB_PORT}${NC}"
        echo -e "${GREEN}    ✓ GUI iniciado em http://localhost:${GREENRAN_GUI_PORT}${NC}"
    else
        echo -e "${RED}    ERRO ao iniciar Docker services${NC}"
        echo -e "${YELLOW}    Saída do erro:${NC}"
        $DOCKER_CMD up -d 2>&1 | head -20
        echo -e "${RED}    Tentando com sudo...${NC}"
        sudo $DOCKER_CMD down 2>/dev/null || true
        if sudo $DOCKER_CMD up -d; then
            echo -e "${GREEN}    ✓ Grafana iniciado com sudo em http://localhost:${GREENRAN_GRAFANA_PORT}${NC}"
            echo -e "${GREEN}    ✓ InfluxDB iniciado com sudo${NC}"
            echo -e "${GREEN}    ✓ GUI iniciado com sudo${NC}"
        else
            echo -e "${RED}    ERRO: Não foi possível iniciar Docker services${NC}"
            echo -e "${YELLOW}    Verifique se o Docker daemon está rodando:${NC}"
            echo -e "${YELLOW}    systemctl status docker${NC}"
        fi
    fi
    
    cd $BASE_DIR
    
    # Iniciar push de stats para InfluxDB
    sleep 5
    echo -e "${BLUE}    Iniciando Push Stats para InfluxDB...${NC}"
    setsid python3 ./push/push_stats_to_influx.py --interval "$GREENRAN_PUSH_INTERVAL" --host "$GREENRAN_INFLUXDB_HOST" --port "$GREENRAN_INFLUXDB_PORT" --db "$GREENRAN_INFLUXDB_DB" > "$GREENRAN_PUSH_STATS_LOG" 2>&1 &
    echo -e "${GREEN}    Push Stats iniciado (logs: $GREENRAN_PUSH_STATS_LOG)${NC}"
    
    # Iniciar push de CVaR para InfluxDB
    sleep 2
    echo -e "${BLUE}    Iniciando Push CVaR para InfluxDB...${NC}"
    setsid python3 ./push/push_cvar_to_influx.py --interval "$GREENRAN_PUSH_INTERVAL" > "$GREENRAN_PUSH_CVAR_LOG" 2>&1 &
    echo -e "${GREEN}    Push CVaR iniciado (logs: $GREENRAN_PUSH_CVAR_LOG)${NC}"

    # Iniciar push da App1 para InfluxDB
    sleep 2
    echo -e "${BLUE}    Iniciando Push App1 para InfluxDB...${NC}"
    setsid python3 ./push/push_app1_to_influx.py --interval "$GREENRAN_PUSH_INTERVAL" --host "$GREENRAN_INFLUXDB_HOST" --port "$GREENRAN_INFLUXDB_PORT" --db "$GREENRAN_INFLUXDB_DB" > "$GREENRAN_PUSH_APP1_LOG" 2>&1 &
    echo $! > "$GREENRAN_PUSH_APP1_PID"
    echo -e "${GREEN}    Push App1 iniciado (logs: $GREENRAN_PUSH_APP1_LOG)${NC}"

    # Iniciar push da App2 para InfluxDB
    sleep 2
    echo -e "${BLUE}    Iniciando Push App2 para InfluxDB...${NC}"
    setsid python3 ./push/push_app2_to_influx.py --interval "$GREENRAN_PUSH_INTERVAL" --host "$GREENRAN_INFLUXDB_HOST" --port "$GREENRAN_INFLUXDB_PORT" --db "$GREENRAN_INFLUXDB_DB" > "$GREENRAN_PUSH_APP2_LOG" 2>&1 &
    echo $! > "$GREENRAN_PUSH_APP2_PID"
    echo -e "${GREEN}    Push App2 iniciado (logs: $GREENRAN_PUSH_APP2_LOG)${NC}"

    # Iniciar push da App3 para InfluxDB
    sleep 2
    echo -e "${BLUE}    Iniciando Push App3 para InfluxDB...${NC}"
    setsid python3 ./push/push_app3_to_influx.py --interval "$GREENRAN_PUSH_INTERVAL" --host "$GREENRAN_INFLUXDB_HOST" --port "$GREENRAN_INFLUXDB_PORT" --db "$GREENRAN_INFLUXDB_DB" > "$GREENRAN_PUSH_APP3_LOG" 2>&1 &
    echo $! > "$GREENRAN_PUSH_APP3_PID"
    echo -e "${GREEN}    Push App3 iniciado (logs: $GREENRAN_PUSH_APP3_LOG)${NC}"

    # Criar/atualizar dashboard App1 no Grafana
    sleep 3
    echo -e "${BLUE}    Registrando dashboard App1 no Grafana...${NC}"
    if python3 ./push/create_grafana_app1.py > "$STATE_DIR/create_grafana_app1.log" 2>&1; then
        echo -e "${GREEN}    Dashboard App1 registrado no Grafana${NC}"
    else
        echo -e "${RED}    Aviso: falha ao registrar dashboard App1 no Grafana${NC}"
        echo -e "${RED}    Verifique: $STATE_DIR/create_grafana_app1.log${NC}"
    fi

    # Criar/atualizar dashboard App2 no Grafana
    sleep 1
    echo -e "${BLUE}    Registrando dashboard App2 no Grafana...${NC}"
    register_app2_dashboard_when_ready

    # Criar/atualizar dashboard App3 no Grafana
    sleep 1
    echo -e "${BLUE}    Registrando dashboard App3 no Grafana...${NC}"
    register_app3_dashboard_when_ready
else
    echo -e "${RED}    AVISO: Docker-compose não encontrado. Execute:${NC}"
    echo -e "${RED}    sudo apt install docker-compose${NC}"
fi

echo -e "${BLUE}=== [13/13] Iniciando Dashboard Python ===${NC}"
setsid python3 ./src/rapp_dashboard.py --host "$GREENRAN_DASHBOARD_HOST" --port "$GREENRAN_DASHBOARD_PORT" > "$GREENRAN_DASHBOARD_LOG" 2>&1 &
echo $! > "$GREENRAN_DASHBOARD_PID"
sleep 2
echo -e "${GREEN}    Dashboard disponível em http://localhost:${GREENRAN_DASHBOARD_PORT}${NC}"

if wait_for_armd_artifacts 15 2; then
    echo -e "${GREEN}    ARMD-GreenRAN respondeu nas políticas A1${NC}"
else
    echo -e "${YELLOW}    ARMD-GreenRAN ainda não apareceu nas políticas; exibindo estado atual${NC}"
fi
print_armd_operational_summary

echo -e "${BLUE}=== Sistema GreenRAN ativo ===${NC}"
snapshot_greenran_state

echo -e "\n${GREEN}==========================================${NC}"
echo -e "${GREEN}  SISTEMA GREENRAN INICIADO!${NC}"
echo -e "${GREEN}==========================================${NC}"
echo -e "Monitoramento:"
echo -e "  - Grafana:   http://localhost:${GREENRAN_GRAFANA_PORT} (admin/admin)"
echo -e "  - Dashboard: http://localhost:${GREENRAN_DASHBOARD_PORT}"
echo -e "  - App1:      http://localhost:${APP1_PORT}"
echo -e "  - App2:      http://localhost:${APP2_PORT}"
echo -e "  - App3:      http://localhost:${APP3_PORT}"
echo -e "  - GUI:       http://localhost:${GREENRAN_GUI_PORT}"
echo -e "  - Run Dir:   ${GREENRAN_RUN_DIR}"
echo -e "  - ARMD:      mode=${GREENRAN_ARMD_MODE} min_conf=${GREENRAN_ARMD_MIN_CONFIDENCE}"
echo -e "  - Etapas:    staged_control=${GREENRAN_STAGED_CONTROL} required_streak=${GREENRAN_STAGE_REQUIRED_STREAK}"
echo -e ""
echo -e "O rApp controla automaticamente os xApps:"
echo -e "  - SLICER: sempre ativo (prioridade)"
echo -e "  - VEHICLE: sempre ativo (App3)"
echo -e "  - ENERGY: ativado quando permitido"
echo -e "O observador de etapas nao injeta scenario_control:"
echo -e "  - Etapa 1: detecta ALLOWED natural"
echo -e "  - Etapa 2: detecta BLOCKED natural"
echo -e ""
echo -e "Use './stop_all.sh' para parar manualmente."
echo -e "==========================================\n"
