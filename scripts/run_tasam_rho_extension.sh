#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TRACE="$ROOT/runs/tasam_greenran_real_article_faithful/tasam_greenran_real_trace_article_faithful.jsonl"
OUTPUT_ROOT="$ROOT/runs/tasam_greenran_real_article_rho"
FIGURE_OUT="$ROOT/runs/tasam_greenran_comparison/tasam_rho_bar_figure.png"
STAMP="$(date +%Y%m%d_%H%M%S)"

archive_if_needed() {
  local target="$1"
  if [[ ! -d "$target" ]]; then
    return 0
  fi
  if [[ -f "$target/both_sam/tasam_marl_summary.json" && -f "$target/actor_sam/tasam_marl_summary.json" && -f "$target/critic_sam/tasam_marl_summary.json" ]]; then
    return 0
  fi
  mv "$target" "${target}_interrupted_${STAMP}"
}

run_suite() {
  local rho="$1"
  local suffix="${rho/./_}"
  local output_dir="$OUTPUT_ROOT/rho_${suffix}"

  archive_if_needed "$output_dir"

  python3 "$ROOT/scripts/run_tasam_article_suite.py" \
    --skip-export \
    --trace-jsonl "$TRACE" \
    --output-root "$output_dir" \
    --modes both_sam,actor_sam,critic_sam \
    --epochs 25 \
    --trainer-backend article_sac \
    --article-hidden \
    --activation tanh \
    --warmup-epochs 2 \
    --seed 42 \
    --train-python python3 \
    --sam-rho 0.5 \
    --sam-rho-final "$rho" \
    --actor-sam-rho "$rho" \
    --actor-sam-rho-final "$rho" \
    --critic-sam-rho "$rho" \
    --critic-sam-rho-final "$rho"
}

run_suite 0.03 >"$OUTPUT_ROOT/rho_0_03_run.log" 2>&1 &
pid_03=$!
run_suite 0.04 >"$OUTPUT_ROOT/rho_0_04_run.log" 2>&1 &
pid_04=$!

wait "$pid_03"
wait "$pid_04"

python3 "$ROOT/scripts/generate_tasam_rho_bar_figure.py" --paper-caption-mode --output "$FIGURE_OUT"
