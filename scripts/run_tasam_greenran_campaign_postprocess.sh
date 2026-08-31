#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CAMPAIGN_ROOT="${GREENRAN_CAMPAIGN_ROOT:?GREENRAN_CAMPAIGN_ROOT obrigatório}"
CAMPAIGN_PID_FILE="$CAMPAIGN_ROOT/campaign.pid"
BASE_TRACE="${GREENRAN_BASE_TRACE:-$ROOT/runs/greenran_tasam_3du_article_adapted_20260730/tasam_article_export/tasam_article_trace.jsonl}"
MERGED_DIR="$CAMPAIGN_ROOT/consolidated"
TRAIN_ROOT="${GREENRAN_CAMPAIGN_TRAIN_ROOT:-$ROOT/runs/tasam_greenran_diversity_training_20260802}"
LOG="$CAMPAIGN_ROOT/postprocess.log"

echo "[POST] aguardando campanha: $CAMPAIGN_ROOT" | tee -a "$LOG"
while [[ -f "$CAMPAIGN_PID_FILE" ]] && kill -0 "$(cat "$CAMPAIGN_PID_FILE")" 2>/dev/null; do
  sleep 30
done

mkdir -p "$MERGED_DIR"
inputs=("$BASE_TRACE")
for id in reference_20ue camera_heavy_20ue vehicle_heavy_20ue mixed_stress_20ue; do
  trace="$CAMPAIGN_ROOT/$id/tasam_article_export/tasam_article_trace.jsonl"
  if [[ ! -s "$trace" ]]; then
    echo "[POST] trace ausente: $trace" | tee -a "$LOG"
    exit 1
  fi
  inputs+=("$trace")
done

echo "[POST] consolidando ${#inputs[@]} traces" | tee -a "$LOG"
/usr/bin/python3 "$ROOT/scripts/merge_tasam_article_traces.py" \
  --inputs "${inputs[@]}" \
  --output "$MERGED_DIR/tasam_article_trace_merged.jsonl" \
  --summary "$MERGED_DIR/tasam_article_trace_merged_summary.json" >> "$LOG" 2>&1

echo "[POST] iniciando treino multi-seed article-SAC" | tee -a "$LOG"
mkdir -p "$TRAIN_ROOT"
exec /usr/bin/python3 "$ROOT/scripts/run_tasam_greenran_multiseed.py" \
  --trace-jsonl "$MERGED_DIR/tasam_article_trace_merged.jsonl" \
  --output-root "$TRAIN_ROOT" \
  --seeds 42,43,45,47 \
  --modes no_sam,tasam_selective \
  --epochs 50 \
  --skip-quality-gate \
  --sequential \
  --train-python /usr/bin/python3 >> "$LOG" 2>&1
