#!/bin/bash
#
# =============================================================================
# GreenRAN O-RAN - Script de Execução Completa
# =============================================================================
# Inicia todos os componentes do sistema GreenRAN:
#   - nearRT-RIC
#   - ns-3 (20 UEs)
#   - xApp-SLICER
#   - xApp-ENERGY
#   - csv_to_metrics
#   - rApp-ResourceOptimizer
#
# Uso: ./run_greenran_complete.sh
# =============================================================================

set -e

# Cores
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Pastas
ORANGE_DIR="/home/robert/orange_nuclear"
NS3_DIR="$ORANGE_DIR/ns-O-RAN-flexric/mmwave-LENA-oran"
RIC_DIR="$ORANGE_DIR/flexric/build_e2ap_v1"
FLEXRIC_LIB="$ORANGE_DIR/flexric_lib"

# Funções
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[OK]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

separator() {
    echo "================================================================"
}

# Verificar se está rodando
check_running() {
    local count=$(ps aux | grep -E "nearRT-RIC|ns3.42|xapp_slicer|xapp_energy" | grep -v grep | wc -l)
    if [ $count -gt 0 ]; then
        log_warning "Já existe(n) $count processo(s) rodando!"
        echo "Deseja continuar? (Ctrl+C para cancelar, Enter para parar processos antigos)"
        read
        stop_all
    fi
}

# Parar todos os processos
stop_all() {
    log_info "Parando processos antigos..."
    pkill -9 -f "xapp_slicer" 2>/dev/null || true
    pkill -9 -f "xapp_energy" 2>/dev/null || true
    pkill -9 -f "nearRT-RIC" 2>/dev/null || true
    pkill -9 -f "ns3.42-scenario" 2>/dev/null || true
    pkill -9 -f "csv_to_metrics" 2>/dev/null || true
    pkill -9 -f "rapp_orchestrator" 2>/dev/null || true
    sleep 2
    log_success "Processos antigos parados"
}

