#!/usr/bin/env bash
set -u

# Clean ten-repetition campaign for the per-UE resource-floor policy.
# The previous seed45 campaign is intentionally never reused or overwritten.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_ROOT="${GREENRAN_FLOOR_REPETITIONS_ROOT:-$ROOT/runs/tasam_seed45_repetitions_floor_v1_20260823}"
CHECKPOINT="${GREENRAN_CVAR_CHECKPOINT:-$ROOT/runs/tasam_greenran_focused_tasam_20260805/seed_0045/tasam_selective}"
STATUS_FILE="$OUT_ROOT/campaign_status.json"
LOG_FILE="$OUT_ROOT/campaign.log"
REPORT_ROOT="$OUT_ROOT/report_all10"

mkdir -p "$OUT_ROOT" "$REPORT_ROOT"

python3 - "$STATUS_FILE" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
path.write_text(json.dumps({
    "schema": "greenran.tasam_seed45_floor_campaign.v1",
    "status": "running",
    "total_repetitions": 10,
    "completed_repetitions": [],
    "failed_repetitions": [],
    "policy": "per_ue_floor_v1",
    "baseline": "rapp_only",
    "assistant": "armd_plus_tasam",
}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
PY

status=0
for repetition in $(seq 1 10); do
  run_dir="$OUT_ROOT/rep_${repetition}"
  mkdir -p "$run_dir"
  echo "[floor-v1] repetição ${repetition}/10 iniciando $(date --iso-8601=seconds)" | tee -a "$LOG_FILE"
  env GREENRAN_CVAR_VALIDATION_ROOT="$run_dir" \
    GREENRAN_CVAR_VALIDATION_SEED=45 \
    GREENRAN_CVAR_CHECKPOINT="$CHECKPOINT" \
    GREENRAN_METRICS_TARGET=93 \
    GREENRAN_METRICS_MIN_TARGET=93 \
    bash "$ROOT/scripts/run_tasam_cvar_validation_100.sh" \
    > "$run_dir/validation.log" 2>&1
  code=$?
  if [[ "$code" -eq 0 ]]; then
    python3 - "$STATUS_FILE" "$repetition" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
payload = json.loads(path.read_text(encoding="utf-8"))
rep = int(sys.argv[2])
payload.setdefault("completed_repetitions", []).append(rep)
payload["completed_repetitions"] = sorted(set(payload["completed_repetitions"]))
path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
PY
  else
    status=1
    python3 - "$STATUS_FILE" "$repetition" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
payload = json.loads(path.read_text(encoding="utf-8"))
rep = int(sys.argv[2])
payload.setdefault("failed_repetitions", []).append(rep)
payload["failed_repetitions"] = sorted(set(payload["failed_repetitions"]))
path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
PY
  fi
  echo "[floor-v1] repetição ${repetition}/10 terminou code=${code} $(date --iso-8601=seconds)" | tee -a "$LOG_FILE"
done

PAIR_ARGS=()
for repetition in $(seq 1 10); do
  PAIR_ARGS+=(
    --pair "$OUT_ROOT/rep_${repetition}/baseline" \
           "$OUT_ROOT/rep_${repetition}/assistant" 45 "$repetition"
  )
done

python3 "$ROOT/scripts/evaluate_tasam_network_campaign.py" \
  "${PAIR_ARGS[@]}" \
  --target-decisions 100 \
  --allow-metric-gap \
  --profile tasam_cvar_balanced_v3_resource_accounting \
  --output "$REPORT_ROOT/network_resource_result.json" \
  >> "$LOG_FILE" 2>&1
report_code=$?
if [[ "$report_code" -ne 0 ]]; then status=1; fi

if [[ -f "$REPORT_ROOT/network_resource_result.json" ]]; then
  cp "$REPORT_ROOT/network_resource_result.json" "$OUT_ROOT/network_resource_result_all10.json"
fi
if [[ -f "$REPORT_ROOT/network_resource_result_resources.csv" ]]; then
  cp "$REPORT_ROOT/network_resource_result_resources.csv" "$OUT_ROOT/network_resource_result_all10.csv"
fi

python3 - "$STATUS_FILE" "$status" "$report_code" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
payload = json.loads(path.read_text(encoding="utf-8"))
payload["status"] = "completed" if int(sys.argv[2]) == 0 else "completed_with_errors"
payload["report_code"] = int(sys.argv[3])
payload["report"] = str(path.parent / "report_all10" / "network_resource_result.json")
path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
PY

echo "[floor-v1] campanha finalizada status=${status} report=${REPORT_ROOT}/network_resource_result.json" | tee -a "$LOG_FILE"
exit "$status"
