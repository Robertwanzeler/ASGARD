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
NS3_ENABLE_ENERGY_CSV="${GREENRAN_NS3_ENABLE_ENERGY_CSV:-0}"
NS3_ENERGY_OUTPUT_DIR="${GREENRAN_NS3_ENERGY_OUTPUT_DIR:-$STATE_DIR/ns3_energy}"
NS3_RNG_RUN="${GREENRAN_NS3_RNG_RUN:-1}"
NS3_FIXED_POWER_PERCENT="${GREENRAN_NS3_FIXED_POWER_PERCENT:-100}"
RESTART_DELAY="${GREENRAN_NS3_RESTART_DELAY:-2}"
SINGLE_RUN="${GREENRAN_NS3_SINGLE_RUN:-0}"
NS3_UE_COUNT="${GREENRAN_NS3_UE_COUNT:-20}"
NS3_CAMERA_UE_COUNT="${GREENRAN_NS3_CAMERA_UE_COUNT:-3}"
NS3_VEHICLE_UE_COUNT="${GREENRAN_NS3_VEHICLE_UE_COUNT:-5}"
NS3_MMWAVE_ENB_NODES="${GREENRAN_NS3_MMWAVE_ENB_NODES:-4}"
NS3_UE_SPEED_MIN="${GREENRAN_NS3_UE_SPEED_MIN:-2}"
NS3_UE_SPEED_MAX="${GREENRAN_NS3_UE_SPEED_MAX:-4}"
NS3_ENABLE_TRACES_AFTER_ATTACH="${GREENRAN_NS3_ENABLE_TRACES_AFTER_ATTACH:-0}"
NS3_NATIVE_MINIMAL_TRACES="${GREENRAN_NS3_NATIVE_MINIMAL_TRACES:-0}"
NS3_NATIVE_AGGREGATED_EVIDENCE="${GREENRAN_NS3_NATIVE_AGGREGATED_EVIDENCE:-0}"
NS3_NATIVE_EVIDENCE_PERIOD_MS="${GREENRAN_NS3_NATIVE_EVIDENCE_PERIOD_MS:-500}"
NS3_USE_MC_UE_DEVICES="${GREENRAN_NS3_USE_MC_UE_DEVICES:-true}"
NS3_E2LTE_ENABLED="${GREENRAN_NS3_E2LTE_ENABLED:-false}"
NS3_E2NR_ENABLED="${GREENRAN_NS3_E2NR_ENABLED:-false}"
NS3_E2DU_ENABLED="${GREENRAN_NS3_E2DU_ENABLED:-false}"
NS3_E2CUUP_ENABLED="${GREENRAN_NS3_E2CUUP_ENABLED:-false}"
NS3_E2CUCP_ENABLED="${GREENRAN_NS3_E2CUCP_ENABLED:-false}"
NS3_E2_CONTROL_ENABLED="${GREENRAN_NS3_E2_CONTROL_ENABLED:-false}"
NS3_ENABLE_E2_FILE_LOGGING="${GREENRAN_NS3_ENABLE_E2_FILE_LOGGING:-false}"
NS3_BEARER_STATS_EPOCH_MS="${GREENRAN_NS3_BEARER_STATS_EPOCH_MS:-100}"
NS3_BANDWIDTH_MHZ="${GREENRAN_NS3_BANDWIDTH_MHZ:-100}"
NS3_CAMERA_PACKET_SIZE_BYTES="${GREENRAN_NS3_CAMERA_PACKET_SIZE_BYTES:-1000}"
NS3_CAMERA_PACKET_INTERVAL_US="${GREENRAN_NS3_CAMERA_PACKET_INTERVAL_US:-320}"
NS3_BACKGROUND_PACKET_SIZE_BYTES="${GREENRAN_NS3_BACKGROUND_PACKET_SIZE_BYTES:-128}"
NS3_BACKGROUND_PACKET_INTERVAL_US="${GREENRAN_NS3_BACKGROUND_PACKET_INTERVAL_US:-10000}"
NS3_VEHICLE_PACKET_SIZE_BYTES="${GREENRAN_NS3_VEHICLE_PACKET_SIZE_BYTES:-800}"
NS3_VEHICLE_PACKET_INTERVAL_US="${GREENRAN_NS3_VEHICLE_PACKET_INTERVAL_US:-4000}"
NS3_ACTIVE_CELLS="${GREENRAN_NS3_ACTIVE_CELLS:-$NS3_MMWAVE_ENB_NODES}"
E2_PORT_PLAN_JSON="$(python3 "$PROJECT_ROOT/src/greenran_e2_ports.py" \
  --e2-term-port "${GREENRAN_E2_TERM_PORT:-40301}" \
  --e2-xapp-port "${GREENRAN_E2_XAPP_PORT:-40302}" \
  --e2-local-port "${GREENRAN_E2_LOCAL_PORT:-40320}")" || {
  echo "ERRO: plano de portas E2 inválido; ns-3 não será iniciado" >&2
  exit 2
}
echo "E2 port plan: $E2_PORT_PLAN_JSON"

