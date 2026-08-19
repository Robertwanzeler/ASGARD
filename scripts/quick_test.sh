#!/bin/bash

# =============================================================================
# Execução Rápida de Teste
# =============================================================================
# Inicia tudo rapidamente para teste
# =============================================================================

BASE_DIR="/home/robert/orange_nuclear"

echo "=== Limpando processos ==="
pkill -9 -f "nearRT-RIC" 2>/dev/null
pkill -9 -f "xapp_slicer" 2>/dev/null
pkill -9 -f "xapp_energy" 2>/dev/null
pkill -9 -f "ns3.42" 2>/dev/null
sleep 2

export LD_LIBRARY_PATH=$BASE_DIR/flexric/build_e2ap_v1/src/ric:$BASE_DIR/flexric_lib:$BASE_DIR/flexric/build_e2ap_v1/src/xApp:$LD_LIBRARY_PATH

# Limpar logs
> /tmp/ric.log
> /tmp/ns3.log
> /tmp/xapp_slicer.log
> /tmp/xapp_energy.log

echo "=== 1. RIC ==="
cd $BASE_DIR
$BASE_DIR/flexric/build_e2ap_v1/examples/ric/nearRT-RIC -c flexric/flexric.conf -p flexric_lib/ > /tmp/ric.log 2>&1 &
sleep 3

echo "=== 2. ns3 ==="
cd $BASE_DIR/ns-O-RAN-flexric/mmwave-LENA-oran
./build/scratch/ns3.42-scenario-base-debug --e2TermIp=127.0.0.1 --e2lteEnabled=true --e2nrEnabled=true --simTime=60 > /tmp/ns3.log 2>&1 &
echo "ns3 rodando (aguarde 15s para conectar)..."
sleep 15

echo "=== 3. xApps ==="
cd $BASE_DIR
$BASE_DIR/flexric/build_e2ap_v1/examples/xApp/c/xapp_slicer -c flexric/flexric.conf -p flexric_lib/ > /tmp/xapp_slicer.log 2>&1 &
sleep 2
$BASE_DIR/flexric/build_e2ap_v1/examples/xApp/c/xapp_energy_saver -c flexric/flexric.conf -p flexric_lib/ > /tmp/xapp_energy.log 2>&1 &

echo ""
echo "=== Em 10 segundos, as métricas devem aparecer ==="
echo ""
sleep 10

echo "=== ÚLTIMAS MÉTRICAS - SLICER ==="
grep -E "CAMERA|UE |volume_dl|pacotes_dl|bitrate_dl|delay_dl|problema" /tmp/xapp_slicer.log | tail -10

echo ""
echo "=== ÚLTIMAS MÉTRICAS - ENERGY ==="
grep -E "CAMERA|UE |volume_dl|pacotes_dl|bitrate_dl|delay_dl|ENERGY" /tmp/xapp_energy.log | tail -10

echo ""
echo "Para continuar monitorando:"
echo "  tail -f /tmp/xapp_slicer.log"
echo "  tail -f /tmp/xapp_energy.log"
