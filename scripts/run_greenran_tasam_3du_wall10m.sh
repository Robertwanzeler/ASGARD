#!/usr/bin/env bash
set -euo pipefail

# GreenRAN TA-SAM validation with a ten-minute wall-clock budget.
# The ns-3 simTime is deliberately large: the wall supervisor, not ns-3 time,
# owns the lifetime of this run.
#
# Runtime policy: ARMD defines the safety envelope and TA-SAM owns the RAN/IA
# split. The rApp arbitrates their outputs. In assistant-only mode there is no
# silent live-allocator/heuristic fallback; an invalid proposal invalidates
# the run.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

export GREENRAN_STATE_DIR="${GREENRAN_STATE_DIR:-$PROJECT_ROOT/runs/greenran_autonomous_priority_wall10m_20260810}"
export GREENRAN_FIXED_SCENARIO_CONFIG="${GREENRAN_FIXED_SCENARIO_CONFIG:-$PROJECT_ROOT/config/greenran_fixed_scenario.json}"
export GREENRAN_SIM_TIME="${GREENRAN_SIM_TIME:-100000}"
export GREENRAN_WALL_TIME_LIMIT_SECONDS="${GREENRAN_WALL_TIME_LIMIT_SECONDS:-600}"
export GREENRAN_WALL_KEEP_RIC="${GREENRAN_WALL_KEEP_RIC:-1}"
export GREENRAN_CLEAN_SCOPE="${GREENRAN_CLEAN_SCOPE:-instance}"
export GREENRAN_COLLECTION_EVENT_PROFILE="${GREENRAN_COLLECTION_EVENT_PROFILE:-none}"
export GREENRAN_COLLECTION_DISABLE_APP_OVERRIDES="${GREENRAN_COLLECTION_DISABLE_APP_OVERRIDES:-1}"
export GREENRAN_RAN_PRESSURE_PROFILE="${GREENRAN_RAN_PRESSURE_PROFILE:-greenran_autonomous_priority_wall10m_v1}"
export GREENRAN_RAN_PRESSURE_TIME_SOURCE="wall"
export GREENRAN_REAL_ONLY="1"
export GREENRAN_REQUIRE_REAL_PDCP="1"
export GREENRAN_START_RIC="${GREENRAN_START_RIC:-0}"
export GREENRAN_NS3_UE_COUNT="${GREENRAN_NS3_UE_COUNT:-20}"
export GREENRAN_NS3_CAMERA_UE_COUNT="${GREENRAN_NS3_CAMERA_UE_COUNT:-3}"
export GREENRAN_NS3_VEHICLE_UE_COUNT="${GREENRAN_NS3_VEHICLE_UE_COUNT:-5}"
export GREENRAN_NS3_MMWAVE_ENB_NODES="${GREENRAN_NS3_MMWAVE_ENB_NODES:-3}"
export GREENRAN_NS3_VEHICLE_PACKET_SIZE_BYTES="${GREENRAN_NS3_VEHICLE_PACKET_SIZE_BYTES:-800}"
export GREENRAN_NS3_VEHICLE_PACKET_INTERVAL_US="${GREENRAN_NS3_VEHICLE_PACKET_INTERVAL_US:-4000}"
export GREENRAN_NS3_CAMERA_PACKET_SIZE_BYTES="${GREENRAN_NS3_CAMERA_PACKET_SIZE_BYTES:-1000}"
export GREENRAN_NS3_CAMERA_PACKET_INTERVAL_US="${GREENRAN_NS3_CAMERA_PACKET_INTERVAL_US:-320}"
export GREENRAN_NS3_UE_SPEED_MIN="${GREENRAN_NS3_UE_SPEED_MIN:-10}"
export GREENRAN_NS3_UE_SPEED_MAX="${GREENRAN_NS3_UE_SPEED_MAX:-20}"
export GREENRAN_NS3_BANDWIDTH_MHZ="${GREENRAN_NS3_BANDWIDTH_MHZ:-100}"
export GREENRAN_NS3_USE_MC_UE_DEVICES="${GREENRAN_NS3_USE_MC_UE_DEVICES:-false}"
export GREENRAN_NS3_E2NR_ENABLED="${GREENRAN_NS3_E2NR_ENABLED:-true}"
export GREENRAN_NS3_E2DU_ENABLED="${GREENRAN_NS3_E2DU_ENABLED:-true}"
export GREENRAN_NS3_E2CUUP_ENABLED="${GREENRAN_NS3_E2CUUP_ENABLED:-false}"
export GREENRAN_NS3_E2CUCP_ENABLED="${GREENRAN_NS3_E2CUCP_ENABLED:-false}"
export GREENRAN_NS3_ENABLE_TRACES="${GREENRAN_NS3_ENABLE_TRACES:-1}"
export GREENRAN_TASAM_ADVISOR_ENABLED="${GREENRAN_TASAM_ADVISOR_ENABLED:-1}"
export GREENRAN_TASAM_ADVISOR_MODE="${GREENRAN_TASAM_ADVISOR_MODE:-assistant_only_control}"
export GREENRAN_CONTROL_TRIAL_ENABLED="${GREENRAN_CONTROL_TRIAL_ENABLED:-1}"
export GREENRAN_CONTROL_TRIAL_FRACTION="${GREENRAN_CONTROL_TRIAL_FRACTION:-1.0}"
export GREENRAN_TASAM_EVAL_MANIFEST="${GREENRAN_TASAM_EVAL_MANIFEST:-$PROJECT_ROOT/runs/sac_bootstrap/tasam_candidate_evaluation_latest.json}"
export GREENRAN_TASAM_EXPORT_ALLOW_PROXY="0"
export GREENRAN_TASAM_TRUE_ONLINE_ENABLED="${GREENRAN_TASAM_TRUE_ONLINE_ENABLED:-0}"
export GREENRAN_ML_RETRAIN_ENABLED="${GREENRAN_ML_RETRAIN_ENABLED:-false}"
export GREENRAN_PDCP_STALE_SECONDS="600"
export GREENRAN_COLLECTION_STARTUP_WAIT_SECONDS="${GREENRAN_COLLECTION_STARTUP_WAIT_SECONDS:-10}"
export GREENRAN_PER_UE_POLICY_CONSUMER_ENABLED="${GREENRAN_PER_UE_POLICY_CONSUMER_ENABLED:-0}"
export GREENRAN_PER_UE_POLICY_CONSUMER_POLL_S="${GREENRAN_PER_UE_POLICY_CONSUMER_POLL_S:-0.5}"