if [[ "$RAN_PRESSURE" == "tasam_training_balanced_v6_1_v2x_gbr_deadline_mc_fallback_baseline_max" ]]; then
  NS3_USE_MC_UE_DEVICES=true
  NS3_E2LTE_ENABLED=true
  # E2-LTE provides the fallback readback. The rApp native contract keeps
  # E2-NR disabled because that report path is not part of this campaign.
  NS3_E2NR_ENABLED=false
  NS3_E2DU_ENABLED=true
fi
if [[ "$RAN_PRESSURE" == "tasam_training_economic_vehicle_safe_v1" ]]; then
  # The economic curriculum is safe at the policy level, but native vehicle
  # traffic uses the validated LTE-anchored MC fallback.  The previous
  # mmWave-only path produced 90--100% packet loss even at the 100% baseline.
  NS3_USE_MC_UE_DEVICES=true
  NS3_E2LTE_ENABLED=true
  NS3_E2NR_ENABLED=false
  NS3_E2DU_ENABLED=true
fi

mkdir -p "$STATE_DIR" "$NS3_ENERGY_OUTPUT_DIR"
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
    "$NS3_CWD/UlE2RlcStatsLte.txt" \
    "$NS3_CWD/VehiclePdcpPduTrace.csv" \
    "$NS3_CWD/VehicleLinkTrace.csv" \
    "$NS3_CWD/VehicleCellSinrTrace.csv"
  GREENRAN_NS3_ENERGY_OUTPUT_DIR="$NS3_ENERGY_OUTPUT_DIR" \
  "$NS3_BIN" \
    --e2TermIp=127.0.0.1 \
    --simTime="$SIM_TIME" \
    --ranPressureProfile="$RAN_PRESSURE" \
    --enableTraces="$ENABLE_TRACES" \
    --enableEnergyCsvDump="$NS3_ENABLE_ENERGY_CSV" \
    --energyOutputDir="$NS3_ENERGY_OUTPUT_DIR" \
    --RngRun="$NS3_RNG_RUN" \
    --fixedTxPowerPercent="$NS3_FIXED_POWER_PERCENT" \
    --activeCells="$NS3_ACTIVE_CELLS" \
    --ueCount="$NS3_UE_COUNT" \
    --cameraUeCount="$NS3_CAMERA_UE_COUNT" \
    --vehicleUeCount="$NS3_VEHICLE_UE_COUNT" \
    --mmWaveEnbNodes="$NS3_MMWAVE_ENB_NODES" \
    --ueSpeedMin="$NS3_UE_SPEED_MIN" \
    --ueSpeedMax="$NS3_UE_SPEED_MAX" \
    --bandwidthMHz="$NS3_BANDWIDTH_MHZ" \
    --cameraPacketSizeBytes="$NS3_CAMERA_PACKET_SIZE_BYTES" \
    --cameraPacketIntervalUs="$NS3_CAMERA_PACKET_INTERVAL_US" \
    --backgroundPacketSizeBytes="$NS3_BACKGROUND_PACKET_SIZE_BYTES" \
    --backgroundPacketIntervalUs="$NS3_BACKGROUND_PACKET_INTERVAL_US" \
    --vehiclePacketSizeBytes="$NS3_VEHICLE_PACKET_SIZE_BYTES" \
    --vehiclePacketIntervalUs="$NS3_VEHICLE_PACKET_INTERVAL_US" \
    --bearerStatsEpochMs="$NS3_BEARER_STATS_EPOCH_MS" \
    --enableTracesAfterAttach="$NS3_ENABLE_TRACES_AFTER_ATTACH" \
    --nativeMinimalTraces="$NS3_NATIVE_MINIMAL_TRACES" \
    --nativeAggregatedEvidence="$NS3_NATIVE_AGGREGATED_EVIDENCE" \
    --nativeEvidencePeriodMs="$NS3_NATIVE_EVIDENCE_PERIOD_MS" \
    --useMcUeDevices="$NS3_USE_MC_UE_DEVICES" \
    --enableE2FileLogging="$NS3_ENABLE_E2_FILE_LOGGING" \
    --e2TermPort="${GREENRAN_E2_TERM_PORT:-40301}" \
    --e2LocalPort="${GREENRAN_E2_LOCAL_PORT:-40320}" \
    --e2lteEnabled="$NS3_E2LTE_ENABLED" \
    --e2nrEnabled="$NS3_E2NR_ENABLED" \
    --e2du="$NS3_E2DU_ENABLED" \
    --e2cuUp="$NS3_E2CUUP_ENABLED" \
    --e2cuCp="$NS3_E2CUCP_ENABLED" \
    --e2ControlEnabled="$NS3_E2_CONTROL_ENABLED" \
    >> "$NS3_LOG" 2>&1 &
  child_pid=$!
  echo "$child_pid" > "$PID_FILE"
  wait "$child_pid" || exit_code=$?
  exit_code="${exit_code:-0}"
  printf '[NS3_SUPERVISOR] ns3 exited code %s at %s; restarting in %ss
