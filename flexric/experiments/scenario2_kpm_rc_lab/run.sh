#!/bin/bash
set -euo pipefail

BASE="$HOME/orange_nuclear/flexric"
EXP="$BASE/experiments/scenario2_kpm_rc_lab"
LOG="$EXP/logs"
RESULTS="$EXP/results"
CONFIG="$EXP/config"
DURATION=15

mkdir -p "$LOG" "$RESULTS" "$CONFIG"
rm -f "$LOG"/*.log "$RESULTS"/*

date '+%Y-%m-%d %H:%M:%S' > "$RESULTS/run_started_at.txt"
git -C "$BASE" rev-parse HEAD > "$RESULTS/git_commit.txt"
git -C "$BASE" branch --show-current > "$RESULTS/git_branch.txt"

echo "scenario2_kpm_rc_lab" > "$RESULTS/scenario_name.txt"
echo "nearRT-RIC + emu_agent_gnb + xapp_kpm_rc_lab" > "$RESULTS/components.txt"
echo "$DURATION" > "$RESULTS/duration_seconds.txt"

cleanup() {
  echo "Stopping background processes..."
  kill "${AGENT_PID:-}" "${RIC_PID:-}" 2>/dev/null || true
  wait "${AGENT_PID:-}" "${RIC_PID:-}" 2>/dev/null || true
}
trap cleanup EXIT

echo "Starting nearRT-RIC..."
stdbuf -oL -eL "$BASE/build/examples/ric/nearRT-RIC" > "$LOG/ric.log" 2>&1 &
RIC_PID=$!

sleep 2

echo "Starting E2 agent..."
stdbuf -oL -eL "$BASE/build/examples/emulator/agent/emu_agent_gnb" > "$LOG/agent.log" 2>&1 &
AGENT_PID=$!

sleep 2

echo "Starting xApp..."
stdbuf -oL -eL "$BASE/build/examples/xApp/c/kpm_rc/xapp_kpm_rc_lab" > "$LOG/xapp.log" 2>&1 &
XAPP_PID=$!

echo "Running for ${DURATION}s..."
sleep "$DURATION"

echo "Stopping xApp gracefully..."
kill -INT "$XAPP_PID" 2>/dev/null || true
wait "$XAPP_PID" 2>/dev/null || true

date '+%Y-%m-%d %H:%M:%S' > "$RESULTS/run_finished_at.txt"

echo "=== quick summary ===" > "$RESULTS/summary.txt"
echo "RIC log lines: $(wc -l < "$LOG/ric.log")" >> "$RESULTS/summary.txt"
echo "Agent log lines: $(wc -l < "$LOG/agent.log")" >> "$RESULTS/summary.txt"
echo "xApp log lines: $(wc -l < "$LOG/xapp.log")" >> "$RESULTS/summary.txt"
echo "KPM indications: $(grep -c 'KPM ind_msg latency' "$LOG/xapp.log" || true)" >> "$RESULTS/summary.txt"
echo "Subscription response count: $(grep -c 'SUBSCRIPTION RESPONSE rx' "$LOG/xapp.log" || true)" >> "$RESULTS/summary.txt"
echo "RC disabled log count: $(grep -c 'RC control disabled' "$LOG/xapp.log" || true)" >> "$RESULTS/summary.txt"
echo "Successful stop count: $(grep -c 'Successfully stopped' "$LOG/xapp.log" || true)" >> "$RESULTS/summary.txt"

echo "Scenario 2 finished."
