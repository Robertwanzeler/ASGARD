#!/usr/bin/env bash
set -euo pipefail

# Fully local causal campaign.  The compatible checkpoint must already have
# been imported into runs/tasam_local_checkpoint_seed47_20260906.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHECKPOINT="$ROOT/runs/tasam_local_checkpoint_seed47_20260906"
CAMPAIGN="${GREENRAN_LOCAL_CAMPAIGN_DIR:-$ROOT/runs/tasam_local_causal_pilot_seed47_20260906_v11}"
SMOKE="$CAMPAIGN/smoke_shadow"
SHADOW="$CAMPAIGN/shadow_300"
GATE="$CAMPAIGN/control_gate"
SHADOW_DB="$SHADOW/rapp_data_lake.db"
RESUME="${GREENRAN_LOCAL_CAMPAIGN_RESUME:-0}"

for local_path in "$CHECKPOINT" "$CAMPAIGN"; do
  resolved_path="$(realpath -m "$local_path")"
  case "$resolved_path" in
    "$ROOT"/*) ;;
    *)
      echo "ERRO: caminho fora do workspace local: $resolved_path" >&2
      exit 2
      ;;
  esac
done

if [[ "$(id -u)" -ne 0 ]]; then
  echo "ERRO: este launcher precisa ser executado como root para aplicar o cgroup." >&2
  exit 4
fi
if [[ ! -f "$CHECKPOINT/tasam_marl_checkpoint_meta.json" ]]; then
  echo "ERRO: checkpoint local ausente: $CHECKPOINT" >&2
  exit 2
fi
campaign_artifact=""
if [[ -e "$CAMPAIGN" ]]; then
  campaign_artifact="$(find "$CAMPAIGN" -mindepth 1 -maxdepth 1 ! -name 'campaign_console.log' -print -quit)"
fi
if [[ "$RESUME" != "1" && -n "$campaign_artifact" ]]; then
  echo "ERRO: campanha local já contém artefatos; nenhum arquivo será sobrescrito: $CAMPAIGN" >&2
  exit 3
fi
mkdir -p "$CAMPAIGN"

export PYTHONPATH="$ROOT/src"
export GREENRAN_LOCAL_ONLY=1
export GREENRAN_CGROUP_ENFORCE=1
export GREENRAN_CGROUP_ALLOW_UNENFORCED=0
export GREENRAN_NS3_ENABLE_TRACES=1
export GREENRAN_NS3_ENABLE_TRACES_AFTER_ATTACH=0
export GREENRAN_TASAM_REQUIRE_CHECKPOINT=1
export GREENRAN_TASAM_ADVISOR_MODE=shadow
export GREENRAN_REAL_ONLY=1
export GREENRAN_REQUIRE_REAL_PDCP=1
export GREENRAN_TASAM_EXPORT_ALLOW_PROXY=0
export GREENRAN_NS3_E2DU_ENABLED=true
export GREENRAN_NS3_E2NR_ENABLED=false

run_shadow_with_target() {
  local run_dir="$1"
  local wall_time="$2"
  local target="$3"
  # run_tasam_online_arm.py requires a pristine run directory.  Keep the
  # launcher log beside the run directory instead of creating a file inside
  # it before the arm has initialized its manifest.
  local arm_log="${run_dir}.arm_launcher.log"
  local target_log="$run_dir/decision_target.log"
  python3 "$ROOT/scripts/run_tasam_online_arm.py" \
    --mode combined_shadow \
    --run-dir "$run_dir" \
    --seed 47 \
    --profile tasam_training_balanced_v3 \
    --wall-time "$wall_time" \
    --sim-time "$wall_time" \
    --decision-target "$target" \
    --checkpoint "$CHECKPOINT" \
    --min-free-gib 10 > "$arm_log" 2>&1 &
  local arm_pid=$!
  local deadline=$((SECONDS + 120))
  while (( SECONDS < deadline )); do
    if [[ -f "$run_dir/rapp.pid" && -f "$run_dir/ns3_supervisor.pid" && -f "$run_dir/wall_clock_supervisor.pid" ]]; then
      break
    fi
    if ! kill -0 "$arm_pid" 2>/dev/null; then
      cat "$arm_log" >&2 || true
      return 4
    fi
    sleep 1
  done
  if [[ ! -f "$run_dir/rapp.pid" || ! -f "$run_dir/ns3_supervisor.pid" || ! -f "$run_dir/wall_clock_supervisor.pid" ]]; then
    echo "ERRO: timeout aguardando runtime de $run_dir" >&2
    return 4
  fi
  # Keep the watcher as a normal child of this launcher.  It now uses only
  # individual PID signals, so a separate session is unnecessary and could
  # hide a watcher termination before its result was flushed to the log.
  PYTHONUNBUFFERED=1 python3 "$ROOT/scripts/stop_on_decision_target.py" \
    --state-dir "$run_dir" \
    --db "$run_dir/rapp_data_lake.db" \
    --target "$target" \
    --wall-only > "$target_log" 2>&1 &
  local watcher_pid=$!
  local watcher_status=0
  wait "$watcher_pid" || watcher_status=$?
  local arm_status=0
  wait "$arm_pid" || arm_status=$?
  if [[ "$watcher_status" -ne 0 ]] || ! grep -q "'decisions': $target" "$target_log" 2>/dev/null; then
    echo "ERRO: watcher de decisões falhou em $run_dir" >&2
    cat "$target_log" >&2 || true
    cat "$arm_log" >&2 || true
    return "${watcher_status:-$arm_status}"
  fi
  # The watcher owns shutdown, but the arm owns manifest finalization.  Do
  # not allow a database with the target count to be mistaken for a completed
  # run if the arm was killed before writing its final manifest.
  if ! grep -q '"status": "finished"' "$run_dir/arm_manifest.json" 2>/dev/null; then
    echo "ERRO: arm não finalizou o manifesto em $run_dir" >&2
    cat "$target_log" >&2 || true
    cat "$arm_log" >&2 || true
    return 5
  fi
  # Clean workers only after the arm has written its final manifest.  This
  # separates lifetime control from evidence finalization and prevents a
  # worker shutdown from terminating the arm prematurely.
  PYTHONUNBUFFERED=1 python3 "$ROOT/scripts/stop_on_decision_target.py" \
    --state-dir "$run_dir" \
    --db "$run_dir/rapp_data_lake.db" \
    --target "$target" \
    --cleanup-only \
    --finalize-real-metrics >> "$target_log" 2>&1
  return 0
}

smoke_ready=0
if [[ -f "$SMOKE/arm_manifest.json" ]] && grep -q '"status": "finished"' "$SMOKE/arm_manifest.json"; then
  smoke_ready=1
elif [[ "$RESUME" == "1" \
        && -f "$SMOKE/rapp_decisions.jsonl" \
        && "$(wc -l < "$SMOKE/rapp_decisions.jsonl")" -ge 20 \
        && -s "$SMOKE/xapp_metrics/extended_metrics.json" ]]; then
  # A target watcher intentionally terminates the wall-clock supervisor with
  # SIGTERM.  Treat that preserved, real-PDCP smoke as complete on resume;
  # never start a second arm inside the non-empty directory.
  smoke_ready=1
fi
if (( smoke_ready == 0 )); then
  run_shadow_with_target "$SMOKE" 120 20
fi

shadow_decisions=0
if [[ -f "$SHADOW/rapp_decisions.jsonl" ]]; then
  shadow_decisions="$(wc -l < "$SHADOW/rapp_decisions.jsonl")"
fi
if (( shadow_decisions < 300 )); then
  run_shadow_with_target "$SHADOW" 4500 300
fi

python3 "$ROOT/scripts/prepare_tasam_control_trial.py" \
  --checkpoint "$CHECKPOINT" \
  --shadow-db "$SHADOW_DB" \
  --output-dir "$GATE" \
  --window 300 \
  --min-samples 300 \
  --min-positive-rate 0.80

python3 "$ROOT/scripts/run_tasam_causal_pilot.py" \
  --execute \
  --campaign-dir "$CAMPAIGN/main" \
  --checkpoint "$CHECKPOINT" \
  --shadow-db "$SHADOW_DB" \
  --control-gate "$GATE/marl_control_gate.json" \
  --seed 47 \
  --profile tasam_training_balanced_v3 \
  --wall-time 600 \
  --sim-time 600 \
  --canary-wall-time 120 \
  --min-free-gib 10 \
  --min-shadow-samples 300 \
  --min-positive-rate 0.80 \
  --control-fraction 0.10

echo "Campanha local concluída: $CAMPAIGN/main/causal_comparison.json"
