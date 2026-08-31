#!/usr/bin/env bash
set -euo pipefail

# Paired real-PDCP validation across the four GreenRAN scenario regimes.
# Each profile uses one pair for seeds 44, 45 and 46 (12 pairs total).
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MATRIX_ROOT="${GREENRAN_SCENARIO_MATRIX_ROOT:-$ROOT/runs/tasam_ab_scenario_matrix_20260817}"
CHECKPOINT_ROOT="${GREENRAN_TASAM_CHECKPOINT_ROOT:-$ROOT/runs/tasam_greenran_train_20260808/tasam_selective}"
PROFILES=(
  tasam_training_balanced_v2
  drl_allowed_only_v1
  drl_balanced_borderline_v1
  drl_balanced_blocked_v1
)

for seed in 44 45 46; do
  checkpoint="$CHECKPOINT_ROOT/seed_00${seed}/tasam_selective"
  if [[ ! -f "$checkpoint/tasam_marl_actors.pt" ]]; then
    echo "checkpoint ausente para seed $seed: $checkpoint" >&2
    exit 2
  fi
done

for profile in "${PROFILES[@]}"; do
  profile_root="$MATRIX_ROOT/profiles/$profile"
  for seed in 44 45 46; do
    checkpoint="$CHECKPOINT_ROOT/seed_00${seed}/tasam_selective"
    pair_root="$profile_root/seed_${seed}/rep_1"
    baseline="$pair_root/baseline"
    assistant="$pair_root/assistant"
    pair_json="$pair_root/network_pair.json"
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
      echo "[scenario matrix] profile=$profile seed=$seed já concluído; reutilizando"
      continue
    fi

    echo "[scenario matrix] profile=$profile seed=$seed baseline $(date --iso-8601=seconds)"
    GREENRAN_COLLECTION_EVENT_PROFILE="$profile" \
      bash "$ROOT/scripts/run_tasam_stress_ab_round.sh" baseline "$seed" "$baseline"
    echo "[scenario matrix] profile=$profile seed=$seed assistant $(date --iso-8601=seconds)"
    GREENRAN_COLLECTION_EVENT_PROFILE="$profile" \
      bash "$ROOT/scripts/run_tasam_stress_ab_round.sh" joint "$seed" "$assistant" "$checkpoint"
    python3 "$ROOT/scripts/evaluate_tasam_network_campaign.py" \
      --pair "$baseline" "$assistant" "$seed" 1 \
      --profile "$profile" \
      --output "$pair_json"
  done

  python3 "$ROOT/scripts/evaluate_tasam_network_campaign.py" \
    --campaign-root "$profile_root" \
    --output "$profile_root/profile_report.json"
done

python3 "$ROOT/scripts/evaluate_tasam_network_campaign.py" \
  --campaign-root "$MATRIX_ROOT" \
  --output "$MATRIX_ROOT/scenario_matrix_report.json"
