#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

export GREENRAN_STATE_DIR="${GREENRAN_STATE_DIR:-$PROJECT_ROOT/runs/tasam_article_ns3_collection}"
export GREENRAN_FIXED_SCENARIO_CONFIG="${GREENRAN_FIXED_SCENARIO_CONFIG:-$PROJECT_ROOT/config/tasam_article_ns3_collection.json}"
export GREENRAN_CLEAN_SCOPE="${GREENRAN_CLEAN_SCOPE:-instance}"
export GREENRAN_APP1_CAMERA_SOURCE_MODE="real"
export GREENRAN_ENABLE_CARLA_STACK="${GREENRAN_ENABLE_CARLA_STACK:-0}"
export GREENRAN_NS3_CWD="${GREENRAN_NS3_CWD:-$GREENRAN_STATE_DIR/ns3_traces}"
export GREENRAN_COLLECTION_EVENT_PROFILE="${GREENRAN_COLLECTION_EVENT_PROFILE:-tasam_training_balanced_v1}"
export GREENRAN_RAN_PRESSURE_PROFILE="${GREENRAN_RAN_PRESSURE_PROFILE:-tasam_training_balanced_v1}"
export GREENRAN_COLLECTION_EVENT_CYCLES="${GREENRAN_COLLECTION_EVENT_CYCLES:-0}"
export GREENRAN_COLLECTION_EVENT_TICK_S="${GREENRAN_COLLECTION_EVENT_TICK_S:-1.0}"
export GREENRAN_COLLECTION_EVENT_TIME_SOURCE="${GREENRAN_COLLECTION_EVENT_TIME_SOURCE:-wall}"
export GREENRAN_COLLECTION_DISABLE_APP_OVERRIDES="1"

. "$SCRIPT_DIR/core_runtime.sh"
load_greenran_runtime

# Strict real-only collection needs a runtime scale that still produces
# PDCP/RLC bearer rows in this ns-3 scenario. Keep the article contract in the
# manifest, but default the live collector to a viable real-only split unless
# the operator explicitly overrides it.
export GREENRAN_REAL_ONLY_NS3_UE_COUNT="${GREENRAN_REAL_ONLY_NS3_UE_COUNT:-20}"
export GREENRAN_REAL_ONLY_NS3_CAMERA_UE_COUNT="${GREENRAN_REAL_ONLY_NS3_CAMERA_UE_COUNT:-8}"
export GREENRAN_REAL_ONLY_NS3_VEHICLE_UE_COUNT="${GREENRAN_REAL_ONLY_NS3_VEHICLE_UE_COUNT:-4}"

export GREENRAN_NS3_UE_COUNT="${GREENRAN_NS3_UE_COUNT:-$GREENRAN_REAL_ONLY_NS3_UE_COUNT}"
export GREENRAN_NS3_CAMERA_UE_COUNT="${GREENRAN_NS3_CAMERA_UE_COUNT:-$GREENRAN_REAL_ONLY_NS3_CAMERA_UE_COUNT}"
export GREENRAN_NS3_VEHICLE_UE_COUNT="${GREENRAN_NS3_VEHICLE_UE_COUNT:-$GREENRAN_REAL_ONLY_NS3_VEHICLE_UE_COUNT}"
export GREENRAN_NS3_MMWAVE_ENB_NODES="${GREENRAN_NS3_MMWAVE_ENB_NODES:-2}"
export GREENRAN_NS3_UE_SPEED_MIN="${GREENRAN_NS3_UE_SPEED_MIN:-2}"
export GREENRAN_NS3_UE_SPEED_MAX="${GREENRAN_NS3_UE_SPEED_MAX:-4}"
export GREENRAN_PDCP_STALE_SECONDS="${GREENRAN_PDCP_STALE_SECONDS:-30}"
export GREENRAN_REQUIRE_REAL_PDCP="1"
export GREENRAN_NS3_ENABLE_TRACES_AFTER_ATTACH="${GREENRAN_NS3_ENABLE_TRACES_AFTER_ATTACH:-0}"
export GREENRAN_NS3_E2NR_ENABLED="${GREENRAN_NS3_E2NR_ENABLED:-false}"
export GREENRAN_NS3_USE_MC_UE_DEVICES="${GREENRAN_NS3_USE_MC_UE_DEVICES:-true}"
export GREENRAN_NS3_E2CUUP_ENABLED="${GREENRAN_NS3_E2CUUP_ENABLED:-false}"
export GREENRAN_NS3_ENABLE_E2_FILE_LOGGING="${GREENRAN_NS3_ENABLE_E2_FILE_LOGGING:-false}"
export GREENRAN_NS3_BEARER_STATS_EPOCH_MS="${GREENRAN_NS3_BEARER_STATS_EPOCH_MS:-100}"
export GREENRAN_COLLECTION_EVENT_LOG="${GREENRAN_COLLECTION_EVENT_LOG:-$GREENRAN_STATE_DIR/collection_event_alternator.log}"
export GREENRAN_COLLECTION_EVENT_PID="${GREENRAN_COLLECTION_EVENT_PID:-$GREENRAN_STATE_DIR/collection_event_alternator.pid}"