# Limpar dados antigos
clean_data() {
    log_info "Limpando dados antigos..."
    rm -f /tmp/rapp_data_lake.db 2>/dev/null || true
    rm -f "$NS3_DIR"/*.txt 2>/dev/null || true
    rm -f /tmp/xapp_metrics/*.json 2>/dev/null || true
    rm -f /tmp/rapp_policies/*.json 2>/dev/null || true
    mkdir -p /tmp/xapp_metrics /tmp/xapp_intents /tmp/rapp_policies
    log_success "Dados limpos"
}

# Verificar dependências
check_deps() {
    log_info "Verificando dependências..."
    
    # nearRT-RIC
    if [ ! -f "$RIC_DIR/examples/ric/nearRT-RIC" ]; then
        log_error "nearRT-RIC não encontrado em $RIC_DIR/examples/ric/"
        exit 1
    fi
    
    # xApps
    if [ ! -f "$RIC_DIR/examples/xApp/c/xapp_slicer" ]; then
        log_error "xApp-SLICER não encontrado"
        exit 1
    fi
    if [ ! -f "$RIC_DIR/examples/xApp/c/xapp_energy_saver" ]; then
        log_error "xApp-ENERGY não encontrado"
        exit 1
    fi
    
    # ns-3
    if [ ! -f "$NS3_DIR/build_rebuild/scratch/ns3.42-scenario-greenran-debug" ]; then
        log_error "ns-3 não encontrado"
        exit 1
    fi
    
    # Python scripts
    if [ ! -f "$ORANGE_DIR/csv_to_metrics.py" ]; then
        log_error "csv_to_metrics.py não encontrado"
        exit 1
    fi
    if [ ! -f "$ORANGE_DIR/rapp_orchestrator.py" ]; then
        log_error "rapp_orchestrator.py não encontrado"
        exit 1
    fi
    
    log_success "Todas dependências OK"
}

# Iniciar nearRT-RIC
start_ric() {
    log_info "Iniciando nearRT-RIC..."
    cd "$ORANGE_DIR"
    export LD_LIBRARY_PATH="$RIC_DIR/src/ric:$FLEXRIC_LIB:$RIC_DIR/src/xApp:$LD_LIBRARY_PATH"
    $RIC_DIR/examples/ric/nearRT-RIC -c "$ORANGE_DIR/flexric/flexric.conf" -p "$FLEXRIC_LIB/" > /tmp/ric.log 2>&1 &
    echo $! > /tmp/ric.pid
    sleep 3
    log_success "nearRT-RIC iniciado (PID: $(cat /tmp/ric.pid))"
}

# Iniciar ns-3
start_ns3() {
    log_info "Iniciando ns-3 (20 UEs)..."
    cd "$NS3_DIR"
    ./build_rebuild/scratch/ns3.42-scenario-greenran-debug --e2TermIp=127.0.0.1 > /tmp/ns3.log 2>&1 &
    echo $! > /tmp/ns3.pid
    sleep 2
    log_success "ns-3 iniciado (PID: $(cat /tmp/ns3.pid))"
}

# Iniciar xApps
start_xapps() {
    log_info "Iniciando xApp-SLICER..."
    cd "$ORANGE_DIR"
    export LD_LIBRARY_PATH="$RIC_DIR/src/ric:$FLEXRIC_LIB:$RIC_DIR/src/xApp:$LD_LIBRARY_PATH"
    $RIC_DIR/examples/xApp/c/xapp_slicer > /tmp/xapp_slicer.log 2>&1 &
    echo $! > /tmp/xapp_slicer.pid
    sleep 1
    
    log_info "Iniciando xApp-ENERGY..."
    $RIC_DIR/examples/xApp/c/xapp_energy_saver > /tmp/xapp_energy.log 2>&1 &
    echo $! > /tmp/xapp_energy.pid
    sleep 2
    log_success "xApps iniciados (SLICER: $(cat /tmp/xapp_slicer.pid), ENERGY: $(cat /tmp/xapp_energy.pid))"
}

# Iniciar coletores Python
start_collectors() {
    log_info "Iniciando csv_to_metrics..."
    cd "$ORANGE_DIR"
    python3 ./csv_to_metrics.py --input-dir "$NS3_DIR" --output /tmp/xapp_metrics/metrics.json --poll-interval 1 > /tmp/csv_metrics.log 2>&1 &
    echo $! > /tmp/csv_metrics.pid
    sleep 1
    
    log_info "Iniciando rApp-ResourceOptimizer..."
    python3 ./rapp_orchestrator.py --interval 5 > /tmp/rapp.log 2>&1 &
    echo $! > /tmp/rapp.pid
    sleep 1
    log_success "Coletores iniciados (csv_to_metrics: $(cat /tmp/csv_metrics.pid), rApp: $(cat /tmp/rapp.pid))"
}

# Status do sistema
show_status() {
    separator
    echo -e "${CYAN}              GreenRAN O-RAN - Status do Sistema${NC}"
    separator
    
    echo ""
    echo -e "${YELLOW}[1] PROCESSOS${NC}"
    echo "----------------------------------------"
    local ric=$(ps aux | grep nearRT-RIC | grep -v grep | wc -l)
    local ns3=$(ps aux | grep "ns3.42-scenario" | grep -v grep | wc -l)
    local slicer=$(ps aux | grep xapp_slicer | grep -v grep | wc -l)
    local energy=$(ps aux | grep xapp_energy | grep -v grep | wc -l)
    local csv=$(ps aux | grep csv_to_metrics | grep -v grep | wc -l)
    local rapp=$(ps aux | grep rapp_orchestrator | grep -v grep | wc -l)
    
    echo -e "  nearRT-RIC:      $([ $ric -gt 0 ] && echo -e "${GREEN}✓ Rodando${NC}" || echo -e "${RED}✗ Parado${NC}")"
    echo -e "  ns-3 (20 UEs):  $([ $ns3 -gt 0 ] && echo -e "${GREEN}✓ Rodando${NC}" || echo -e "${RED}✗ Parado${NC}")"
    echo -e "  xApp-SLICER:    $([ $slicer -gt 0 ] && echo -e "${GREEN}✓ Rodando${NC}" || echo -e "${RED}✗ Parado${NC}")"
    echo -e "  xApp-ENERGY:    $([ $energy -gt 0 ] && echo -e "${GREEN}✓ Rodando${NC}" || echo -e "${RED}✗ Parado${NC}")"
    echo -e "  csv_to_metrics:  $([ $csv -gt 0 ] && echo -e "${GREEN}✓ Rodando${NC}" || echo -e "${RED}✗ Parado${NC}")"
    echo -e "  rApp:           $([ $rapp -gt 0 ] && echo -e "${GREEN}✓ Rodando${NC}" || echo -e "${RED}✗ Parado${NC}")"
    
    echo ""
    echo -e "${YELLOW}[2] DATA LAKE${NC}"
    echo "----------------------------------------"
    if [ -f /tmp/rapp_data_lake.db ]; then
        local size=$(du -h /tmp/rapp_data_lake.db | cut -f1)
        local records=$(python3 -c "import sqlite3; conn=sqlite3.connect('/tmp/rapp_data_lake.db'); c=conn.cursor(); c.execute('SELECT COUNT(*) FROM extended_metrics'); print(c.fetchone()[0])" 2>/dev/null || echo "0")
        echo "  Arquivo: /tmp/rapp_data_lake.db (${size})"
        echo "  Registros: ${records}"
    else
        echo -e "  ${RED}Data Lake não encontrado${NC}"
    fi
    
    echo ""
    echo -e "${YELLOW}[3] MÉTRICAS ATUAIS${NC}"
    echo "----------------------------------------"
    if [ -f /tmp/xapp_metrics/extended_metrics.json ]; then
        python3 << 'PYEOF' 2>/dev/null
import json
with open('/tmp/xapp_metrics/extended_metrics.json') as f:
    d = json.load(f)
    gm = d.get('global_metrics', {})
    lat = gm.get('global_avg_latency_us', 0) / 1000
    worst = gm.get('global_worst_latency_us', 0) / 1000
    tp = gm.get('throughput_kbps', 0) / 1000
    ues = gm.get('total_active_ues', 0)
    print(f"  Latência Média: {lat:.1f} ms")
    print(f"  Latência Pior:  {worst:.1f} ms")
    print(f"  Throughput:      {tp:.1f} Mbps")
    print(f"  UEs Ativas:     {ues}")
PYEOF
    else
        echo -e "  ${RED}Sem dados de métricas${NC}"
    fi
    
    echo ""
    echo -e "${YELLOW}[4] POLÍTICAS DO RAPP${NC}"
    echo "----------------------------------------"
    if [ -f /tmp/rapp_policies/energy_policy.json ]; then
        local e_status=$(python3 -c "import json; d=json.load(open('/tmp/rapp_policies/energy_policy.json')); print(d.get('status', 'N/A'))" 2>/dev/null || echo "N/A")
        local s_state=$(python3 -c "import json; d=json.load(open('/tmp/rapp_policies/slice_policy.json')); print(d.get('slicer_state', 'N/A'))" 2>/dev/null || echo "N/A")
        echo "  Energy Policy: ${e_status}"
        echo "  Slicer State: ${s_state}"
    else
        echo -e "  ${RED}Sem políticas geradas${NC}"
    fi
    
    echo ""
    separator
    echo -e "${CYAN}Logs: /tmp/rapp.log | /tmp/xapp_slicer.log | /tmp/xapp_energy.log${NC}"
    separator
}

# Menu interativo
show_menu() {
    clear
    show_status
    echo ""
    echo -e "${GREEN}Pressione Enter para atualizar ou Ctrl+C para sair...${NC}"
    read
    show_menu
}

# =============================================================================
# MAIN
# =============================================================================

main() {
    clear
    separator
    echo -e "${CYAN}        GreenRAN O-RAN - Sistema Completo${NC}"
    separator
    echo ""
    
    # Verificar se já está rodando
    check_running
    
    # Parar processos antigos
    stop_all
    
    # Limpar dados
    clean_data
    
    # Verificar dependências
    check_deps
    
    echo ""
    separator
    echo -e "${GREEN}           INICIANDO SISTEMA${NC}"
    separator
    
    # Iniciar componentes
    start_ric
    start_ns3
    start_xapps
    start_collectors
    
    echo ""
    separator
    log_success "Sistema GreenRAN iniciado com sucesso!"
    separator
    
    # Menu de monitoramento
    show_menu
}

# Tratamento de Ctrl+C
trap 'echo ""; log_info "Parando sistema..."; stop_all; exit 0' INT

# Executar
main
