#!/usr/bin/env bash
set -u

# Ten paired repetitions for one target-state profile. Existing campaigns are
# never reused or overwritten; pass a new output root for every campaign.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE="${1:?uso: $0 PROFILE TARGET_STATE OUTPUT_ROOT}"
TARGET_STATE="${2:?uso: $0 PROFILE TARGET_STATE OUTPUT_ROOT}"
OUT_ROOT="${3:?uso: $0 PROFILE TARGET_STATE OUTPUT_ROOT}"
REPETITIONS="${GREENRAN_STATE_REPETITIONS:-10}"
CHECKPOINT="${GREENRAN_CVAR_CHECKPOINT:-$ROOT/runs/tasam_greenran_focused_tasam_20260805/seed_0045/tasam_selective}"
WALL_TIME_LIMIT_SECONDS="${GREENRAN_WALL_TIME_LIMIT_SECONDS:-720}"
# One repetition runs baseline and assistant sequentially. Leave a grace
# period after both wall-clock windows so the final real-metric export can
# complete without killing a valid pair halfway through.
VALIDATION_TIMEOUT_SECONDS="${GREENRAN_VALIDATION_TIMEOUT_SECONDS:-$((WALL_TIME_LIMIT_SECONDS * 2 + 300))}"
STATUS_FILE="$OUT_ROOT/campaign_status.json"
LOG_FILE="$OUT_ROOT/campaign.log"
REPORT_ROOT="$OUT_ROOT/report_all"

case "$TARGET_STATE" in
  CONDITIONAL|BLOCKED) ;;
  *) echo "estado alvo inválido: $TARGET_STATE" >&2; exit 2 ;;
esac
if [[ ! -f "$CHECKPOINT/tasam_marl_actors.pt" ]]; then
  echo "checkpoint TA-SAM ausente: $CHECKPOINT/tasam_marl_actors.pt" >&2
  exit 2
fi

mkdir -p "$OUT_ROOT" "$REPORT_ROOT"
echo "[floor-state] configuração: wall=${WALL_TIME_LIMIT_SECONDS}s timeout_por_repetição=${VALIDATION_TIMEOUT_SECONDS}s" | tee -a "$LOG_FILE"
python3 - "$STATUS_FILE" "$PROFILE" "$TARGET_STATE" "$REPETITIONS" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
path.write_text(json.dumps({
    "schema": "greenran.tasam_floor_state_campaign.v1",
    "status": "running",
    "profile": sys.argv[2],
    "target_state": sys.argv[3],
    "total_repetitions": int(sys.argv[4]),
    "completed_repetitions": [],
    "failed_repetitions": [],
    "baseline": "rapp_only",
    "assistant": "armd_plus_tasam",
}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
PY

status=0
for repetition in $(seq 1 "$REPETITIONS"); do
  run_dir="$OUT_ROOT/rep_${repetition}"
  mkdir -p "$run_dir"
  echo "[floor-state] perfil=$PROFILE estado=$TARGET_STATE repetição ${repetition}/${REPETITIONS} iniciando $(date --iso-8601=seconds)" | tee -a "$LOG_FILE"
  timeout --foreground "$VALIDATION_TIMEOUT_SECONDS" env \
    GREENRAN_WALL_TIME_LIMIT_SECONDS="$WALL_TIME_LIMIT_SECONDS" \
    GREENRAN_CVAR_VALIDATION_ROOT="$run_dir" \
    GREENRAN_CVAR_VALIDATION_SEED=45 \
    GREENRAN_CVAR_CHECKPOINT="$CHECKPOINT" \
    GREENRAN_COLLECTION_EVENT_PROFILE="$PROFILE" \
    GREENRAN_COLLECTION_EVENT_TIME_SOURCE=wall \
    GREENRAN_COLLECTION_EVENT_CYCLES=0 \
    GREENRAN_METRICS_TARGET=93 \
    GREENRAN_METRICS_MIN_TARGET=93 \
    bash "$ROOT/scripts/run_tasam_cvar_validation_100.sh" \
    > "$run_dir/validation.log" 2>&1
  code=$?
  python3 - "$STATUS_FILE" "$repetition" "$code" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
payload = json.loads(path.read_text(encoding="utf-8"))
rep = int(sys.argv[2])
code = int(sys.argv[3])
key = "completed_repetitions" if code == 0 else "failed_repetitions"
payload.setdefault(key, []).append(rep)
payload[key] = sorted(set(payload[key]))
path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
PY
  if [[ "$code" -ne 0 ]]; then status=1; fi
  echo "[floor-state] repetição ${repetition}/${REPETITIONS} terminou code=${code} $(date --iso-8601=seconds)" | tee -a "$LOG_FILE"
done

PAIR_ARGS=()
for repetition in $(seq 1 "$REPETITIONS"); do
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

python3 "$ROOT/scripts/validate_tasam_floor_state_campaign.py" \
  --root "$OUT_ROOT" \
  --target-state "$TARGET_STATE" \
  --output "$REPORT_ROOT/state_validation.json" \
  >> "$LOG_FILE" 2>&1
validation_code=$?
if [[ "$validation_code" -ne 0 ]]; then status=1; fi

python3 - "$STATUS_FILE" "$status" "$report_code" "$validation_code" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
payload = json.loads(path.read_text(encoding="utf-8"))
campaign_status = int(sys.argv[2])
payload["status"] = "completed" if campaign_status == 0 else "completed_with_errors"
payload["report_code"] = int(sys.argv[3])
payload["validation_code"] = int(sys.argv[4])
payload["report"] = str(path.parent / "report_all" / "network_resource_result.json")
payload["state_validation"] = str(path.parent / "report_all" / "state_validation.json")
path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
PY

echo "[floor-state] campanha finalizada status=${status} report=${REPORT_ROOT}/network_resource_result.json validation=${REPORT_ROOT}/state_validation.json" | tee -a "$LOG_FILE"
exit "$status"
