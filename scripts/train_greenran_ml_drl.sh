#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

ML_PYTHON="python3"
DRL_PYTHON="$BASE_DIR/drlexp/.venv/bin/python"
DB_PATH="/tmp/rapp_data_lake.db"
ML_HOURS="24"
SBILSTM_EPOCHS="600"
SBILSTM_PW="100"
A3C_EPISODES="10000"
A3C_WORKERS="8"
RUN_DIR=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --db)
      DB_PATH="$2"
      shift 2
      ;;
    --ml-hours)
      ML_HOURS="$2"
      shift 2
      ;;
    --sbilstm-epochs)
      SBILSTM_EPOCHS="$2"
      shift 2
      ;;
    --sbilstm-pw)
      SBILSTM_PW="$2"
      shift 2
      ;;
    --a3c-episodes)
      A3C_EPISODES="$2"
      shift 2
      ;;
    --a3c-workers)
      A3C_WORKERS="$2"
      shift 2
      ;;
    --run-dir)
      RUN_DIR="$2"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

if [[ -z "$RUN_DIR" ]]; then
  RUN_DIR="$BASE_DIR/runs/training/$(date +%Y%m%d_%H%M%S)_ml_drl"
fi

mkdir -p "$RUN_DIR"

ML_LOG="$RUN_DIR/ml_train.log"
SBILSTM_LOG="$RUN_DIR/sbilstm_train.log"
A3C_LOG="$RUN_DIR/a3c_train.log"
SUMMARY_FILE="$RUN_DIR/summary.txt"

{
  echo "GreenRAN ML + DRL Training"
  echo "Started: $(date --iso-8601=seconds)"
  echo "DB: $DB_PATH"
  echo "ML hours: $ML_HOURS"
  echo "SBiLSTM epochs: $SBILSTM_EPOCHS"
  echo "SBiLSTM prediction window: $SBILSTM_PW"
  echo "A3C episodes: $A3C_EPISODES"
  echo "A3C workers: $A3C_WORKERS"
  echo
} | tee "$SUMMARY_FILE"

cd "$BASE_DIR"

if [[ ! -x "$DRL_PYTHON" ]]; then
  echo "DRL Python not found: $DRL_PYTHON" | tee -a "$SUMMARY_FILE" >&2
  exit 1
fi

echo "[1/4] Training ML..." | tee -a "$SUMMARY_FILE"
"$ML_PYTHON" -u training/train_ml_model.py \
  --db "$DB_PATH" \
  --retrain \
  --hours "$ML_HOURS" \
  --output "$BASE_DIR/models" \
  > "$ML_LOG" 2>&1
echo "  ML done -> $ML_LOG" | tee -a "$SUMMARY_FILE"

echo "[2/4] Training SBiLSTM..." | tee -a "$SUMMARY_FILE"
"$DRL_PYTHON" -u drlexp/training/train_sbilstm.py \
  --mode train \
  --db "$DB_PATH" \
  --epochs "$SBILSTM_EPOCHS" \
  --pw "$SBILSTM_PW" \
  > "$SBILSTM_LOG" 2>&1
echo "  SBiLSTM done -> $SBILSTM_LOG" | tee -a "$SUMMARY_FILE"

echo "[3/4] Training A3C..." | tee -a "$SUMMARY_FILE"
"$DRL_PYTHON" -u drlexp/training/train_a3c.py \
  --mode train \
  --db "$DB_PATH" \
  --episodes "$A3C_EPISODES" \
  --workers "$A3C_WORKERS" \
  > "$A3C_LOG" 2>&1
echo "  A3C done -> $A3C_LOG" | tee -a "$SUMMARY_FILE"

echo "[4/4] Exporting A3C runtime weights..." | tee -a "$SUMMARY_FILE"
"$DRL_PYTHON" -u drlexp/training/export_a3c_runtime_weights.py \
  --checkpoint "$BASE_DIR/drlexp/models/a3c/checkpoint.pt" \
  --output-dir "$BASE_DIR/drlexp/models/a3c" \
  >> "$A3C_LOG" 2>&1
echo "  Runtime weights updated in drlexp/models/a3c" | tee -a "$SUMMARY_FILE"

{
  echo
  echo "Finished: $(date --iso-8601=seconds)"
  echo "ML models: $BASE_DIR/models"
  echo "DRL models: $BASE_DIR/drlexp/models"
} | tee -a "$SUMMARY_FILE"