' "$exit_code" "$(date --iso-8601=seconds)" "$RESTART_DELAY" >> "$NS3_LOG"
  if [[ "$SINGLE_RUN" == "1" && "$exit_code" == "0" ]]; then
    rm -f "$PID_FILE" "$SUPERVISOR_FILE"
    exit 0
  fi
  child_pid=""
  sleep "$RESTART_DELAY"
  # Higiene pós-mortem: se o braço (pai) desapareceu, o supervisor NÃO pode
  # reiniciar o ns-3 — um restart recria/trunca os CSVs de evidência e
  # destruiu as provas do r23 depois do kill.  Reparentado para o init
  # (ppid=1) significa braço morto: sair sem reiniciar.
  supervisor_ppid="$(ps -o ppid= -p "$$" 2>/dev/null | tr -d ' ' || true)"
  if [[ -z "$supervisor_ppid" || "$supervisor_ppid" == "1" || "$supervisor_ppid" == "0" ]]; then
    printf '[NS3_SUPERVISOR] braço pai desapareceu; saindo sem reiniciar em %s
' "$(date --iso-8601=seconds)" >> "$NS3_LOG"
    if [[ -n "$child_pid" ]] && kill -0 "$child_pid" 2>/dev/null; then
      kill "$child_pid" 2>/dev/null || true
    fi
    rm -f "$PID_FILE" "$SUPERVISOR_FILE"
    exit 0
  fi
  unset exit_code
  current_supervisor="$(cat "$SUPERVISOR_FILE" 2>/dev/null || true)"
  if [[ "$current_supervisor" != "$$" ]]; then
    exit 0
  fi
done
