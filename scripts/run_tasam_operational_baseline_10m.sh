#!/usr/bin/env bash
set -euo pipefail

# Matched ten-minute real-PDCP baseline: the rApp operates without ARMD and
# without TA-SAM.  Topology, seed, stage profile and wall-clock budget match
# run_tasam_operational_assistant_only_10m.sh.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
MODEL_SEED="${1:-46}"
RUN_DIR="${2:-$PROJECT_ROOT/runs/tasam_operational_baseline_20260814_seed46}"
RUN_DIR="$(realpath -m "$RUN_DIR")"
MANIFEST="$RUN_DIR/disabled_tasam_manifest.json"

mkdir -p "$RUN_DIR"
for pid_file in "$RUN_DIR/rapp.pid" "$RUN_DIR/ns3.pid" "$RUN_DIR/ns3_supervisor.pid" "$RUN_DIR/wall_clock_supervisor.pid"; do
  if [[ -f "$pid_file" ]]; then
    pid="$(cat "$pid_file" 2>/dev/null || true)"
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      echo "run_dir em uso: $RUN_DIR ($pid_file=$pid)" >&2
      exit 3
    fi
    rm -f "$pid_file"
  fi
done

python3 "$SCRIPT_DIR/prepare_tasam_ab_manifest.py" --disabled --output "$MANIFEST" --label "operational_baseline_seed_${MODEL_SEED}"

export GREENRAN_STATE_DIR="$RUN_DIR"
export GREENRAN_FIXED_SCENARIO_CONFIG="$PROJECT_ROOT/config/greenran_fixed_scenario.json"
export GREENRAN_SIM_TIME="100000"
export GREENRAN_WALL_TIME_LIMIT_SECONDS="${GREENRAN_WALL_TIME_LIMIT_SECONDS:-600}"
export GREENRAN_WALL_KEEP_RIC="0"
export GREENRAN_CLEAN_SCOPE="instance"
export GREENRAN_COLLECTION_EVENT_PROFILE="${GREENRAN_COLLECTION_EVENT_PROFILE:-drl_article_conflict_forced_fast_v1}"
export GREENRAN_COLLECTION_EVENT_TIME_SOURCE="${GREENRAN_COLLECTION_EVENT_TIME_SOURCE:-wall}"
export GREENRAN_COLLECTION_EVENT_TICK_S="0.25"
export GREENRAN_COLLECTION_EVENT_CYCLES="0"
export GREENRAN_COLLECTION_DISABLE_APP_OVERRIDES="0"
export GREENRAN_REAL_ONLY="1"
export GREENRAN_REQUIRE_REAL_PDCP="1"
export GREENRAN_START_RIC="${GREENRAN_START_RIC:-1}"
export GREENRAN_ORCHESTRATOR_INTERVAL="2"
export GREENRAN_TASAM_EXPORT_ALLOW_PROXY="0"
export GREENRAN_TASAM_TRUE_ONLINE_ENABLED="0"
export GREENRAN_ML_RETRAIN_ENABLED="false"
export GREENRAN_PDCP_STALE_SECONDS="600"
export GREENRAN_ARMD_MODE="off"
unset GREENRAN_ASSISTANT_DECISION_MODE
export GREENRAN_TASAM_ADVISOR_ENABLED="0"
export GREENRAN_TASAM_ADVISOR_MODE="shadow"
export GREENRAN_TASAM_EVAL_MANIFEST="$MANIFEST"
export GREENRAN_CONTROL_TRIAL_ENABLED="0"
export GREENRAN_CONTROL_TRIAL_STATE="$RUN_DIR/control_trial_state.json"
export GREENRAN_STOP_ON_DECISION_TARGET="0"
export GREENRAN_AB_MODE="operational_baseline"
export GREENRAN_AB_MODEL_SEED="$MODEL_SEED"
export NS_GLOBAL_VALUE="RngRun=$MODEL_SEED"

run_status=0
bash "$SCRIPT_DIR/run_greenran_tasam_3du_wall10m.sh" || run_status=$?
wall_pid_file="$RUN_DIR/wall_clock_supervisor.pid"
if [[ -f "$wall_pid_file" ]]; then
  wall_pid="$(cat "$wall_pid_file" 2>/dev/null || true)"
  while [[ -n "$wall_pid" ]] && kill -0 "$wall_pid" 2>/dev/null; do
    sleep 2
  done
fi

python3 "$SCRIPT_DIR/evaluate_tasam_operational_baseline.py" \
  --run-dir "$RUN_DIR" \
  --output "$RUN_DIR/baseline_summary.json" || run_status=$?
exit "$run_status"
