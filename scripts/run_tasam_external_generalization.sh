#!/usr/bin/env bash
set -euo pipefail

# External generalization validation.  Model seeds and scenario seeds are
# intentionally separated: checkpoints come from the completed training, but
# ns-3 receives unseen RNG seeds and a different event profile.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT_ROOT="${GREENRAN_EXTERNAL_ROOT:-$ROOT/runs/tasam_ab_external_generalization_20260819}"
PROFILE="${GREENRAN_EXTERNAL_PROFILE:-tasam_training_balanced_v2}"
CHECKPOINT_ROOT="${GREENRAN_EXTERNAL_CHECKPOINT_ROOT:-$ROOT/runs/tasam_greenran_train_efficiency_20260818}"
REPETITIONS="${GREENRAN_EXTERNAL_REPETITIONS:-5}"
PAIR_MAP="${GREENRAN_EXTERNAL_PAIR_MAP:-44:47,45:48,46:49}"

if ! [[ "$REPETITIONS" =~ ^[1-9][0-9]*$ ]]; then
  echo "REPETITIONS deve ser um inteiro positivo: $REPETITIONS" >&2
  exit 2
fi

IFS=',' read -r -a PAIRS <<< "$PAIR_MAP"
if [[ "${#PAIRS[@]}" -eq 0 ]]; then
  echo "PAIR_MAP vazio" >&2
  exit 2
fi

for pair in "${PAIRS[@]}"; do
  if [[ ! "$pair" =~ ^([0-9]+):([0-9]+)$ ]]; then
    echo "par inválido em PAIR_MAP: $pair (esperado modelo:cenário)" >&2
    exit 2
  fi
  model_seed="${BASH_REMATCH[1]}"
  checkpoint="$CHECKPOINT_ROOT/seed_00${model_seed}/tasam_selective"
  if [[ ! -f "$checkpoint/tasam_marl_actors.pt" ]]; then
    echo "checkpoint ausente para seed do modelo $model_seed: $checkpoint" >&2
    exit 2
  fi
done

for repetition in $(seq 1 "$REPETITIONS"); do
  for pair in "${PAIRS[@]}"; do
    model_seed="${pair%%:*}"
    scenario_seed="${pair##*:}"
    pair_root="$OUTPUT_ROOT/profiles/$PROFILE/model_${model_seed}/scenario_${scenario_seed}/rep_${repetition}"
    baseline="$pair_root/baseline"
    assistant="$pair_root/assistant"
    pair_json="$pair_root/network_pair.json"
    checkpoint="$CHECKPOINT_ROOT/seed_00${model_seed}/tasam_selective"
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
      echo "[external] model=$model_seed scenario=$scenario_seed repetition=$repetition já concluído"
      continue
    fi

    echo "[external] model=$model_seed scenario=$scenario_seed repetition=$repetition baseline $(date --iso-8601=seconds)"
    GREENRAN_COLLECTION_EVENT_PROFILE="$PROFILE" \
      bash "$ROOT/scripts/run_tasam_stress_ab_round.sh" baseline "$scenario_seed" "$baseline"

    echo "[external] model=$model_seed scenario=$scenario_seed repetition=$repetition assistant $(date --iso-8601=seconds)"
    GREENRAN_COLLECTION_EVENT_PROFILE="$PROFILE" \
      bash "$ROOT/scripts/run_tasam_stress_ab_round.sh" joint "$scenario_seed" "$assistant" "$checkpoint"

    python3 "$ROOT/scripts/evaluate_tasam_network_campaign.py" \
      --pair "$baseline" "$assistant" "$scenario_seed" "$repetition" \
      --profile "$PROFILE" \
      --allow-metric-gap \
      --output "$pair_json"
  done
done

python3 "$ROOT/scripts/evaluate_tasam_network_campaign.py" \
  --campaign-root "$OUTPUT_ROOT/profiles/$PROFILE" \
  --output "$OUTPUT_ROOT/profiles/$PROFILE/profile_report.json"
python3 "$ROOT/scripts/evaluate_tasam_network_campaign.py" \
  --campaign-root "$OUTPUT_ROOT" \
  --output "$OUTPUT_ROOT/external_generalization_report.json"
