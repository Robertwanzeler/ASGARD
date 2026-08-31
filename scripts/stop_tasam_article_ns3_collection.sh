#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
STATE_DIR="${GREENRAN_STATE_DIR:-$PROJECT_ROOT/runs/tasam_article_ns3_collection}"

if [[ "${1:-}" == "--state-dir" && -n "${2:-}" ]]; then
  STATE_DIR="$2"
  shift 2
fi
if [[ "$#" -ne 0 ]]; then
  echo "uso: $0 [--state-dir CAMPAIGN_STATE_DIR]" >&2
  exit 2
fi

kill_pid_file() {
  local pid_file="$1"
  if [[ ! -f "$pid_file" ]]; then
    return 0
  fi
  local pid
  pid="$(cat "$pid_file" 2>/dev/null || true)"
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null || true
    sleep 0.2
    if kill -0 "$pid" 2>/dev/null; then
      kill -9 "$pid" 2>/dev/null || true
    fi
  fi
  rm -f "$pid_file"
}

kill_pid_file "$STATE_DIR/rapp.pid"
kill_pid_file "$STATE_DIR/csv_metrics.pid"
kill_pid_file "$STATE_DIR/xapp_slicer.pid"
kill_pid_file "$STATE_DIR/xapp_energy.pid"
kill_pid_file "$STATE_DIR/xapp_vehicle.pid"
kill_pid_file "$STATE_DIR/ns3.pid"
kill_pid_file "$STATE_DIR/ns3_supervisor.pid"
kill_pid_file "$STATE_DIR/ric.pid"
kill_pid_file "$STATE_DIR/db_snapshot.pid"
kill_pid_file "$STATE_DIR/tasam_article_export.pid"
kill_pid_file "$STATE_DIR/rapp_online_retrain.pid"
kill_pid_file "$STATE_DIR/collection_event_alternator.pid"
kill_pid_file "$STATE_DIR/per_ue_policy_consumer.pid"
# The controlled online launcher has two additional supervisors outside the
# article services above.  Stop them by their exact instance PID before
# cleaning stale state, otherwise a restart can leave the old controller
# writing the same SQLite database concurrently with the new one.
kill_pid_file "$STATE_DIR/greenran_online_runtime.pid"
kill_pid_file "$STATE_DIR/online_controller.pid"
kill_pid_file "$STATE_DIR/tasam_true_online_real.pid"


kill_matching_state_dir() {
  local pattern="$1"
  ps -eo pid=,cmd= | awk -v state_dir="$STATE_DIR" -v pattern="$pattern" '
    index($0, state_dir) && index($0, pattern) { print $1 }
  ' | while read -r pid; do
    [[ -z "$pid" ]] && continue
    kill "$pid" 2>/dev/null || true
    sleep 0.1
    if kill -0 "$pid" 2>/dev/null; then
      kill -9 "$pid" 2>/dev/null || true
    fi
  done
}

# Keep process-tree cleanup scoped to this campaign's exact state directory.
# The named helper is also used for the ns-3 supervisor wrapper below.
kill_process_tree_by_pattern() {
  kill_matching_state_dir "$1"
}

kill_matching_state_dir "csv_to_metrics.py"
kill_matching_state_dir "rapp_orchestrator.py"
kill_matching_state_dir "snapshot_sqlite_db.py"
kill_matching_state_dir "run_tasam_article_export.py"
kill_matching_state_dir "run_tasam_online_controlled.py"
kill_matching_state_dir "collection_event_alternator.py"
kill_matching_state_dir "consume_per_ue_policy.py"
kill_process_tree_by_pattern "$PROJECT_ROOT/scripts/start_ns3_supervisor.sh"
# Never kill processes by a global command-line pattern: multiple campaigns
# run concurrently. PID files and state-dir matches above define the scope.
rm -f "$STATE_DIR/article00_scenario_control.json"

python3 - "$STATE_DIR" <<'PY'
import json
import pathlib
import sys
import time

state_dir = pathlib.Path(sys.argv[1])
for name in ("online_status.json", "online_state.json"):
    path = state_dir / name
    if not path.exists():
        continue
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        continue
    payload["status"] = "stopped"
    payload["stopped_at"] = int(time.time())
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
PY

echo "TA-SAM article ns-3 collection parada: $STATE_DIR"
