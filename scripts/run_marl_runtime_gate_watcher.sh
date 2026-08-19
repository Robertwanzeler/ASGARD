#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_FILE="${MARL_GATE_WATCH_LOG:-/tmp/marl_runtime_gate_watch.log}"
REFRESH_SECONDS="${MARL_GATE_WATCH_REFRESH:-15}"
WINDOW_SIZE="${MARL_GATE_WATCH_WINDOW:-300}"
MIN_SAMPLES="${MARL_GATE_WATCH_MIN_SAMPLES:-120}"
MIN_CHECKPOINT_COVERAGE="${MARL_GATE_WATCH_MIN_CHECKPOINT_COVERAGE:-0.90}"
DB_PATH="${MARL_GATE_WATCH_DB:-${GREENRAN_STATE_DIR:-/tmp}/rapp_data_lake.db}"

cd "$ROOT_DIR"

echo "[MARL_GATE] starting watcher" >&2
echo "[MARL_GATE] db=$DB_PATH refresh=${REFRESH_SECONDS}s window=$WINDOW_SIZE log=$LOG_FILE" >&2

setsid python3 scripts/watch_marl_runtime_gate.py \
  --db "$DB_PATH" \
  --window "$WINDOW_SIZE" \
  --min-samples "$MIN_SAMPLES" \
  --min-checkpoint-coverage "$MIN_CHECKPOINT_COVERAGE" \
  --refresh "$REFRESH_SECONDS" \
  > "$LOG_FILE" 2>&1 &

echo $!
