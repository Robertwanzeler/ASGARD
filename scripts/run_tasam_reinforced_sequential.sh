#!/usr/bin/env bash
set -euo pipefail

ROOT="/home/robert/orange_nuclear"
TRACE="$ROOT/runs/tasam_training_weak_reinforced_20260812/trainable_trace.jsonl"
EVAL_TRACE="$ROOT/runs/tasam_training_weak_reinforced_20260812/eval_reinforcement_900.jsonl"
OUT_ROOT="$ROOT/runs/tasam_training_weak_reinforced_20260812/training_final"

# Impede duas filas concorrentes gravando checkpoints do mesmo experimento.
mkdir -p "$OUT_ROOT"
exec 9>"$OUT_ROOT/.queue.lock"
if ! flock -n 9; then
  echo "[TA-SAM] outra fila já está em execução; encerrando esta cópia." >&2
  exit 2
fi

# Reduz a multiplicação de threads e a pressão de memória no host.
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-1}"
export MKL_NUM_THREADS="${MKL_NUM_THREADS:-1}"
export OPENBLAS_NUM_THREADS="${OPENBLAS_NUM_THREADS:-1}"

run_seed() {
  local seed="$1"
  local output="$OUT_ROOT/seed_00${seed}/tasam_selective"
  local resume_args=()
  if [[ -f "$output/resume_checkpoint.pt" ]]; then
    resume_args+=(--resume)
  fi
  /usr/bin/python3 "$ROOT/drlexp/training/train_tasam_marl.py" \
    --trace-jsonl "$TRACE" \
    --eval-trace-jsonl "$EVAL_TRACE" \
    --output-dir "$output" \
    --epochs 200 \
    --trainer-backend article_sac \
    --lr 0.0001 \
    --alpha-lr 0.0001 \
    --sam-mode tasam_selective \
    --actor-sam-rho 0.5 \
    --actor-sam-rho-final 0.01 \
    --critic-sam-rho 0.5 \
    --critic-sam-rho-final 0.01 \
    --td-var-threshold 0.01 \
    --min-selected-fraction 0.1 \
    --warmup-epochs 2 \
    --bc-weight 0.0 \
    --value-weight 0.0 \
    --gamma 0.99 \
    --tau 0.01 \
    --alpha-init 0.03 \
    --target-entropy-scale 1.0 \
    --batch-size 32 \
    --updates-per-epoch 1 \
    --checkpoint-every 25 \
    --seed "$seed" \
    --article-hidden \
    --activation tanh \
    "${resume_args[@]}"
}

for seed in 44 45 46; do
  echo "[TA-SAM] iniciando seed=$seed em $(date --iso-8601=seconds)"
  run_seed "$seed"
  echo "[TA-SAM] concluído seed=$seed em $(date --iso-8601=seconds)"
done
