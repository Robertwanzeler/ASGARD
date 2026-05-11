#!/bin/bash
# GreenRAN CARLA + ns-3 wrapper

. "$(dirname "$0")/core_runtime.sh"
load_greenran_runtime
create_greenran_run "greenran_carla_ns3" >/dev/null

cd "$BASE_DIR"

CARLA_MODE="${GREENRAN_CARLA_MODE:-mock}"
CARLA_HOST="${GREENRAN_CARLA_HOST:-127.0.0.1}"
CARLA_PORT="${GREENRAN_CARLA_PORT:-2000}"
CARLA_POLL_INTERVAL="${GREENRAN_CARLA_POLL_INTERVAL:-0.2}"
CARLA_MAPPER_POLL_INTERVAL="${GREENRAN_CARLA_MAPPER_POLL_INTERVAL:-0.5}"
CARLA_FALLBACK_TO_MOCK="${GREENRAN_CARLA_FALLBACK_TO_MOCK:-1}"
CARLA_BRIDGE_LOG="${STATE_DIR}/carla_bridge.log"
CARLA_BRIDGE_PID="${STATE_DIR}/carla_bridge.pid"
CARLA_MAPPER_LOG="${STATE_DIR}/carla_ns3_mapper.log"
CARLA_MAPPER_PID="${STATE_DIR}/carla_ns3_mapper.pid"

mkdir -p "$STATE_DIR/carla_state"
rm -f "$CARLA_BRIDGE_PID" "$CARLA_MAPPER_PID"

pkill -9 -f "python3 ./src/carla_bridge.py" 2>/dev/null || true
pkill -9 -f "python3 ./src/carla_ns3_mapper.py" 2>/dev/null || true

probe_carla_port() {
  python3 - "$1" "$2" <<'PY'
import socket
import sys

host = sys.argv[1]
port = int(sys.argv[2])
s = socket.socket()
s.settimeout(1.5)
try:
    s.connect((host, port))
except Exception:
    print("down")
    raise SystemExit(1)
finally:
    s.close()
print("up")
PY
}

EFFECTIVE_CARLA_MODE="$CARLA_MODE"
if [ "$CARLA_MODE" = "carla" ]; then
  if ! probe_carla_port "$CARLA_HOST" "$CARLA_PORT" >/dev/null 2>&1; then
    if [ "$CARLA_FALLBACK_TO_MOCK" = "1" ]; then
      echo "=== [CARLA 0/3] Aviso: ${CARLA_HOST}:${CARLA_PORT} indisponível; fallback automático para mock ==="
      EFFECTIVE_CARLA_MODE="mock"
    else
      echo "=== [CARLA 0/3] Erro: ${CARLA_HOST}:${CARLA_PORT} indisponível e fallback desabilitado ==="
      exit 1
    fi
  fi
fi

echo "=== [CARLA 1/3] Iniciando bridge do CARLA (${EFFECTIVE_CARLA_MODE}) ==="
nohup python3 ./src/carla_bridge.py \
  --mode "$EFFECTIVE_CARLA_MODE" \
  --host "$CARLA_HOST" \
  --port "$CARLA_PORT" \
  --poll-interval "$CARLA_POLL_INTERVAL" \
  > "$CARLA_BRIDGE_LOG" 2>&1 &
echo $! > "$CARLA_BRIDGE_PID"
sleep 1

echo "=== [CARLA 2/3] Iniciando mapeador vehicle_id -> IMSI ==="
nohup python3 ./src/carla_ns3_mapper.py \
  --base-imsi 16 \
  --max-vehicles 5 \
  --poll-interval "$CARLA_MAPPER_POLL_INTERVAL" \
  > "$CARLA_MAPPER_LOG" 2>&1 &
echo $! > "$CARLA_MAPPER_PID"
sleep 1

echo "=== [CARLA 3/3] Subindo runtime GreenRAN padrão ==="
echo "    - modo CARLA solicitado: $CARLA_MODE"
echo "    - modo CARLA efetivo: $EFFECTIVE_CARLA_MODE"
echo "    - host/porta: ${CARLA_HOST}:${CARLA_PORT}"
echo "    - logs bridge: $CARLA_BRIDGE_LOG"
echo "    - logs mapper: $CARLA_MAPPER_LOG"

exec ./scripts/run_greenran_v2.sh