mkdir -p "$GREENRAN_STATE_DIR/xapp_metrics" "$GREENRAN_STATE_DIR/xapp_intents" "$GREENRAN_STATE_DIR/rapp_policies" "$GREENRAN_NS3_CWD"
mkdir -p "$GREENRAN_DB_SNAPSHOT_DIR" "$GREENRAN_TASAM_EXPORT_DIR"

python3 "$PROJECT_ROOT/scripts/generate_article_ns3_device_roles.py" \
  --config "$GREENRAN_FIXED_SCENARIO_CONFIG" \
  --ue-count "$GREENRAN_NS3_UE_COUNT" \
  --camera-ue-count "$GREENRAN_NS3_CAMERA_UE_COUNT" \
  --vehicle-ue-count "$GREENRAN_NS3_VEHICLE_UE_COUNT" \
  --state-dir "$GREENRAN_STATE_DIR" >/dev/null

start_if_missing() {
  local pid_file="$1"
  shift
  if [[ -f "$pid_file" ]]; then
    local pid
    pid="$(cat "$pid_file" 2>/dev/null || true)"
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      return 0
    fi
  fi
  "$@"
}

start_ns3() {
  setsid env \
    GREENRAN_PROJECT_DIR="$PROJECT_ROOT" \
    GREENRAN_STATE_DIR="$GREENRAN_STATE_DIR" \
    GREENRAN_NS3_CWD="$GREENRAN_NS3_CWD" \
    GREENRAN_NS3_LOG="$GREENRAN_NS3_LOG" \
    GREENRAN_SIM_TIME="$GREENRAN_SIM_TIME" \
    GREENRAN_NS3_UE_COUNT="$GREENRAN_NS3_UE_COUNT" \
    GREENRAN_NS3_CAMERA_UE_COUNT="$GREENRAN_NS3_CAMERA_UE_COUNT" \
    GREENRAN_NS3_VEHICLE_UE_COUNT="$GREENRAN_NS3_VEHICLE_UE_COUNT" \
    GREENRAN_NS3_MMWAVE_ENB_NODES="$GREENRAN_NS3_MMWAVE_ENB_NODES" \
    GREENRAN_NS3_UE_SPEED_MIN="$GREENRAN_NS3_UE_SPEED_MIN" \
    GREENRAN_NS3_UE_SPEED_MAX="$GREENRAN_NS3_UE_SPEED_MAX" \
    GREENRAN_NS3_ENABLE_TRACES_AFTER_ATTACH="$GREENRAN_NS3_ENABLE_TRACES_AFTER_ATTACH" \
    GREENRAN_NS3_USE_MC_UE_DEVICES="$GREENRAN_NS3_USE_MC_UE_DEVICES" \
    GREENRAN_NS3_E2NR_ENABLED="$GREENRAN_NS3_E2NR_ENABLED" \
    GREENRAN_NS3_E2CUUP_ENABLED="$GREENRAN_NS3_E2CUUP_ENABLED" \
    GREENRAN_NS3_ENABLE_E2_FILE_LOGGING="$GREENRAN_NS3_ENABLE_E2_FILE_LOGGING" \
    GREENRAN_NS3_BEARER_STATS_EPOCH_MS="$GREENRAN_NS3_BEARER_STATS_EPOCH_MS" \
    "$PROJECT_ROOT/scripts/start_ns3_supervisor.sh" >/dev/null 2>&1 &
  sleep 1
}

