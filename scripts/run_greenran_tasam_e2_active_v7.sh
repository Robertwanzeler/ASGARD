#!/usr/bin/env bash
set -euo pipefail

# Clean GreenRAN TA-SAM real-PDCP collection after the v6 disk-full failure.
# The targeted monitor owns the raw-to-valid export; the generic article
# exporter is intentionally disabled to avoid duplicating large JSONL traces.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

export GREENRAN_STATE_DIR="${GREENRAN_STATE_DIR:-$PROJECT_ROOT/runs/greenran_tasam_e2_active_20260827_v7}"
export GREENRAN_FIXED_SCENARIO_CONFIG="$PROJECT_ROOT/config/greenran_fixed_scenario.json"
export GREENRAN_SIM_TIME="${GREENRAN_SIM_TIME:-600}"
export GREENRAN_COLLECTION_EVENT_PROFILE="tasam_training_balanced_v3"
export GREENRAN_RAN_PRESSURE_PROFILE="tasam_training_balanced_v3"
export GREENRAN_COLLECTION_EVENT_CYCLES="0"
export GREENRAN_COLLECTION_EVENT_TICK_S="0.25"
export GREENRAN_COLLECTION_EVENT_TIME_SOURCE="sim"
export GREENRAN_COLLECTION_DISABLE_APP_OVERRIDES="0"

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

# ARMD and TA-SAM propose complete allocations; the rApp Judge selects the
# winner. The current checkpoint is shadow-ready, so control-trial gating is
# kept off while the assistant proposals remain fully observable and scored.
export GREENRAN_ARMD_MODE="assist"
export GREENRAN_ASSISTANT_DECISION_MODE="assistant_judge"
export GREENRAN_TASAM_ADVISOR_ENABLED="1"
export GREENRAN_TASAM_ADVISOR_MODE="assistant_only_control"
export GREENRAN_MARL_SHADOW_ENABLE="1"
export GREENRAN_CONTROL_TRIAL_ENABLED="0"
export GREENRAN_TASAM_EVAL_MANIFEST="$PROJECT_ROOT/runs/sac_bootstrap/tasam_candidate_evaluation_latest.json"
export GREENRAN_MARL_CONTROL_GATE_MANIFEST="$PROJECT_ROOT/runs/sac_bootstrap/marl_control_gate_latest.json"

# Disk-safe runtime services.
export GREENRAN_DB_SNAPSHOT_RETENTION="2"
export GREENRAN_DB_SNAPSHOT_INTERVAL="1800"
export GREENRAN_TASAM_EXPORT_ENABLED="0"
export GREENRAN_TASAM_EXPORT_INTERVAL="1800"
export GREENRAN_TASAM_TRUE_ONLINE_ENABLED="0"
export GREENRAN_ML_RETRAIN_ENABLED="false"

if [[ -e "$GREENRAN_STATE_DIR/rapp_data_lake.db" && "${GREENRAN_RESUME_EXISTING:-0}" != "1" ]]; then
  echo "state-dir já contém um banco; escolha um diretório novo: $GREENRAN_STATE_DIR" >&2
  exit 3
fi

exec "$SCRIPT_DIR/run_greenran_tasam_3du_collection.sh"
