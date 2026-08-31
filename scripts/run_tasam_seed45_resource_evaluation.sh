#!/usr/bin/env bash
set -u

# Re-evaluate the preserved seed-45 campaign with direct resource accounting.
# This creates a new report and never overwrites the approved network report.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_ROOT="${GREENRAN_SEED45_REPETITIONS_ROOT:-$ROOT/runs/tasam_seed45_repetitions_20260823}"
REPORT_ROOT="$OUT_ROOT/resource_report_all10"
mkdir -p "$REPORT_ROOT"

PAIR_ARGS=(
  --pair "$ROOT/runs/tasam_cvar_validation_100_seed45_20260823/baseline" \
         "$ROOT/runs/tasam_cvar_validation_100_seed45_20260823/assistant" 45 1
)
for repetition in 2 3 4 5 6 7 8 9 10; do
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
  --output "$REPORT_ROOT/network_resource_result.json"

cp "$REPORT_ROOT/network_resource_result.json" "$OUT_ROOT/network_resource_result_all10.json"
cp "$REPORT_ROOT/network_resource_result_resources.csv" "$OUT_ROOT/network_resource_result_all10.csv"
echo "resource_report=$REPORT_ROOT/network_resource_result.json"
echo "resource_csv=$REPORT_ROOT/network_resource_result_resources.csv"
