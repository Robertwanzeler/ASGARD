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
#   - (Opcional) rApp-Dashboard Flask
#   - (Opcional) Watchdog xApps
#
# Uso: 
#   ./run_greenran_complete.sh          # Sem dashboard
#   ./run_greenran_complete.sh --dashboard  # Com dashboard
#   ./run_greenran_complete.sh --watchdog    # Com watchdog
#   ./run_greenran_complete.sh --all        # Com tudo
# =============================================================================

set -e

. "$(dirname "$0")/core_runtime.sh"
load_greenran_runtime
create_greenran_run "greenran_complete" >/dev/null

# Cores
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Pastas
ORANGE_DIR="$BASE_DIR"

resolve_ric_binary() {
    local candidate
    for candidate in \
        "$RIC_DIR/examples/ric/nearRT-RIC" \
        "$BASE_DIR/flexric/build/examples/ric/nearRT-RIC"; do
        if [ -x "$candidate" ]; then
            echo "$candidate"
            return 0
        fi
    done
    return 1
}

resolve_xapp_binary() {
    local name="$1"
    find "$RIC_DIR/examples/xApp" -type f -name "$name" | sort | head -n 1
}

resolve_ns3_binary() {
    local candidates=(
        "$NS3_DIR/build/scratch/ns3.42-scenario-greenran-default"
        "$NS3_DIR/build/scratch/ns3.42-Energy_saving_with_cell_utilization_scenario-default"
        "$NS3_DIR/build/scratch/ns3.42-Energy_Saving_with_load_balancing_scenario-default"
        "$NS3_DIR/build/scratch/ns3.42-scenario-zero-default"
    )
    local candidate
    for candidate in "${candidates[@]}"; do
        if [ -x "$candidate" ]; then
            echo "$candidate"
            return 0
        fi
    done
    return 1
}

ensure_started() {
    local pid_file="$1"
    local name="$2"
    local log_file="$3"
    sleep 1
    if [ ! -f "$pid_file" ] || ! kill -0 "$(cat "$pid_file")" 2>/dev/null; then
        log_error "$name encerrou durante o startup"
        [ -f "$log_file" ] && tail -n 40 "$log_file"
        exit 1
    fi
}

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
    pkill -9 -f "rapp_dashboard" 2>/dev/null || true
    pkill -9 -f "watchdog_xapps" 2>/dev/null || true
    rm -f "$GREENRAN_DASHBOARD_PID" "$GREENRAN_WATCHDOG_PID" 2>/dev/null || true
    sleep 2
    log_success "Processos antigos parados"
}

# Limpar dados (DESATIVADO - dataset é persistente)
clean_data() {
    # NÃO APAGA DADOS - dataset é persistente entre execuções
    # Para limpar manualmente: rm $GREENRAN_DB_PATH
    mkdir -p "$STATE_DIR/xapp_metrics" "$STATE_DIR/xapp_intents" "$STATE_DIR/rapp_policies"
    log_info "Diretórios verificados (dataset persistente)"
}

# Mostrar status do dataset existente
show_dataset_status() {
    if [ -f "$GREENRAN_DB_PATH" ]; then
        local size=$(du -h "$GREENRAN_DB_PATH" | cut -f1)
        local records=$(python3 -c "import sqlite3; conn=sqlite3.connect('$GREENRAN_DB_PATH'); c=conn.cursor(); c.execute('SELECT COUNT(*) FROM extended_metrics'); print(c.fetchone()[0])" 2>/dev/null || echo "0")
        local period=$(python3 -c "import sqlite3; conn=sqlite3.connect('$GREENRAN_DB_PATH'); c=conn.cursor(); c.execute('SELECT MIN(timestamp), MAX(timestamp) FROM extended_metrics'); r=c.fetchone(); print(f'{int(r[1]-r[0])}')" 2>/dev/null || echo "0")
        local hours=$((period / 3600))
        local mins=$(((period % 3600) / 60))
        
        echo ""
        log_info "=========================================="
        log_info "  DATASET EXISTENTE ENCONTRADO"
        log_info "=========================================="
        echo "  Arquivo:  $GREENRAN_DB_PATH"
        echo "  Tamanho:  ${size}"
        echo "  Registros: ${records}"
        if [ "$period" -gt 0 ]; then
            echo "  Período:  ${hours}h ${mins}m de dados"
        fi
        echo ""
        echo -e "  ${YELLOW}Novos dados serão ADICIONADOS aos existentes${NC}"
        echo "  Para LIMPAR: rm $GREENRAN_DB_PATH"
        echo ""
        log_info "=========================================="
        echo ""
    else
        echo ""
        log_info "Iniciando NOVO dataset..."
        echo ""
    fi
}

