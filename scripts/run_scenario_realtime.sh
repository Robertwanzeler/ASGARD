#!/bin/bash

# =============================================================================
# GreenRAN Scenario Base - Script de Execução com Logs em Tempo Real
# =============================================================================
# Launcher legado com logs em tempo real.
# O baseline canônico do cenário vem de config/greenran_fixed_scenario.json.
#
# Uso: ./run_scenario_realtime.sh [opções]
#   -s, --short    Rodar simulação curta (60s) para teste
#   -v, --verbose   Modo verboso (mostra todos os logs)
#   -h, --help      Mostra esta ajuda
#
# Autor: UFPA - GreenRAN Project
# =============================================================================

. "$(dirname "$0")/core_runtime.sh"
load_greenran_runtime

# Processar argumentos
SHORT_SIM=false
VERBOSE=false

while [[ $# -gt 0 ]]; do
    case $1 in
        -s|--short)
            SHORT_SIM=true
            shift
            ;;
        -v|--verbose)
            VERBOSE=true
            shift
            ;;
        -h|--help)
            echo "Uso: $0 [opções]"
            echo "  -s, --short    Rodar simulação curta (60s) para teste"
            echo "  -v, --verbose   Modo verboso"
            exit 0
            ;;
        *)
            echo "Opção desconhecida: $1"
            exit 1
            ;;
    esac
done

echo "=========================================="
echo "  GreenRAN Scenario Base - UFPA"
echo "  Modo Tempo Real"
echo "=========================================="
echo ""
print_greenran_fixed_scenario_summary
echo ""

# Diretórios
NS3_DIR="$BASE_DIR/ns-O-RAN-flexric/mmwave-LENA-oran"
RESULTS_DIR="$BASE_DIR/results"

# Configura LD_LIBRARY_PATH
export LD_LIBRARY_PATH=$BASE_DIR/flexric/build_e2ap_v1/src/ric:$BASE_DIR/flexric_lib:$BASE_DIR/flexric/build_e2ap_v1/src/xApp:$LD_LIBRARY_PATH

# Tempo de simulação
if [ "$SHORT_SIM" = true ]; then
    SIM_TIME=60
else
    SIM_TIME=600
fi

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

# Limpar logs antigos
: > "$GREENRAN_RIC_LOG"
: > "$GREENRAN_NS3_LOG"
: > "$GREENRAN_XAPP_SLICER_LOG"
: > "$GREENRAN_XAPP_ENERGY_LOG"

echo ""
echo "=== 1. Iniciando nearRT-RIC (E2AP v1) ==="
cd $BASE_DIR
stdbuf -oL -eL nohup ./flexric/build_e2ap_v1/examples/ric/nearRT-RIC \
    -c flexric/flexric.conf \
    -p flexric_lib/ \
    > "$GREENRAN_RIC_LOG" 2>&1 &
RIC_PID=$!
echo "RIC iniciado (PID: $RIC_PID)"
sleep 3

echo ""
echo "=== 2. Iniciando ns3 (scenario-base) ==="
cd $NS3_DIR
stdbuf -oL -eL nohup ./build/scratch/ns3.42-scenario-base-debug \
    --e2TermIp=127.0.0.1 \
    --e2lteEnabled=true \
    --e2nrEnabled=true \
    --simTime=$SIM_TIME \
    > "$GREENRAN_NS3_LOG" 2>&1 &
NS3_PID=$!
echo "ns3 iniciado (PID: $NS3_PID)"

# Aguardar warmup (90s) + tempo extra para conexão (20s) = 110s
# Isso evita timeout dos xApps pois haverá métricas sendo enviadas
WARMUP_WAIT=110
echo "Aguardando ${WARMUP_WAIT}s para warmup + conexão..."
sleep $WARMUP_WAIT

echo ""
echo "=== 3. Verificando se há nós E2 conectados ==="
CONNECTED=false
for i in {1..3}; do
    if grep -q "E2 node connected\|SETUP-REQUEST\|SETUP-RESPONSE\|Registered E2 Nodes" "$GREENRAN_RIC_LOG" 2>/dev/null; then
        CONNECTED=true
        break
    fi
    echo "Aguardando... ($i/3)"
    sleep 2
done

if [ "$CONNECTED" = true ]; then
    echo "Nós E2 conectados!"
