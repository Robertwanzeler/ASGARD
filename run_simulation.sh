#!/bin/bash

echo "=========================================="
echo "  O-RAN Simulation - Working Configuration"
echo "=========================================="

BASE_DIR="/home/robert/orange_nuclear"

# Configura LD_LIBRARY_PATH
export LD_LIBRARY_PATH=$BASE_DIR/flexric/build_e2ap_v1/src/ric:$BASE_DIR/flexric_lib:$BASE_DIR/flexric/build_e2ap_v1/src/xApp:$LD_LIBRARY_PATH

# Mata processos antigos
echo ""
echo "===清理 processos antigos ==="
pkill -f "nearRT-RIC" 2>/dev/null || true
pkill -f "xapp_slicer" 2>/dev/null || true
pkill -f "xapp_energy" 2>/dev/null || true
pkill -f "ns3.42-scenario-zero" 2>/dev/null || true
sleep 2

echo ""
echo "=== 1. Iniciando nearRT-RIC (E2AP v1) ==="
cd $BASE_DIR
$BASE_DIR/flexric/build_e2ap_v1/examples/ric/nearRT-RIC -c flexric/flexric.conf -p flexric_lib/ > /tmp/ric.log 2>&1 &
RIC_PID=$!
echo "RIC iniciado (PID: $RIC_PID)"
sleep 3

echo ""
echo "=== 2. Iniciando ns3 (scenario-zero com E2) ==="
cd $BASE_DIR/ns-O-RAN-flexric/mmwave-LENA-oran
$BASE_DIR/ns-O-RAN-flexric/mmwave-LENA-oran/build/scratch/ns3.42-scenario-zero-default --e2TermIp=127.0.0.1 --e2lteEnabled=true --e2nrEnabled=true --simTime=60 > /tmp/ns3.log 2>&1 &
NS3_PID=$!
echo "ns3 iniciado (PID: $NS3_PID)"
sleep 10

echo ""
echo "=== 3. Iniciando xApp Slicer ==="
cd $BASE_DIR
$BASE_DIR/flexric/build_e2ap_v1/examples/xApp/c/xapp_slicer -c flexric/flexric.conf -p flexric_lib/ > /tmp/xapp_slicer.log 2>&1 &
SLICER_PID=$!
echo "xApp Slicer iniciado (PID: $SLICER_PID)"
sleep 3

echo ""
echo "=== 4. Iniciando xApp Energy Saver ==="
$BASE_DIR/flexric/build_e2ap_v1/examples/xApp/c/xapp_energy_saver -c flexric/flexric.conf -p flexric_lib/ > /tmp/xapp_energy.log 2>&1 &
ENERGY_PID=$!
echo "xApp Energy Saver iniciado (PID: $ENERGY_PID)"
sleep 5

echo ""
echo "=== Verificação dos processos ==="
ps aux | grep -E "(nearRT-RIC|xapp_slicer|xapp_energy|ns3.42-scenario-zero)" | grep -v grep

echo ""
echo "=========================================="
echo "  Simulação em execução!"
echo "=========================================="
echo ""
echo "Para monitorar os logs:"
echo "  tail -f /tmp/xapp_slicer.log    # xApp Slicer"
echo "  tail -f /tmp/xapp_energy.log   # xApp Energy Saver"
echo "  tail -f /tmp/ns3.log           # ns3"
echo "  tail -f /tmp/ric.log           # RIC"
echo ""
echo "Pressione Ctrl+C para encerrar"
echo "=========================================="

# Espera infinita
wait
