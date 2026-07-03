#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="${GREENRAN_PROJECT_DIR:-/home/robert/orange_nuclear}"
STATE_DIR="${GREENRAN_STATE_DIR:-$PROJECT_ROOT/runs/tasam_article_ns3_collection}"
NS3_BIN="${GREENRAN_NS3_BIN:-$PROJECT_ROOT/ns-O-RAN-flexric/mmwave-LENA-oran/build/scratch/ns3.42-Energy_saving_with_cell_utilization_scenario-default}"
NS3_CWD="${GREENRAN_NS3_CWD:-$PROJECT_ROOT/ns-O-RAN-flexric/mmwave-LENA-oran}"
NS3_LOG="${GREENRAN_NS3_LOG:-$STATE_DIR/ns3.log}"
PID_FILE="$STATE_DIR/ns3.pid"
SUPERVISOR_FILE="$STATE_DIR/ns3_supervisor.pid"
SIM_TIME="${GREENRAN_SIM_TIME:-100000}"
RAN_PRESSURE="${GREENRAN_RAN_PRESSURE_PROFILE:-drl_article_v1}"
ENABLE_TRACES="${GREENRAN_NS3_ENABLE_TRACES:-1}"
RESTART_DELAY="${GREENRAN_NS3_RESTART_DELAY:-2}"
NS3_UE_COUNT="${GREENRAN_NS3_UE_COUNT:-20}"
NS3_CAMERA_UE_COUNT="${GREENRAN_NS3_CAMERA_UE_COUNT:-3}"
NS3_VEHICLE_UE_COUNT="${GREENRAN_NS3_VEHICLE_UE_COUNT:-5}"
NS3_MMWAVE_ENB_NODES="${GREENRAN_NS3_MMWAVE_ENB_NODES:-4}"
NS3_UE_SPEED_MIN="${GREENRAN_NS3_UE_SPEED_MIN:-2}"
NS3_UE_SPEED_MAX="${GREENRAN_NS3_UE_SPEED_MAX:-4}"
NS3_ENABLE_TRACES_AFTER_ATTACH="${GREENRAN_NS3_ENABLE_TRACES_AFTER_ATTACH:-0}"
NS3_USE_MC_UE_DEVICES="${GREENRAN_NS3_USE_MC_UE_DEVICES:-true}"
NS3_E2LTE_ENABLED="${GREENRAN_NS3_E2LTE_ENABLED:-false}"
NS3_E2NR_ENABLED="${GREENRAN_NS3_E2NR_ENABLED:-false}"
NS3_E2DU_ENABLED="${GREENRAN_NS3_E2DU_ENABLED:-false}"
NS3_E2CUUP_ENABLED="${GREENRAN_NS3_E2CUUP_ENABLED:-false}"
NS3_E2CUCP_ENABLED="${GREENRAN_NS3_E2CUCP_ENABLED:-false}"
NS3_ENABLE_E2_FILE_LOGGING="${GREENRAN_NS3_ENABLE_E2_FILE_LOGGING:-false}"
NS3_BEARER_STATS_EPOCH_MS="${GREENRAN_NS3_BEARER_STATS_EPOCH_MS:-100}"

mkdir -p "$STATE_DIR"
cd "$NS3_CWD"

if [[ ! -x "$NS3_BIN" ]]; then
  echo "ERRO: binario ns-3 nao executavel: $NS3_BIN" >&2
  exit 1
fi

child_pid=""
handle_term() {
  if [[ -n "$child_pid" ]] && kill -0 "$child_pid" 2>/dev/null; then
    kill "$child_pid" 2>/dev/null || true
    wait "$child_pid" 2>/dev/null || true
  fi
  rm -f "$SUPERVISOR_FILE"
  exit 0
}
trap handle_term TERM INT

echo $$ > "$SUPERVISOR_FILE"
while true; do
  rm -f \
    "$NS3_CWD/DlPdcpStats.txt" \
    "$NS3_CWD/UlPdcpStats.txt" \
    "$NS3_CWD/DlRlcStats.txt" \
    "$NS3_CWD/UlRlcStats.txt" \
    "$NS3_CWD/DlE2PdcpStats.txt" \
    "$NS3_CWD/UlE2PdcpStats.txt" \
    "$NS3_CWD/DlE2RlcStats.txt" \
    "$NS3_CWD/UlE2RlcStats.txt" \
    "$NS3_CWD/DlE2PdcpStatsLte.txt" \
    "$NS3_CWD/UlE2PdcpStatsLte.txt" \
    "$NS3_CWD/DlE2RlcStatsLte.txt" \
    "$NS3_CWD/UlE2RlcStatsLte.txt"
  "$NS3_BIN" \
    --e2TermIp=127.0.0.1 \
    --simTime="$SIM_TIME" \
    --ranPressureProfile="$RAN_PRESSURE" \
    --enableTraces="$ENABLE_TRACES" \
    --ueCount="$NS3_UE_COUNT" \
    --cameraUeCount="$NS3_CAMERA_UE_COUNT" \
    --vehicleUeCount="$NS3_VEHICLE_UE_COUNT" \
    --mmWaveEnbNodes="$NS3_MMWAVE_ENB_NODES" \
    --ueSpeedMin="$NS3_UE_SPEED_MIN" \
    --ueSpeedMax="$NS3_UE_SPEED_MAX" \
    --bearerStatsEpochMs="$NS3_BEARER_STATS_EPOCH_MS" \
    --enableTracesAfterAttach="$NS3_ENABLE_TRACES_AFTER_ATTACH" \
    --useMcUeDevices="$NS3_USE_MC_UE_DEVICES" \
    --enableE2FileLogging="$NS3_ENABLE_E2_FILE_LOGGING" \
    --e2lteEnabled="$NS3_E2LTE_ENABLED" \
    --e2nrEnabled="$NS3_E2NR_ENABLED" \
    --e2du="$NS3_E2DU_ENABLED" \
    --e2cuUp="$NS3_E2CUUP_ENABLED" \
    --e2cuCp="$NS3_E2CUCP_ENABLED" \
    >> "$NS3_LOG" 2>&1 &
  child_pid=$!
  echo "$child_pid" > "$PID_FILE"
  wait "$child_pid" || exit_code=$?
  exit_code="${exit_code:-0}"
  printf '[NS3_SUPERVISOR] ns3 exited code %s at %s; restarting in %ss
' "$exit_code" "$(date --iso-8601=seconds)" "$RESTART_DELAY" >> "$NS3_LOG"
  child_pid=""
  sleep "$RESTART_DELAY"
  unset exit_code
  current_supervisor="$(cat "$SUPERVISOR_FILE" 2>/dev/null || true)"
  if [[ "$current_supervisor" != "$$" ]]; then
    exit 0
  fi
done
