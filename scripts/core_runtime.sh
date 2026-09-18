#!/bin/bash

# Runtime compartilhado para scripts operacionais do GreenRAN.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
STATE_DIR="${GREENRAN_STATE_DIR:-/tmp}"
RUNTIME_JSON="$PROJECT_DIR/config/core/runtime.json"
FIXED_SCENARIO_JSON="$PROJECT_DIR/config/greenran_fixed_scenario.json"

export GREENRAN_PROJECT_DIR="$PROJECT_DIR"
export GREENRAN_STATE_DIR="$STATE_DIR"
export GREENRAN_RUNS_DIR="${GREENRAN_RUNS_DIR:-$PROJECT_DIR/runs}"
export GREENRAN_INSTANCE_NAME="${GREENRAN_INSTANCE_NAME:-default}"
export GREENRAN_PORT_OFFSET="${GREENRAN_PORT_OFFSET:-0}"

resolve_ric_dir() {
    local candidates=(
        "$PROJECT_DIR/flexric/build_e2ap_v1"
        "$PROJECT_DIR/flexric/build"
    )
    local candidate
    for candidate in "${candidates[@]}"; do
        if [ -d "$candidate/examples" ]; then
            echo "$candidate"
            return 0
        fi
    done
    return 1
}
runtime_json_get() {
    local dotted_key="$1"
    python3 - "$RUNTIME_JSON" "$dotted_key" <<'PY'
import json
import sys
from pathlib import Path

json_path = Path(sys.argv[1])
dotted_key = sys.argv[2]

if not json_path.exists():
    raise SystemExit(1)

with open(json_path, "r", encoding="utf-8") as f:
    data = json.load(f)

value = data
for part in dotted_key.split("."):
    value = value[part]

if isinstance(value, bool):
    print("true" if value else "false")
else:
    print(value)
PY
}

fixed_scenario_json_get() {
    local dotted_key="$1"
    python3 - "${GREENRAN_FIXED_SCENARIO_CONFIG:-$FIXED_SCENARIO_JSON}" "$dotted_key" <<'PY'
import json
import sys
from pathlib import Path

json_path = Path(sys.argv[1])
dotted_key = sys.argv[2]

if not json_path.exists():
    raise SystemExit(1)

with open(json_path, "r", encoding="utf-8") as f:
    data = json.load(f)

value = data
for part in dotted_key.split("."):
    value = value[part]

if isinstance(value, bool):
    print("true" if value else "false")
else:
    print(value)
PY
}

