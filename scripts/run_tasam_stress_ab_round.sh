#!/usr/bin/env bash
set -euo pipefail

# Paired real-PDCP stress validation. Both modes use the same seed, topology,
# event profile and configurable decision stopping rule; only the assistants differ.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

MODE="${1:-}"
MODEL_SEED="${2:-46}"
RUN_DIR="${3:-}"
CHECKPOINT_DIR="${4:-}"

if [[ "$MODE" != "baseline" && "$MODE" != "joint" ]]; then
  echo "uso: $0 baseline|joint <model_seed> <run_dir> [checkpoint_dir]" >&2
  exit 2
fi
if [[ -z "$RUN_DIR" ]]; then
  echo "run_dir e obrigatorio" >&2
  exit 2
fi
if [[ "$MODE" == "joint" && -z "$CHECKPOINT_DIR" ]]; then
  echo "checkpoint_dir e obrigatorio no modo joint" >&2
  exit 2
fi

RUN_DIR="$(realpath -m "$RUN_DIR")"
mkdir -p "$RUN_DIR"
for pid_file in "$RUN_DIR/rapp.pid" "$RUN_DIR/csv_metrics.pid" "$RUN_DIR/ns3.pid" "$RUN_DIR/ns3_supervisor.pid" "$RUN_DIR/wall_clock_supervisor.pid" "$RUN_DIR/decision_target_supervisor.pid"; do
  if [[ -f "$pid_file" ]]; then
    pid="$(cat "$pid_file" 2>/dev/null || true)"
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      echo "run_dir parece estar em uso: $RUN_DIR ($pid_file=$pid)" >&2
      exit 3
    fi
    rm -f "$pid_file"
  fi
done

