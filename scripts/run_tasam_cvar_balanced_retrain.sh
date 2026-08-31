#!/usr/bin/env bash
set -euo pipefail

# Reweight the existing real-only trace with the calibrated tail-risk reward
# and train an isolated TA-SAM checkpoint.  No active runtime checkpoint is
# changed by this script.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TRACE="${GREENRAN_CVAR_SOURCE_TRACE:-$ROOT/runs/greenran_tasam_3du_collection/tasam_article_export/rapp_online_trainable_trace.jsonl}"
RUN_ROOT="${GREENRAN_CVAR_RETRAIN_ROOT:-$ROOT/runs/tasam_cvar_balanced_retrain_20260822_v2}"
REWEIGHTED_TRACE="$RUN_ROOT/tasam_cvar_balanced_trace.jsonl"
REWEIGHT_SUMMARY="$RUN_ROOT/tasam_cvar_balanced_reward_summary.json"
TRAIN_ROOT="$RUN_ROOT/seed_0046/tasam_selective"
EPOCHS="${GREENRAN_CVAR_RETRAIN_EPOCHS:-150}"
SEED="${GREENRAN_CVAR_RETRAIN_SEED:-46}"
RESUME_ARGS=()

mkdir -p "$RUN_ROOT"
exec 9>"$RUN_ROOT/.train.lock"
if ! flock -n 9; then
  echo "Já existe outro retreino usando este diretório: $RUN_ROOT" >&2
  exit 3
fi
if [[ ! -s "$TRACE" ]]; then
  echo "Trace real-only não encontrado: $TRACE" >&2
  exit 2
fi

if [[ -f "$TRAIN_ROOT/resume_checkpoint.pt" ]]; then
  RESUME_ARGS+=(--resume)
fi

/usr/bin/python3 "$ROOT/scripts/reweight_tasam_trace.py" \
  --input-jsonl "$TRACE" \
  --output-jsonl "$REWEIGHTED_TRACE" \
  --summary-json "$REWEIGHT_SUMMARY"

exec /usr/bin/python3 "$ROOT/drlexp/training/train_tasam_marl.py" \
  --trace-jsonl "$REWEIGHTED_TRACE" \
  --output-dir "$TRAIN_ROOT" \
  --epochs "$EPOCHS" \
  --max-epochs "$EPOCHS" \
  --trainer-backend article_sac \
  --lr 0.0001 \
  --alpha-lr 0.0001 \
  --sam-mode tasam_selective \
  --actor-sam-rho 0.01 \
  --actor-sam-rho-final 0.001 \
  --critic-sam-rho 0.01 \
  --critic-sam-rho-final 0.001 \
  --td-var-threshold 0.01 \
  --min-selected-fraction 0.20 \
  --warmup-epochs 10 \
  --bc-weight 0 \
  --value-weight 0 \
  --gamma 0.99 \
  --tau 0.01 \
  --alpha-init 0.03 \
  --target-entropy-scale 1.0 \
  --batch-size 128 \
  --checkpoint-every 1 \
  --milestone-epochs 50,100,150 \
  --seed "$SEED" \
  --actor-hidden-dims 300,400,400 \
  --critic-hidden-dims 300,400,400 \
  --activation tanh \
  "${RESUME_ARGS[@]}"
