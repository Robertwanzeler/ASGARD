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
# Article track default: GREENRAN_COLLECTION_DISABLE_APP_OVERRIDES="1".
export GREENRAN_COLLECTION_DISABLE_APP_OVERRIDES="${GREENRAN_COLLECTION_DISABLE_APP_OVERRIDES:-1}"
export GREENRAN_START_RIC="${GREENRAN_START_RIC:-0}"
export GREENRAN_XAPP_MODE="${GREENRAN_XAPP_MODE:-file}"

. "$SCRIPT_DIR/core_runtime.sh"
load_greenran_runtime

# Official article collection defaults to the canonical preset from the TA-SAM
# paper. Use explicit environment overrides only for smoke runs.
export GREENRAN_REAL_ONLY_NS3_UE_COUNT="${GREENRAN_REAL_ONLY_NS3_UE_COUNT:-200}"
export GREENRAN_REAL_ONLY_NS3_CAMERA_UE_COUNT="${GREENRAN_REAL_ONLY_NS3_CAMERA_UE_COUNT:-80}"
export GREENRAN_REAL_ONLY_NS3_VEHICLE_UE_COUNT="${GREENRAN_REAL_ONLY_NS3_VEHICLE_UE_COUNT:-40}"

export GREENRAN_NS3_UE_COUNT="${GREENRAN_NS3_UE_COUNT:-$GREENRAN_REAL_ONLY_NS3_UE_COUNT}"
export GREENRAN_NS3_CAMERA_UE_COUNT="${GREENRAN_NS3_CAMERA_UE_COUNT:-$GREENRAN_REAL_ONLY_NS3_CAMERA_UE_COUNT}"
export GREENRAN_NS3_VEHICLE_UE_COUNT="${GREENRAN_NS3_VEHICLE_UE_COUNT:-$GREENRAN_REAL_ONLY_NS3_VEHICLE_UE_COUNT}"
export GREENRAN_NS3_MMWAVE_ENB_NODES="${GREENRAN_NS3_MMWAVE_ENB_NODES:-6}"
export GREENRAN_NS3_UE_SPEED_MIN="${GREENRAN_NS3_UE_SPEED_MIN:-10}"
export GREENRAN_NS3_UE_SPEED_MAX="${GREENRAN_NS3_UE_SPEED_MAX:-20}"
export GREENRAN_PDCP_STALE_SECONDS="600"
# Article track default: GREENRAN_REQUIRE_REAL_PDCP="1".
export GREENRAN_REQUIRE_REAL_PDCP="${GREENRAN_REQUIRE_REAL_PDCP:-1}"
export GREENRAN_NS3_ENABLE_TRACES_AFTER_ATTACH="${GREENRAN_NS3_ENABLE_TRACES_AFTER_ATTACH:-0}"
export GREENRAN_NS3_ENABLE_ENERGY_CSV="${GREENRAN_NS3_ENABLE_ENERGY_CSV:-0}"
export GREENRAN_NS3_ENERGY_OUTPUT_DIR="${GREENRAN_NS3_ENERGY_OUTPUT_DIR:-$GREENRAN_STATE_DIR/ns3_energy}"
export GREENRAN_NS3_RNG_RUN="${GREENRAN_NS3_RNG_RUN:-1}"
export GREENRAN_NS3_FIXED_POWER_PERCENT="${GREENRAN_NS3_FIXED_POWER_PERCENT:-100}"
export GREENRAN_NS3_ACTIVE_CELLS="${GREENRAN_NS3_ACTIVE_CELLS:-$GREENRAN_NS3_MMWAVE_ENB_NODES}"
export GREENRAN_NS3_E2NR_ENABLED="${GREENRAN_NS3_E2NR_ENABLED:-false}"
export GREENRAN_NS3_USE_MC_UE_DEVICES="${GREENRAN_NS3_USE_MC_UE_DEVICES:-true}"
export GREENRAN_NS3_E2CUUP_ENABLED="${GREENRAN_NS3_E2CUUP_ENABLED:-false}"
export GREENRAN_NS3_ENABLE_E2_FILE_LOGGING="${GREENRAN_NS3_ENABLE_E2_FILE_LOGGING:-false}"
export GREENRAN_NS3_BEARER_STATS_EPOCH_MS="${GREENRAN_NS3_BEARER_STATS_EPOCH_MS:-100}"
export GREENRAN_COLLECTION_EVENT_LOG="${GREENRAN_COLLECTION_EVENT_LOG:-$GREENRAN_STATE_DIR/collection_event_alternator.log}"
export GREENRAN_COLLECTION_EVENT_PID="${GREENRAN_COLLECTION_EVENT_PID:-$GREENRAN_STATE_DIR/collection_event_alternator.pid}"
export GREENRAN_COLLECTION_STARTUP_WAIT_SECONDS="${GREENRAN_COLLECTION_STARTUP_WAIT_SECONDS:-120}"
export GREENRAN_NS3_SUPERVISOR_LAUNCH_LOG="${GREENRAN_NS3_SUPERVISOR_LAUNCH_LOG:-$GREENRAN_STATE_DIR/ns3_supervisor_launch.log}"