# Verificar dependências
check_deps() {
    log_info "Verificando dependências..."

    local ric_bin
    local xapp_slicer_bin
    local xapp_energy_bin
    local ns3_bin
    ric_bin="$(resolve_ric_binary || true)"
    xapp_slicer_bin="$(resolve_xapp_binary xapp_slicer || true)"
    xapp_energy_bin="$(resolve_xapp_binary xapp_energy_saver || true)"
    ns3_bin="$(resolve_ns3_binary || true)"
    
    if [ -z "$ric_bin" ]; then
        log_error "nearRT-RIC não encontrado em $RIC_DIR/examples/ric/"
        exit 1
    fi
    
    if [ -z "$xapp_slicer_bin" ]; then
        log_error "xApp-SLICER não encontrado"
        exit 1
    fi
    if [ -z "$xapp_energy_bin" ]; then
        log_error "xApp-ENERGY não encontrado"
        exit 1
    fi
    
    if [ -z "$ns3_bin" ]; then
        log_error "ns-3 não encontrado"
        exit 1
    fi
    
    if [ ! -f "$ORANGE_DIR/src/csv_to_metrics.py" ]; then
        log_error "csv_to_metrics.py não encontrado"
        exit 1
    fi
    if [ ! -f "$ORANGE_DIR/src/rapp_orchestrator.py" ]; then
        log_error "rapp_orchestrator.py não encontrado"
        exit 1
    fi
    
    log_success "Todas dependências OK"
}

# Iniciar nearRT-RIC
start_ric() {
    log_info "Iniciando nearRT-RIC..."
    local ric_bin
    ric_bin="$(resolve_ric_binary)"
    cd "$ORANGE_DIR"
    export LD_LIBRARY_PATH="$RIC_DIR/src/ric:$FLEXRIC_LIB:$RIC_DIR/src/xApp:$LD_LIBRARY_PATH"
    "$ric_bin" -c "$ORANGE_DIR/flexric/flexric.conf" -p "$FLEXRIC_LIB/" > /tmp/ric.log 2>&1 &
    echo $! > /tmp/ric.pid
    sleep 3
    ensure_started /tmp/ric.pid "nearRT-RIC" /tmp/ric.log
    log_success "nearRT-RIC iniciado (PID: $(cat /tmp/ric.pid))"
}

# Iniciar ns-3
start_ns3() {
    log_info "Iniciando ns-3 (20 UEs)..."
    local ns3_bin
    ns3_bin="$(resolve_ns3_binary)"
    cd "$NS3_DIR"
    "$ns3_bin" --e2TermIp=127.0.0.1 --simTime=100000 > /tmp/ns3.log 2>&1 &
    echo $! > /tmp/ns3.pid
    sleep 2
    ensure_started /tmp/ns3.pid "ns-3" /tmp/ns3.log
    log_success "ns-3 iniciado (PID: $(cat /tmp/ns3.pid))"
}

# Iniciar xApps
start_xapps() {
    log_info "Iniciando xApp-SLICER..."
    local xapp_slicer_bin
    local xapp_energy_bin
    xapp_slicer_bin="$(resolve_xapp_binary xapp_slicer)"
    xapp_energy_bin="$(resolve_xapp_binary xapp_energy_saver)"
    cd "$ORANGE_DIR"
    export LD_LIBRARY_PATH="$RIC_DIR/src/ric:$FLEXRIC_LIB:$RIC_DIR/src/xApp:$LD_LIBRARY_PATH"
    "$xapp_slicer_bin" -c "$ORANGE_DIR/flexric/flexric.conf" -p "$FLEXRIC_LIB/" > /tmp/xapp_slicer.log 2>&1 &
    echo $! > /tmp/xapp_slicer.pid
    sleep 1
    ensure_started /tmp/xapp_slicer.pid "xApp-SLICER" /tmp/xapp_slicer.log
    
    log_info "Iniciando xApp-ENERGY..."
    "$xapp_energy_bin" -c "$ORANGE_DIR/flexric/flexric.conf" -p "$FLEXRIC_LIB/" > /tmp/xapp_energy.log 2>&1 &
    echo $! > /tmp/xapp_energy.pid
    sleep 2
    ensure_started /tmp/xapp_energy.pid "xApp-ENERGY" /tmp/xapp_energy.log
    log_success "xApps iniciados (SLICER: $(cat /tmp/xapp_slicer.pid), ENERGY: $(cat /tmp/xapp_energy.pid))"
}

