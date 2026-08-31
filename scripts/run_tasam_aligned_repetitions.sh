#!/usr/bin/env bash
set -euo pipefail

# Extend the already validated aligned campaign to repetitions 2 and 3.
# Repetition 1 remains in the flat seed_<N>/network_pair.json layout; the
# evaluator accepts both layouts and aggregates all nine pairs.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CAMPAIGN_ROOT="${GREENRAN_ALIGNED_CAMPAIGN_ROOT:-$ROOT/runs/tasam_ab_aligned_20260817}"
CHECKPOINT_ROOT="${GREENRAN_TASAM_CHECKPOINT_ROOT:-$ROOT/runs/tasam_greenran_train_20260808/tasam_selective}"

for seed in 44 45 46; do
  checkpoint="$CHECKPOINT_ROOT/seed_00${seed}/tasam_selective"
  if [[ ! -f "$checkpoint/tasam_marl_actors.pt" ]]; then
    echo "checkpoint ausente para seed $seed: $checkpoint" >&2
    exit 2
  fi
done

for seed in 44 45 46; do
  checkpoint="$CHECKPOINT_ROOT/seed_00${seed}/tasam_selective"
  for rep in 2 3; do
    pair_root="$CAMPAIGN_ROOT/seed_${seed}/rep_${rep}"
    baseline="$pair_root/baseline"
    assistant="$pair_root/assistant"
    pair_json="$pair_root/network_pair.json"
    mkdir -p "$pair_root"
    if [[ -f "$pair_json" ]] && python3 - "$pair_json" <<'PY'
import json
import sys
try:
    payload = json.load(open(sys.argv[1], encoding="utf-8"))
    raise SystemExit(0 if payload.get("pairs", [{}])[0].get("valid") else 1)
except (OSError, ValueError, IndexError, KeyError, TypeError):
    raise SystemExit(1)
PY
    then
      echo "[aligned repetitions] seed=$seed rep=$rep já concluído; reutilizando"
      continue
    fi
    echo "[aligned repetitions] seed=$seed rep=$rep baseline $(date --iso-8601=seconds)"
    bash "$ROOT/scripts/run_tasam_stress_ab_round.sh" baseline "$seed" "$baseline"
    echo "[aligned repetitions] seed=$seed rep=$rep assistant $(date --iso-8601=seconds)"
    bash "$ROOT/scripts/run_tasam_stress_ab_round.sh" joint "$seed" "$assistant" "$checkpoint"
    python3 "$ROOT/scripts/evaluate_tasam_network_campaign.py" \
      --pair "$baseline" "$assistant" "$seed" "$rep" \
      --output "$pair_json"
  done
done

python3 "$ROOT/scripts/evaluate_tasam_network_campaign.py" \
  --campaign-root "$CAMPAIGN_ROOT" \
  --output "$CAMPAIGN_ROOT/campaign_9_pairs_report.json"
