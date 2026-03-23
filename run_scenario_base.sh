#!/bin/bash

# =============================================================================
# GreenRAN Scenario Base - Script de Execução
# =============================================================================
# Cenário: 5 Torres + 10 UEs + 3 Câmaras
# 
# Parâmetros:
# - 5 torres mmWave (distribuição hexagonal)
# - 3 câmaras fixas (25 Mbps cada, latência < 100ms)
# - 10 UEs com mobilidade (50% parados, 40% andando, 10% veículos)
# - Tempo de simulação: 300s
#
# Autor: UFPA - GreenRAN Project
# =============================================================================

echo "=========================================="
echo "  GreenRAN Scenario Base - UFPA"
echo "=========================================="
echo ""

# Diretórios
BASE_DIR="/home/robert/orange_nuclear"
NS3_DIR="$BASE_DIR/ns-O-RAN-flexric/mmwave-LENA-oran"
RESULTS_DIR="$BASE_DIR/results"

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
echo "=== Criando diretório de resultados ==="
mkdir -p $RESULTS_DIR

echo ""
echo "=== 1. Iniciando nearRT-RIC (E2AP v1) ==="
cd $BASE_DIR
$BASE_DIR/flexric/build_e2ap_v1/examples/ric/nearRT-RIC -c flexric/flexric.conf -p flexric_lib/ > /tmp/ric.log 2>&1 &
RIC_PID=$!
echo "RIC iniciado (PID: $RIC_PID)"
sleep 3

echo ""
echo "=== 2. Iniciando ns3 (scenario-base) ==="
cd $NS3_DIR
$NS3_DIR/build/scratch/ns3.42-scenario-base-debug \
    --e2TermIp=127.0.0.1 \
    --e2lteEnabled=true \
    --e2nrEnabled=true \
    --simTime=60 \
    > /tmp/ns3_base.log 2>&1 &
NS3_PID=$!
echo "ns3 iniciado (PID: $NS3_PID)"
echo "Aguardando 5s para ns3 conectar..."
sleep 5

echo ""
echo "=== 3. Verificando se há nós E2 conectados ==="
if grep -q "E2 node connected" /tmp/ric.log || grep -q "SETUP-REQUEST" /tmp/ns3_base.log; then
    echo "Nós E2 conectados!"
else
    echo "Aguardando mais 5s..."
    sleep 5
fi

echo ""
echo "=== 4. Iniciando xApp Slicer ==="
cd $BASE_DIR
$BASE_DIR/flexric/build_e2ap_v1/examples/xApp/c/xapp_slicer -c flexric/flexric.conf -p flexric_lib/ > /tmp/xapp_slicer.log 2>&1 &
SLICER_PID=$!
echo "xApp Slicer iniciado (PID: $SLICER_PID)"
sleep 3

echo ""
echo "=== 5. Iniciando xApp Energy Saver ==="
$BASE_DIR/flexric/build_e2ap_v1/examples/xApp/c/xapp_energy_saver -c flexric/flexric.conf -p flexric_lib/ > /tmp/xapp_energy.log 2>&1 &
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
echo "Duração: 60 segundos (1 minuto)"
echo "Tempo de warmup: 5 segundos (métricas serão descartadas)"
echo ""
echo "Para monitorar os logs:"
echo "  tail -f /tmp/xapp_slicer.log    # xApp Slicer"
echo "  tail -f /tmp/xapp_energy.log   # xApp Energy Saver"
echo "  tail -f /tmp/ns3_base.log      # ns3"
echo "  tail -f /tmp/ric.log           # RIC"
echo ""
echo "Resultados serão salvos em:"
echo "  $RESULTS_DIR/"
echo ""
echo "Pressione Ctrl+C para encerrar"
echo "=========================================="

# Espera infinita
wait
