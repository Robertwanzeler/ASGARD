#!/usr/bin/env bash
set -euo pipefail

# Repeated paired validation for the reward-shaped TA-SAM candidate.
# The baseline remains rApp-only; the assistant side uses ARMD + TA-SAM.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT_ROOT="${GREENRAN_TUNED_BORDERLINE_ROOT:-$ROOT/runs/tasam_ab_tuned_borderline_20260817}"
CHECKPOINT_ROOT="${GREENRAN_TUNED_CHECKPOINT_ROOT:-$ROOT/runs/tasam_greenran_train_targeted_20260817_v2}"
PROFILE="${GREENRAN_TUNED_BORDERLINE_PROFILE:-drl_balanced_borderline_v1}"
REPETITIONS="${GREENRAN_TUNED_REPETITIONS:-5}"

if ! [[ "$REPETITIONS" =~ ^[1-9][0-9]*$ ]]; then
  echo "REPETITIONS deve ser um inteiro positivo: $REPETITIONS" >&2
  exit 2
fi

for seed in 44 45 46; do
  checkpoint="$CHECKPOINT_ROOT/seed_00${seed}/tasam_selective"
  if [[ ! -f "$checkpoint/tasam_marl_actors.pt" ]]; then
    echo "checkpoint ajustado ausente para seed $seed: $checkpoint" >&2
    exit 2
  fi
done

for repetition in $(seq 1 "$REPETITIONS"); do
  for seed in 44 45 46; do
    pair_root="$OUTPUT_ROOT/profiles/$PROFILE/seed_${seed}/rep_${repetition}"
    baseline="$pair_root/baseline"
    assistant="$pair_root/assistant"
    pair_json="$pair_root/network_pair.json"
    checkpoint="$CHECKPOINT_ROOT/seed_00${seed}/tasam_selective"
    mkdir -p "$pair_root"

    if [[ -f "$pair_json" ]] && python3 - "$pair_json" <<'PY'
import json
import sys
try:
    payload = json.load(open(sys.argv[1], encoding="utf-8"))
    pair = (payload.get("pairs") or [{}])[0]
    raise SystemExit(0 if pair.get("valid") else 1)
except (OSError, ValueError, IndexError, TypeError):
    raise SystemExit(1)
PY
    then
      echo "[tuned borderline] repetition=$repetition seed=$seed já concluído"
      continue
    fi

    echo "[tuned borderline] repetition=$repetition seed=$seed baseline $(date --iso-8601=seconds)"
    GREENRAN_COLLECTION_EVENT_PROFILE="$PROFILE" \
      bash "$ROOT/scripts/run_tasam_stress_ab_round.sh" baseline "$seed" "$baseline"

    echo "[tuned borderline] repetition=$repetition seed=$seed assistant $(date --iso-8601=seconds)"
    GREENRAN_COLLECTION_EVENT_PROFILE="$PROFILE" \
      bash "$ROOT/scripts/run_tasam_stress_ab_round.sh" joint "$seed" "$assistant" "$checkpoint"

    python3 "$ROOT/scripts/evaluate_tasam_network_campaign.py" \
      --pair "$baseline" "$assistant" "$seed" "$repetition" \
      --profile "$PROFILE" \
      --output "$pair_json"
  done
done

python3 "$ROOT/scripts/evaluate_tasam_network_campaign.py" \
  --campaign-root "$OUTPUT_ROOT/profiles/$PROFILE" \
  --output "$OUTPUT_ROOT/profiles/$PROFILE/profile_report.json"
python3 "$ROOT/scripts/evaluate_tasam_network_campaign.py" \
  --campaign-root "$OUTPUT_ROOT" \
  --output "$OUTPUT_ROOT/tuned_borderline_report.json"
