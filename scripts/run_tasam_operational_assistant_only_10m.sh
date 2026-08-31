#!/usr/bin/env bash
set -euo pipefail

# One-off operational validation: real PDCP, ten minutes of wall time, with
# ARMD and TA-SAM as the only assistants.  This is intentionally not an A/B
# campaign and does not stop after a fixed number of decisions.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

MODEL_SEED="${1:-46}"
RUN_DIR="${2:-$PROJECT_ROOT/runs/tasam_operational_assistant_only_20260814_seed46}"
CHECKPOINT_DIR="${3:-$PROJECT_ROOT/runs/tasam_greenran_train_20260808/tasam_selective/seed_00${MODEL_SEED}/tasam_selective}"
RUN_DIR="$(realpath -m "$RUN_DIR")"

if [[ ! -f "$CHECKPOINT_DIR/tasam_marl_actors.pt" ]]; then
  echo "checkpoint TA-SAM ausente: $CHECKPOINT_DIR/tasam_marl_actors.pt" >&2
  exit 2
fi

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

TASAM_MANIFEST="$RUN_DIR/tasam_operational_manifest.json"
GATE_MANIFEST="$RUN_DIR/marl_control_gate.json"
python3 "$SCRIPT_DIR/prepare_tasam_ab_manifest.py" \
  --checkpoint-dir "$CHECKPOINT_DIR" \
  --output "$TASAM_MANIFEST" \
  --label "operational_assistant_only_seed_${MODEL_SEED}"
python3 "$SCRIPT_DIR/prepare_tasam_evaluation_gate.py" \
  --checkpoint-dir "$CHECKPOINT_DIR" \
  --output "$GATE_MANIFEST"

export GREENRAN_STATE_DIR="$RUN_DIR"
export GREENRAN_FIXED_SCENARIO_CONFIG="$PROJECT_ROOT/config/greenran_fixed_scenario.json"
export GREENRAN_SIM_TIME="100000"
export GREENRAN_WALL_TIME_LIMIT_SECONDS="${GREENRAN_WALL_TIME_LIMIT_SECONDS:-600}"
export GREENRAN_WALL_KEEP_RIC="0"
export GREENRAN_CLEAN_SCOPE="instance"
export GREENRAN_COLLECTION_EVENT_PROFILE="${GREENRAN_COLLECTION_EVENT_PROFILE:-drl_article_conflict_forced_fast_v1}"
# This launcher is a wall-clock operational run. Drive the scenario stages
# from real time so all camera/vehicle/sensor contention and recovery windows
# are exercised during the ten-minute budget.
export GREENRAN_COLLECTION_EVENT_TIME_SOURCE="${GREENRAN_COLLECTION_EVENT_TIME_SOURCE:-wall}"
export GREENRAN_COLLECTION_EVENT_TICK_S="0.25"
export GREENRAN_COLLECTION_EVENT_CYCLES="0"
export GREENRAN_COLLECTION_DISABLE_APP_OVERRIDES="0"
export GREENRAN_REAL_ONLY="1"
export GREENRAN_REQUIRE_REAL_PDCP="1"
# Allow a lab with an externally managed nearRT-RIC, or a restricted host
# without SCTP privileges, to run the local real-PDCP validation.  The
# assistant policy is independent of whether this launcher owns the RIC.
export GREENRAN_START_RIC="${GREENRAN_START_RIC:-1}"
export GREENRAN_ORCHESTRATOR_INTERVAL="2"
export GREENRAN_TASAM_EXPORT_ALLOW_PROXY="0"
export GREENRAN_TASAM_TRUE_ONLINE_ENABLED="0"
export GREENRAN_ML_RETRAIN_ENABLED="false"
export GREENRAN_PDCP_STALE_SECONDS="600"

# Strict assistant-only arbitration: TA-SAM supplies the resource allocation,
# ARMD supplies the safety envelope, and no live allocator or heuristic may
# replace either assistant.
export GREENRAN_ARMD_MODE="assist"
# Operational mode: ARMD supplies the safety envelope and TA-SAM optimizes
# inside it; the rApp Judge remains the final authority.
export GREENRAN_ASSISTANT_DECISION_MODE="${GREENRAN_ASSISTANT_DECISION_MODE:-cooperative_hierarchy}"
export GREENRAN_ARMD_SUMMARY="$PROJECT_ROOT/runs/graphsage_article00_hybrid_final/hybrid_final_summary.json"
export GREENRAN_TASAM_ADVISOR_ENABLED="1"
export GREENRAN_TASAM_ADVISOR_MODE="assistant_only_control"
export GREENRAN_TASAM_EVAL_MANIFEST="$TASAM_MANIFEST"
export GREENRAN_MARL_CONTROL_GATE_MANIFEST="$GATE_MANIFEST"
export GREENRAN_CONTROL_TRIAL_ENABLED="1"
export GREENRAN_CONTROL_TRIAL_FRACTION="1.0"
export GREENRAN_CONTROL_TRIAL_TARGET_DECISIONS="1000000"
export GREENRAN_CONTROL_TRIAL_ROLLING_WINDOW="30"
export GREENRAN_CONTROL_TRIAL_CRITICAL_STREAK="3"
export GREENRAN_CONTROL_TRIAL_MIN_CONFIDENCE="0.60"
export GREENRAN_CONTROL_TRIAL_MIN_RAN_DELTA="-0.01"
export GREENRAN_CONTROL_TRIAL_MIN_AI_DELTA="-0.01"
export GREENRAN_CONTROL_TRIAL_STATE="$RUN_DIR/control_trial_state.json"
export GREENRAN_STOP_ON_DECISION_TARGET="0"
export GREENRAN_DECISION_TARGET="1000000"
export GREENRAN_AB_MODE="operational_assistant_only"
export GREENRAN_AB_MODEL_SEED="$MODEL_SEED"
export NS_GLOBAL_VALUE="RngRun=$MODEL_SEED"

run_status=0
bash "$SCRIPT_DIR/run_greenran_tasam_3du_wall10m.sh" || run_status=$?

# The collection launcher starts the wall-clock supervisor in the background.
# Keep this wrapper alive until that supervisor has really ended; otherwise
# the post-run report would inspect an empty/partial Data Lake.
wall_pid_file="$RUN_DIR/wall_clock_supervisor.pid"
if [[ -f "$wall_pid_file" ]]; then
  wall_pid="$(cat "$wall_pid_file" 2>/dev/null || true)"
  while [[ -n "$wall_pid" ]] && kill -0 "$wall_pid" 2>/dev/null; do
    sleep 2
  done
fi

if [[ -f "$RUN_DIR/rapp_data_lake.db" ]]; then
  python3 "$SCRIPT_DIR/evaluate_tasam_operational_run.py" \
    --run-dir "$RUN_DIR" \
    --output "$RUN_DIR/operational_summary.json" || run_status=$?
else
  echo "[TASAM_OPERATIONAL] Data Lake ausente; a rodada não foi avaliada porque o runtime não iniciou corretamente: $RUN_DIR/rapp_data_lake.db" >&2
  run_status="${run_status:-1}"
fi

exit "$run_status"
