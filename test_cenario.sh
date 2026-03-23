#!/bin/bash
# GreenRAN - Teste Rápido de Cenário
# Testa se o cenário está gerando congestionamento
# ======================================

cd ~/orange_nuclear

echo "=========================================="
echo "  Teste Rápido: Cenário com Congestionamento"
echo "=========================================="
echo ""

# Limpar processos
pkill -9 -f "nearRT-RIC" 2>/dev/null || true
pkill -9 -f "xapp" 2>/dev/null || true
pkill -9 -f "ns3.42" 2>/dev/null || true
sleep 2

# LD_LIBRARY_PATH
export LD_LIBRARY_PATH=/home/robert/orange_nuclear/flexric/build_e2ap_v1/src/ric:/home/robert/orange_nuclear/flexric_lib:/home/robert/orange_nuclear/flexric/build_e2ap_v1/src/xApp:$LD_LIBRARY_PATH

# Iniciar RIC
echo "[1] Iniciando RIC..."
./flexric/build_e2ap_v1/examples/ric/nearRT-RIC -c ./flexric/flexric.conf -p ./flexric_lib/ > /tmp/ric_test.log 2>&1 &
sleep 5

# Iniciar ns-3 (simTime=60s para teste rápido)
echo "[2] Iniciando ns-3 (60s de teste)..."
cd ns-O-RAN-flexric/mmwave-LENA-oran
./build/scratch/ns3.42-scenario-greenran-debug \
    --e2TermIp=127.0.0.1 \
    --e2lteEnabled=true \
    --e2nrEnabled=true \
    --simTime=60 > /tmp/ns3_test.log 2>&1 &

echo "[3] Aguardando 30s para coleta de métricas..."
sleep 30

echo ""
echo "=========================================="
echo "  Verificando logs..."
echo "=========================================="

# Ver logs do ns-3
echo ""
echo "--- ns-3 Logs (últimas 30 linhas) ---"
tail -30 /tmp/ns3_test.log 2>/dev/null | grep -E "(CAMERA|UE-|CENÁRIO|PDCP|S1u|Rx)" | head -20

# Verificar se há rajadas
echo ""
echo "--- Verificando rajadas ---"
grep -c "CAMERA" /tmp/ns3_test.log 2>/dev/null && echo "Câmaras detectadas" || echo "Sem registro de câmaras"

echo ""
echo "=========================================="
echo "  Aguardando término da simulação..."
echo "=========================================="
echo ""
echo "Após 60s, o ns-3 irá finalizar automaticamente."
echo ""
echo "Para ver logs em tempo real:"
echo "  tail -f /tmp/ns3_test.log"
echo ""
echo "Para iniciar xApps após verificar o cenário:"
echo "  ./flexric/build_e2ap_v1/examples/xApp/c/xapp_slicer -c ./flexric/flexric.conf -p ./flexric_lib/"
echo "  ./flexric/build_e2ap_v1/examples/xApp/c/xapp_energy_saver -c ./flexric/flexric.conf -p ./flexric_lib/"