mkdir -p "$GREENRAN_STATE_DIR/xapp_metrics" "$GREENRAN_STATE_DIR/xapp_intents" "$GREENRAN_STATE_DIR/rapp_policies" "$GREENRAN_NS3_CWD" "$GREENRAN_NS3_ENERGY_OUTPUT_DIR"
mkdir -p "$GREENRAN_DB_SNAPSHOT_DIR"
if [[ "${GREENRAN_TASAM_EXPORT_ENABLED:-1}" == "1" ]]; then
  mkdir -p "$GREENRAN_TASAM_EXPORT_DIR"
fi

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

CGROUP_PREFIX=()
set_cgroup_prefix() {
  CGROUP_PREFIX=()
  if [[ "${GREENRAN_CGROUP_ENFORCE:-0}" == "1" ]]; then
    CGROUP_PREFIX=(python3 "$PROJECT_ROOT/scripts/greenran_cgroup_exec.py" --group "$1" --)
  fi
}

start_ric() {
  if [[ "$GREENRAN_START_RIC" != "1" ]]; then
    return 0
  fi

  local ric_bin=""
  local candidate
  for candidate in \
    "$PROJECT_ROOT/flexric/build_e2ap_v1/examples/ric/nearRT-RIC" \
    "$PROJECT_ROOT/flexric/build/examples/ric/nearRT-RIC"; do
    if [[ -x "$candidate" ]]; then
      ric_bin="$candidate"
      break
    fi
  done
  if [[ -z "$ric_bin" ]]; then
    echo "nearRT-RIC não encontrado; procure em flexric/build_e2ap_v1/examples/ric/nearRT-RIC" >&2
    return 1
  fi

  export LD_LIBRARY_PATH="$PROJECT_ROOT/flexric/build_e2ap_v1/src/ric:$PROJECT_ROOT/flexric_lib:$PROJECT_ROOT/flexric/build_e2ap_v1/src/xApp:${LD_LIBRARY_PATH:-}"
  set_cgroup_prefix ric_xapps
  setsid "${CGROUP_PREFIX[@]}" "$ric_bin" \
    -c "$PROJECT_ROOT/flexric/flexric.conf" \
    -p "$PROJECT_ROOT/flexric_lib/" \
    > "$GREENRAN_RIC_LOG" 2>&1 &
  echo $! > "$GREENRAN_RIC_PID"
  sleep 2
  if ! kill -0 "$(cat "$GREENRAN_RIC_PID" 2>/dev/null)" 2>/dev/null; then
    echo "nearRT-RIC encerrou durante o startup; verifique $GREENRAN_RIC_LOG" >&2
    return 1
  fi
}

