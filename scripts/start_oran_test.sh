#!/bin/bash

echo "=========================================="
echo "  O-RAN E2 Test - Scenario Setup"
echo "=========================================="

BASE_DIR="/home/robert/orange_nuclear"

# Configura LD_LIBRARY_PATH para usar libs do build
export LD_LIBRARY_PATH=$BASE_DIR/flexric/build_e2ap_v1/src/ric:$BASE_DIR/flexric/build_e2ap_v1/src/sm/kpm_sm:$BASE_DIR/flexric/build_e2ap_v1/src/sm/rc_sm:$BASE_DIR/flexric/build_e2ap_v1/src/xApp:$LD_LIBRARY_PATH

# Para processos antigos
pkill -f "nearRT-RIC|xapp_slicer|xapp_energy|ns3.42" 2>/dev/null || true
sleep 1

echo ""
echo "=== 1. Iniciando nearRT-RIC (E2AP v1) ==="
cd $BASE_DIR
$BASE_DIR/flexric/build_e2ap_v1/examples/ric/nearRT-RIC > /tmp/ric.log 2>&1 &
RIC_PID=$!
echo "RIC PID: $RIC_PID"
sleep 3

# Verifica se RIC está rodando
if ! ps -p $RIC_PID > /dev/null; then
    echo "ERRO: RIC não iniciou"
    cat /tmp/ric.log
    exit 1
fi
echo "RIC iniciado com sucesso!"

echo ""
echo "=== 2. Iniciando ns3 ==="
cd $BASE_DIR/ns-O-RAN-flexric/mmwave-LENA-oran
$BASE_DIR/ns-O-RAN-flexric/mmwave-LENA-oran/build/scratch/ns3.42-scenario-zero-default --e2TermIp=127.0.0.1 > /tmp/ns3.log 2>&1 &
NS3_PID=$!
echo "ns3 PID: $NS3_PID"
sleep 10

echo ""
echo "=== 3. Iniciando xApp Slicer ==="
cd $BASE_DIR
$BASE_DIR/flexric/build_e2ap_v1/examples/xApp/c/xapp_slicer > /tmp/xapp_slicer.log 2>&1 &
SLICER_PID=$!
echo "Slicer PID: $SLICER_PID"
sleep 3

echo ""
echo "=== 4. Iniciando xApp Energy Saver ==="
$BASE_DIR/flexric/build_e2ap_v1/examples/xApp/c/xapp_energy_saver > /tmp/xapp_energy.log 2>&1 &
ENERGY_PID=$!
echo "Energy Saver PID: $ENERGY_PID"
sleep 5

echo ""
echo "=== 5. Verificando processos ==="
ps aux | grep -E "(nearRT-RIC|xapp_slicer|xapp_energy|ns3.42)" | grep -v grep

echo ""
echo "=== 6. Logs (últimas 10 linhas) ==="
echo "--- Slicer ---"
tail -10 /tmp/xapp_slicer.log
echo ""
echo "--- Energy ---"
tail -10 /tmp/xapp_energy.log

echo ""
echo "=========================================="
echo "  Para monitorar os logs:"
echo "  tail -f /tmp/xapp_slicer.log"
echo "  tail -f /tmp/xapp_energy.log"
echo "  tail -f /tmp/ns3.log"
echo "=========================================="

# Espera infinita
wait
