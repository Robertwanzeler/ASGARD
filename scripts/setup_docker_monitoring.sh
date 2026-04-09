#!/bin/bash
# GreenRAN - Script de Instalação do Docker e Monitoramento
# Execute este script com: sudo bash setup_docker_monitoring.sh

set -e

echo "=========================================="
echo "  GreenRAN - Setup Docker + Grafana"
echo "=========================================="

# 1. Atualizar pacotes
echo "[1/6] Atualizando pacotes..."
apt update

# 2. Instalar dependências
echo "[2/6] Instalando dependências..."
apt install -y ca-certificates curl gnupg lsb-release

# 3. Adicionar repositório Docker
echo "[3/6] Adicionando repositório Docker..."
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg

echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null

# 4. Instalar Docker
echo "[4/6] Instalando Docker..."
apt update
apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# 5. Configurar Docker para rodar sem sudo
echo "[5/6] Configurando Docker..."
usermod -aG docker $USER
systemctl start docker
systemctl enable docker

# 6. Verificar instalação
echo "[6/6] Verificando instalação..."
docker --version
docker compose version

echo ""
echo "=========================================="
echo "  Docker instalado com sucesso!"
echo "=========================================="
echo ""
echo "IMPORTANTE: Faça logout e login novamente"
echo "para usar Docker sem sudo."
echo ""
echo "Depois execute:"
echo "  cd /home/robert/orange_nuclear/ns-O-RAN-flexric/mmwave-LENA-oran/GUI"
echo "  docker compose up -d"
echo ""