start_ns3() {
  set_cgroup_prefix simulator
  setsid "${CGROUP_PREFIX[@]}" env \
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
    GREENRAN_NS3_ENABLE_ENERGY_CSV="$GREENRAN_NS3_ENABLE_ENERGY_CSV" \
    GREENRAN_NS3_ENERGY_OUTPUT_DIR="$GREENRAN_NS3_ENERGY_OUTPUT_DIR" \
    GREENRAN_NS3_RNG_RUN="$GREENRAN_NS3_RNG_RUN" \
    GREENRAN_NS3_FIXED_POWER_PERCENT="$GREENRAN_NS3_FIXED_POWER_PERCENT" \
    GREENRAN_NS3_ACTIVE_CELLS="$GREENRAN_NS3_ACTIVE_CELLS" \
    GREENRAN_NS3_USE_MC_UE_DEVICES="$GREENRAN_NS3_USE_MC_UE_DEVICES" \
    GREENRAN_NS3_E2NR_ENABLED="$GREENRAN_NS3_E2NR_ENABLED" \
    GREENRAN_NS3_E2DU_ENABLED="$GREENRAN_NS3_E2DU_ENABLED" \
    GREENRAN_NS3_E2CUUP_ENABLED="$GREENRAN_NS3_E2CUUP_ENABLED" \
    GREENRAN_NS3_ENABLE_E2_FILE_LOGGING="$GREENRAN_NS3_ENABLE_E2_FILE_LOGGING" \
    GREENRAN_NS3_BEARER_STATS_EPOCH_MS="$GREENRAN_NS3_BEARER_STATS_EPOCH_MS" \
    GREENRAN_RAN_PRESSURE_PROFILE="$GREENRAN_RAN_PRESSURE_PROFILE" \
    "$PROJECT_ROOT/scripts/start_ns3_supervisor.sh" > "$GREENRAN_NS3_SUPERVISOR_LAUNCH_LOG" 2>&1 &
  sleep 1
}

start_collector() {
  set_cgroup_prefix collectors
  setsid "${CGROUP_PREFIX[@]}" /bin/bash -lc "cd '$PROJECT_ROOT' && export GREENRAN_STATE_DIR='$GREENRAN_STATE_DIR' GREENRAN_DB_PATH='$GREENRAN_DB_PATH' GREENRAN_FIXED_SCENARIO_CONFIG='$GREENRAN_FIXED_SCENARIO_CONFIG' GREENRAN_PDCP_STALE_SECONDS='$GREENRAN_PDCP_STALE_SECONDS' GREENRAN_REQUIRE_REAL_PDCP='$GREENRAN_REQUIRE_REAL_PDCP' && while true; do python3 ./src/csv_to_metrics.py --input-dir '$GREENRAN_NS3_CWD' --output '$GREENRAN_STATE_DIR/xapp_metrics/metrics.json' --extended-output '$GREENRAN_STATE_DIR/xapp_metrics/extended_metrics.json' --poll-interval '$GREENRAN_COLLECTOR_POLL_INTERVAL'; code=\$?; echo \"[CSV_METRICS_SUPERVISOR] collector exited with code \$code at \$(date -Is); restarting in 2s\"; sleep 2; done" > "$GREENRAN_CSV_LOG" 2>&1 &
  echo $! > "$GREENRAN_CSV_PID"
  sleep 1
}

start_rapp() {
  set_cgroup_prefix rapp_armd
  setsid "${CGROUP_PREFIX[@]}" env GREENRAN_STATE_DIR="$GREENRAN_STATE_DIR" GREENRAN_DB_PATH="$GREENRAN_DB_PATH" GREENRAN_FIXED_SCENARIO_CONFIG="$GREENRAN_FIXED_SCENARIO_CONFIG" GREENRAN_CLEAN_SCOPE="$GREENRAN_CLEAN_SCOPE" python3 "$PROJECT_ROOT/src/rapp_orchestrator.py" --synthetic 0 --interval "$GREENRAN_ORCHESTRATOR_INTERVAL" > "$GREENRAN_RAPP_LOG" 2>&1 &
  echo $! > "$GREENRAN_RAPP_PID"
  sleep 1
}

