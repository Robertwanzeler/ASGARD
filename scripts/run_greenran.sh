#!/bin/bash
# GreenRAN O-RAN - Execução Completa
# Cenário: Taxa 15-100 Mbps + Threshold 100ms (App1-Vigilância SLA)
# ============================================

cd ~/orange_nuclear

# Limpar processos antigos
pkill -9 -f "nearRT-RIC" 2>/dev/null || true
pkill -9 -f "xapp" 2>/dev/null || true
pkill -9 -f "ns3.42" 2>/dev/null || true
sleep 2

# LD_LIBRARY_PATH
export LD_LIBRARY_PATH=/home/robert/orange_nuclear/flexric/build_e2ap_v1/src/ric:/home/robert/orange_nuclear/flexric_lib:/home/robert/orange_nuclear/flexric/build_e2ap_v1/src/xApp:$LD_LIBRARY_PATH

echo "=========================================="
echo "  GreenRAN O-RAN - UFPA"
echo "  Cenário: Taxa 15-100 Mbps + 100ms (SLA)"
echo "=========================================="

# Iniciar RIC
echo "[1] Iniciando RIC..."
./flexric/build_e2ap_v1/examples/ric/nearRT-RIC -c ./flexric/flexric.conf -p ./flexric_lib/ > /tmp/ric.log 2>&1 &
sleep 5

# Iniciar ns-3
echo "[2] Iniciando ns-3..."
cd ns-O-RAN-flexric/mmwave-LENA-oran
./build/scratch/ns3.42-scenario-greenran-debug --e2TermIp=127.0.0.1 --e2lteEnabled=true --e2nrEnabled=true --simTime=600 > /tmp/ns3.log 2>&1 &

# Aguardar ns-3 iniciar e registrar os nós E2
echo "[2b] Aguardando ns-3 iniciar..."
for i in {1..30}; do
    if grep -q "Simulation time is" /tmp/ns3.log 2>/dev/null; then
        echo "     ns-3 iniciado, aguardando nós E2..."
        break
    fi
    sleep 1
done

# Aguardar mais 5 segundos para nós E2 ficarem prontos
sleep 5

# Iniciar xApp
echo "[3] Iniciando xApp..."
cd ~/orange_nuclear
./flexric/build_e2ap_v1/examples/xApp/c/xapp_energy_saver -c ./flexric/flexric.conf -p ./flexric_lib/ > /tmp/xapp_energy.log 2>&1 &

echo ""
echo "=========================================="
echo "  Sistema em execução!"
echo "=========================================="
echo "Logs disponíveis em:"
echo "  tail -f /tmp/xapp_energy.log"
echo "  tail -f /tmp/ns3.log"
echo "  tail -f /tmp/ric.log"
echo "=========================================="
