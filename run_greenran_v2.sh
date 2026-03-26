#!/bin/bash
# GreenRAN Integrated Execution Script - V2 (LONG RUN MODE)
# ==========================================
# Este script inicia a coleta de longa duração (infinito)
# 
# ARQUITETURA:
#   - nearRT-RIC: gerencia conexões E2
#   - ns-3: simulador de rede
#   - csv_to_metrics: coleta métricas do simulador
#   - rApp: orquestrador que controla xApps
#     └── xApp SLICER: iniciado automaticamente pelo rApp (prioridade)
#     └── xApp ENERGY: iniciado pelo rApp quando permitido
# ==========================================

BASE_DIR="/home/robert/orange_nuclear"
cd $BASE_DIR

# 100.000 segundos (~27 horas de simulação)
SIM_TIME=100000

# Cores para o terminal
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${BLUE}=== [1/6] Limpando processos e arquivos antigos ===${NC}"

# Matar processos principais
pkill -9 -f "nearRT-RIC" 2>/dev/null || true
pkill -9 -f "xapp" 2>/dev/null || true
pkill -9 -f "ns3.42" 2>/dev/null || true
pkill -9 -f "python3.*rapp" 2>/dev/null || true
pkill -9 -f "csv_to_metrics" 2>/dev/null || true
pkill -9 -f "watchdog_xapps" 2>/dev/null || true
pkill -9 -f "rapp_dashboard" 2>/dev/null || true

# Limpar processos zumbis (defunct)
echo -e "${BLUE}    Limpando processos zumbis...${NC}"
ZOMBIES=$(ps aux | grep -E "defunct|zombie" | grep -v grep | awk '{print $2}' | wc -l)
if [ $ZOMBIES -gt 0 ]; then
    ps aux | grep -E "defunct|zombie" | grep -v grep | awk '{print $2}' | xargs -r kill -9 2>/dev/null || true
    echo -e "${GREEN}    $ZOMBIES processos zumbis eliminados${NC}"
fi

# Limpar arquivos PID antigos
echo -e "${BLUE}    Limpando arquivos PID antigos...${NC}"
rm -f /tmp/xapp_slicer.pid
rm -f /tmp/xapp_energy_saver.pid
rm -f /tmp/xapp_energy.pid
rm -f /tmp/ns3.pid
rm -f /tmp/ric.pid
rm -f /tmp/csv_metrics.pid
rm -f /tmp/rapp.pid
rm -f /tmp/dashboard.pid
rm -f /tmp/watchdog.pid

# Limpar sessões tmux se existirem
tmux kill-session -t greenran 2>/dev/null || true
tmux kill-session -t slicer 2>/dev/null || true
tmux kill-session -t energy 2>/dev/null || true

# Configura as bibliotecas (CRÍTICO para todos os processos)
export LD_LIBRARY_PATH=$BASE_DIR/flexric/build_e2ap_v1/src/ric:$BASE_DIR/flexric_lib:$BASE_DIR/flexric/build_e2ap_v1/src/xApp:$LD_LIBRARY_PATH

echo -e "${BLUE}=== [2/6] Verificando processos restantes ===${NC}"

# Verificar se ainda há processos rodando
sleep 2
REMAINING=$(ps aux | grep -E "nearRT-RIC|xapp_slicer|xapp_energy|ns3.42|rapp_orchestrator|csv_to_metrics" | grep -v grep | wc -l)
if [ $REMAINING -gt 0 ]; then
    echo -e "${RED}    AVISO: $REMAINING processos ainda rodando!${NC}"
    ps aux | grep -E "nearRT-RIC|xapp_slicer|xapp_energy|ns3.42|rapp_orchestrator|csv_to_metrics" | grep -v grep
    echo -e "${RED}    Tentando eliminar novamente...${NC}"
    pkill -9 -f "nearRT-RIC" 2>/dev/null || true
    pkill -9 -f "xapp_slicer" 2>/dev/null || true
    pkill -9 -f "xapp_energy" 2>/dev/null || true
    pkill -9 -f "ns3.42" 2>/dev/null || true
    pkill -9 -f "python3.*rapp" 2>/dev/null || true
    pkill -9 -f "csv_to_metrics" 2>/dev/null || true
    sleep 2
