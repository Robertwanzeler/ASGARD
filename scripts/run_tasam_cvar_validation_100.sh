#!/usr/bin/env bash
set -euo pipefail

# Paired validation for the CVaR-adjusted TA-SAM checkpoint.
# Baseline: rApp-only. Assistant: rApp + ARMD + TA-SAM judged together.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODEL_SEED="${GREENRAN_CVAR_VALIDATION_SEED:-46}"
RUN_ROOT="${GREENRAN_CVAR_VALIDATION_ROOT:-$ROOT/runs/tasam_cvar_validation_100_seed46_20260823}"
CHECKPOINT="${GREENRAN_CVAR_CHECKPOINT:-$ROOT/runs/tasam_cvar_balanced_retrain_20260822_v5/seed_0046/tasam_selective}"
BASELINE="$RUN_ROOT/baseline"
ASSISTANT="$RUN_ROOT/assistant"
PAIR="$RUN_ROOT/network_pair.json"
RESULT="$RUN_ROOT/network_result.json"

mkdir -p "$RUN_ROOT"
exec 9>"$RUN_ROOT/.validation.lock"
if ! flock -n 9; then
  echo "Já existe outra validação usando este diretório: $RUN_ROOT" >&2
  exit 3
fi

if [[ ! -f "$CHECKPOINT/tasam_marl_actors.pt" ]]; then
  echo "Checkpoint TA-SAM ausente: $CHECKPOINT/tasam_marl_actors.pt" >&2
  exit 2
fi

# The local GreenRAN build currently needs the explicit no-E2 route for its
# real PDCP/RLC rows. This is real-only collection, never proxy/synthetic.
export GREENRAN_ALLOW_PDCP_WITHOUT_E2=1
export GREENRAN_NS3_E2NR_ENABLED=false
export GREENRAN_NS3_E2DU_ENABLED=false
export GREENRAN_NS3_E2CUUP_ENABLED=false
export GREENRAN_NS3_E2CUCP_ENABLED=false
export GREENRAN_START_RIC=0
export GREENRAN_DECISION_TARGET=100
export GREENRAN_METRICS_TARGET="${GREENRAN_METRICS_TARGET:-94}"
export GREENRAN_METRICS_MIN_TARGET="${GREENRAN_METRICS_MIN_TARGET:-$GREENRAN_METRICS_TARGET}"
export GREENRAN_METRICS_TIMEOUT_SECONDS=90
export GREENRAN_COLLECTION_EVENT_PROFILE="${GREENRAN_COLLECTION_EVENT_PROFILE:-drl_article_conflict_forced_fast_v1}"

if [[ ! -f "$BASELINE/validation_readiness.json" ]]; then
  echo "[validation] baseline: $(date --iso-8601=seconds)"
  bash "$ROOT/scripts/run_tasam_stress_ab_round.sh" baseline "$MODEL_SEED" "$BASELINE"
else
  echo "[validation] baseline já concluído; reutilizando"
fi

if [[ ! -f "$ASSISTANT/validation_readiness.json" ]]; then
  echo "[validation] assistant: $(date --iso-8601=seconds)"
  bash "$ROOT/scripts/run_tasam_stress_ab_round.sh" joint "$MODEL_SEED" "$ASSISTANT" "$CHECKPOINT"
else
  echo "[validation] assistant já concluído; reutilizando"
fi

python3 "$ROOT/scripts/evaluate_tasam_network_campaign.py" \
  --pair "$BASELINE" "$ASSISTANT" "$MODEL_SEED" 1 \
  --target-decisions 100 \
  --allow-metric-gap \
  --profile tasam_cvar_balanced_v3 \
  --output "$PAIR"

cp "$PAIR" "$RESULT"
echo "[validation] resultado: $RESULT"