else
    echo "AVISO: Verifique a conexão E2 manualmente"
fi

echo ""
echo "=== 4. Verificando se há tráfego (métricas KPM) ==="
TRAFFIC=false
for i in {1..5}; do
    if grep -q "volume_dl\|bitrate_dl" "$GREENRAN_NS3_LOG" 2>/dev/null; then
        TRAFFIC=true
        break
    fi
    echo "Aguardando tráfego... ($i/5)"
    sleep 2
done

if [ "$TRAFFIC" = true ]; then
    echo "Tráfego detectado! Iniciando xApps..."
else
    echo "AVISO: Pouco tráfego detectado, xApps podem ter timeout"
fi

echo ""
echo "=== 5. Iniciando xApp Slicer ==="
cd $BASE_DIR
stdbuf -oL -eL nohup ./flexric/build_e2ap_v1/examples/xApp/c/xapp_slicer \
    -c flexric/flexric.conf \
    -p flexric_lib/ \
    > "$GREENRAN_XAPP_SLICER_LOG" 2>&1 &
SLICER_PID=$!
echo "xApp Slicer iniciado (PID: $SLICER_PID)"
sleep 15

echo ""
echo "=== 6. Iniciando xApp Energy Saver ==="
stdbuf -oL -eL nohup ./flexric/build_e2ap_v1/examples/xApp/c/xapp_energy_saver \
    -c flexric/flexric.conf \
    -p flexric_lib/ \
    > "$GREENRAN_XAPP_ENERGY_LOG" 2>&1 &
ENERGY_PID=$!
echo "xApp Energy Saver iniciado (PID: $ENERGY_PID)"
sleep 10

echo ""
echo "=========================================="
echo "  Simulação em execução!"
echo "=========================================="
echo ""
echo "Duração: $SIM_TIME segundos (10 minutos)"
echo "Warmup: 90 segundos"
echo ""

# Função para monitorar logs
monitor_logs() {
    echo ""
    echo ">>> MONITORANDO LOGS EM TEMPO REAL <<<"
    echo "Pressione Ctrl+C para parar o monitoramento e encerrar"
    echo ""
    
    # Cores para terminal
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    YELLOW='\033[1;33m'
    BLUE='\033[0;34m'
    NC='\033[0m' # No Color
    
    while true; do
        # Verificar se processos estão rodando
        if ! kill -0 $NS3_PID 2>/dev/null; then
            echo -e "${RED}>>> ns3 FINALIZOU <<<${NC}"
            break
        fi
        
        # Mostrar última linha de cada log
        echo -e "${BLUE}=== SLICER (últimas métricas) ===${NC}"
        grep -E "CAMERA|UE |volume_dl|pacotes_dl|bitrate_dl|delay_dl|problema|acao" "$GREENRAN_XAPP_SLICER_LOG" 2>/dev/null | tail -5
        
        echo ""
        echo -e "${GREEN}=== ENERGY SAVER (últimas métricas) ===${NC}"
        grep -E "CAMERA|UE |volume_dl|pacotes_dl|bitrate_dl|delay_dl|ENERGY|acao" "$GREENRAN_XAPP_ENERGY_LOG" 2>/dev/null | tail -5
        
        echo ""
        echo "---"
        sleep 3
    done
}

# Oferecer opção de monitoramento
echo "Deseja monitorar os logs em tempo real? (s/n)"
read -r resposta

if [[ "$resposta" =~ ^[sS]$ ]]; then
    monitor_logs
else
    echo ""
    echo "Para monitorar manualmente, use:"
    echo "  ./monitor_logs.sh"
    echo ""
    echo "Ou em terminais separados:"
    echo "  tail -f $GREENRAN_XAPP_SLICER_LOG"
    echo "  tail -f $GREENRAN_XAPP_ENERGY_LOG"
    echo "  tail -f $GREENRAN_NS3_LOG"
fi

# Espera final
echo ""
echo "Aguardando simulação terminar..."
wait

echo ""
echo "=== Simulação concluída ==="
echo "Logs salvos em:"
echo "  $GREENRAN_RIC_LOG"
echo "  $GREENRAN_NS3_LOG"
echo "  $GREENRAN_XAPP_SLICER_LOG"
echo "  $GREENRAN_XAPP_ENERGY_LOG"
