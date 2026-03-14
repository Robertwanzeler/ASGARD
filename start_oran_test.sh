#!/bin/bash

echo "=========================================="
echo "  O-RAN E2 Test - Scenario Setup"
echo "=========================================="

# Verifica se o RIC está compilado
if [ ! -f "flexric/build_e2ap_v1/examples/ric/nearRT-RIC" ]; then
    echo "ERRO: nearRT-RIC não encontrado em flexric/build_e2ap_v1/"
    exit 1
fi

# Verifica se o ns3 está compilado
if [ ! -f "ns-O-RAN-flexric/mmwave-LENA-oran/build/scratch/ns3.42-scenario-zero-default" ]; then
    echo "ERRO: ns3 não encontrado"
    exit 1
fi

echo ""
echo "=== 1. Iniciando nearRT-RIC (E2AP v1) ==="
flexric/build_e2ap_v1/examples/ric/nearRT-RIC > /tmp/ric.log 2>&1 &
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
echo "=== 2. Iniciando xApp Slicer ==="
flexric/build_e2ap_v1/examples/xApp/c/xapp_slicer > /tmp/xapp_slicer.log 2>&1 &
SLICER_PID=$!
echo "Slicer PID: $SLICER_PID"
sleep 3

echo ""
echo "=== 3. Iniciando xApp Energy Saver ==="
flexric/build_e2ap_v1/examples/xApp/c/xapp_energy_saver > /tmp/xapp_energy.log 2>&1 &
ENERGY_PID=$!
echo "Energy Saver PID: $ENERGY_PID"
sleep 3

echo ""
echo "=== 4. Verificando processos ==="
ps aux | grep -E "(nearRT-RIC|xapp_slicer|xapp_energy)" | grep -v grep

echo ""
echo "=== 5. Verificando logs dos xApps ==="
echo "--- xApp Slicer (últimas 20 linhas) ---"
tail -20 /tmp/xapp_slicer.log

echo ""
echo "--- xApp Energy Saver (últimas 20 linhas) ---"
tail -20 /tmp/xapp_energy.log

echo ""
echo "=========================================="
echo "  Para iniciar o ns3, execute em outro terminal:"
echo "  cd ns-O-RAN-flexric/mmwave-LENA-oran"
echo "  ./build/scratch/ns3.42-scenario-zero-default --e2TermIp=127.0.0.1"
echo "=========================================="
echo ""
echo "Para monitorar os logs:"
echo "  tail -f /tmp/xapp_slicer.log"
echo "  tail -f /tmp/xapp_energy.log"
echo "  tail -f /tmp/ric.log"

# Espera infinita
wait
