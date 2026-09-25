#!/bin/bash

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT"

source "$PROJECT_ROOT/scripts/core_runtime.sh" 2>/dev/null || true
load_greenran_runtime 2>/dev/null || true

HOST="${APP3_HOST:-127.0.0.1}"
PORT="${APP3_PORT:-5300}"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo "=========================================="
echo "  GreenRAN - App3-Veicular"
echo "=========================================="
echo ""

mkdir -p /tmp/app3_veicular

if pgrep -f "apps/app3_veicular/backend/app.py" > /dev/null; then
    echo -e "${YELLOW}App3-Veicular já está em execução${NC}"
else
    echo -e "${GREEN}Iniciando App3-Veicular...${NC}"
    setsid -f env PYTHONUNBUFFERED=1 python3 "$PROJECT_ROOT/apps/app3_veicular/backend/app.py" \
        --host "$HOST" \
        --port "$PORT" \
        > /tmp/app3_backend.log 2>&1
fi

echo ""
echo -e "${GREEN}==========================================${NC}"
echo -e "${GREEN}App3-Veicular iniciado com sucesso!${NC}"
echo -e "${GREEN}==========================================${NC}"
echo ""
echo "URLs:"
echo "  App3 Backend:  http://localhost:$PORT"
echo "  API Health:    http://localhost:$PORT/api/health"
