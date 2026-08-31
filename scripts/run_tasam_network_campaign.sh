#!/usr/bin/env bash
set -euo pipefail

# Four seeds x three repetitions, sequentially because the RIC/ns-3 runtime
# uses fixed ports. Every pair is isolated and stops at 65 decisions.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CAMPAIGN_ROOT="${GREENRAN_NETWORK_CAMPAIGN_ROOT:-$ROOT/runs/tasam_network_campaign_20260813}"
CHECKPOINT_ROOT="${GREENRAN_TASAM_CHECKPOINT_ROOT:-$ROOT/runs/tasam_greenran_train_20260808/tasam_selective}"
mkdir -p "$CAMPAIGN_ROOT"

for seed in 43 44 45 46; do
  checkpoint="$CHECKPOINT_ROOT/seed_00${seed}/tasam_selective"
  if [[ ! -f "$checkpoint/tasam_marl_actors.pt" ]]; then
    echo "checkpoint ausente para seed $seed: $checkpoint" >&2
    exit 2
  fi
done

for seed in 43 44 45 46; do
  checkpoint="$CHECKPOINT_ROOT/seed_00${seed}/tasam_selective"
  for rep in 1 2 3; do
    pair_root="$CAMPAIGN_ROOT/seed_${seed}/rep_${rep}"
    baseline="$pair_root/baseline"
    assistant="$pair_root/assistant"
    pair_json="$pair_root/network_pair.json"
    mkdir -p "$pair_root"
    if [[ ! -f "$pair_json" ]]; then
      echo "[network campaign] seed=$seed rep=$rep baseline $(date --iso-8601=seconds)"
      bash "$ROOT/scripts/run_tasam_stress_ab_round.sh" baseline "$seed" "$baseline"
      echo "[network campaign] seed=$seed rep=$rep assistant $(date --iso-8601=seconds)"
      bash "$ROOT/scripts/run_tasam_stress_ab_round.sh" joint "$seed" "$assistant" "$checkpoint"
      python3 "$ROOT/scripts/evaluate_tasam_network_campaign.py" \
        --pair "$baseline" "$assistant" "$seed" "$rep" \
        --output "$pair_json"
    else
      echo "[network campaign] seed=$seed rep=$rep já concluído; reutilizando"
    fi
    python3 "$ROOT/scripts/evaluate_tasam_network_campaign.py" \
      --campaign-root "$CAMPAIGN_ROOT" \
      --output "$CAMPAIGN_ROOT/network_campaign_live.json" >/dev/null
  done
done

python3 "$ROOT/scripts/evaluate_tasam_network_campaign.py" \
  --campaign-root "$CAMPAIGN_ROOT" \
  --output "$CAMPAIGN_ROOT/network_campaign_result.json"
