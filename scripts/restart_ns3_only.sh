#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="${GREENRAN_PROJECT_DIR:-/home/robert/orange_nuclear}"
STATE_DIR="${GREENRAN_STATE_DIR:-${GREENRAN_TASAM_ACTIVE_STATE_DIR:-$PROJECT_ROOT/runs/tasam_article_ns3_collection}}"
PID_FILE="$STATE_DIR/ns3.pid"
SUPERVISOR_FILE="$STATE_DIR/ns3_supervisor.pid"
START_SCRIPT="$PROJECT_ROOT/scripts/start_ns3_supervisor.sh"

if [[ ! -f "$PID_FILE" ]]; then
  echo "ERRO: PID do ns-3 não encontrado: $PID_FILE" >&2
  exit 1
fi

pid="$(cat "$PID_FILE" 2>/dev/null || true)"
if [[ -z "$pid" ]]; then
  echo "ERRO: $PID_FILE vazio" >&2
  exit 1
fi

if kill -0 "$pid" 2>/dev/null; then
  echo "Reiniciando apenas ns-3 filho PID $pid"
  kill "$pid"
else
  echo "ns-3 PID $pid já não está vivo"
fi

supervisor_pid="$(cat "$SUPERVISOR_FILE" 2>/dev/null || true)"
if [[ -n "$supervisor_pid" ]] && kill -0 "$supervisor_pid" 2>/dev/null; then
  echo "Supervisor ns-3: $supervisor_pid"
else
  echo "Supervisor ns-3 ausente; relançando"
  nohup "$START_SCRIPT" > /dev/null 2>&1 &
  sleep 1
fi

for _ in $(seq 1 20); do
  new_pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "$new_pid" ]] && kill -0 "$new_pid" 2>/dev/null; then
    echo "ns-3 ativo novamente: PID $new_pid"
    exit 0
  fi
  sleep 0.5
done

echo "AVISO: ns-3 ainda não está ativo; veja $STATE_DIR/ns3.log" >&2
exit 2
