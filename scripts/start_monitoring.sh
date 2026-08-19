#!/bin/bash
# GreenRAN - Script para iniciar serviços de monitoramento
# Execute este script DEPOIS de instalar Docker e fazer logout/login

set -e

BASE_DIR="/home/robert/orange_nuclear"
GUI_DIR="$BASE_DIR/ns-O-RAN-flexric/mmwave-LENA-oran/GUI"

echo "=========================================="
echo "  GreenRAN - Iniciando Monitoramento"
echo "=========================================="

# Verificar se Docker está disponível
if ! command -v docker &> /dev/null; then
    echo "ERRO: Docker não encontrado!"
    echo "Execute primeiro: sudo bash setup_docker_monitoring.sh"
    exit 1
fi

# Verificar se está no grupo docker
if ! groups | grep -q docker; then
    echo "AVISO: Usuário não está no grupo docker"
    echo "Execute: sudo usermod -aG docker \$USER && logout"
fi

# Parar serviços existentes se houver
echo "[1/5] Parando serviços existentes..."
cd "$GUI_DIR"
docker-compose down 2>/dev/null || true

# Limpar volumes antigos se necessário
echo "[2/5] Limpando dados antigos..."
docker-compose rm -f 2>/dev/null || true

# Iniciar serviços
echo "[3/5] Iniciando InfluxDB + Grafana..."
cd "$GUI_DIR"
docker-compose up -d

# Aguardar serviços iniciarem
echo "[4/5] Aguardando serviços iniciarem..."
sleep 10

# Verificar status
echo "[5/5] Verificando status..."
docker-compose ps

echo ""
echo "=========================================="
echo "  Monitoramento Iniciado!"
echo "=========================================="
echo ""
echo "Serviços disponíveis:"
echo "  - Grafana:  http://localhost:3000 (admin/admin)"
echo "  - InfluxDB: http://localhost:8086"
echo "  - GUI:      http://localhost:8000"
echo ""
echo "Para parar os serviços:"
echo "  cd $GUI_DIR && docker-compose down"
echo ""
echo "Para ver logs:"
echo "  cd $GUI_DIR && docker-compose logs -f"
echo ""