load_greenran_runtime() {
    export BASE_DIR="$PROJECT_DIR"
    export NS3_DIR="$PROJECT_DIR/ns-O-RAN-flexric/mmwave-LENA-oran"
    export RIC_DIR="${GREENRAN_RIC_DIR:-$(resolve_ric_dir || true)}"
    if [ -z "$RIC_DIR" ]; then
        export RIC_DIR="$PROJECT_DIR/flexric/build"
    fi
    export FLEXRIC_LIB="$PROJECT_DIR/flexric_lib"
    export GREENRAN_PYTHON_BIN="${GREENRAN_PYTHON_BIN:-python3}"
    export GREENRAN_PYTHONPATH="${GREENRAN_PYTHONPATH:-$PROJECT_DIR:$PROJECT_DIR/src}"
    export PYTHONPATH="$GREENRAN_PYTHONPATH${PYTHONPATH:+:$PYTHONPATH}"

    export GREENRAN_DASHBOARD_HOST="${GREENRAN_DASHBOARD_HOST:-$(runtime_json_get dashboard.host)}"
    export GREENRAN_DASHBOARD_PORT="${GREENRAN_DASHBOARD_PORT:-$(runtime_json_get dashboard.port)}"
    export GREENRAN_GRAFANA_PORT="${GREENRAN_GRAFANA_PORT:-$(runtime_json_get monitoring.grafana_port)}"
    export GREENRAN_INFLUXDB_HOST="${GREENRAN_INFLUXDB_HOST:-$(runtime_json_get monitoring.influxdb_host)}"
    export GREENRAN_INFLUXDB_PORT="${GREENRAN_INFLUXDB_PORT:-$(runtime_json_get monitoring.influxdb_port)}"
    export GREENRAN_INFLUXDB_DB="${GREENRAN_INFLUXDB_DB:-$(runtime_json_get monitoring.influxdb_db)}"
    export GREENRAN_GUI_PORT="${GREENRAN_GUI_PORT:-$(runtime_json_get monitoring.gui_port)}"
    export GREENRAN_PUSH_INTERVAL="${GREENRAN_PUSH_INTERVAL:-$(runtime_json_get monitoring.push_interval_seconds)}"
    export GREENRAN_ORCHESTRATOR_INTERVAL="${GREENRAN_ORCHESTRATOR_INTERVAL:-$(runtime_json_get orchestrator.interval_seconds)}"
    export GREENRAN_ORCHESTRATOR_SYNTHETIC_DAYS="${GREENRAN_ORCHESTRATOR_SYNTHETIC_DAYS:-$(runtime_json_get orchestrator.synthetic_days)}"
    export GREENRAN_ML_ENABLED="${GREENRAN_ML_ENABLED:-$(runtime_json_get ml.enabled)}"
    export GREENRAN_ML_RETRAIN_ENABLED="${GREENRAN_ML_RETRAIN_ENABLED:-$(runtime_json_get ml.retrain_enabled)}"
    export GREENRAN_COLLECTOR_POLL_INTERVAL="${GREENRAN_COLLECTOR_POLL_INTERVAL:-$(runtime_json_get collector.poll_interval_seconds)}"
    export GREENRAN_PDCP_STALE_SECONDS="${GREENRAN_PDCP_STALE_SECONDS:-30}"
    export GREENRAN_REQUIRE_REAL_PDCP="${GREENRAN_REQUIRE_REAL_PDCP:-0}"
    export GREENRAN_NS3_BEARER_STATS_EPOCH_MS="${GREENRAN_NS3_BEARER_STATS_EPOCH_MS:-100}"
    export GREENRAN_SIM_TIME="${GREENRAN_SIM_TIME:-$(runtime_json_get simulation.default_sim_time_seconds)}"
    export GREENRAN_DB_SNAPSHOT_INTERVAL="${GREENRAN_DB_SNAPSHOT_INTERVAL:-1800}"
    export GREENRAN_DB_SNAPSHOT_RETENTION="${GREENRAN_DB_SNAPSHOT_RETENTION:-2}"
    export GREENRAN_TASAM_EXPORT_INTERVAL="${GREENRAN_TASAM_EXPORT_INTERVAL:-1800}"
    # Keep the live article export bounded so traces do not grow without limit.
    export GREENRAN_TASAM_EXPORT_LIMIT="${GREENRAN_TASAM_EXPORT_LIMIT:-20000}"
    export GREENRAN_TASAM_EXPORT_ENABLED="${GREENRAN_TASAM_EXPORT_ENABLED:-1}"
    export GREENRAN_TASAM_EXPORT_ALLOW_PROXY="${GREENRAN_TASAM_EXPORT_ALLOW_PROXY:-0}"
    export GREENRAN_TASAM_TRUE_ONLINE_ENABLED="${GREENRAN_TASAM_TRUE_ONLINE_ENABLED:-1}"
    export GREENRAN_TASAM_TRUE_ONLINE_INTERVAL="${GREENRAN_TASAM_TRUE_ONLINE_INTERVAL:-60}"
    export GREENRAN_TASAM_TRUE_ONLINE_MIN_NEW_SNAPSHOTS="${GREENRAN_TASAM_TRUE_ONLINE_MIN_NEW_SNAPSHOTS:-500}"
    export GREENRAN_TASAM_TRUE_ONLINE_MIN_TRAINABLE_TRANSITIONS="${GREENRAN_TASAM_TRUE_ONLINE_MIN_TRAINABLE_TRANSITIONS:-1500}"
    export GREENRAN_TASAM_TRUE_ONLINE_BOOTSTRAP_EPOCHS="${GREENRAN_TASAM_TRUE_ONLINE_BOOTSTRAP_EPOCHS:-5}"
    export GREENRAN_TASAM_TRUE_ONLINE_EPOCHS_PER_UPDATE="${GREENRAN_TASAM_TRUE_ONLINE_EPOCHS_PER_UPDATE:-2}"
    export GREENRAN_TASAM_TRUE_ONLINE_TRAIN_PYTHON="${GREENRAN_TASAM_TRUE_ONLINE_TRAIN_PYTHON:-/usr/bin/python3}"
    export GREENRAN_RAPP_ONLINE_RETRAIN_INTERVAL="${GREENRAN_RAPP_ONLINE_RETRAIN_INTERVAL:-300}"
    export GREENRAN_RAPP_ONLINE_RETRAIN_TARGET="${GREENRAN_RAPP_ONLINE_RETRAIN_TARGET:-1000}"
    export GREENRAN_RAPP_ONLINE_RETRAIN_MIN_NEW_ROWS="${GREENRAN_RAPP_ONLINE_RETRAIN_MIN_NEW_ROWS:-100}"
    export GREENRAN_RAPP_ONLINE_RETRAIN_MIN_RF_ACCURACY="${GREENRAN_RAPP_ONLINE_RETRAIN_MIN_RF_ACCURACY:-0.95}"
    export GREENRAN_RAPP_ONLINE_RETRAIN_MIN_R2="${GREENRAN_RAPP_ONLINE_RETRAIN_MIN_R2:-0.80}"
    export GREENRAN_RAPP_ONLINE_RETRAIN_MIN_HEALTHY_ALLOWED_RECALL="${GREENRAN_RAPP_ONLINE_RETRAIN_MIN_HEALTHY_ALLOWED_RECALL:-0.70}"

    export GREENRAN_FIXED_SCENARIO_CONFIG="${GREENRAN_FIXED_SCENARIO_CONFIG:-$FIXED_SCENARIO_JSON}"
    export GREENRAN_FIXED_TOTAL_UES="${GREENRAN_FIXED_TOTAL_UES:-$(fixed_scenario_json_get ns3.total_ues)}"
    export GREENRAN_FIXED_ACTIVE_CAMERAS="${GREENRAN_FIXED_ACTIVE_CAMERAS:-$(fixed_scenario_json_get apps.app1.active_cameras)}"
    export GREENRAN_FIXED_VEHICLE_BASE_IMSI="${GREENRAN_FIXED_VEHICLE_BASE_IMSI:-$(fixed_scenario_json_get apps.app3.base_imsi)}"
    export GREENRAN_FIXED_MAX_VEHICLES="${GREENRAN_FIXED_MAX_VEHICLES:-$(fixed_scenario_json_get apps.app3.max_vehicles)}"
    export GREENRAN_FIXED_BACKGROUND_UES="${GREENRAN_FIXED_BACKGROUND_UES:-$((GREENRAN_FIXED_TOTAL_UES - GREENRAN_FIXED_ACTIVE_CAMERAS))}"

    export GREENRAN_DB_PATH="$STATE_DIR/rapp_data_lake.db"
    export GREENRAN_RAPP_LOG="$STATE_DIR/rapp.log"
    export GREENRAN_DASHBOARD_LOG="$STATE_DIR/dashboard.log"
    export GREENRAN_WATCHDOG_LOG="$STATE_DIR/watchdog.log"
    export GREENRAN_RIC_LOG="$STATE_DIR/ric.log"
    export GREENRAN_NS3_LOG="$STATE_DIR/ns3.log"
    export GREENRAN_XAPP_SLICER_LOG="$STATE_DIR/xapp_slicer.log"
    export GREENRAN_XAPP_ENERGY_LOG="$STATE_DIR/xapp_energy.log"
    export GREENRAN_XAPP_VEHICLE_LOG="$STATE_DIR/xapp_vehicle.log"
    export GREENRAN_CSV_LOG="$STATE_DIR/csv_metrics.log"
    export GREENRAN_PUSH_STATS_LOG="$STATE_DIR/push_stats.log"
    export GREENRAN_PUSH_CVAR_LOG="$STATE_DIR/push_cvar.log"
    export GREENRAN_PUSH_APP1_LOG="$STATE_DIR/push_app1.log"
    export GREENRAN_PUSH_APP2_LOG="$STATE_DIR/push_app2.log"
    export GREENRAN_PUSH_APP3_LOG="$STATE_DIR/push_app3.log"
    export GREENRAN_APP1_LOG="$STATE_DIR/app1_vigilancia.log"
    export GREENRAN_APP1_SIMULATOR_LOG="$STATE_DIR/app1_camera_simulator.log"
    export GREENRAN_APP2_LOG="$STATE_DIR/app2_monitoramento.log"
    export GREENRAN_APP2_SIMULATOR_LOG="$STATE_DIR/app2_simulator.log"
    export GREENRAN_APP3_LOG="$STATE_DIR/app3_veicular.log"
    export GREENRAN_CARLA_BRIDGE_LOG="$STATE_DIR/carla_bridge.log"
    export GREENRAN_CARLA_MAPPER_LOG="$STATE_DIR/carla_ns3_mapper.log"
    export GREENRAN_MARL_GATE_WATCH_LOG="$STATE_DIR/marl_runtime_gate_watch.log"
    export GREENRAN_DB_SNAPSHOT_LOG="$STATE_DIR/db_snapshot.log"
    export GREENRAN_TASAM_EXPORT_LOG="$STATE_DIR/tasam_article_export.log"
    export GREENRAN_TASAM_TRUE_ONLINE_LOG="$STATE_DIR/tasam_true_online_real.log"
    export GREENRAN_RAPP_ONLINE_RETRAIN_LOG="$STATE_DIR/rapp_online_retrain.log"
    export GREENRAN_DB_SNAPSHOT_DIR="${GREENRAN_DB_SNAPSHOT_DIR:-$STATE_DIR/db_snapshots}"
    export GREENRAN_DB_SNAPSHOT_ENABLED="${GREENRAN_DB_SNAPSHOT_ENABLED:-1}"
    export GREENRAN_TASAM_EXPORT_DIR="${GREENRAN_TASAM_EXPORT_DIR:-$STATE_DIR/tasam_article_export}"
    export GREENRAN_TASAM_TRUE_ONLINE_DIR="${GREENRAN_TASAM_TRUE_ONLINE_DIR:-$STATE_DIR/tasam_true_online_real}"

    local port_offset="${GREENRAN_PORT_OFFSET:-0}"
    export APP1_HOST="${APP1_HOST:-0.0.0.0}"
    export APP1_PORT="${APP1_PORT:-$((5100 + port_offset))}"
    export GREENRAN_APP1_CAMERA_SOURCE_MODE="${GREENRAN_APP1_CAMERA_SOURCE_MODE:-simulated}"
    export GREENRAN_APP1_CAMERAS_BOOTSTRAP="${GREENRAN_APP1_CAMERAS_BOOTSTRAP:-$BASE_DIR/config/core/app1_cameras.json}"
    export GREENRAN_APP1_SIMULATOR_INTERVAL="${GREENRAN_APP1_SIMULATOR_INTERVAL:-20}"
    export APP2_HOST="${APP2_HOST:-0.0.0.0}"
    export APP2_PORT="${APP2_PORT:-$((5200 + port_offset))}"
    export APP3_HOST="${APP3_HOST:-0.0.0.0}"
    export APP3_PORT="${APP3_PORT:-$((5300 + port_offset))}"
    export GREENRAN_ENABLE_CARLA_STACK="${GREENRAN_ENABLE_CARLA_STACK:-1}"
    export GREENRAN_CARLA_MODE="${GREENRAN_CARLA_MODE:-mock}"
    export GREENRAN_CARLA_HOST="${GREENRAN_CARLA_HOST:-127.0.0.1}"
    export GREENRAN_CARLA_PORT="${GREENRAN_CARLA_PORT:-2000}"
    export GREENRAN_CARLA_POLL_INTERVAL="${GREENRAN_CARLA_POLL_INTERVAL:-0.2}"
    export GREENRAN_CARLA_MAPPER_POLL_INTERVAL="${GREENRAN_CARLA_MAPPER_POLL_INTERVAL:-0.5}"
    export GREENRAN_CARLA_FALLBACK_TO_MOCK="${GREENRAN_CARLA_FALLBACK_TO_MOCK:-1}"
    export GREENRAN_ARMD_SUMMARY="${GREENRAN_ARMD_SUMMARY:-$PROJECT_DIR/runs/graphsage_article00_hybrid_final/hybrid_final_summary.json}"
    export GREENRAN_ARMD_MODE="${GREENRAN_ARMD_MODE:-assist}"
    export GREENRAN_ARMD_MIN_CONFIDENCE="${GREENRAN_ARMD_MIN_CONFIDENCE:-0.85}"
    export GREENRAN_STAGED_CONTROL="${GREENRAN_STAGED_CONTROL:-1}"
    export GREENRAN_STAGE_REQUIRED_STREAK="${GREENRAN_STAGE_REQUIRED_STREAK:-3}"
    export GREENRAN_ENABLE_MARL_GATE_WATCHER="${GREENRAN_ENABLE_MARL_GATE_WATCHER:-1}"
    export GREENRAN_MARL_GATE_WATCH_REFRESH="${GREENRAN_MARL_GATE_WATCH_REFRESH:-15}"
    export GREENRAN_MARL_GATE_WATCH_WINDOW="${GREENRAN_MARL_GATE_WATCH_WINDOW:-300}"
    export GREENRAN_MARL_GATE_WATCH_MIN_SAMPLES="${GREENRAN_MARL_GATE_WATCH_MIN_SAMPLES:-120}"
    export GREENRAN_MARL_GATE_WATCH_MIN_CHECKPOINT_COVERAGE="${GREENRAN_MARL_GATE_WATCH_MIN_CHECKPOINT_COVERAGE:-0.90}"

    export GREENRAN_DASHBOARD_PID="$STATE_DIR/dashboard.pid"
    export GREENRAN_WATCHDOG_PID="$STATE_DIR/watchdog.pid"
    export GREENRAN_RIC_PID="$STATE_DIR/ric.pid"
    export GREENRAN_NS3_PID="$STATE_DIR/ns3.pid"
    export GREENRAN_NS3_SUPERVISOR_PID="$STATE_DIR/ns3_supervisor.pid"
    export GREENRAN_XAPP_SLICER_PID="$STATE_DIR/xapp_slicer.pid"
    export GREENRAN_XAPP_ENERGY_PID="$STATE_DIR/xapp_energy.pid"
    export GREENRAN_XAPP_VEHICLE_PID="$STATE_DIR/xapp_vehicle.pid"
    export GREENRAN_CSV_PID="$STATE_DIR/csv_metrics.pid"
    export GREENRAN_RAPP_PID="$STATE_DIR/rapp.pid"
    export GREENRAN_PUSH_APP1_PID="$STATE_DIR/push_app1.pid"
    export GREENRAN_PUSH_APP2_PID="$STATE_DIR/push_app2.pid"
    export GREENRAN_PUSH_APP3_PID="$STATE_DIR/push_app3.pid"
    export GREENRAN_APP1_PID="$STATE_DIR/app1_vigilancia.pid"
    export GREENRAN_APP1_SIMULATOR_PID="$STATE_DIR/app1_camera_simulator.pid"
    export GREENRAN_APP2_PID="$STATE_DIR/app2_monitoramento.pid"
    export GREENRAN_APP2_SIMULATOR_PID="$STATE_DIR/app2_simulator.pid"
    export GREENRAN_APP3_PID="$STATE_DIR/app3_veicular.pid"
    export GREENRAN_CARLA_BRIDGE_PID="$STATE_DIR/carla_bridge.pid"
    export GREENRAN_CARLA_MAPPER_PID="$STATE_DIR/carla_ns3_mapper.pid"
    export GREENRAN_STAGE_CONTROLLER_PID="$STATE_DIR/runtime_stage_controller.pid"
    export GREENRAN_MARL_GATE_WATCH_PID="$STATE_DIR/marl_runtime_gate_watch.pid"
    export GREENRAN_DB_SNAPSHOT_PID="$STATE_DIR/db_snapshot.pid"
    export GREENRAN_TASAM_EXPORT_PID="$STATE_DIR/tasam_article_export.pid"
    export GREENRAN_TASAM_TRUE_ONLINE_PID="$STATE_DIR/tasam_true_online_real.pid"
    export GREENRAN_RAPP_ONLINE_RETRAIN_PID="$STATE_DIR/rapp_online_retrain.pid"

    export GREENRAN_STAGE_CONTROLLER_LOG="$STATE_DIR/runtime_stage_controller.log"

    mkdir -p "$GREENRAN_RUNS_DIR"
    mkdir -p "$STATE_DIR/xapp_metrics" "$STATE_DIR/xapp_intents" "$STATE_DIR/rapp_policies" "$STATE_DIR/app3_veicular" "$GREENRAN_DB_SNAPSHOT_DIR" "$GREENRAN_TASAM_TRUE_ONLINE_DIR"
    # Do not create the historical article-export directory for controlled
    # campaigns that explicitly disable article export.  This keeps the
    # artifact contract truthful: disabled means no export directory either.
    if [[ "${GREENRAN_TASAM_EXPORT_ENABLED:-1}" == "1" ]]; then
        mkdir -p "$GREENRAN_TASAM_EXPORT_DIR"
    fi
}