# Iniciar coletores Python
start_collectors() {
    log_info "Iniciando csv_to_metrics..."
    cd "$ORANGE_DIR"
    "$GREENRAN_PYTHON_BIN" ./src/csv_to_metrics.py --input-dir "$NS3_DIR" --output "$STATE_DIR/xapp_metrics/metrics.json" --poll-interval "$GREENRAN_COLLECTOR_POLL_INTERVAL" > "$GREENRAN_CSV_LOG" 2>&1 &
    echo $! > "$GREENRAN_CSV_PID"
    sleep 1
    ensure_started "$GREENRAN_CSV_PID" "csv_to_metrics" "$GREENRAN_CSV_LOG"
    
    log_info "Iniciando rApp-ResourceOptimizer..."
    "$GREENRAN_PYTHON_BIN" ./src/rapp_orchestrator.py --interval "$GREENRAN_ORCHESTRATOR_INTERVAL" --synthetic "$GREENRAN_ORCHESTRATOR_SYNTHETIC_DAYS" > "$GREENRAN_RAPP_LOG" 2>&1 &
    echo $! > "$GREENRAN_RAPP_PID"
    sleep 1
    ensure_started "$GREENRAN_RAPP_PID" "rApp-ResourceOptimizer" "$GREENRAN_RAPP_LOG"
    log_success "Coletores iniciados (csv_to_metrics: $(cat "$GREENRAN_CSV_PID"), rApp: $(cat "$GREENRAN_RAPP_PID"))"
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
    
    # Dashboard e Watchdog
    local dashboard=$(ps aux | grep rapp_dashboard | grep -v grep | wc -l)
    local watchdog=$(ps aux | grep watchdog_xapps | grep -v grep | wc -l)
    echo ""
    echo -e "${YELLOW}[1b] COMPONENTES OPCIONAIS${NC}"
    echo "----------------------------------------"
    echo -e "  Dashboard Flask: $([ $dashboard -gt 0 ] && echo -e "${GREEN}✓ Rodando${NC}" || echo -e "${RED}✗ Parado${NC}")"
    echo -e "  Watchdog:       $([ $watchdog -gt 0 ] && echo -e "${GREEN}✓ Rodando${NC}" || echo -e "${RED}✗ Parado${NC}")"
    
    echo ""
    echo -e "${YELLOW}[2] DATA LAKE${NC}"
    echo "----------------------------------------"
    if [ -f "$GREENRAN_DB_PATH" ]; then
        local size=$(du -h "$GREENRAN_DB_PATH" | cut -f1)
        local records=$(python3 -c "import sqlite3; conn=sqlite3.connect('$GREENRAN_DB_PATH'); c=conn.cursor(); c.execute('SELECT COUNT(*) FROM extended_metrics'); print(c.fetchone()[0])" 2>/dev/null || echo "0")
        echo "  Arquivo: $GREENRAN_DB_PATH (${size})"
        echo "  Registros: ${records}"
    else
        echo -e "  ${RED}Data Lake não encontrado${NC}"
    fi
    
    echo ""
    echo -e "${YELLOW}[3] MÉTRICAS ATUAIS${NC}"
    echo "----------------------------------------"
    if [ -f "$STATE_DIR/xapp_metrics/extended_metrics.json" ]; then
        python3 << 'PYEOF' 2>/dev/null
import json
import os
with open(os.path.join(os.environ['GREENRAN_STATE_DIR'], 'xapp_metrics', 'extended_metrics.json')) as f:
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
    if [ -f "$STATE_DIR/rapp_policies/energy_policy.json" ]; then
        local e_status=$(python3 -c "import json,os; d=json.load(open(os.path.join(os.environ['GREENRAN_STATE_DIR'],'rapp_policies','energy_policy.json'))); print(d.get('status', 'N/A'))" 2>/dev/null || echo "N/A")
        local s_state=$(python3 -c "import json,os; d=json.load(open(os.path.join(os.environ['GREENRAN_STATE_DIR'],'rapp_policies','slice_policy.json'))); print(d.get('slicer_state', 'N/A'))" 2>/dev/null || echo "N/A")
        echo "  Energy Policy: ${e_status}"
        echo "  Slicer State: ${s_state}"
    else
        echo -e "  ${RED}Sem políticas geradas${NC}"
    fi
    
    echo ""
    
    # Verificar dashboard
    if [ -f "$GREENRAN_DASHBOARD_PID" ] && kill -0 $(cat "$GREENRAN_DASHBOARD_PID") 2>/dev/null; then
        echo -e "${CYAN}  Dashboard: http://localhost:${GREENRAN_DASHBOARD_PORT}${NC}"
    fi
    
    separator
    echo -e "${CYAN}Logs: $GREENRAN_RAPP_LOG | $GREENRAN_XAPP_SLICER_LOG | $GREENRAN_XAPP_ENERGY_LOG${NC}"
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

# Iniciar Dashboard Flask (opcional)
start_dashboard() {
    log_info "Iniciando rApp-Dashboard Flask..."
    cd "$ORANGE_DIR"
    python3 ./rapp_dashboard.py --host "$GREENRAN_DASHBOARD_HOST" --port "$GREENRAN_DASHBOARD_PORT" > "$GREENRAN_DASHBOARD_LOG" 2>&1 &
    echo $! > "$GREENRAN_DASHBOARD_PID"
    sleep 2
    
    if kill -0 $(cat "$GREENRAN_DASHBOARD_PID") 2>/dev/null; then
        log_success "Dashboard iniciado (PID: $(cat "$GREENRAN_DASHBOARD_PID"))"
        echo ""
        echo -e "${CYAN}  Dashboard disponível em: http://localhost:${GREENRAN_DASHBOARD_PORT}${NC}"
    else
        log_error "Falha ao iniciar dashboard. Verifique: python3 -m pip install flask"
    fi
}

# Iniciar Watchdog (opcional)
start_watchdog() {
    log_info "Iniciando Watchdog xApps..."
    cd "$ORANGE_DIR"
    python3 ./watchdog_xapps.py > "$GREENRAN_WATCHDOG_LOG" 2>&1 &
    echo $! > "$GREENRAN_WATCHDOG_PID"
    sleep 1
    log_success "Watchdog iniciado (PID: $(cat "$GREENRAN_WATCHDOG_PID"))"
}

# =============================================================================
# MAIN
# =============================================================================

main() {
    # Parse argumentos
    DASHBOARD=false
    WATCHDOG=false
    
    while [[ $# -gt 0 ]]; do
        case $1 in
            --dashboard|--all)
                DASHBOARD=true
                ;;
            --watchdog|--all)
                WATCHDOG=true
                ;;
        esac
        shift
    done
    
    clear
    separator
    echo -e "${CYAN}        GreenRAN O-RAN - Sistema Completo${NC}"
    separator
    echo ""
    
    echo "Opções selecionadas:"
    echo "  Dashboard: $DASHBOARD"
    echo "  Watchdog:  $WATCHDOG"
    echo ""
    
    # Verificar se já está rodando
    check_running
    
    # Parar processos antigos
    stop_all
    
    # Limpar/preparar dados (DESATIVADO - dataset é persistente)
    clean_data
    
    # Mostrar status do dataset existente
    show_dataset_status
    
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
    
    # Iniciar Dashboard se solicitado
    if [ "$DASHBOARD" = true ]; then
        start_dashboard
    fi
    
    # Iniciar Watchdog se solicitado
    if [ "$WATCHDOG" = true ]; then
        start_watchdog
    fi
    
    echo ""
    separator
    log_success "Sistema GreenRAN iniciado com sucesso!"
    separator
    
    if [ "$DASHBOARD" = true ]; then
        echo -e "${CYAN}  Dashboard: http://localhost:${GREENRAN_DASHBOARD_PORT}${NC}"
    fi
    echo -e "${CYAN}  Run Dir:   ${GREENRAN_RUN_DIR}${NC}"
    echo ""
    
    # Menu de monitoramento
    show_menu
}

# Tratamento de Ctrl+C
trap 'echo ""; log_info "Parando sistema..."; snapshot_greenran_state; stop_all; exit 0' INT

# Executar
main