start_collector() {
  setsid /bin/bash -lc "cd '$PROJECT_ROOT' && export GREENRAN_STATE_DIR='$GREENRAN_STATE_DIR' GREENRAN_DB_PATH='$GREENRAN_DB_PATH' GREENRAN_FIXED_SCENARIO_CONFIG='$GREENRAN_FIXED_SCENARIO_CONFIG' GREENRAN_PDCP_STALE_SECONDS='$GREENRAN_PDCP_STALE_SECONDS' GREENRAN_REQUIRE_REAL_PDCP='$GREENRAN_REQUIRE_REAL_PDCP' && while true; do python3 ./src/csv_to_metrics.py --input-dir '$GREENRAN_NS3_CWD' --output '$GREENRAN_STATE_DIR/xapp_metrics/metrics.json' --extended-output '$GREENRAN_STATE_DIR/xapp_metrics/extended_metrics.json' --poll-interval '$GREENRAN_COLLECTOR_POLL_INTERVAL'; code=\$?; echo \"[CSV_METRICS_SUPERVISOR] collector exited with code \$code at \$(date -Is); restarting in 2s\"; sleep 2; done" > "$GREENRAN_CSV_LOG" 2>&1 &
  echo $! > "$GREENRAN_CSV_PID"
  sleep 1
}

start_rapp() {
  setsid env GREENRAN_STATE_DIR="$GREENRAN_STATE_DIR" GREENRAN_DB_PATH="$GREENRAN_DB_PATH" GREENRAN_FIXED_SCENARIO_CONFIG="$GREENRAN_FIXED_SCENARIO_CONFIG" GREENRAN_CLEAN_SCOPE="$GREENRAN_CLEAN_SCOPE" python3 "$PROJECT_ROOT/src/rapp_orchestrator.py" --synthetic 0 --interval "$GREENRAN_ORCHESTRATOR_INTERVAL" > "$GREENRAN_RAPP_LOG" 2>&1 &
  echo $! > "$GREENRAN_RAPP_PID"
  sleep 1
}

start_db_snapshot_service() {
  setsid /bin/bash -lc "cd '$PROJECT_ROOT' && export GREENRAN_DB_PATH='$GREENRAN_DB_PATH' GREENRAN_DB_SNAPSHOT_DIR='$GREENRAN_DB_SNAPSHOT_DIR' GREENRAN_DB_SNAPSHOT_RETENTION='$GREENRAN_DB_SNAPSHOT_RETENTION' && while true; do if [[ -f '$GREENRAN_DB_PATH' ]]; then python3 ./scripts/snapshot_sqlite_db.py --db '$GREENRAN_DB_PATH' --snapshot-dir '$GREENRAN_DB_SNAPSHOT_DIR' --retain '$GREENRAN_DB_SNAPSHOT_RETENTION'; code=\$?; else echo \"[DB_SNAPSHOT] waiting for DB at $GREENRAN_DB_PATH\"; code=0; fi; echo \"[DB_SNAPSHOT] cycle finished with code \$code at \$(date -Is); sleeping ${GREENRAN_DB_SNAPSHOT_INTERVAL}s\"; sleep '$GREENRAN_DB_SNAPSHOT_INTERVAL'; done" > "$GREENRAN_DB_SNAPSHOT_LOG" 2>&1 &
  echo $! > "$GREENRAN_DB_SNAPSHOT_PID"
  sleep 1
}

