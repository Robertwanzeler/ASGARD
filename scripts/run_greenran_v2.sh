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

. "$(dirname "$0")/core_runtime.sh"
load_greenran_runtime
create_greenran_run "greenran_v2" >/dev/null

register_app2_dashboard_when_ready() {
    local attempts=20
    local sleep_s=5
    local i

    for ((i=1; i<=attempts; i++)); do
        if python3 ./push/create_grafana_app2.py > "$STATE_DIR/create_grafana_app2.log" 2>&1; then
            echo -e "${GREEN}    Dashboard App2 registrado no Grafana${NC}"
            return 0
        fi
        sleep "$sleep_s"
    done

    echo -e "${RED}    Aviso: falha ao registrar dashboard App2 no Grafana${NC}"
    echo -e "${RED}    Verifique: $STATE_DIR/create_grafana_app2.log${NC}"
    return 1
}

cd $BASE_DIR

# Tempo de simulacao configuravel
SIM_TIME="$GREENRAN_SIM_TIME"

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
pkill -9 -f "app1_vigilancia/backend/app.py" 2>/dev/null || true
pkill -9 -f "app1_vigilancia/backend/simulate_cameras.py" 2>/dev/null || true
pkill -9 -f "app2_monitoramento/backend/app.py" 2>/dev/null || true
pkill -9 -f "app3_veicular/backend/app.py" 2>/dev/null || true
pkill -9 -f "simulate_sensors.py" 2>/dev/null || true
pkill -9 -f "push_stats_to_influx.py" 2>/dev/null || true
pkill -9 -f "push_cvar_to_influx.py" 2>/dev/null || true
pkill -9 -f "push_app1_to_influx.py" 2>/dev/null || true
pkill -9 -f "push_app2_to_influx.py" 2>/dev/null || true

# Limpar processos zumbis (defunct)
echo -e "${BLUE}    Limpando processos zumbis...${NC}"
ZOMBIES=$(ps aux | grep -E "defunct|zombie" | grep -v grep | awk '{print $2}' | wc -l)
if [ $ZOMBIES -gt 0 ]; then
    ps aux | grep -E "defunct|zombie" | grep -v grep | awk '{print $2}' | xargs -r kill -9 2>/dev/null || true
    echo -e "${GREEN}    $ZOMBIES processos zumbis eliminados${NC}"
fi

# Limpar arquivos PID antigos
echo -e "${BLUE}    Limpando arquivos PID antigos...${NC}"
rm -f "$GREENRAN_XAPP_SLICER_PID"
rm -f "$STATE_DIR/xapp_energy_saver.pid"
rm -f "$GREENRAN_XAPP_ENERGY_PID"
rm -f "$GREENRAN_NS3_PID"
rm -f "$GREENRAN_RIC_PID"
rm -f "$GREENRAN_CSV_PID"
rm -f "$GREENRAN_RAPP_PID"
rm -f "$GREENRAN_DASHBOARD_PID"
rm -f "$GREENRAN_WATCHDOG_PID"
rm -f "$GREENRAN_APP1_PID"
rm -f "$GREENRAN_APP1_SIMULATOR_PID"
rm -f "$GREENRAN_APP2_PID"
rm -f "$GREENRAN_APP2_SIMULATOR_PID"
rm -f "$GREENRAN_APP3_PID"
rm -f "$GREENRAN_PUSH_APP1_PID"
rm -f "$GREENRAN_PUSH_APP2_PID"

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
    pkill -9 -f "app1_vigilancia/backend/app.py" 2>/dev/null || true
    pkill -9 -f "app1_vigilancia/backend/simulate_cameras.py" 2>/dev/null || true
    pkill -9 -f "app2_monitoramento/backend/app.py" 2>/dev/null || true
    pkill -9 -f "app3_veicular/backend/app.py" 2>/dev/null || true
    pkill -9 -f "simulate_sensors.py" 2>/dev/null || true
    pkill -9 -f "push_stats_to_influx.py" 2>/dev/null || true
    pkill -9 -f "push_cvar_to_influx.py" 2>/dev/null || true
    pkill -9 -f "push_app1_to_influx.py" 2>/dev/null || true
    pkill -9 -f "push_app2_to_influx.py" 2>/dev/null || true
    sleep 2
