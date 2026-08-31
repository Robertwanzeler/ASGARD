#!/usr/bin/env bash
set -euo pipefail

ROOT="/home/robert/orange_nuclear"
CAMPAIGN_ROOT="${GREENRAN_AB_CAMPAIGN_ROOT:-$ROOT/runs/tasam_ab_validation_20260812}"
CHECKPOINT_ROOT="$ROOT/runs/tasam_training_weak_reinforced_20260812/training_final"
mkdir -p "$CAMPAIGN_ROOT"

run_pair() {
  local seed="$1"
  local checkpoint="$CHECKPOINT_ROOT/seed_00${seed}/tasam_selective"
  local baseline_dir="$CAMPAIGN_ROOT/seed_${seed}/baseline"
  local tasam_dir="$CAMPAIGN_ROOT/seed_${seed}/tasam"
  local result="$CAMPAIGN_ROOT/seed_${seed}/ab_result.json"

  mkdir -p "$CAMPAIGN_ROOT/seed_${seed}"
  if python3 - "$baseline_dir/wall_clock_status.json" <<'PY'
import json
import sys
from pathlib import Path
path = Path(sys.argv[1])
try:
    finished = json.loads(path.read_text(encoding="utf-8")).get("phase") == "finished"
except (OSError, json.JSONDecodeError):
    finished = False
raise SystemExit(0 if finished else 1)
PY
  then
    echo "[TA-SAM A/B] seed=$seed baseline já concluído; reutilizando: $(date --iso-8601=seconds)"
  else
    echo "[TA-SAM A/B] seed=$seed baseline: $(date --iso-8601=seconds)"
    bash "$ROOT/scripts/run_tasam_ab_round.sh" baseline "$seed" "$baseline_dir"
  fi
  echo "[TA-SAM A/B] seed=$seed tasam: $(date --iso-8601=seconds)"
  bash "$ROOT/scripts/run_tasam_ab_round.sh" tasam "$seed" "$tasam_dir" "$checkpoint"
  python3 "$ROOT/scripts/evaluate_tasam_ab.py" \
    --baseline "$baseline_dir" \
    --tasam "$tasam_dir" \
    --seed "$seed" \
    --output "$result"
  echo "[TA-SAM A/B] seed=$seed concluído: $(date --iso-8601=seconds)"
}

for seed in 44 46; do
  run_pair "$seed"
done

python3 - "$CAMPAIGN_ROOT" <<'PY'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
results = []
for path in sorted(root.glob("seed_*/ab_result.json")):
    results.append(json.loads(path.read_text(encoding="utf-8")))
payload = {
    "schema": "greenran.tasam_ab_campaign_result.v1",
    "campaign_root": str(root.resolve()),
    "seeds": results,
    "valid_runs": sum(bool(item.get("valid_comparison")) for item in results),
}
(root / "campaign_result.json").write_text(
    json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
)
print(json.dumps({"campaign_root": str(root), "valid_runs": payload["valid_runs"], "total_runs": len(results)}, indent=2))
PY