start_tasam_export_service() {
  setsid /bin/bash -lc "cd '$PROJECT_ROOT' && export GREENRAN_DB_PATH='$GREENRAN_DB_PATH' GREENRAN_TASAM_EXPORT_DIR='$GREENRAN_TASAM_EXPORT_DIR' GREENRAN_TASAM_EXPORT_LIMIT='$GREENRAN_TASAM_EXPORT_LIMIT' GREENRAN_TASAM_EXPORT_ALLOW_PROXY='$GREENRAN_TASAM_EXPORT_ALLOW_PROXY' && while true; do if [[ -f '$GREENRAN_DB_PATH' ]]; then python3 ./scripts/run_tasam_article_export.py --db '$GREENRAN_DB_PATH' --output-dir '$GREENRAN_TASAM_EXPORT_DIR'; code=\$?; else echo \"[TASAM_EXPORT] waiting for DB at $GREENRAN_DB_PATH\"; code=0; fi; echo \"[TASAM_EXPORT] cycle finished with code \$code at \$(date -Is); sleeping ${GREENRAN_TASAM_EXPORT_INTERVAL}s\"; sleep '$GREENRAN_TASAM_EXPORT_INTERVAL'; done" > "$GREENRAN_TASAM_EXPORT_LOG" 2>&1 &
  echo $! > "$GREENRAN_TASAM_EXPORT_PID"
  sleep 1
}

start_tasam_true_online_real_service() {
  if [[ "${GREENRAN_TASAM_TRUE_ONLINE_ENABLED:-1}" != "1" ]]; then
    rm -f "$GREENRAN_TASAM_TRUE_ONLINE_PID"
    cat > "$GREENRAN_TASAM_TRUE_ONLINE_LOG" <<EOF
[TASAM_TRUE_ONLINE_REAL] disabled by runtime config: enabled=${GREENRAN_TASAM_TRUE_ONLINE_ENABLED:-0}
EOF
    return 0
  fi
  setsid /bin/bash -lc "cd '$PROJECT_ROOT' && while true; do if [[ -f '$GREENRAN_DB_PATH' ]]; then python3 ./scripts/run_tasam_true_online_real.py --db '$GREENRAN_DB_PATH' --output-root '$GREENRAN_TASAM_TRUE_ONLINE_DIR' --train-python '$GREENRAN_TASAM_TRUE_ONLINE_TRAIN_PYTHON' --min-new-snapshots '$GREENRAN_TASAM_TRUE_ONLINE_MIN_NEW_SNAPSHOTS' --min-trainable-transitions '$GREENRAN_TASAM_TRUE_ONLINE_MIN_TRAINABLE_TRANSITIONS' --bootstrap-epochs '$GREENRAN_TASAM_TRUE_ONLINE_BOOTSTRAP_EPOCHS' --epochs-per-update '$GREENRAN_TASAM_TRUE_ONLINE_EPOCHS_PER_UPDATE' --poll-seconds '$GREENRAN_TASAM_TRUE_ONLINE_INTERVAL'; code=\$?; else echo \"[TASAM_TRUE_ONLINE_REAL] waiting for DB at $GREENRAN_DB_PATH\"; code=0; fi; echo \"[TASAM_TRUE_ONLINE_REAL] runner exited with code \$code at \$(date -Is); restarting in 2s\"; sleep 2; done" > "$GREENRAN_TASAM_TRUE_ONLINE_LOG" 2>&1 &
  echo $! > "$GREENRAN_TASAM_TRUE_ONLINE_PID"
  sleep 1
}