else
    echo -e "${GREEN}    Todos os processos foram eliminados!${NC}"
fi

# NÃO removemos mais o banco de dados ($GREENRAN_DB_PATH)
# Apenas limpamos os arquivos de texto temporários
rm -f "$STATE_DIR"/xapp_intents/*.txt
rm -f "$STATE_DIR"/xapp_intents/*.json
rm -f "$STATE_DIR"/xapp_metrics/*.json
mkdir -p "$STATE_DIR/xapp_intents" "$STATE_DIR/xapp_metrics" "$STATE_DIR/app1_vigilancia" "$STATE_DIR/app2_monitoramento/sensors" "$STATE_DIR/app3_veicular/vehicles" "$STATE_DIR/app3_veicular/events"
sleep 2

echo -e "${BLUE}=== [3/9] Iniciando nearRT-RIC ===${NC}"
nohup $BASE_DIR/flexric/build_e2ap_v1/examples/ric/nearRT-RIC -c $BASE_DIR/flexric/flexric.conf -p $BASE_DIR/flexric_lib/ > "$GREENRAN_RIC_LOG" 2>&1 &
sleep 3

echo -e "${BLUE}=== [4/9] Iniciando ns-3 (Scenario GreenRAN - 1 hora) ===${NC}"
cd $BASE_DIR/ns-O-RAN-flexric/mmwave-LENA-oran
# Forçamos o LD_LIBRARY_PATH aqui também para o ns-3
export LD_LIBRARY_PATH=$LD_LIBRARY_PATH
    nohup ./build/scratch/ns3.42-scenario-greenran-optimized --e2TermIp=127.0.0.1 --simTime=$SIM_TIME > "$GREENRAN_NS3_LOG" 2>&1 &
cd $BASE_DIR

# Aguarda o ns-3 estabelecer conexão E2
echo -e "${BLUE}    Aguardando conexão E2...${NC}"
for i in {1..30}; do
    if grep -q "E2setupResponse" "$GREENRAN_NS3_LOG" 2>/dev/null; then
        echo -e "${GREEN}    Conexão E2 estabelecida!${NC}"
        break
    fi
    sleep 1
done

echo -e "${BLUE}=== [5/9] Iniciando Leitor de Métricas ===${NC}"
nohup python3 ./src/csv_to_metrics.py --input-dir ./ns-O-RAN-flexric/mmwave-LENA-oran --output "$STATE_DIR/xapp_metrics/metrics.json" --poll-interval "$GREENRAN_COLLECTOR_POLL_INTERVAL" > "$GREENRAN_CSV_LOG" 2>&1 &
sleep 2

# NÃO INICIAMOS xApps DIRETAMENTE!
# O rApp controla o ciclo de vida dos xApps:
#   - SLICER: iniciado automaticamente pelo rApp (prioridade)
#   - ENERGY: iniciado pelo rApp quando condições permitirem
echo -e "${BLUE}=== [6/9] xApps serao iniciados pelo rApp ===${NC}"
echo -e "${BLUE}    - xApp SLICER: iniciado com rApp (prioridade) ===${NC}"
echo -e "${BLUE}    - xApp ENERGY: ativado pelo rApp quando permitido ===${NC}"

echo -e "${BLUE}=== [7/9] Initiating rApp Orchestrator (ML TRAINING) ===${NC}"
nohup python3 ./src/rapp_orchestrator.py --synthetic "$GREENRAN_ORCHESTRATOR_SYNTHETIC_DAYS" --interval "$GREENRAN_ORCHESTRATOR_INTERVAL" > "$GREENRAN_RAPP_LOG" 2>&1 &
echo $! > "$GREENRAN_RAPP_PID"
sleep 3

# Aguarda rApp iniciar e reportar status
echo -e "${BLUE}    Aguardando rApp inicializar...${NC}"
for i in {1..10}; do
    if grep -q "xApp SLICER" "$GREENRAN_RAPP_LOG" 2>/dev/null; then
        echo -e "${GREEN}    rApp iniciou xApp SLICER!${NC}"
        break
    fi
    sleep 1
done

echo -e "${BLUE}=== [8/11] Iniciando Simulador de Câmeras da App1 ===${NC}"
mkdir -p "$STATE_DIR/app1_vigilancia/camera_sources"
nohup python3 ./apps/app1_vigilancia/backend/simulate_cameras.py --interval 60 --duration 2 --resolution 3840x2160 > "$GREENRAN_APP1_SIMULATOR_LOG" 2>&1 &
echo $! > "$GREENRAN_APP1_SIMULATOR_PID"
sleep 2
echo -e "${GREEN}    Simulador App1 iniciado; fontes em $STATE_DIR/app1_vigilancia/camera_sources${NC}"

echo -e "${BLUE}=== [9/11] Iniciando App1-Vigilancia ===${NC}"
nohup python3 ./apps/app1_vigilancia/backend/app.py --host "$APP1_HOST" --port "$APP1_PORT" > "$GREENRAN_APP1_LOG" 2>&1 &
echo $! > "$GREENRAN_APP1_PID"
sleep 2
echo -e "${GREEN}    App1 disponível em http://localhost:${APP1_PORT}${NC}"

echo -e "${BLUE}=== [10/12] Iniciando App2-Monitoramento ===${NC}"
APP2_SENSOR_SOURCE="${GREENRAN_APP2_SENSOR_SOURCE:-ns3}"
if [ "$APP2_SENSOR_SOURCE" = "mock" ]; then
    nohup python3 ./apps/app2_monitoramento/backend/simulate_sensors.py --num-sensors 17 --interval 5.0 > "$GREENRAN_APP2_SIMULATOR_LOG" 2>&1 &
    echo $! > "$GREENRAN_APP2_SIMULATOR_PID"
    sleep 2
    echo -e "${GREEN}    App2 usando simulador Python de sensores${NC}"
else
    rm -f "$GREENRAN_APP2_SIMULATOR_PID"
    echo -e "${GREEN}    App2 usando sensores reais exportados do ns-3${NC}"
fi
nohup python3 ./apps/app2_monitoramento/backend/app.py --host "$APP2_HOST" --port "$APP2_PORT" > "$GREENRAN_APP2_LOG" 2>&1 &
echo $! > "$GREENRAN_APP2_PID"
sleep 2
echo -e "${GREEN}    App2 disponível em http://localhost:${APP2_PORT}${NC}"

echo -e "${BLUE}=== [11/12] Iniciando App3-Veicular ===${NC}"
nohup python3 ./apps/app3_veicular/backend/app.py --host "$APP3_HOST" --port "$APP3_PORT" > "$GREENRAN_APP3_LOG" 2>&1 &
echo $! > "$GREENRAN_APP3_PID"
sleep 2
echo -e "${GREEN}    App3 disponível em http://localhost:${APP3_PORT}${NC}"

echo -e "${BLUE}=== [12/12] Iniciando Monitoramento (Grafana + InfluxDB) ===${NC}"
# Verificar se Docker está disponível
if command -v docker-compose &> /dev/null || docker compose version &> /dev/null; then
    # Verificar acesso ao Docker
    # Verificar Docker Compose (V1 usa "docker-compose", V2 usa "docker compose")
    if docker-compose --version &>/dev/null; then
        DOCKER_CMD="docker-compose"
        echo -e "${GREEN}    Docker Compose V1 detectado${NC}"
    elif docker compose version &>/dev/null; then
        DOCKER_CMD="docker compose"
        echo -e "${GREEN}    Docker Compose V2 detectado${NC}"
    else
        echo -e "${RED}    Docker Compose não encontrado!${NC}"
        echo -e "${YELLOW}    Instale com: sudo apt install docker-compose${NC}"
        exit 1
    fi
    
    cd $BASE_DIR/ns-O-RAN-flexric/mmwave-LENA-oran/GUI
    
    echo -e "${BLUE}    diretório: $(pwd)${NC}"
    
    # Parar serviços existentes (sem -v para preservar dados)
    echo -e "${BLUE}    Parando serviços existentes (preservando volumes)...${NC}"
    $DOCKER_CMD down 2>/dev/null || true
    sudo $DOCKER_CMD down 2>/dev/null || true
    
    # Iniciar serviços
    echo -e "${BLUE}    Iniciando serviços Docker...${NC}"
    if $DOCKER_CMD up -d; then
        echo -e "${GREEN}    ✓ Grafana iniciado em http://localhost:${GREENRAN_GRAFANA_PORT}${NC}"
        echo -e "${GREEN}    ✓ InfluxDB iniciado em http://${GREENRAN_INFLUXDB_HOST}:${GREENRAN_INFLUXDB_PORT}${NC}"
        echo -e "${GREEN}    ✓ GUI iniciado em http://localhost:${GREENRAN_GUI_PORT}${NC}"
    else
        echo -e "${RED}    ERRO ao iniciar Docker services${NC}"
        echo -e "${YELLOW}    Saída do erro:${NC}"
        $DOCKER_CMD up -d 2>&1 | head -20
        echo -e "${RED}    Tentando com sudo...${NC}"
        sudo $DOCKER_CMD down 2>/dev/null || true
        if sudo $DOCKER_CMD up -d; then
            echo -e "${GREEN}    ✓ Grafana iniciado com sudo em http://localhost:${GREENRAN_GRAFANA_PORT}${NC}"
            echo -e "${GREEN}    ✓ InfluxDB iniciado com sudo${NC}"
            echo -e "${GREEN}    ✓ GUI iniciado com sudo${NC}"
        else
            echo -e "${RED}    ERRO: Não foi possível iniciar Docker services${NC}"
            echo -e "${YELLOW}    Verifique se o Docker daemon está rodando:${NC}"
            echo -e "${YELLOW}    systemctl status docker${NC}"
        fi
    fi
    
    cd $BASE_DIR
    
    # Iniciar push de stats para InfluxDB
    sleep 5
    echo -e "${BLUE}    Iniciando Push Stats para InfluxDB...${NC}"
    nohup python3 ./push/push_stats_to_influx.py --interval "$GREENRAN_PUSH_INTERVAL" --host "$GREENRAN_INFLUXDB_HOST" --port "$GREENRAN_INFLUXDB_PORT" --db "$GREENRAN_INFLUXDB_DB" > "$GREENRAN_PUSH_STATS_LOG" 2>&1 &
    echo -e "${GREEN}    Push Stats iniciado (logs: $GREENRAN_PUSH_STATS_LOG)${NC}"
    
    # Iniciar push de CVaR para InfluxDB
    sleep 2
    echo -e "${BLUE}    Iniciando Push CVaR para InfluxDB...${NC}"
    nohup python3 ./push/push_cvar_to_influx.py --interval "$GREENRAN_PUSH_INTERVAL" > "$GREENRAN_PUSH_CVAR_LOG" 2>&1 &
    echo -e "${GREEN}    Push CVaR iniciado (logs: $GREENRAN_PUSH_CVAR_LOG)${NC}"

    # Iniciar push da App1 para InfluxDB
    sleep 2
    echo -e "${BLUE}    Iniciando Push App1 para InfluxDB...${NC}"
    nohup python3 ./push/push_app1_to_influx.py --interval "$GREENRAN_PUSH_INTERVAL" --host "$GREENRAN_INFLUXDB_HOST" --port "$GREENRAN_INFLUXDB_PORT" --db "$GREENRAN_INFLUXDB_DB" > "$GREENRAN_PUSH_APP1_LOG" 2>&1 &
    echo $! > "$GREENRAN_PUSH_APP1_PID"
    echo -e "${GREEN}    Push App1 iniciado (logs: $GREENRAN_PUSH_APP1_LOG)${NC}"

    # Iniciar push da App2 para InfluxDB
    sleep 2
    echo -e "${BLUE}    Iniciando Push App2 para InfluxDB...${NC}"
    nohup python3 ./push/push_app2_to_influx.py --interval "$GREENRAN_PUSH_INTERVAL" --host "$GREENRAN_INFLUXDB_HOST" --port "$GREENRAN_INFLUXDB_PORT" --db "$GREENRAN_INFLUXDB_DB" > "$GREENRAN_PUSH_APP2_LOG" 2>&1 &
    echo $! > "$GREENRAN_PUSH_APP2_PID"
    echo -e "${GREEN}    Push App2 iniciado (logs: $GREENRAN_PUSH_APP2_LOG)${NC}"

    # Criar/atualizar dashboard App1 no Grafana
    sleep 3
    echo -e "${BLUE}    Registrando dashboard App1 no Grafana...${NC}"
    if python3 ./push/create_grafana_app1.py > "$STATE_DIR/create_grafana_app1.log" 2>&1; then
        echo -e "${GREEN}    Dashboard App1 registrado no Grafana${NC}"
    else
        echo -e "${RED}    Aviso: falha ao registrar dashboard App1 no Grafana${NC}"
        echo -e "${RED}    Verifique: $STATE_DIR/create_grafana_app1.log${NC}"
    fi

    # Criar/atualizar dashboard App2 no Grafana
    sleep 1
    echo -e "${BLUE}    Registrando dashboard App2 no Grafana...${NC}"
    register_app2_dashboard_when_ready
else
    echo -e "${RED}    AVISO: Docker-compose não encontrado. Execute:${NC}"
    echo -e "${RED}    sudo apt install docker-compose${NC}"
fi

echo -e "${BLUE}=== [12/12] Iniciando Dashboard Python ===${NC}"
nohup python3 ./src/rapp_dashboard.py --host "$GREENRAN_DASHBOARD_HOST" --port "$GREENRAN_DASHBOARD_PORT" > "$GREENRAN_DASHBOARD_LOG" 2>&1 &
echo $! > "$GREENRAN_DASHBOARD_PID"
sleep 2
echo -e "${GREEN}    Dashboard disponível em http://localhost:${GREENRAN_DASHBOARD_PORT}${NC}"

echo -e "${BLUE}=== Sistema GreenRAN ativo ===${NC}"
snapshot_greenran_state

echo -e "\n${GREEN}==========================================${NC}"
echo -e "${GREEN}  SISTEMA GREENRAN INICIADO!${NC}"
echo -e "${GREEN}==========================================${NC}"
echo -e "Monitoramento:"
echo -e "  - Grafana:   http://localhost:${GREENRAN_GRAFANA_PORT} (admin/admin)"
echo -e "  - Dashboard: http://localhost:${GREENRAN_DASHBOARD_PORT}"
echo -e "  - App1:      http://localhost:${APP1_PORT}"
echo -e "  - App2:      http://localhost:${APP2_PORT}"
echo -e "  - App3:      http://localhost:${APP3_PORT}"
echo -e "  - GUI:       http://localhost:${GREENRAN_GUI_PORT}"
echo -e "  - Run Dir:   ${GREENRAN_RUN_DIR}"
echo -e ""
echo -e "O rApp controla automaticamente os xApps:"
echo -e "  - SLICER: sempre ativo (prioridade)"
echo -e "  - ENERGY: ativado quando permitido"
echo -e ""
echo -e "Use './stop_all.sh' para parar manualmente."
echo -e "==========================================\n"