create_greenran_run() {
    local run_label="${1:-manual}"
    local timestamp
    timestamp="$(date '+%Y%m%d_%H%M%S')"

    export GREENRAN_RUN_ID="${timestamp}_${run_label}"
    export GREENRAN_RUN_DIR="$GREENRAN_RUNS_DIR/$GREENRAN_RUN_ID"
    export GREENRAN_RUN_META_DIR="$GREENRAN_RUN_DIR/meta"
    export GREENRAN_RUN_LOGS_DIR="$GREENRAN_RUN_DIR/logs"
    export GREENRAN_RUN_STATE_DIR="$GREENRAN_RUN_DIR/state_snapshot"

    mkdir -p "$GREENRAN_RUN_META_DIR" "$GREENRAN_RUN_LOGS_DIR" "$GREENRAN_RUN_STATE_DIR"

    cat > "$GREENRAN_RUN_META_DIR/runtime.env" <<EOF
GREENRAN_RUN_ID=$GREENRAN_RUN_ID
GREENRAN_PROJECT_DIR=$GREENRAN_PROJECT_DIR
GREENRAN_STATE_DIR=$GREENRAN_STATE_DIR
GREENRAN_RUNS_DIR=$GREENRAN_RUNS_DIR
GREENRAN_DASHBOARD_HOST=$GREENRAN_DASHBOARD_HOST
GREENRAN_DASHBOARD_PORT=$GREENRAN_DASHBOARD_PORT
GREENRAN_GRAFANA_PORT=$GREENRAN_GRAFANA_PORT
GREENRAN_INFLUXDB_HOST=$GREENRAN_INFLUXDB_HOST
GREENRAN_INFLUXDB_PORT=$GREENRAN_INFLUXDB_PORT
GREENRAN_INFLUXDB_DB=$GREENRAN_INFLUXDB_DB
GREENRAN_GUI_PORT=$GREENRAN_GUI_PORT
GREENRAN_PUSH_INTERVAL=$GREENRAN_PUSH_INTERVAL
GREENRAN_ORCHESTRATOR_INTERVAL=$GREENRAN_ORCHESTRATOR_INTERVAL
GREENRAN_ORCHESTRATOR_SYNTHETIC_DAYS=$GREENRAN_ORCHESTRATOR_SYNTHETIC_DAYS
GREENRAN_COLLECTOR_POLL_INTERVAL=$GREENRAN_COLLECTOR_POLL_INTERVAL
GREENRAN_SIM_TIME=$GREENRAN_SIM_TIME
GREENRAN_ARMD_SUMMARY=$GREENRAN_ARMD_SUMMARY
GREENRAN_ARMD_MODE=$GREENRAN_ARMD_MODE
GREENRAN_ARMD_MIN_CONFIDENCE=$GREENRAN_ARMD_MIN_CONFIDENCE
GREENRAN_STAGED_CONTROL=$GREENRAN_STAGED_CONTROL
GREENRAN_STAGE_REQUIRED_STREAK=$GREENRAN_STAGE_REQUIRED_STREAK
APP1_HOST=$APP1_HOST
APP1_PORT=$APP1_PORT
GREENRAN_APP1_CAMERA_SOURCE_MODE=$GREENRAN_APP1_CAMERA_SOURCE_MODE
GREENRAN_APP1_SIMULATOR_INTERVAL=$GREENRAN_APP1_SIMULATOR_INTERVAL
APP2_HOST=$APP2_HOST
APP2_PORT=$APP2_PORT
APP3_HOST=$APP3_HOST
APP3_PORT=$APP3_PORT
EOF

    cp "$RUNTIME_JSON" "$GREENRAN_RUN_META_DIR/runtime.json"

    cat > "$GREENRAN_RUN_META_DIR/README.txt" <<EOF
GreenRAN run directory
run_id: $GREENRAN_RUN_ID
created_at: $(date --iso-8601=seconds)
project_dir: $GREENRAN_PROJECT_DIR
state_dir: $GREENRAN_STATE_DIR

This directory stores metadata, log snapshots, and state snapshots
for this execution.
EOF

    ln -sfn "$GREENRAN_RUN_DIR" "$GREENRAN_RUNS_DIR/latest"
    echo "$GREENRAN_RUN_DIR"
}

