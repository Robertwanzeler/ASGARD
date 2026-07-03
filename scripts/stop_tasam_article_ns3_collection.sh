#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
STATE_DIR="${GREENRAN_STATE_DIR:-$PROJECT_ROOT/runs/tasam_article_ns3_collection}"

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
kill_pid_file "$STATE_DIR/db_snapshot.pid"
kill_pid_file "$STATE_DIR/tasam_article_export.pid"
kill_pid_file "$STATE_DIR/rapp_online_retrain.pid"
kill_pid_file "$STATE_DIR/collection_event_alternator.pid"


kill_process_tree_by_pattern() {
  local pattern="$1"
  ps -eo pid=,cmd= | awk -v pattern="$pattern" '
    index($0, pattern) { print $1 }
  ' | while read -r pid; do
    [[ -z "$pid" ]] && continue
    ps -eo pid=,ppid= | awk -v parent="$pid" '$2 == parent { print $1 }' | while read -r child; do
      [[ -z "$child" ]] && continue
      kill "$child" 2>/dev/null || true
      sleep 0.1
      if kill -0 "$child" 2>/dev/null; then
        kill -9 "$child" 2>/dev/null || true
      fi
    done
    kill "$pid" 2>/dev/null || true
    sleep 0.1
    if kill -0 "$pid" 2>/dev/null; then
      kill -9 "$pid" 2>/dev/null || true
    fi
  done
}

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

kill_matching_state_dir "csv_to_metrics.py"
kill_matching_state_dir "rapp_orchestrator.py"
kill_matching_state_dir "snapshot_sqlite_db.py"
kill_matching_state_dir "run_tasam_article_export.py"
kill_matching_state_dir "collection_event_alternator.py"
kill_process_tree_by_pattern "$PROJECT_ROOT/scripts/start_ns3_supervisor.sh"
kill_process_tree_by_pattern "$PROJECT_ROOT/src/rapp_orchestrator.py"
kill_process_tree_by_pattern "run_rapp_online_retrain.py"
rm -f "$STATE_DIR/article00_scenario_control.json"

echo "TA-SAM article ns-3 collection parada: $STATE_DIR"
