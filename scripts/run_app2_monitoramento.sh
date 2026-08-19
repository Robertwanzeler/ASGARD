#!/bin/bash
# ============================================================
# GreenRAN - Executar App2-Monitoramento + Sensores Simulados
# ============================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT"

# Cores para output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo "=========================================="
echo "  GreenRAN - App2-Monitoramento"
echo "=========================================="
echo ""

# Carregar configurações
source "$PROJECT_ROOT/scripts/core_runtime.sh" 2>/dev/null || true

# Criar diretórios necessários
mkdir -p /tmp/app2_monitoramento/sensors
mkdir -p /tmp

if pgrep -f "apps/app2_monitoramento/backend/simulate_sensors.py" > /dev/null; then
    echo -e "${YELLOW}Simulador de sensores já está em execução${NC}"
else
    echo -e "${GREEN}Iniciando simulador de sensores...${NC}"
    setsid -f env PYTHONUNBUFFERED=1 python3 "$PROJECT_ROOT/apps/app2_monitoramento/backend/simulate_sensors.py" \
        --num-sensors 17 \
        --interval 5.0 \
        > /tmp/app2_simulator.log 2>&1
fi

# Aguardar um pouco para o simulador iniciar
sleep 2

if pgrep -f "apps/app2_monitoramento/backend/app.py" > /dev/null; then
    echo -e "${YELLOW}App2-Monitoramento já está em execução${NC}"
else
    echo -e "${GREEN}Iniciando App2-Monitoramento...${NC}"
    setsid -f env PYTHONUNBUFFERED=1 python3 "$PROJECT_ROOT/apps/app2_monitoramento/backend/app.py" \
        --host 0.0.0.0 \
        --port 5200 \
        > /tmp/app2_backend.log 2>&1
fi

# Aguardar
sleep 2

if pgrep -f "push/push_app2_to_influx.py" > /dev/null; then
    echo -e "${YELLOW}Push App2 para InfluxDB já está em execução${NC}"
else
    echo -e "${GREEN}Iniciando push para InfluxDB...${NC}"
    setsid -f env PYTHONUNBUFFERED=1 python3 "$PROJECT_ROOT/push/push_app2_to_influx.py" \
        --interval 5.0 \
        > /tmp/push_app2.log 2>&1
fi

echo -e "${GREEN}Atualizando dashboard App2 no Grafana...${NC}"
python3 "$PROJECT_ROOT/push/create_grafana_app2.py" || true

echo ""
echo -e "${GREEN}==========================================${NC}"
echo -e "${GREEN}App2-Monitoramento iniciado com sucesso!${NC}"
echo -e "${GREEN}==========================================${NC}"
echo ""
echo "URLs:"
echo "  App2 Backend:  http://localhost:5200"
echo "  API Health:    http://localhost:5200/api/health"
echo ""
echo "Para verificar o status:"
echo "  curl http://localhost:5200/api/health"
echo ""
echo "Para visualizar no Grafana:"
echo "  http://localhost:3001/d/8NfKIQhDk/app2-monitoramento-ambiental-ufpa"