start_db_snapshot_service() {
  set_cgroup_prefix collectors
  setsid "${CGROUP_PREFIX[@]}" /bin/bash -lc "cd '$PROJECT_ROOT' && export GREENRAN_DB_PATH='$GREENRAN_DB_PATH' GREENRAN_DB_SNAPSHOT_DIR='$GREENRAN_DB_SNAPSHOT_DIR' GREENRAN_DB_SNAPSHOT_RETENTION='$GREENRAN_DB_SNAPSHOT_RETENTION' && while true; do if [[ -f '$GREENRAN_DB_PATH' ]]; then python3 ./scripts/snapshot_sqlite_db.py --db '$GREENRAN_DB_PATH' --snapshot-dir '$GREENRAN_DB_SNAPSHOT_DIR' --retain '$GREENRAN_DB_SNAPSHOT_RETENTION'; code=\$?; else echo \"[DB_SNAPSHOT] waiting for DB at $GREENRAN_DB_PATH\"; code=0; fi; echo \"[DB_SNAPSHOT] cycle finished with code \$code at \$(date -Is); sleeping ${GREENRAN_DB_SNAPSHOT_INTERVAL}s\"; sleep '$GREENRAN_DB_SNAPSHOT_INTERVAL'; done" > "$GREENRAN_DB_SNAPSHOT_LOG" 2>&1 &
  echo $! > "$GREENRAN_DB_SNAPSHOT_PID"
  sleep 1
}

start_tasam_export_service() {
  if [[ "${GREENRAN_TASAM_EXPORT_ENABLED:-1}" != "1" ]]; then
    rm -f "$GREENRAN_TASAM_EXPORT_PID"
    printf '[TASAM_EXPORT] disabled by runtime config; targeted monitor remains authoritative\n' > "$GREENRAN_TASAM_EXPORT_LOG"
    return 0
  fi
  set_cgroup_prefix collectors
  setsid "${CGROUP_PREFIX[@]}" /bin/bash -lc "cd '$PROJECT_ROOT' && export GREENRAN_DB_PATH='$GREENRAN_DB_PATH' GREENRAN_TASAM_EXPORT_DIR='$GREENRAN_TASAM_EXPORT_DIR' GREENRAN_TASAM_EXPORT_LIMIT='$GREENRAN_TASAM_EXPORT_LIMIT' && while true; do if [[ -f '$GREENRAN_DB_PATH' ]]; then python3 ./scripts/run_tasam_article_export.py --db '$GREENRAN_DB_PATH' --output-dir '$GREENRAN_TASAM_EXPORT_DIR'; code=\$?; else echo \"[TASAM_EXPORT] waiting for DB at $GREENRAN_DB_PATH\"; code=0; fi; echo \"[TASAM_EXPORT] cycle finished with code \$code at \$(date -Is); sleeping ${GREENRAN_TASAM_EXPORT_INTERVAL}s\"; sleep '$GREENRAN_TASAM_EXPORT_INTERVAL'; done" > "$GREENRAN_TASAM_EXPORT_LOG" 2>&1 &
  echo $! > "$GREENRAN_TASAM_EXPORT_PID"
  sleep 1
}

