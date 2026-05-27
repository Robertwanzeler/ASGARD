#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_DIR="${GREENRAN_HANDOFF_STATE_DIR:-/tmp/greenran_fixed_awac_pressure_smoke_clean_v5}"
CSV_PATH="${GREENRAN_HANDOFF_CSV_PATH:-$ROOT_DIR/runs/sac_bootstrap/workload_trace_fixed_baseline_v1_pressure_smoke_clean_v5.csv}"
STATUS_JSON="${GREENRAN_HANDOFF_STATUS_JSON:-$ROOT_DIR/runs/sac_bootstrap/fixed_awac_pressure_smoke_clean_v5_status.json}"
CHECKPOINT_DIR="${GREENRAN_HANDOFF_CHECKPOINT_DIR:-$ROOT_DIR/runs/sac_bootstrap/fixed_awac_pressure_smoke_clean_v5_status_checkpoints}"
LATEST_CHECKPOINT="$CHECKPOINT_DIR/latest_checkpoint.json"
MANIFEST_JSON="${GREENRAN_HANDOFF_MANIFEST_JSON:-$ROOT_DIR/runs/sac_bootstrap/awac_retrain_handoff_latest.json}"
COLLECTION_PROFILE="${GREENRAN_HANDOFF_COLLECTION_PROFILE:-drl_article_conflict_smoke_v1}"
ROW_TARGET="${GREENRAN_HANDOFF_ROW_TARGET:-5000}"
MIN_ROWS="${GREENRAN_HANDOFF_MIN_ROWS:-300}"
GOAL_SIM_TIME="${GREENRAN_HANDOFF_GOAL_SIM_TIME:-120}"
RUNTIME_SIM_TIME="${GREENRAN_HANDOFF_RUNTIME_SIM_TIME:-120}"
APP2_WAIT="${GREENRAN_HANDOFF_APP2_WAIT_SECONDS:-15}"
STOP_ROWS="${GREENRAN_HANDOFF_STOP_ROWS:-1500}"
POLL_SECONDS="${GREENRAN_HANDOFF_POLL_SECONDS:-20}"
PROBE_LOG="${GREENRAN_HANDOFF_PROBE_LOG:-/tmp/greenran_awac_handoff_probe.log}"
TRAIN_LOG="${GREENRAN_HANDOFF_TRAIN_LOG:-/tmp/greenran_awac_handoff.log}"
TRAIN_ON_READY="${GREENRAN_HANDOFF_TRAIN_ON_READY:-0}"

cd "$ROOT_DIR"

echo "[handoff-watch] state_dir=$STATE_DIR manifest=$MANIFEST_JSON train_on_ready=$TRAIN_ON_READY"

while true; do
  python3 scripts/run_fixed_awac_controlled_collection.py     --state-dir "$STATE_DIR"     --csv-path "$CSV_PATH"     --status-json "$STATUS_JSON"     --launcher-log /tmp/greenran_awac_handoff_launcher.log     --checkpoint-dir "$CHECKPOINT_DIR"     --collection-profile "$COLLECTION_PROFILE"     --row-target "$ROW_TARGET"     --min-rows "$MIN_ROWS"     --goal-sim-time "$GOAL_SIM_TIME"     --runtime-sim-time "$RUNTIME_SIM_TIME"     --app2-wait-seconds "$APP2_WAIT"     --no-start --once > "$PROBE_LOG" 2>&1 || true

  python3 scripts/prepare_awac_retrain_handoff.py     --status-json "$STATUS_JSON"     --latest-checkpoint "$LATEST_CHECKPOINT"     --manifest-json "$MANIFEST_JSON"     --stop-rows "$STOP_ROWS"     --once > "$TRAIN_LOG" 2>&1 || true

  STOP_READY=$(python3 - <<'PY2'
import json
from pathlib import Path
p = Path(r"/home/robert/orange_nuclear/runs/sac_bootstrap/awac_retrain_handoff_latest.json")
try:
    payload = json.loads(p.read_text(encoding="utf-8"))
except Exception:
    print("0")
else:
    print("1" if payload.get("stop_ready") else "0")
PY2
)

  if [[ "$STOP_READY" == "1" ]]; then
    echo "[handoff-watch] stop criterion reached"
    if [[ "$TRAIN_ON_READY" == "1" ]]; then
      python3 scripts/prepare_awac_retrain_handoff.py         --status-json "$STATUS_JSON"         --latest-checkpoint "$LATEST_CHECKPOINT"         --manifest-json "$MANIFEST_JSON"         --stop-rows "$STOP_ROWS"         --train --once >> "$TRAIN_LOG" 2>&1 || true
    fi
    exit 0
  fi

  sleep "$POLL_SECONDS"
done
