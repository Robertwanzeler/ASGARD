#!/bin/bash
# GreenRAN O-RAN - Script de Execução Completa
# =============================================================================

cd ~/orange_nuclear

echo "=========================================="
echo "  GreenRAN O-RAN - UFPA"
echo "=========================================="

# Limpar processos antigos
echo "[1] Limpando processos antigos..."
pkill -9 -f "nearRT-RIC" 2>/dev/null || true
pkill -9 -f "xapp" 2>/dev/null || true
pkill -9 -f "ns3.42" 2>/dev/null || true
sleep 2

echo "    Threshold configurado: 3ms (3000 us)"
echo "    Rede instavel: delay 10-250ms + taxa 15-100 Mbps"

# LD_LIBRARY_PATH com caminhos absolutos
echo "[2] Configurando LD_LIBRARY_PATH..."
export LD_LIBRARY_PATH=/home/robert/orange_nuclear/flexric/build_e2ap_v1/src/ric:/home/robert/orange_nuclear/flexric_lib:/home/robert/orange_nuclear/flexric/build_e2ap_v1/src/xApp:$LD_LIBRARY_PATH

# Iniciar RIC
echo "[3] Iniciando nearRT-RIC..."
nohup /home/robert/orange_nuclear/flexric/build_e2ap_v1/examples/ric/nearRT-RIC \
    -c /home/robert/orange_nuclear/flexric/flexric.conf \
    -p /home/robert/orange_nuclear/flexric_lib/ > /tmp/ric.log 2>&1 &
sleep 3

# Verificar RIC
if ps aux | grep -v grep | grep nearRT-RIC | grep -v defunct > /dev/null; then
    echo "    ✓ RIC iniciado com sucesso"
else
    echo "    ✗ ERRO: RIC não iniciou"
    cat /tmp/ric.log
fi

# Iniciar ns-3
echo "[4] Iniciando ns-3 scenario-greenran..."
cd /home/robert/orange_nuclear/ns-O-RAN-flexric/mmwave-LENA-oran
nohup ./build/scratch/ns3.42-scenario-greenran-debug \
    --e2TermIp=127.0.0.1 \
    --e2lteEnabled=true \
    --e2nrEnabled=true \
    --simTime=60 > /tmp/ns3.log 2>&1 &
sleep 8

# Iniciar xApp Energy Saver
echo "[5] Iniciando xApp Energy Saver..."
nohup /home/robert/orange_nuclear/flexric/build_e2ap_v1/examples/xApp/c/xapp_energy_saver \
    -c /home/robert/orange_nuclear/flexric/flexric.conf \
    -p /home/robert/orange_nuclear/flexric_lib/ > /tmp/xapp_energy.log 2>&1 &

echo ""
echo "=========================================="
echo "  Sistema em execução!"
echo "=========================================="
echo ""
echo "Para monitorar os logs:"
echo "  tail -f /tmp/xapp_energy.log  # Métricas em tempo real"
echo "  tail -f /tmp/ns3.log          # Logs do ns-3"
echo "  tail -f /tmp/ric.log          # Logs do RIC"
echo ""
echo "Pressione Ctrl+C para encerrar"
echo "=========================================="

# Espera infinita
wait