start_tasam_true_online_real_service() {
  if [[ "${GREENRAN_TASAM_TRUE_ONLINE_EXTERNAL_CONTROLLER:-0}" == "1" ]]; then
    rm -f "$GREENRAN_TASAM_TRUE_ONLINE_PID"
    cat > "$GREENRAN_TASAM_TRUE_ONLINE_LOG" <<EOF
[TASAM_TRUE_ONLINE_REAL] external controller owns online updates: ${GREENRAN_ONLINE_UPDATE_OWNER:-unknown}
EOF
    return 0
  fi
  if [[ "${GREENRAN_TASAM_TRUE_ONLINE_ENABLED:-1}" != "1" ]]; then
    rm -f "$GREENRAN_TASAM_TRUE_ONLINE_PID"
    cat > "$GREENRAN_TASAM_TRUE_ONLINE_LOG" <<EOF
[TASAM_TRUE_ONLINE_REAL] disabled by runtime config: enabled=${GREENRAN_TASAM_TRUE_ONLINE_ENABLED:-0}
EOF
    return 0
  fi
  setsid /bin/bash -lc "cd '$PROJECT_ROOT' && while true; do if [[ -f '$GREENRAN_DB_PATH' ]]; then python3 ./scripts/run_tasam_true_online_real.py --db '$GREENRAN_DB_PATH' --output-root '$GREENRAN_TASAM_TRUE_ONLINE_DIR' --train-python '$GREENRAN_TASAM_TRUE_ONLINE_TRAIN_PYTHON' --init-checkpoint-dir '$GREENRAN_TASAM_TRUE_ONLINE_INIT_CHECKPOINT' --seed '$GREENRAN_TASAM_TRUE_ONLINE_SEED' --min-new-snapshots '$GREENRAN_TASAM_TRUE_ONLINE_MIN_NEW_SNAPSHOTS' --min-trainable-transitions '$GREENRAN_TASAM_TRUE_ONLINE_MIN_TRAINABLE_TRANSITIONS' --bootstrap-epochs '$GREENRAN_TASAM_TRUE_ONLINE_BOOTSTRAP_EPOCHS' --epochs-per-update '$GREENRAN_TASAM_TRUE_ONLINE_EPOCHS_PER_UPDATE' --poll-seconds '$GREENRAN_TASAM_TRUE_ONLINE_INTERVAL'; code=\$?; else echo \"[TASAM_TRUE_ONLINE_REAL] waiting for DB at $GREENRAN_DB_PATH\"; code=0; fi; echo \"[TASAM_TRUE_ONLINE_REAL] runner exited with code \$code at \$(date -Is); restarting in 2s\"; sleep 2; done" > "$GREENRAN_TASAM_TRUE_ONLINE_LOG" 2>&1 &
  echo $! > "$GREENRAN_TASAM_TRUE_ONLINE_PID"
  sleep 1
}

start_rapp_online_retrain_service() {
  if [[ "${GREENRAN_TASAM_EXPORT_ENABLED:-1}" != "1" ]]; then
    rm -f "$GREENRAN_RAPP_ONLINE_RETRAIN_PID"
    cat > "$GREENRAN_RAPP_ONLINE_RETRAIN_LOG" <<EOF
[RAPP_ONLINE_RETRAIN] disabled because article export is disabled
EOF
    return 0
  fi
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

  set_cgroup_prefix collectors
  setsid "${CGROUP_PREFIX[@]}" /bin/bash -lc "cd '$PROJECT_ROOT' && python3 ./scripts/collection_event_alternator.py --profile '$GREENRAN_COLLECTION_EVENT_PROFILE' --cycles '$GREENRAN_COLLECTION_EVENT_CYCLES' --tick-s '$GREENRAN_COLLECTION_EVENT_TICK_S' --time-source '$GREENRAN_COLLECTION_EVENT_TIME_SOURCE' --state-file '$GREENRAN_STATE_DIR/article00_scenario_control.json'" > "$GREENRAN_COLLECTION_EVENT_LOG" 2>&1 &
  echo $! > "$GREENRAN_COLLECTION_EVENT_PID"
  sleep 1
}

start_if_missing "$GREENRAN_RIC_PID" start_ric
start_if_missing "$GREENRAN_NS3_SUPERVISOR_PID" start_ns3
start_if_missing "$GREENRAN_CSV_PID" start_collector
start_if_missing "$GREENRAN_RAPP_PID" start_rapp
if [[ "${GREENRAN_DB_SNAPSHOT_ENABLED:-1}" == "1" ]]; then
  start_if_missing "$GREENRAN_DB_SNAPSHOT_PID" start_db_snapshot_service
else
  rm -f "$GREENRAN_DB_SNAPSHOT_PID"
  printf '[DB_SNAPSHOT] disabled by runtime config; SQLite primary remains authoritative\n' > "$GREENRAN_DB_SNAPSHOT_LOG"
