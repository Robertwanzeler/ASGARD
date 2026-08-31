#!/usr/bin/env bash
set -euo pipefail

# One isolated A/B round.  It never reuses the active collection directory.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

MODE="${1:-}"
MODEL_SEED="${2:-}"
RUN_DIR="${3:-}"
CHECKPOINT_DIR="${4:-}"

if [[ "$MODE" != "baseline" && "$MODE" != "tasam" ]]; then
  echo "uso: $0 baseline|tasam <model_seed> <run_dir> [checkpoint_dir]" >&2
  exit 2
fi
if [[ -z "$MODEL_SEED" || -z "$RUN_DIR" ]]; then
  echo "model_seed e run_dir sao obrigatorios" >&2
  exit 2
fi
if [[ "$MODE" == "tasam" && -z "$CHECKPOINT_DIR" ]]; then
  echo "checkpoint_dir e obrigatorio no modo tasam" >&2
  exit 2
fi

RUN_DIR="$(realpath -m "$RUN_DIR")"
mkdir -p "$RUN_DIR"
for pid_file in "$RUN_DIR/rapp.pid" "$RUN_DIR/ns3.pid" "$RUN_DIR/ns3_supervisor.pid" "$RUN_DIR/wall_clock_supervisor.pid"; do
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
export GREENRAN_SIM_TIME="${GREENRAN_SIM_TIME:-100000}"
export GREENRAN_WALL_TIME_LIMIT_SECONDS="${GREENRAN_WALL_TIME_LIMIT_SECONDS:-600}"
export GREENRAN_WALL_KEEP_RIC="0"
export GREENRAN_CLEAN_SCOPE="instance"
export GREENRAN_COLLECTION_EVENT_PROFILE="none"
export GREENRAN_COLLECTION_DISABLE_APP_OVERRIDES="1"
export GREENRAN_RAN_PRESSURE_PROFILE="greenran_autonomous_priority_wall10m_v1"
export GREENRAN_RAN_PRESSURE_TIME_SOURCE="wall"
export GREENRAN_REAL_ONLY="1"
export GREENRAN_REQUIRE_REAL_PDCP="1"
export GREENRAN_START_RIC="1"
export GREENRAN_ORCHESTRATOR_INTERVAL="2"
export GREENRAN_TASAM_EXPORT_ALLOW_PROXY="0"
export GREENRAN_TASAM_TRUE_ONLINE_ENABLED="0"
export GREENRAN_ML_RETRAIN_ENABLED="false"
export GREENRAN_ARMD_MODE="off"
export GREENRAN_CONTROL_TRIAL_STATE="$RUN_DIR/control_trial_state.json"
export GREENRAN_CONTROL_TRIAL_TARGET_DECISIONS="300"
export GREENRAN_CONTROL_TRIAL_ROLLING_WINDOW="30"
export GREENRAN_CONTROL_TRIAL_CRITICAL_STREAK="3"
export GREENRAN_CONTROL_TRIAL_MIN_CONFIDENCE="0.60"
export GREENRAN_CONTROL_TRIAL_MIN_RAN_DELTA="-0.01"
export GREENRAN_CONTROL_TRIAL_MIN_AI_DELTA="-0.01"
export GREENRAN_AB_MODE="$MODE"
export GREENRAN_AB_MODEL_SEED="$MODEL_SEED"
export NS_GLOBAL_VALUE="RngRun=$MODEL_SEED"

if [[ "$MODE" == "baseline" ]]; then
  export GREENRAN_TASAM_ADVISOR_ENABLED="0"
  export GREENRAN_TASAM_ADVISOR_MODE="shadow"
  export GREENRAN_CONTROL_TRIAL_ENABLED="0"
  export GREENRAN_TASAM_EVAL_MANIFEST="$RUN_DIR/disabled_tasam_manifest.json"
  python3 "$SCRIPT_DIR/prepare_tasam_ab_manifest.py" \
    --disabled \
    --output "$GREENRAN_TASAM_EVAL_MANIFEST" \
    --label "baseline_${MODEL_SEED}"
else
  export GREENRAN_TASAM_ADVISOR_ENABLED="1"
  export GREENRAN_TASAM_ADVISOR_MODE="tasam_only_control"
  export GREENRAN_CONTROL_TRIAL_ENABLED="1"
  export GREENRAN_TASAM_EVAL_MANIFEST="$RUN_DIR/tasam_ab_manifest.json"
  python3 "$SCRIPT_DIR/prepare_tasam_ab_manifest.py" \
    --checkpoint-dir "$CHECKPOINT_DIR" \
    --output "$GREENRAN_TASAM_EVAL_MANIFEST" \
    --label "seed_${MODEL_SEED}"
fi

bash "$SCRIPT_DIR/run_greenran_tasam_3du_wall10m.sh"

wall_pid_file="$RUN_DIR/wall_clock_supervisor.pid"
if [[ ! -f "$wall_pid_file" ]]; then
  echo "supervisor wall-clock nao foi iniciado: $wall_pid_file" >&2
  exit 4
fi
wall_pid="$(cat "$wall_pid_file" 2>/dev/null || true)"
if [[ -z "$wall_pid" ]]; then
  echo "PID invalido do supervisor wall-clock: $wall_pid_file" >&2
  exit 4
fi
echo "[$MODE] aguardando supervisor wall-clock pid=$wall_pid por ${GREENRAN_WALL_TIME_LIMIT_SECONDS}s"
while kill -0 "$wall_pid" 2>/dev/null; do
  sleep 5
done
echo "[$MODE] supervisor wall-clock finalizado: $(date --iso-8601=seconds)"