# The collection launcher starts the runtime services and then returns.  Keep
# this wall-clock wrapper alive while the state-scoped supervisor owns the
# ten-minute lifetime; without this step the caller immediately evaluated an
# empty Data Lake.
mkdir -p "$GREENRAN_STATE_DIR"
if [[ "$GREENRAN_PER_UE_POLICY_CONSUMER_ENABLED" == "1" ]]; then
  consumer_log="${GREENRAN_PER_UE_POLICY_CONSUMER_LOG:-$GREENRAN_STATE_DIR/per_ue_policy_consumer.log}"
  setsid python3 "$SCRIPT_DIR/consume_per_ue_policy.py" \
    --state-dir "$GREENRAN_STATE_DIR" --poll-s "$GREENRAN_PER_UE_POLICY_CONSUMER_POLL_S" \
    > "$consumer_log" 2>&1 &
  consumer_pid=$!
  echo "$consumer_pid" > "$GREENRAN_STATE_DIR/per_ue_policy_consumer.pid"
  echo "[wall10m] consumidor per-UE ativo; pid=$consumer_pid log=$consumer_log"
fi
collection_log="${GREENRAN_COLLECTION_RUNTIME_LOG:-$GREENRAN_STATE_DIR/collection_runtime.log}"
setsid "$SCRIPT_DIR/run_greenran_tasam_3du_collection.sh" > "$collection_log" 2>&1 &
collection_pid=$!
echo "$collection_pid" > "$GREENRAN_STATE_DIR/collection_launcher.pid"

collection_status=0
wait "$collection_pid" || collection_status=$?
if [[ "$collection_status" -ne 0 ]]; then
  echo "[wall10m] lançamento da coleta falhou (code=$collection_status); veja $collection_log" >&2
  exit "$collection_status"
fi

if [[ ! -f "$GREENRAN_STATE_DIR/ns3_supervisor.pid" || ! -f "$GREENRAN_STATE_DIR/rapp.pid" ]]; then
  echo "[wall10m] runtime incompleto: faltam ns3_supervisor.pid ou rapp.pid; veja $collection_log" >&2
  exit 4
fi

wall_log="${GREENRAN_WALL_SUPERVISOR_LOG:-$GREENRAN_STATE_DIR/wall_clock_supervisor.log}"
wall_cmd=(
  python3 "$SCRIPT_DIR/wall_clock_collection_supervisor.py"
  --state-dir "$GREENRAN_STATE_DIR"
  --duration-seconds "$GREENRAN_WALL_TIME_LIMIT_SECONDS"
)
if [[ "$GREENRAN_WALL_KEEP_RIC" == "1" ]]; then
  wall_cmd+=(--keep-ric)
fi

# Keep the PID file bound to the actual Python supervisor.  The target
# watcher stops this PID directly before arm finalization; wrapping it in
# setsid can leave the file pointing at a short-lived launcher process.
"${wall_cmd[@]}" > "$wall_log" 2>&1 &
wall_pid=$!
echo "$wall_pid" > "$GREENRAN_STATE_DIR/wall_clock_supervisor.pid"
echo "[wall10m] coleta iniciada; supervisor pid=$wall_pid duração=${GREENRAN_WALL_TIME_LIMIT_SECONDS}s"
echo "[wall10m] acompanhamento: GREENRAN_STATE_DIR='$GREENRAN_STATE_DIR' python3 scripts/status.py"

wall_status=0
if [[ "${GREENRAN_NS3_SINGLE_RUN:-0}" == "1" ]]; then
  # Fidelity/benchmark arms represent one finite ns-3 execution.  The
  # historical wall wrapper waits for a fixed wall-clock budget, but a
  # cleanly finished single-run supervisor removes ns3_supervisor.pid.  Stop
  # the wall supervisor at that boundary so the arm can finalize its terminal
  # traces and performance evidence instead of waiting for the full budget.
  while kill -0 "$wall_pid" 2>/dev/null; do
    if [[ ! -f "$GREENRAN_STATE_DIR/ns3_supervisor.pid" ]]; then
      echo "[wall10m] ns-3 single-run concluído; encerrando supervisor de wall-clock"
      kill -TERM "$wall_pid" 2>/dev/null || true
      break
    fi
    supervisor_pid="$(cat "$GREENRAN_STATE_DIR/ns3_supervisor.pid" 2>/dev/null || true)"
    if [[ -z "$supervisor_pid" ]] || ! kill -0 "$supervisor_pid" 2>/dev/null; then
      echo "[wall10m] supervisor ns-3 não está ativo após single-run; encerrando wall-clock"
      kill -TERM "$wall_pid" 2>/dev/null || true
      break
    fi
    sleep 1
  done
  wait "$wall_pid" || wall_status=$?
else
  wait "$wall_pid" || wall_status=$?
fi
exit "$wall_status"