export GREENRAN_STATE_DIR="$RUN_DIR"
export GREENRAN_FIXED_SCENARIO_CONFIG="$PROJECT_ROOT/config/greenran_fixed_scenario.json"
export GREENRAN_SIM_TIME="100000"
export GREENRAN_WALL_TIME_LIMIT_SECONDS="${GREENRAN_WALL_TIME_LIMIT_SECONDS:-600}"
export GREENRAN_WALL_KEEP_RIC="0"
export GREENRAN_CLEAN_SCOPE="instance"
export GREENRAN_COLLECTION_EVENT_PROFILE="${GREENRAN_COLLECTION_EVENT_PROFILE:-drl_article_conflict_forced_fast_v1}"
# Keep the ten-minute wall-clock budget, but drive scenario stages from ns-3
# simulation time so baseline and assistant runs observe the same event order.
export GREENRAN_COLLECTION_EVENT_TIME_SOURCE="${GREENRAN_COLLECTION_EVENT_TIME_SOURCE:-sim}"
export GREENRAN_COLLECTION_EVENT_TICK_S="${GREENRAN_COLLECTION_EVENT_TICK_S:-0.25}"
export GREENRAN_COLLECTION_EVENT_CYCLES="${GREENRAN_COLLECTION_EVENT_CYCLES:-0}"
export GREENRAN_COLLECTION_DISABLE_APP_OVERRIDES="${GREENRAN_COLLECTION_DISABLE_APP_OVERRIDES:-0}"
export GREENRAN_REAL_ONLY="1"
export GREENRAN_REQUIRE_REAL_PDCP="1"
export GREENRAN_WAIT_FOR_REAL_PDCP_BEFORE_RAPP="1"
export GREENRAN_WAIT_FOR_REAL_PDCP_TIMEOUT_SECONDS="${GREENRAN_WAIT_FOR_REAL_PDCP_TIMEOUT_SECONDS:-180}"
export GREENRAN_DECISION_TARGET="${GREENRAN_DECISION_TARGET:-65}"
export GREENRAN_METRICS_TARGET="${GREENRAN_METRICS_TARGET:-$GREENRAN_DECISION_TARGET}"
metric_gap=$((GREENRAN_DECISION_TARGET / 20))
if (( metric_gap < 3 )); then metric_gap=3; fi
export GREENRAN_METRICS_MIN_TARGET="${GREENRAN_METRICS_MIN_TARGET:-$((GREENRAN_DECISION_TARGET - metric_gap))}"
export GREENRAN_METRICS_TIMEOUT_SECONDS="${GREENRAN_METRICS_TIMEOUT_SECONDS:-60}"
export GREENRAN_START_RIC="${GREENRAN_START_RIC:-1}"
export GREENRAN_RIC_REUSE_IF_ACTIVE="${GREENRAN_RIC_REUSE_IF_ACTIVE:-1}"
export GREENRAN_E2TERM_IP="${GREENRAN_E2TERM_IP:-127.0.0.1}"
# The PDCP collector publishes one real snapshot per trace refresh, not one
# per rApp call. A slower, fixed decision cadence prevents the decision stream
# from outrunning the real-metric stream and makes the paired window exact.
export GREENRAN_ORCHESTRATOR_INTERVAL="${GREENRAN_ORCHESTRATOR_INTERVAL:-4}"
export GREENRAN_TASAM_EXPORT_ALLOW_PROXY="0"
export GREENRAN_TASAM_TRUE_ONLINE_ENABLED="0"
export GREENRAN_ML_RETRAIN_ENABLED="false"
export GREENRAN_ARMD_SUMMARY="$PROJECT_ROOT/runs/graphsage_article00_hybrid_final/hybrid_final_summary.json"
export GREENRAN_CONTROL_TRIAL_STATE="$RUN_DIR/control_trial_state.json"
export GREENRAN_CONTROL_TRIAL_TARGET_DECISIONS="$GREENRAN_DECISION_TARGET"
export GREENRAN_CONTROL_TRIAL_FRACTION="1.0"
export GREENRAN_CONTROL_TRIAL_ROLLING_WINDOW="30"
export GREENRAN_CONTROL_TRIAL_CRITICAL_STREAK="3"
export GREENRAN_CONTROL_TRIAL_MIN_CONFIDENCE="0.60"
export GREENRAN_CONTROL_TRIAL_MIN_RAN_DELTA="-0.01"
export GREENRAN_CONTROL_TRIAL_MIN_AI_DELTA="-0.01"
export GREENRAN_STOP_ON_DECISION_TARGET="1"
export GREENRAN_AB_MODE="$MODE"
export GREENRAN_AB_MODEL_SEED="$MODEL_SEED"
export NS_GLOBAL_VALUE="RngRun=$MODEL_SEED"

is_true() {
  case "${1,,}" in
    1|true|yes|on) return 0 ;;
    *) return 1 ;;
  esac
}

# Real-PDCP A/B is not meaningful without the ns-3 E2 producers.  Fail before
# starting a run instead of leaving a database with decisions but no metrics.
if ! is_true "${GREENRAN_NS3_E2NR_ENABLED:-true}" || ! is_true "${GREENRAN_NS3_E2DU_ENABLED:-true}"; then
  if ! is_true "${GREENRAN_ALLOW_PDCP_WITHOUT_E2:-0}"; then
    echo "A/B exige GREENRAN_NS3_E2NR_ENABLED=true e GREENRAN_NS3_E2DU_ENABLED=true para o modo E2." >&2
    echo "Para a rota de traces PDCP locais, use GREENRAN_ALLOW_PDCP_WITHOUT_E2=1 explicitamente." >&2
    exit 5
  fi
  # The local ns-3 bearer calculators still produce real PDCP/RLC rows without
  # the legacy E2 transport.  This project build currently crashes in the
  # E2Termination thread during startup, so make the no-E2 route explicit and
  # never let it be mistaken for a proxy/synthetic run.
  echo "A/B: usando PDCP/RLC real local com E2/SCTP desabilitado; RIC não será iniciado." >&2
  export GREENRAN_START_RIC="0"
  export GREENRAN_NS3_E2NR_ENABLED="false"
  export GREENRAN_NS3_E2DU_ENABLED="false"
  # Preserve an explicit target requested by a paired test.  The fallback of
  # one row is only for callers that did not request a metric window.
  export GREENRAN_METRICS_TARGET="${GREENRAN_METRICS_TARGET:-1}"
  export GREENRAN_METRICS_MIN_TARGET="${GREENRAN_METRICS_MIN_TARGET:-1}"