start_rapp_online_retrain_service() {
  if [[ "${GREENRAN_ML_RETRAIN_ENABLED:-true}" != "true" ]]; then
    rm -f "$GREENRAN_RAPP_ONLINE_RETRAIN_PID"
    cat > "$GREENRAN_RAPP_ONLINE_RETRAIN_LOG" <<EOF
[RAPP_ONLINE_RETRAIN] disabled by runtime config: ml.enabled=${GREENRAN_ML_ENABLED:-false} ml.retrain_enabled=${GREENRAN_ML_RETRAIN_ENABLED:-false}
EOF
    return 0
  fi
  setsid /bin/bash -lc "cd '$PROJECT_ROOT' && export GREENRAN_STATE_DIR='$GREENRAN_STATE_DIR' GREENRAN_TASAM_EXPORT_DIR='$GREENRAN_TASAM_EXPORT_DIR' && while true; do if [[ -f '$GREENRAN_TASAM_EXPORT_DIR/rapp_online_trainable_summary.json' && -f '$GREENRAN_TASAM_EXPORT_DIR/rapp_online_trainable_trace.jsonl' ]]; then python3 ./scripts/run_rapp_online_retrain.py --state-dir '$GREENRAN_STATE_DIR' --models-dir '$PROJECT_ROOT/models' --target-trainable '$GREENRAN_RAPP_ONLINE_RETRAIN_TARGET' --min-new-rows '$GREENRAN_RAPP_ONLINE_RETRAIN_MIN_NEW_ROWS' --min-rf-accuracy '$GREENRAN_RAPP_ONLINE_RETRAIN_MIN_RF_ACCURACY' --min-r2 '$GREENRAN_RAPP_ONLINE_RETRAIN_MIN_R2' --min-healthy-allowed-recall '$GREENRAN_RAPP_ONLINE_RETRAIN_MIN_HEALTHY_ALLOWED_RECALL'; code=\$?; else echo \"[RAPP_ONLINE_RETRAIN] waiting for trainable export at $GREENRAN_TASAM_EXPORT_DIR\"; code=0; fi; echo \"[RAPP_ONLINE_RETRAIN] cycle finished with code \$code at \$(date -Is); sleeping ${GREENRAN_RAPP_ONLINE_RETRAIN_INTERVAL}s\"; sleep '$GREENRAN_RAPP_ONLINE_RETRAIN_INTERVAL'; done" > "$GREENRAN_RAPP_ONLINE_RETRAIN_LOG" 2>&1 &
  echo $! > "$GREENRAN_RAPP_ONLINE_RETRAIN_PID"
  sleep 1
}

start_collection_event_service() {
  if [[ "$GREENRAN_COLLECTION_EVENT_PROFILE" == "none" ]]; then
    rm -f "$GREENRAN_COLLECTION_EVENT_PID" "$GREENRAN_STATE_DIR/article00_scenario_control.json"
    return 0
  fi

  setsid /bin/bash -lc "cd '$PROJECT_ROOT' && python3 ./scripts/collection_event_alternator.py --profile '$GREENRAN_COLLECTION_EVENT_PROFILE' --cycles '$GREENRAN_COLLECTION_EVENT_CYCLES' --tick-s '$GREENRAN_COLLECTION_EVENT_TICK_S' --time-source '$GREENRAN_COLLECTION_EVENT_TIME_SOURCE' --state-file '$GREENRAN_STATE_DIR/article00_scenario_control.json'" > "$GREENRAN_COLLECTION_EVENT_LOG" 2>&1 &
  echo $! > "$GREENRAN_COLLECTION_EVENT_PID"
  sleep 1
}

start_if_missing "$GREENRAN_NS3_SUPERVISOR_PID" start_ns3
start_if_missing "$GREENRAN_CSV_PID" start_collector
start_if_missing "$GREENRAN_RAPP_PID" start_rapp
start_if_missing "$GREENRAN_DB_SNAPSHOT_PID" start_db_snapshot_service
start_if_missing "$GREENRAN_TASAM_EXPORT_PID" start_tasam_export_service
if [[ "${GREENRAN_TASAM_TRUE_ONLINE_ENABLED:-1}" == "1" ]]; then
  start_if_missing "$GREENRAN_TASAM_TRUE_ONLINE_PID" start_tasam_true_online_real_service
