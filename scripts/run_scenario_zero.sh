#!/bin/bash

. "$(dirname "$0")/core_runtime.sh"
load_greenran_runtime

echo "=========================================="
echo "  O-RAN Simulation - Scenario Zero"
echo "=========================================="

# Configura LD_LIBRARY_PATH
export LD_LIBRARY_PATH=$BASE_DIR/flexric/build_e2ap_v1/src/ric:$BASE_DIR/flexric_lib:$BASE_DIR/flexric/build_e2ap_v1/src/xApp:$LD_LIBRARY_PATH

# Mata processos antigos
echo "=== Limpando processos antigos ==="
pkill -9 -f "nearRT-RIC" 2>/dev/null || true
pkill -9 -f "xapp_slicer" 2>/dev/null || true
pkill -9 -f "xapp_energy" 2>/dev/null || true
pkill -9 -f "ns3.42" 2>/dev/null || true
sleep 2

echo ""
echo "=== 1. Iniciando nearRT-RIC (E2AP v1) ==="
cd $BASE_DIR
$BASE_DIR/flexric/build_e2ap_v1/examples/ric/nearRT-RIC -c flexric/flexric.conf -p flexric_lib/ > "$GREENRAN_RIC_LOG" 2>&1 &
RIC_PID=$!
echo "RIC iniciado (PID: $RIC_PID)"
sleep 3

echo ""
echo "=== 2. Iniciando ns3 (scenario-zero com E2) ==="
cd $BASE_DIR/ns-O-RAN-flexric/mmwave-LENA-oran
$BASE_DIR/ns-O-RAN-flexric/mmwave-LENA-oran/build/scratch/ns3.42-scenario-zero-debug --e2TermIp=127.0.0.1 --e2lteEnabled=true --e2nrEnabled=true --simTime=120 > "$GREENRAN_NS3_LOG" 2>&1 &
NS3_PID=$!
echo "ns3 iniciado (PID: $NS3_PID)"
echo "Aguardando 10s para ns3 conectar..."
sleep 10

echo ""
echo "=== 3. Verificando se há nós E2 conectados ==="
if grep -q "E2 node connected" "$GREENRAN_RIC_LOG" || grep -q "SETUP-REQUEST" "$GREENRAN_NS3_LOG"; then
    echo "Nós E2 conectados!"
else
    echo "Aguardando mais 5s..."
    sleep 5
fi

echo ""
echo "=== 4. Iniciando xApp Slicer ==="
cd $BASE_DIR
$BASE_DIR/flexric/build_e2ap_v1/examples/xApp/c/xapp_slicer -c flexric/flexric.conf -p flexric_lib/ > "$GREENRAN_XAPP_SLICER_LOG" 2>&1 &
SLICER_PID=$!
echo "xApp Slicer iniciado (PID: $SLICER_PID)"
sleep 3

echo ""
echo "=== 5. Iniciando xApp Energy Saver ==="
$BASE_DIR/flexric/build_e2ap_v1/examples/xApp/c/xapp_energy_saver -c flexric/flexric.conf -p flexric_lib/ > "$GREENRAN_XAPP_ENERGY_LOG" 2>&1 &
ENERGY_PID=$!
echo "xApp Energy Saver iniciado (PID: $ENERGY_PID)"
sleep 3

echo ""
echo "=== Processos em execução ==="
ps aux | grep -E "(nearRT-RIC|xapp_slicer|xapp_energy|ns3.42)" | grep -v grep | grep -v defunct

echo ""
echo "=========================================="
echo "  Simulação em execução!"
echo "=========================================="
echo ""
echo "Para monitorar os logs:"
echo "  tail -f $GREENRAN_XAPP_SLICER_LOG   # xApp Slicer"
echo "  tail -f $GREENRAN_XAPP_ENERGY_LOG  # xApp Energy Saver"
echo "  tail -f $GREENRAN_NS3_LOG          # ns3"
echo "  tail -f $GREENRAN_RIC_LOG          # RIC"
echo ""
echo "Pressione Ctrl+C para encerrar"
echo "=========================================="

# Espera infinita
wait
