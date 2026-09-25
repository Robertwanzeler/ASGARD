#!/bin/bash

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
. "$SCRIPT_DIR/core_runtime.sh"
load_greenran_runtime

HOST="${APP1_HOST:-127.0.0.1}"
PORT="${APP1_PORT:-5100}"

cd "$PROJECT_DIR"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo "============================================"
echo "App1-Vigilancia MVP"
echo "============================================"
echo "Host: $HOST"
echo "Port: $PORT"
echo "GreenRAN Dashboard: http://localhost:${GREENRAN_DASHBOARD_PORT:-5000}"
echo "App1 URL: http://localhost:$PORT"
echo "============================================"

mkdir -p "$STATE_DIR"

if pgrep -f "apps/app1_vigilancia/backend/simulate_cameras.py" > /dev/null; then
    echo -e "${YELLOW}Simulador de câmeras App1 já está em execução${NC}"
else
    echo -e "${GREEN}Iniciando simulador de câmeras App1...${NC}"
    setsid -f env PYTHONUNBUFFERED=1 python3 "$PROJECT_DIR/apps/app1_vigilancia/backend/simulate_cameras.py" \
        --interval 60 \
        --duration 2 \
        --resolution 3840x2160 \
        > "$STATE_DIR/app1_camera_simulator.log" 2>&1
fi

sleep 2

if pgrep -f "apps/app1_vigilancia/backend/app.py" > /dev/null; then
    echo -e "${YELLOW}App1-Vigilancia já está em execução${NC}"
else
    echo -e "${GREEN}Iniciando App1-Vigilancia...${NC}"
    setsid -f env PYTHONUNBUFFERED=1 python3 "$PROJECT_DIR/apps/app1_vigilancia/backend/app.py" \
        --host "$HOST" \
        --port "$PORT" \
        > "$STATE_DIR/app1_backend.log" 2>&1
fi

sleep 2

if pgrep -f "push/push_app1_to_influx.py" > /dev/null; then
    echo -e "${YELLOW}Push App1 para InfluxDB já está em execução${NC}"
else
    echo -e "${GREEN}Iniciando push App1 para InfluxDB...${NC}"
    setsid -f env PYTHONUNBUFFERED=1 python3 "$PROJECT_DIR/push/push_app1_to_influx.py" \
        --interval 5 \
        > "$STATE_DIR/push_app1.log" 2>&1
fi

echo -e "${GREEN}Atualizando dashboard App1 no Grafana...${NC}"
python3 "$PROJECT_DIR/push/create_grafana_app1.py" || true

echo -e "${GREEN}App1-Vigilancia iniciado com sucesso${NC}"
echo "App1 Backend: http://localhost:$PORT"
