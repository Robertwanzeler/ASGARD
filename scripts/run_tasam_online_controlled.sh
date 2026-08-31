#!/usr/bin/env bash
set -euo pipefail

# Isolated real-PDCP GreenRAN runtime for TA-SAM online learning.  The
# offline trainer is unrelated and remains paused in its original directory.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

export GREENRAN_STATE_DIR="${GREENRAN_STATE_DIR:-$PROJECT_ROOT/runs/tasam_block_state_seed45_20260830/online_round_v7_live}"
export GREENRAN_TASAM_CHECKPOINT="${GREENRAN_TASAM_CHECKPOINT:-$PROJECT_ROOT/runs/tasam_block_state_seed45_20260828/online_round_v5_live/candidates/candidate_0006/tasam_selective}"
export GREENRAN_TASAM_HISTORICAL_TRACE="${GREENRAN_TASAM_HISTORICAL_TRACE:-$PROJECT_ROOT/runs/greenran_tasam_e2_active_20260827_v7/final_dataset/tasam_article_trace_final.jsonl}"
export GREENRAN_FIXED_SCENARIO_CONFIG="$PROJECT_ROOT/config/greenran_fixed_scenario.json"
export GREENRAN_SIM_TIME="${GREENRAN_SIM_TIME:-600}"
export GREENRAN_COLLECTION_EVENT_PROFILE="tasam_training_balanced_v3"
export GREENRAN_RAN_PRESSURE_PROFILE="tasam_training_balanced_v3"
export GREENRAN_COLLECTION_EVENT_CYCLES="0"
export GREENRAN_COLLECTION_EVENT_TICK_S="0.25"
# Allow controlled runs to select wall-clock progression.  The online
# isolated runtime has no ns-3 time-feed back into the alternator, so forcing
# sim time can leave the first stage active forever.
export GREENRAN_COLLECTION_EVENT_TIME_SOURCE="${GREENRAN_COLLECTION_EVENT_TIME_SOURCE:-wall}"
export GREENRAN_COLLECTION_DISABLE_APP_OVERRIDES="0"
export GREENRAN_XAPP_MODE="file"

export GREENRAN_NS3_UE_COUNT="20"
export GREENRAN_NS3_CAMERA_UE_COUNT="3"
export GREENRAN_NS3_VEHICLE_UE_COUNT="5"
export GREENRAN_NS3_MMWAVE_ENB_NODES="3"
export GREENRAN_NS3_E2NR_ENABLED="true"
export GREENRAN_NS3_E2DU_ENABLED="true"
export GREENRAN_NS3_E2CUUP_ENABLED="false"
export GREENRAN_NS3_E2CUCP_ENABLED="false"
export GREENRAN_NS3_ENABLE_E2_FILE_LOGGING="true"
export GREENRAN_NS3_USE_MC_UE_DEVICES="true"
export GREENRAN_REQUIRE_REAL_PDCP="1"
export GREENRAN_REAL_ONLY="1"
export GREENRAN_TASAM_EXPORT_ALLOW_PROXY="0"
export GREENRAN_START_RIC="0"
export GREENRAN_PDCP_STALE_SECONDS="600"
# The active seed-45 checkpoint was trained with the 3 contextual
# ALLOWED/CONDITIONAL/BLOCKED features (13 inputs per DU).  Keep the online
# runtime on that same state contract; without it the evaluator silently
# falls back to the heuristic policy.
export GREENRAN_TASAM_EXPLICIT_STATE_FEATURE="1"

# The default online mode gives TA-SAM complete control of every valid
# decision.  Protected validation modes remain selectable explicitly.
export GREENRAN_ARMD_MODE="assist"
export GREENRAN_ASSISTANT_DECISION_MODE="${GREENRAN_ASSISTANT_DECISION_MODE:-cooperative_hierarchy}"
export GREENRAN_TASAM_ADVISOR_ENABLED="1"
export GREENRAN_TASAM_ADVISOR_MODE="${GREENRAN_TASAM_ADVISOR_MODE:-tasam_full_control}"
export GREENRAN_MARL_SHADOW_ENABLE="1"
export GREENRAN_TASAM_REQUIRE_CHECKPOINT="${GREENRAN_TASAM_REQUIRE_CHECKPOINT:-1}"
export GREENRAN_TASAM_STABILITY_WINDOW="${GREENRAN_TASAM_STABILITY_WINDOW:-1}"
export GREENRAN_CONTROL_TRIAL_ENABLED="${GREENRAN_CONTROL_TRIAL_ENABLED:-1}"
export GREENRAN_TASAM_TRUE_ONLINE_ENABLED="${GREENRAN_TASAM_TRUE_ONLINE_ENABLED:-1}"
export GREENRAN_TASAM_TRUE_ONLINE_SEED="${GREENRAN_TASAM_TRUE_ONLINE_SEED:-45}"
export GREENRAN_TASAM_TRUE_ONLINE_INIT_CHECKPOINT="${GREENRAN_TASAM_TRUE_ONLINE_INIT_CHECKPOINT:-$GREENRAN_TASAM_CHECKPOINT}"
export GREENRAN_ML_RETRAIN_ENABLED="false"