snapshot_greenran_state() {
    [ -n "$GREENRAN_RUN_DIR" ] || return 0

    local files=(
        "$GREENRAN_RAPP_LOG"
        "$GREENRAN_RIC_LOG"
        "$GREENRAN_NS3_LOG"
        "$GREENRAN_XAPP_SLICER_LOG"
        "$GREENRAN_XAPP_ENERGY_LOG"
        "$GREENRAN_XAPP_VEHICLE_LOG"
        "$GREENRAN_CSV_LOG"
        "$GREENRAN_DB_SNAPSHOT_LOG"
        "$GREENRAN_TASAM_EXPORT_LOG"
        "$GREENRAN_TASAM_TRUE_ONLINE_LOG"
        "$GREENRAN_RAPP_ONLINE_RETRAIN_LOG"
        "$GREENRAN_PUSH_STATS_LOG"
        "$GREENRAN_PUSH_CVAR_LOG"
        "$GREENRAN_PUSH_APP1_LOG"
        "$GREENRAN_PUSH_APP2_LOG"
        "$GREENRAN_PUSH_APP3_LOG"
        "$GREENRAN_APP1_LOG"
        "$GREENRAN_APP2_LOG"
        "$GREENRAN_APP3_LOG"
        "$GREENRAN_APP2_SIMULATOR_LOG"
        "$GREENRAN_DASHBOARD_LOG"
        "$GREENRAN_WATCHDOG_LOG"
        "$GREENRAN_DB_PATH"
        "$GREENRAN_DB_SNAPSHOT_DIR/latest_snapshot.json"
        "$GREENRAN_TASAM_EXPORT_DIR/latest_export.json"
        "$GREENRAN_TASAM_EXPORT_DIR/rapp_online_retrain_latest.json"
        "$GREENRAN_TASAM_EXPORT_DIR/tasam_article_export_summary.json"
        "$GREENRAN_TASAM_EXPORT_DIR/tasam_article_trace.jsonl"
        "$GREENRAN_TASAM_EXPORT_DIR/rapp_online_trainable_summary.json"
        "$GREENRAN_TASAM_EXPORT_DIR/rapp_online_trainable_trace.jsonl"
        "$GREENRAN_TASAM_TRUE_ONLINE_DIR/true_online_status.json"
        "$GREENRAN_TASAM_TRUE_ONLINE_DIR/true_online_state.json"
        "$GREENRAN_TASAM_TRUE_ONLINE_DIR/tasam_true_online_real_export_summary.json"
        "$GREENRAN_TASAM_TRUE_ONLINE_DIR/tasam_true_online_real_trace.jsonl"
        "$GREENRAN_TASAM_TRUE_ONLINE_DIR/tasam_selective/tasam_marl_summary.json"
        "$GREENRAN_TASAM_TRUE_ONLINE_DIR/tasam_selective/epoch_history.jsonl"
        "$STATE_DIR/xapp_metrics/extended_metrics.json"
        "$STATE_DIR/xapp_metrics/metrics.json"
        "$STATE_DIR/rapp_policies/energy_policy.json"
        "$STATE_DIR/rapp_policies/slice_policy.json"
        "$STATE_DIR/rapp_policies/policy_status.json"
        "$STATE_DIR/xapp_intents/rapp_decision.txt"
        "$STATE_DIR/app1_vigilancia/monitoring_snapshot.json"
        "$STATE_DIR/app1_vigilancia/video_analyses.json"
        "$STATE_DIR/app1_vigilancia/uploaded_videos.json"
        "$STATE_DIR/app2_monitoramento/monitoring_snapshot.json"
        "$STATE_DIR/app3_veicular/monitoring_snapshot.json"
        "$STATE_DIR/carla_state/vehicles.json"
        "$STATE_DIR/carla_state/vehicle_network_map.json"
    )

    for file in "${files[@]}"; do
        if [ -f "$file" ]; then
            cp -f "$file" "$GREENRAN_RUN_STATE_DIR/$(basename "$file")" 2>/dev/null || true
        fi
    done
}


print_greenran_fixed_scenario_summary() {
    echo "Cenario fixo ativo:"
    echo "  - ns-3 UEs: ${GREENRAN_FIXED_TOTAL_UES} (${GREENRAN_FIXED_ACTIVE_CAMERAS} cameras + ${GREENRAN_FIXED_BACKGROUND_UES} background)"
    echo "  - App3 veiculos: ate ${GREENRAN_FIXED_MAX_VEHICLES} (IMSI base ${GREENRAN_FIXED_VEHICLE_BASE_IMSI})"
}