fi

if [[ "$GREENRAN_START_RIC" != "1" && "$GREENRAN_E2TERM_IP" == "127.0.0.1" ]] \
   && (is_true "${GREENRAN_NS3_E2NR_ENABLED:-true}" || is_true "${GREENRAN_NS3_E2DU_ENABLED:-true}"); then
  if ! command -v ss >/dev/null 2>&1 || ! ss -H -n -lA sctp 2>/dev/null | grep -Eq ':36421([[:space:]]|$)'; then
    echo "nearRT-RIC/SCTP não está disponível na porta 36421; a rodada foi bloqueada antes de contaminar os dados." >&2
    echo "Inicie o RIC ou use GREENRAN_START_RIC=1 em um host com SCTP habilitado." >&2
    exit 6
  fi
fi

if [[ "$MODE" == "baseline" ]]; then
  export GREENRAN_ARMD_MODE="off"
  unset GREENRAN_ASSISTANT_DECISION_MODE
  export GREENRAN_TASAM_ADVISOR_ENABLED="0"
  export GREENRAN_TASAM_ADVISOR_MODE="shadow"
  export GREENRAN_CONTROL_TRIAL_ENABLED="0"
  export GREENRAN_TASAM_EVAL_MANIFEST="$RUN_DIR/disabled_tasam_manifest.json"
  python3 "$SCRIPT_DIR/prepare_tasam_ab_manifest.py" \
    --disabled \
    --output "$GREENRAN_TASAM_EVAL_MANIFEST" \
    --label "stress_baseline_${MODEL_SEED}"
else
  export GREENRAN_ARMD_MODE="assist"
  # Cooperative operational mode: ARMD constrains safety/floor and TA-SAM
  # optimizes the resource vector within that envelope.
  export GREENRAN_ASSISTANT_DECISION_MODE="${GREENRAN_ASSISTANT_DECISION_MODE:-cooperative_hierarchy}"
  export GREENRAN_TASAM_ADVISOR_ENABLED="1"
  # ARMD defines the safety envelope and TA-SAM owns the resource vector.
  # The rApp arbitrates their outputs; no live allocator/heuristic fallback is
  # allowed in this evaluation mode.
  export GREENRAN_TASAM_ADVISOR_MODE="assistant_only_control"
  export GREENRAN_CONTROL_TRIAL_ENABLED="1"
  export GREENRAN_TASAM_EVAL_MANIFEST="$RUN_DIR/tasam_stress_manifest.json"
  export GREENRAN_MARL_CONTROL_GATE_MANIFEST="$RUN_DIR/marl_control_gate.json"
  python3 "$SCRIPT_DIR/prepare_tasam_ab_manifest.py" \
    --checkpoint-dir "$CHECKPOINT_DIR" \
    --output "$GREENRAN_TASAM_EVAL_MANIFEST" \
    --label "stress_seed_${MODEL_SEED}"
  python3 "$SCRIPT_DIR/prepare_tasam_evaluation_gate.py" \
    --checkpoint-dir "$CHECKPOINT_DIR" \
    --output "$GREENRAN_MARL_CONTROL_GATE_MANIFEST" \
    --target-decisions "$GREENRAN_DECISION_TARGET"
fi

# Start the wall-clock collection first. The decision watcher must see the
# current instance PIDs (especially wall_clock_supervisor.pid); starting it
# before the collector created a race with stale PID files and allowed runs to
# continue past the target.
export GREENRAN_WAIT_FOR_DECISION_TARGET="0"
echo "A/B: janela=${GREENRAN_DECISION_TARGET} decisoes + ${GREENRAN_METRICS_TARGET} metricas reais; intervalo_rApp=${GREENRAN_ORCHESTRATOR_INTERVAL}s" >&2
launcher_log="$RUN_DIR/wall10m_launcher.log"
bash "$SCRIPT_DIR/run_greenran_tasam_3du_wall10m.sh" > "$launcher_log" 2>&1 &
launcher_pid="$!"
echo "$launcher_pid" > "$RUN_DIR/collection_wrapper.pid"