# This round intentionally lets TA-SAM degrade the simulated network when its
# action is poor.  The next real observation supplies continuous reward; no
# automatic rollback or silent live-allocator fallback is allowed.
export GREENRAN_TASAM_FORCE_FULL_ROLLOUT="${GREENRAN_TASAM_FORCE_FULL_ROLLOUT:-1}"

# Keep the generic exporter off: the controlled online manager owns the
# versioned replay/candidate artifacts and uses the 6-second real-metric gate.
export GREENRAN_TASAM_EXPORT_ENABLED="0"
export GREENRAN_DB_SNAPSHOT_RETENTION="2"
export GREENRAN_DB_SNAPSHOT_INTERVAL="1800"

export GREENRAN_TASAM_ONLINE_ROLLOUT_MANIFEST="$GREENRAN_STATE_DIR/online_rollout.json"
export GREENRAN_TASAM_ONLINE_ROLLOUT_FRACTION="${GREENRAN_TASAM_ONLINE_ROLLOUT_FRACTION:-1.0}"
export GREENRAN_TASAM_EVAL_MANIFEST="$GREENRAN_STATE_DIR/online_eval_manifest.json"
export GREENRAN_MARL_CONTROL_GATE_MANIFEST="$GREENRAN_STATE_DIR/online_control_gate.json"

CONTROLLER_RECOVERY_ARGS=()
if [[ "${GREENRAN_RESUME_AFTER_ROLLBACK:-0}" == "1" ]]; then
  CONTROLLER_RECOVERY_ARGS+=(--resume-after-rollback)
fi

if [[ -e "$GREENRAN_STATE_DIR/rapp_data_lake.db" && "${GREENRAN_RESUME_EXISTING:-0}" != "1" ]]; then
  echo "state-dir já contém um banco; escolha um diretório novo: $GREENRAN_STATE_DIR" >&2
  exit 3
fi

mkdir -p "$GREENRAN_STATE_DIR"
python3 "$SCRIPT_DIR/run_tasam_online_controlled.py" \
  --state-dir "$GREENRAN_STATE_DIR" \
  --checkpoint "$GREENRAN_TASAM_CHECKPOINT" \
  --historical-trace "$GREENRAN_TASAM_HISTORICAL_TRACE" \
  "${CONTROLLER_RECOVERY_ARGS[@]}" \
  --once

nohup setsid env \
  GREENRAN_STATE_DIR="$GREENRAN_STATE_DIR" \
  GREENRAN_TASAM_EVAL_MANIFEST="$GREENRAN_TASAM_EVAL_MANIFEST" \
  GREENRAN_MARL_CONTROL_GATE_MANIFEST="$GREENRAN_MARL_CONTROL_GATE_MANIFEST" \
  GREENRAN_TASAM_ONLINE_ROLLOUT_MANIFEST="$GREENRAN_TASAM_ONLINE_ROLLOUT_MANIFEST" \
  GREENRAN_KEEP_FOREGROUND="1" \
  bash "$SCRIPT_DIR/run_tasam_article_ns3_collection.sh" \
  > "$GREENRAN_STATE_DIR/greenran_online_runtime.log" 2>&1 < /dev/null &
echo $! > "$GREENRAN_STATE_DIR/greenran_online_runtime.pid"

nohup setsid python3 "$SCRIPT_DIR/run_tasam_online_controlled.py" \
  --state-dir "$GREENRAN_STATE_DIR" \
  --checkpoint "$GREENRAN_TASAM_CHECKPOINT" \
  --historical-trace "$GREENRAN_TASAM_HISTORICAL_TRACE" \
  "${CONTROLLER_RECOVERY_ARGS[@]}" \
  > "$GREENRAN_STATE_DIR/online_controller.log" 2>&1 < /dev/null &
echo $! > "$GREENRAN_STATE_DIR/online_controller.pid"

echo "TA-SAM online control iniciado"
echo "  state_dir: $GREENRAN_STATE_DIR"
echo "  checkpoint: seed_0045/tasam_selective"
echo "  modo: TA-SAM full control; Judge somente para auditoria"
echo "  rollout inicial: ${GREENRAN_TASAM_ONLINE_ROLLOUT_FRACTION} (TA-SAM full control; recompensa por métricas reais; rollback desativado)"
echo
echo "Acompanhar:"
echo "  python3 $SCRIPT_DIR/watch_tasam_online_controlled.py --state-dir '$GREENRAN_STATE_DIR' --interval 10"

# Keep the launch supervisor attached to this task.  The actual GreenRAN
# services are children of the foreground runtime and therefore remain alive
# when the original terminal is closed.
while kill -0 "$(cat "$GREENRAN_STATE_DIR/greenran_online_runtime.pid")" 2>/dev/null \
      || kill -0 "$(cat "$GREENRAN_STATE_DIR/online_controller.pid")" 2>/dev/null; do
  sleep 5
done
