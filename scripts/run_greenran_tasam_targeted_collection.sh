#!/usr/bin/env bash
set -euo pipefail

# Complete GreenRAN TA-SAM collection for all nine article-adapted stages.
# The monitor decides when enough valid records exist; this wrapper keeps the
# rApp/live allocator as the only runtime helper so the TA-SAM comparison is
# not confounded by ARMD.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

export GREENRAN_STATE_DIR="${GREENRAN_STATE_DIR:-$PROJECT_ROOT/runs/greenran_tasam_targeted_20260807}"
export GREENRAN_COLLECTION_EVENT_PROFILE="${GREENRAN_COLLECTION_EVENT_PROFILE:-tasam_training_balanced_v2}"
export GREENRAN_COLLECTION_DISABLE_APP_OVERRIDES="0"
export GREENRAN_ARMD_MODE="off"
export GREENRAN_TASAM_ADVISOR_ENABLED="0"
export GREENRAN_MARL_SHADOW_ENABLE="0"
export GREENRAN_TASAM_TRUE_ONLINE_ENABLED="0"
export GREENRAN_ML_RETRAIN_ENABLED="false"
export GREENRAN_TASAM_EXPORT_ALLOW_PROXY="0"
export GREENRAN_TASAM_TRAINABLE_PROFILE="rapp_online_trainable"
export GREENRAN_TASAM_EVAL_MANIFEST="${GREENRAN_TASAM_EVAL_MANIFEST:-$PROJECT_ROOT/runs/sac_bootstrap/tasam_candidate_evaluation_latest.json}"
export GREENRAN_MARL_CONTROL_GATE_MANIFEST="${GREENRAN_MARL_CONTROL_GATE_MANIFEST:-$PROJECT_ROOT/runs/sac_bootstrap/marl_control_gate_latest.json}"
export GREENRAN_REQUIRE_REAL_PDCP="1"
export GREENRAN_REAL_ONLY="1"
export GREENRAN_START_RIC="${GREENRAN_START_RIC:-0}"
export GREENRAN_NS3_E2NR_ENABLED="true"
export GREENRAN_NS3_E2DU_ENABLED="true"
export GREENRAN_NS3_E2CUUP_ENABLED="false"
export GREENRAN_NS3_E2CUCP_ENABLED="false"

exec "$SCRIPT_DIR/run_greenran_tasam_3du_collection.sh"
