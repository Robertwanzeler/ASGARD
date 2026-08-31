#!/usr/bin/env bash
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_ROOT="${GREENRAN_SEED45_REPETITIONS_ROOT:-$ROOT/runs/tasam_seed45_repetitions_20260823}"
CHECKPOINT="$ROOT/runs/tasam_greenran_focused_tasam_20260805/seed_0045/tasam_selective"
status=0

for repetition in 8 9 10; do
  run_dir="$OUT_ROOT/rep_${repetition}"
  mkdir -p "$run_dir"
  echo "[seed45] repetição ${repetition} iniciando $(date --iso-8601=seconds)"
  env GREENRAN_CVAR_VALIDATION_ROOT="$run_dir" \
    GREENRAN_CVAR_VALIDATION_SEED=45 \
    GREENRAN_CVAR_CHECKPOINT="$CHECKPOINT" \
    GREENRAN_METRICS_TARGET=93 \
    GREENRAN_METRICS_MIN_TARGET=93 \
    bash "$ROOT/scripts/run_tasam_cvar_validation_100.sh" \
    > "$run_dir/validation.log" 2>&1
  code=$?
  echo "[seed45] repetição ${repetition} terminou code=${code} $(date --iso-8601=seconds)"
  if [[ "$code" -ne 0 ]]; then status=1; fi
done

REPORT_ROOT="$OUT_ROOT/report_all10"
mkdir -p "$REPORT_ROOT"
PAIR_ARGS=(
  --pair "$ROOT/runs/tasam_cvar_validation_100_seed45_20260823/baseline" "$ROOT/runs/tasam_cvar_validation_100_seed45_20260823/assistant" 45 1
)
for repetition in 2 3 4 5 6 7 8 9 10; do
  PAIR_ARGS+=(
    --pair "$OUT_ROOT/rep_${repetition}/baseline" "$OUT_ROOT/rep_${repetition}/assistant" 45 "$repetition"
  )
done
python3 "$ROOT/scripts/evaluate_tasam_network_campaign.py" \
  "${PAIR_ARGS[@]}" \
  --target-decisions 100 \
  --allow-metric-gap \
  --profile tasam_cvar_balanced_v3 \
  --output "$REPORT_ROOT/network_result.json"
cp "$REPORT_ROOT/network_result.json" "$OUT_ROOT/network_result_all10.json"
exit "$status"
