#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SEED45_PID="${GREENRAN_SEED45_PID:?GREENRAN_SEED45_PID é obrigatório}"
RUN47="${GREENRAN_SEED47_ROOT:-$ROOT/runs/tasam_cvar_validation_100_seed47_20260823}"
CHECKPOINT="$ROOT/runs/tasam_greenran_focused_tasam_20260805/seed_0047/tasam_selective"

while kill -0 "$SEED45_PID" 2>/dev/null; do
  sleep 30
done

echo "[seed47] seed45 encerrou; iniciando $(date --iso-8601=seconds)"
exec env \
  GREENRAN_CVAR_VALIDATION_ROOT="$RUN47" \
  GREENRAN_CVAR_VALIDATION_SEED=47 \
  GREENRAN_CVAR_CHECKPOINT="$CHECKPOINT" \
  GREENRAN_METRICS_TARGET=93 \
  GREENRAN_METRICS_MIN_TARGET=93 \
  bash "$ROOT/scripts/run_tasam_cvar_validation_100.sh"
