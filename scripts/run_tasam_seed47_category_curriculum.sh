#!/usr/bin/env bash
set -euo pipefail

# Build a separate category-pretrained checkpoint, then run the requested
# 600-second online-only combined validation. Existing rounds are never used
# as replay input and are never overwritten.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCAL_RUNS_ROOT="${ROOT}/runs"
SOURCE_CHECKPOINT="${GREENRAN_TASAM_SOURCE_CHECKPOINT:-${LOCAL_RUNS_ROOT}/tasam_local_checkpoint_seed47_20260906}"
CURRICULUM_CHECKPOINT="${GREENRAN_TASAM_CURRICULUM_CHECKPOINT:-${LOCAL_RUNS_ROOT}/tasam_local_category_curriculum_checkpoint_seed47_20260906}"
RUN_DIR="${GREENRAN_TASAM_CURRICULUM_RUN_DIR:-${LOCAL_RUNS_ROOT}/tasam_local_category_curriculum_run_seed47_20260906}"

for local_path in "$SOURCE_CHECKPOINT" "$CURRICULUM_CHECKPOINT" "$RUN_DIR"; do
  resolved_path="$(realpath -m "$local_path")"
  case "$resolved_path" in
    "$LOCAL_RUNS_ROOT"/*) ;;
    *)
      echo "ERRO: o currículo TA-SAM aceita somente caminhos em ${LOCAL_RUNS_ROOT}: ${resolved_path}" >&2
      exit 2
      ;;
  esac
done
export GREENRAN_LOCAL_ONLY=1

python3 "${ROOT}/scripts/pretrain_tasam_category_curriculum.py" \
  --input-checkpoint "${SOURCE_CHECKPOINT}" \
  --output-checkpoint "${CURRICULUM_CHECKPOINT}" \
  --samples-per-stage 64 \
  --epochs 200 \
  --batch-size 48 \
  --learning-rate 0.001 \
  --conditional-weight 1.5 \
  --seed 47

python3 "${ROOT}/scripts/run_tasam_online_arm.py" \
  --mode combined \
  --run-dir "${RUN_DIR}" \
  --seed 47 \
  --profile tasam_training_balanced_v3 \
  --wall-time 600 \
  --checkpoint "${CURRICULUM_CHECKPOINT}" \
  --min-free-gib 40 \
  --min-new-snapshots 60 \
  --min-trainable-transitions 180 \
  --replay-rows 600 \
  --epochs-per-update 3 \
  --prioritize-category-errors \
  --category-error-repeat 1 \
  --category-loss-weight 0.5 \
  --category-head-hidden-dim 64 \
  --controller-poll-seconds 10
