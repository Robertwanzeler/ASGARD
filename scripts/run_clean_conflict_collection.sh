#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
STATE_DIR="${GREENRAN_STATE_DIR:-/tmp}"
RUNTIME_LOG="${STATE_DIR}/greenran_long_collection_runtime.log"
RUNTIME_PID_FILE="${STATE_DIR}/greenran_long_collection_runtime.pid"

ROUNDS="${GREENRAN_COLLECTION_ROUNDS:-20}"
DURATION="${GREENRAN_COLLECTION_DURATION:-600}"
TARGET_ROWS="${GREENRAN_COLLECTION_TARGET_ROWS:-450}"
OUTPUT_ROOT="${GREENRAN_COLLECTION_OUTPUT_ROOT:-$BASE_DIR/runs/experimentos_conflitos}"
PROGRESS_STEP="${GREENRAN_COLLECTION_PROGRESS_STEP:-60}"
WAIT_TIMEOUT="${GREENRAN_COLLECTION_WAIT_TIMEOUT:-180}"

usage() {
  cat <<EOF
Uso:
  bash scripts/run_clean_conflict_collection.sh [opções]

Opções:
  --rounds N                Rodadas por cenário (default: ${ROUNDS})
  --duration S              Duração de cada rodada em segundos (default: ${DURATION})
  --target-rows N           Meta de linhas por cenário (default: ${TARGET_ROWS})
  --output-root DIR         Diretório de saída da coleta (default: ${OUTPUT_ROOT})
  --progress-step S         Passo do countdown em segundos (default: ${PROGRESS_STEP})
  --wait-timeout S          Timeout para readiness do runtime (default: ${WAIT_TIMEOUT})
  --manual                  Passa --manual para o coletor
  --continue                Continua de um experimento anterior incomplet
  --help                    Mostra esta ajuda
EOF
}

MANUAL_MODE=0
CONTINUE_MODE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --rounds)
      ROUNDS="$2"
      shift 2
      ;;
    --duration)
      DURATION="$2"
      shift 2
      ;;
    --target-rows)
      TARGET_ROWS="$2"
      shift 2
      ;;
    --output-root)
      OUTPUT_ROOT="$2"
      shift 2
      ;;
    --progress-step)
      PROGRESS_STEP="$2"
      shift 2
      ;;
    --wait-timeout)
      WAIT_TIMEOUT="$2"
      shift 2
      ;;
    --manual)
      MANUAL_MODE=1
      shift
      ;;
    --continue)
      CONTINUE_MODE=1
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Opção desconhecida: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

require_file() {
  local path="$1"
  [[ -s "$path" ]]
}

wait_runtime_ready() {
  local start_ts now elapsed
  start_ts="$(date +%s)"
  echo "[collection] aguardando runtime ficar pronto..."

  while true; do
    now="$(date +%s)"
    elapsed="$(( now - start_ts ))"
    if (( elapsed > WAIT_TIMEOUT )); then
      echo "[collection] timeout aguardando readiness do runtime (${WAIT_TIMEOUT}s)" >&2
      echo "[collection] verifique: ${RUNTIME_LOG}" >&2
      return 1
    fi

    if pgrep -f "ns3.42-scenario" >/dev/null 2>&1 \
      && pgrep -f "csv_to_metrics.py" >/dev/null 2>&1 \
      && pgrep -f "rapp_orchestrator.py" >/dev/null 2>&1 \
      && pgrep -f "app1_vigilancia/backend/app.py" >/dev/null 2>&1 \
      && pgrep -f "app2_monitoramento/backend/app.py" >/dev/null 2>&1 \
      && pgrep -f "app3_veicular/backend/app.py" >/dev/null 2>&1 \
      && require_file "${STATE_DIR}/xapp_metrics/extended_metrics.json" \
      && require_file "${STATE_DIR}/app1_vigilancia/monitoring_snapshot.json" \
      && require_file "${STATE_DIR}/app2_monitoramento/monitoring_snapshot.json" \
      && require_file "${STATE_DIR}/app3_veicular/monitoring_snapshot.json" \
      && require_file "${STATE_DIR}/xapp_intents/slicer.txt"
    then
      echo "[collection] runtime pronto após ${elapsed}s"
      return 0
    fi

    sleep 2
  done
}

cd "$BASE_DIR"

echo "[collection] parando runtime anterior..."
bash ./scripts/stop_all.sh >/dev/null 2>&1 || true

echo "[collection] iniciando runtime longo..."
nohup bash ./scripts/run_greenran_long_collection_runtime.sh >"${RUNTIME_LOG}" 2>&1 &
echo $! > "${RUNTIME_PID_FILE}"
sleep 2

wait_runtime_ready

echo "[collection] iniciando coleta..."
CMD=(
  ./drlexp/.venv/bin/python
  scripts/run_all_real_conflict_experiments.py
  --rounds "${ROUNDS}"
  --duration "${DURATION}"
  --progress-step "${PROGRESS_STEP}"
  --target-rows-per-scenario "${TARGET_ROWS}"
  --output-root "${OUTPUT_ROOT}"
)

if [[ "${MANUAL_MODE}" == "1" ]]; then
  CMD+=(--manual)
fi

if [[ "${CONTINUE_MODE}" == "1" ]]; then
  CMD+=(--continue)
fi

printf '[collection] comando:'
printf ' %q' "${CMD[@]}"
printf '\n'

exec "${CMD[@]}"