else
  rm -f "$GREENRAN_TASAM_TRUE_ONLINE_PID"
  start_tasam_true_online_real_service
fi
if [[ "${GREENRAN_ML_RETRAIN_ENABLED:-true}" == "true" ]]; then
  start_if_missing "$GREENRAN_RAPP_ONLINE_RETRAIN_PID" start_rapp_online_retrain_service
else
  rm -f "$GREENRAN_RAPP_ONLINE_RETRAIN_PID"
  start_rapp_online_retrain_service
fi
start_if_missing "$GREENRAN_COLLECTION_EVENT_PID" start_collection_event_service

for _ in $(seq 1 60); do
  if [[ -f "$GREENRAN_DB_PATH" && -f "$GREENRAN_STATE_DIR/xapp_metrics/extended_metrics.json" ]]; then
    break
  fi
  sleep 2
done

echo "TA-SAM article ns-3 collection"
echo "  state_dir: $GREENRAN_STATE_DIR"
echo "  db:       $GREENRAN_DB_PATH"
echo "  config:   $GREENRAN_FIXED_SCENARIO_CONFIG"
echo "  ueCount:  $GREENRAN_NS3_UE_COUNT"
echo "  split:    cam=$GREENRAN_NS3_CAMERA_UE_COUNT bg=$(($GREENRAN_NS3_UE_COUNT - $GREENRAN_NS3_CAMERA_UE_COUNT - $GREENRAN_NS3_VEHICLE_UE_COUNT)) veh=$GREENRAN_NS3_VEHICLE_UE_COUNT"
echo "  mmWaveDU: $GREENRAN_NS3_MMWAVE_ENB_NODES"
echo "  speed:    ${GREENRAN_NS3_UE_SPEED_MIN}-${GREENRAN_NS3_UE_SPEED_MAX} m/s"
echo "  traces:   $GREENRAN_NS3_CWD"
echo "  snapshots:$GREENRAN_DB_SNAPSHOT_DIR"
echo "  exports:  $GREENRAN_TASAM_EXPORT_DIR"
if [[ "${GREENRAN_TASAM_TRUE_ONLINE_ENABLED:-1}" == "1" ]]; then
  echo "  online:   real cadence=+${GREENRAN_TASAM_TRUE_ONLINE_MIN_NEW_SNAPSHOTS} snapshots min=${GREENRAN_TASAM_TRUE_ONLINE_MIN_TRAINABLE_TRANSITIONS} transicoes epochs=${GREENRAN_TASAM_TRUE_ONLINE_BOOTSTRAP_EPOCHS}/+${GREENRAN_TASAM_TRUE_ONLINE_EPOCHS_PER_UPDATE}"
else
  echo "  online:   desabilitado"
fi
if [[ "${GREENRAN_ML_RETRAIN_ENABLED:-true}" == "true" ]]; then
  echo "  retrain:  alvo=$GREENRAN_RAPP_ONLINE_RETRAIN_TARGET cadence=+$GREENRAN_RAPP_ONLINE_RETRAIN_MIN_NEW_ROWS/${GREENRAN_RAPP_ONLINE_RETRAIN_INTERVAL}s"
else
  echo "  retrain:  desabilitado (ML legado fora do runtime)"
fi
echo "  eventos:  $GREENRAN_COLLECTION_EVENT_PROFILE ($GREENRAN_COLLECTION_EVENT_TIME_SOURCE)"
echo

echo "Acompanhar:"
echo "  GREENRAN_STATE_DIR='$GREENRAN_STATE_DIR' python3 scripts/status.py"
echo "  watch -n 5 \"GREENRAN_STATE_DIR='$GREENRAN_STATE_DIR' python3 scripts/status.py\""