startup_deadline=$((SECONDS + 120))
while (( SECONDS < startup_deadline )); do
  if [[ -f "$RUN_DIR/rapp.pid" && -f "$RUN_DIR/csv_metrics.pid" \
        && -f "$RUN_DIR/ns3_supervisor.pid" && -f "$RUN_DIR/wall_clock_supervisor.pid" ]]; then
    break
  fi
  if ! kill -0 "$launcher_pid" 2>/dev/null; then
    echo "coleta encerrou antes de publicar os PIDs atuais; veja $launcher_log" >&2
    cat "$launcher_log" >&2 || true
    exit 4
  fi
  sleep 1
done
if [[ ! -f "$RUN_DIR/rapp.pid" || ! -f "$RUN_DIR/csv_metrics.pid" \
      || ! -f "$RUN_DIR/ns3_supervisor.pid" || ! -f "$RUN_DIR/wall_clock_supervisor.pid" ]]; then
  echo "timeout aguardando os PIDs atuais da coleta; veja $launcher_log" >&2
  exit 4
fi

# Now both baseline and assistant stop at the same decision/real-PDCP window.
watcher_pid_file="$RUN_DIR/decision_target_supervisor.pid"
setsid python3 "$SCRIPT_DIR/stop_on_decision_target.py" \
  --state-dir "$RUN_DIR" \
  --db "$RUN_DIR/rapp_data_lake.db" \
  --target "$GREENRAN_DECISION_TARGET" \
  --metrics-target "$GREENRAN_METRICS_TARGET" \
  --metrics-min-target "$GREENRAN_METRICS_MIN_TARGET" \
  --metrics-timeout-seconds "$GREENRAN_METRICS_TIMEOUT_SECONDS" \
  --finalize-real-metrics \
  > "$RUN_DIR/decision_target.log" 2>&1 &
watcher_pid="$!"
echo "$watcher_pid" > "$watcher_pid_file"

watcher_status=0
wait "$watcher_pid" || watcher_status=$?
if [[ "$watcher_status" -ne 0 ]]; then
  echo "supervisor de alvo falhou (code=$watcher_status); veja $RUN_DIR/decision_target.log" >&2
fi

# The watcher terminates the state-scoped wall supervisor after finalization;
# reap the wrapper so no launcher remains attached to this round.
launcher_status=0
wait "$launcher_pid" || launcher_status=$?
if [[ "$watcher_status" -ne 0 ]]; then
  exit "$watcher_status"
fi
if [[ "$launcher_status" -ne 0 ]]; then
  # The target watcher intentionally terminates the wall supervisor; the
  # wrapper may therefore return SIGTERM even though the paired run is valid.
  # Only fail when the watcher log does not confirm the requested decision
  # target.
  if ! grep -q "'decisions': $GREENRAN_DECISION_TARGET" "$RUN_DIR/decision_target.log" 2>/dev/null; then
    exit "$launcher_status"
  fi
  echo "[A/B] wrapper encerrado pelo watcher após o alvo; rodada preservada" >&2
fi

if [[ ! -f "$watcher_pid_file" ]]; then
  echo "supervisor de alvo de decisoes nao foi iniciado: $watcher_pid_file" >&2
  exit 4
fi
watcher_pid="$(cat "$watcher_pid_file" 2>/dev/null || true)"
while [[ -n "$watcher_pid" ]] && kill -0 "$watcher_pid" 2>/dev/null; do
  sleep 2
done
python3 "$SCRIPT_DIR/validate_tasam_stress_run.py" \
  --state-dir "$RUN_DIR" \
  --mode "$MODE" \
  --target "$GREENRAN_DECISION_TARGET" \
  --min-real-pdcp-rows "$GREENRAN_METRICS_MIN_TARGET" \
  --output "$RUN_DIR/validation_readiness.json"
echo "[$MODE] alvo de ${GREENRAN_DECISION_TARGET} decisoes finalizado: $(date --iso-8601=seconds)"
