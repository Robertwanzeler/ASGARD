#!/usr/bin/env bash
set -u

# Matched 20-minute operational validation for checkpoint seed 45.
# Baseline and assistant are intentionally sequential because the GreenRAN
# runtime uses shared ports and local RIC/ns-3 resources.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
RUN_ROOT="${1:-$PROJECT_ROOT/runs/tasam_operational_ab_20260821_seed45}"
CHECKPOINT_DIR="${2:-$PROJECT_ROOT/runs/tasam_greenran_train_balanced_vehicle_energy_20260819/seed_0045/tasam_selective}"
RUN_ROOT="$(realpath -m "$RUN_ROOT")"
BASELINE_DIR="$RUN_ROOT/baseline"
ASSISTANT_DIR="$RUN_ROOT/assistant"
mkdir -p "$RUN_ROOT"

# This ns-3 build has a known E2/SCTP crash in the operational scenario.
# Keep the real local PDCP/RLC path and disable only the crashing E2 hooks.
export GREENRAN_START_RIC="0"
export GREENRAN_NS3_E2NR_ENABLED="false"
export GREENRAN_NS3_E2DU_ENABLED="false"
export GREENRAN_NS3_E2CUUP_ENABLED="false"
export GREENRAN_NS3_E2CUCP_ENABLED="false"
export GREENRAN_PER_UE_POLICY_CONSUMER_ENABLED="1"
export GREENRAN_PER_UE_POLICY_CONSUMER_POLL_S="0.20"

if [[ ! -f "$CHECKPOINT_DIR/tasam_marl_actors.pt" ]]; then
  echo "checkpoint TA-SAM ausente: $CHECKPOINT_DIR/tasam_marl_actors.pt" >&2
  exit 2
fi

status=0
echo "[A/B] Início: $(date --iso-8601=seconds)"
echo "[A/B] Baseline: $BASELINE_DIR"
echo "[A/B] Assistente: $ASSISTANT_DIR"
echo "[A/B] Checkpoint: $CHECKPOINT_DIR"

if ! bash "$SCRIPT_DIR/run_tasam_operational_baseline_10m.sh" 45 "$BASELINE_DIR" \
    2>&1 | tee "$RUN_ROOT/baseline_runtime.log"; then
  status=1
  echo "[A/B] baseline terminou com falha; o relatório será verificado mesmo assim" >&2
fi

if ! bash "$SCRIPT_DIR/run_tasam_operational_assistant_only_10m.sh" 45 "$ASSISTANT_DIR" "$CHECKPOINT_DIR" \
    2>&1 | tee "$RUN_ROOT/assistant_runtime.log"; then
  status=1
  echo "[A/B] assistente terminou com falha; o relatório será verificado mesmo assim" >&2
fi

if [[ -f "$BASELINE_DIR/baseline_summary.json" && -f "$ASSISTANT_DIR/operational_summary.json" ]]; then
  python3 "$SCRIPT_DIR/compare_tasam_operational_runs.py" \
    --baseline "$BASELINE_DIR/baseline_summary.json" \
    --assistant "$ASSISTANT_DIR/operational_summary.json" \
    --output "$RUN_ROOT/operational_comparison.json" \
    2>&1 | tee "$RUN_ROOT/comparison.log" || status=1
else
  echo "[A/B] relatórios operacionais ausentes; comparação não executada" >&2
  status=1
fi

echo "[A/B] Fim: $(date --iso-8601=seconds) status=$status"
exit "$status"