fi
start_if_missing "$GREENRAN_TASAM_EXPORT_PID" start_tasam_export_service
if [[ "${GREENRAN_TASAM_TRUE_ONLINE_ENABLED:-1}" == "1" ]]; then
  start_if_missing "$GREENRAN_TASAM_TRUE_ONLINE_PID" start_tasam_true_online_real_service
else
  rm -f "$GREENRAN_TASAM_TRUE_ONLINE_PID"
  start_tasam_true_online_real_service
fi
if [[ "${GREENRAN_TASAM_EXPORT_ENABLED:-1}" == "1" && "${GREENRAN_ML_RETRAIN_ENABLED:-true}" == "true" ]]; then
  start_if_missing "$GREENRAN_RAPP_ONLINE_RETRAIN_PID" start_rapp_online_retrain_service
else
  rm -f "$GREENRAN_RAPP_ONLINE_RETRAIN_PID"
  start_rapp_online_retrain_service
fi
start_if_missing "$GREENRAN_COLLECTION_EVENT_PID" start_collection_event_service

startup_deadline=$((SECONDS + GREENRAN_COLLECTION_STARTUP_WAIT_SECONDS))
while (( SECONDS < startup_deadline )); do
  if [[ -f "$GREENRAN_DB_PATH" && -f "$GREENRAN_STATE_DIR/xapp_metrics/extended_metrics.json" ]]; then
    break
  fi
  sleep 1
done

echo "TA-SAM article ns-3 collection"
echo "  state_dir: $GREENRAN_STATE_DIR"
echo "  db:       $GREENRAN_DB_PATH"
echo "  config:   $GREENRAN_FIXED_SCENARIO_CONFIG"
echo "  nearRT-RIC: $([[ "$GREENRAN_START_RIC" == "1" ]] && echo gerenciado || echo externo/desabilitado)"
echo "  ueCount:  $GREENRAN_NS3_UE_COUNT"
echo "  split:    cam=$GREENRAN_NS3_CAMERA_UE_COUNT bg=$(($GREENRAN_NS3_UE_COUNT - $GREENRAN_NS3_CAMERA_UE_COUNT - $GREENRAN_NS3_VEHICLE_UE_COUNT)) veh=$GREENRAN_NS3_VEHICLE_UE_COUNT"
echo "  mmWaveDU: $GREENRAN_NS3_MMWAVE_ENB_NODES"
echo "  speed:    ${GREENRAN_NS3_UE_SPEED_MIN}-${GREENRAN_NS3_UE_SPEED_MAX} m/s"
echo "  traces:   $GREENRAN_NS3_CWD"
echo "  snapshots:$GREENRAN_DB_SNAPSHOT_DIR"
echo "  exports:  $GREENRAN_TASAM_EXPORT_DIR (enabled=${GREENRAN_TASAM_EXPORT_ENABLED:-1})"
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

if [[ "${GREENRAN_WAIT_FOR_DECISION_TARGET:-0}" == "1" ]]; then
  target_pid=""
  if [[ -f "$GREENRAN_STATE_DIR/decision_target_supervisor.pid" ]]; then
    target_pid="$(cat "$GREENRAN_STATE_DIR/decision_target_supervisor.pid" 2>/dev/null || true)"
  fi
  while [[ -n "$target_pid" ]] && kill -0 "$target_pid" 2>/dev/null; do
    sleep 2
  done
fi

# Some launchers intentionally keep the runtime parent alive so that system
# supervisors/PTYs do not reap the detached collectors.  The default remains
# the historical one-shot launcher behaviour.
if [[ "${GREENRAN_KEEP_FOREGROUND:-0}" == "1" ]]; then
  while [[ -f "$GREENRAN_NS3_SUPERVISOR_PID" ]]; do
    supervisor_pid="$(cat "$GREENRAN_NS3_SUPERVISOR_PID" 2>/dev/null || true)"
    [[ -n "$supervisor_pid" ]] || break
    kill -0 "$supervisor_pid" 2>/dev/null || break
    sleep 5
  done
fi