else
    echo -e "${GREEN}    Todos os processos foram eliminados!${NC}"
fi

# NÃO removemos mais o banco de dados (/tmp/rapp_data_lake.db)
# Apenas limpamos os arquivos de texto temporários
rm -f /tmp/xapp_intents/*.txt
rm -f /tmp/xapp_intents/*.json
rm -f /tmp/xapp_metrics/*.json
mkdir -p /tmp/xapp_intents /tmp/xapp_metrics
sleep 2

echo -e "${BLUE}=== [4/6] Iniciando nearRT-RIC ===${NC}"
nohup $BASE_DIR/flexric/build_e2ap_v1/examples/ric/nearRT-RIC -c $BASE_DIR/flexric/flexric.conf -p $BASE_DIR/flexric_lib/ > /tmp/ric.log 2>&1 &
sleep 3

echo -e "${BLUE}=== [5/6] Iniciando ns-3 (Scenario GreenRAN) ===${NC}"
cd $BASE_DIR/ns-O-RAN-flexric/mmwave-LENA-oran
# Forçamos o LD_LIBRARY_PATH aqui também para o ns-3
export LD_LIBRARY_PATH=$LD_LIBRARY_PATH
    nohup ./build/scratch/ns3.42-scenario-greenran-optimized --e2TermIp=127.0.0.1 > /tmp/ns3.log 2>&1 &
cd $BASE_DIR

# Aguarda o ns-3 estabelecer conexão E2
echo -e "${BLUE}    Aguardando conexão E2...${NC}"
for i in {1..30}; do
    if grep -q "E2setupResponse" /tmp/ns3.log 2>/dev/null; then
        echo -e "${GREEN}    Conexão E2 estabelecida!${NC}"
        break
    fi
    sleep 1
done

echo -e "${BLUE}=== [6/6] Iniciando Leitor de Métricas ===${NC}"
nohup python3 ./csv_to_metrics.py --input-dir ./ns-O-RAN-flexric/mmwave-LENA-oran --output /tmp/xapp_metrics/metrics.json > /tmp/csv_metrics.log 2>&1 &
sleep 2

# NÃO INICIAMOS xApps DIRETAMENTE!
# O rApp controla o ciclo de vida dos xApps:
#   - SLICER: iniciado automaticamente pelo rApp (prioridade)
#   - ENERGY: iniciado pelo rApp quando condições permitirem
echo -e "${BLUE}=== [7/6] xApps serao iniciados pelo rApp ===${NC}"
echo -e "${BLUE}    - xApp SLICER: iniciado com rApp (prioridade) ===${NC}"
echo -e "${BLUE}    - xApp ENERGY: ativado pelo rApp quando permitido ===${NC}"

echo -e "${BLUE}=== [8/6] Iniciando rApp Orchestrator (TREINAMENTO ML) ===${NC}"
nohup python3 ./rapp_orchestrator.py --synthetic 0 --interval 5 > /tmp/rapp.log 2>&1 &
sleep 3

# Aguarda rApp iniciar e reportar status
echo -e "${BLUE}    Aguardando rApp inicializar...${NC}"
for i in {1..10}; do
    if grep -q "xApp SLICER" /tmp/rapp.log 2>/dev/null; then
        echo -e "${GREEN}    rApp iniciou xApp SLICER!${NC}"
        break
    fi
    sleep 1
done

echo -e "${BLUE}=== Sistema GreenRAN ativo ===${NC}"

echo -e "\n${GREEN}==========================================${NC}"
echo -e "${GREEN}  MODO DE COLETA INFINITA ATIVADO!${NC}"
echo -e "${GREEN}==========================================${NC}"
echo -e "O rApp controla automaticamente os xApps:"
echo -e "  - SLICER: sempre ativo (prioridade)"
echo -e "  - ENERGY: ativado quando permitido"
echo -e "O sistema continuara rodando mesmo se voce fechar o terminal."
echo -e "Use './stop_all.sh' para parar manualmente."
echo -e "==========================================\n"
