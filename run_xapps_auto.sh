#!/bin/bash
#
# =============================================================================
# GreenRAN xApp Auto-Restart Wrapper
# =============================================================================
# Wrapper que inicia e mantém os xApps rodando automaticamente
# Reinicia os xApps quando eles param (timeout de 600s)
#
# Uso: ./run_xapps_auto.sh [start|stop|status]
# =============================================================================

BASE_DIR="/home/robert/orange_nuclear"
RIC_DIR="$BASE_DIR/flexric/build_e2ap_v1"
LOG_DIR="/tmp"

PID_FILE="/tmp/xapps_wrapper.pid"

# Cores
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_ok() { echo -e "${GREEN}[OK]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Verificar se está rodando
is_running() {
    local pid=$1
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        return 0
    fi
    return 1
}

# Iniciar xApp com auto-restart
start_xapp() {
    local name=$1
    local binary=$2
    local logfile=$3
    local max_restarts=100
    local restart_count=0
    
    log_info "Iniciando $name com auto-restart..."
    
    while [ $restart_count -lt $max_restarts ]; do
        # Executar xApp
        export LD_LIBRARY_PATH="$RIC_DIR/src/ric:$BASE_DIR/flexric_lib:$RIC_DIR/src/xApp:$LD_LIBRARY_PATH"
        $binary > "$logfile" 2>&1 &
        local pid=$!
        
        log_info "$name iniciado (PID: $pid)"
        
        # Aguardar até o processo morrer
        while kill -0 "$pid" 2>/dev/null; do
            sleep 5
        done
        
        # Processo morreu
        local exit_code=$?
        restart_count=$((restart_count + 1))
        
        if [ $restart_count -lt $max_restarts ]; then
            log_warn "$name parou (tentativa $restart_count/$max_restarts). Reiniciando em 2s..."
            sleep 2
        else
            log_error "$name excedeu número máximo de reinicializações ($max_restarts)"
        fi
    done
}

# Status
status_xapps() {
    echo ""
    echo "=== Status dos xApps ==="
    echo ""
    
    # SLICER
    local slicer_pid=$(pgrep -f "xapp_slicer" | head -1)
    if [ -n "$slicer_pid" ] && kill -0 "$slicer_pid" 2>/dev/null; then
        echo -e "xApp-SLICER: ${GREEN}RODANDO${NC} (PID: $slicer_pid)"
    else
        echo -e "xApp-SLICER: ${RED}PARADO${NC}"
    fi
    
    # ENERGY
    local energy_pid=$(pgrep -f "xapp_energy" | head -1)
    if [ -n "$energy_pid" ] && kill -0 "$energy_pid" 2>/dev/null; then
        echo -e "xApp-ENERGY: ${GREEN}RODANDO${NC} (PID: $energy_pid)"
    else
        echo -e "xApp-ENERGY: ${RED}PARADO${NC}"
    fi
    
    echo ""
    
    # Cycles dos logs
    if [ -f "$LOG_DIR/xapp_slicer.log" ]; then
        local slicer_cycle=$(tail -50 "$LOG_DIR/xapp_slicer.log" 2>/dev/null | grep "Cycle:" | tail -1 | grep -oE "[0-9]+" || echo "N/A")
        echo "SLICER Cycles (último): $slicer_cycle"
    fi
    
    if [ -f "$LOG_DIR/xapp_energy.log" ]; then
        local energy_cycle=$(tail -50 "$LOG_DIR/xapp_energy.log" 2>/dev/null | grep "Cycle:" | tail -1 | grep -oE "[0-9]+" || echo "N/A")
        echo "ENERGY Cycles (último): $energy_cycle"
    fi
    
    echo ""
}

# Parar xApps
stop_xapps() {
    log_info "Parando xApps..."
    pkill -f "xapp_slicer" 2>/dev/null || true
    pkill -f "xapp_energy" 2>/dev/null || true
    sleep 1
    log_ok "xApps parados"
}

# MAIN
main() {
    local action=${1:-start}
    
    case $action in
        start)
            echo ""
            echo "=========================================="
            echo "  GreenRAN xApp Auto-Restart Wrapper"
            echo "=========================================="
            echo ""
            
            # Parar xApps antigos
            stop_xapps
            
            # Iniciar ambos em background
            start_xapp "SLICER" \
                "$RIC_DIR/examples/xApp/c/xapp_slicer" \
                "$LOG_DIR/xapp_slicer.log" &
            
            start_xapp "ENERGY" \
                "$RIC_DIR/examples/xApp/c/xapp_energy_saver" \
                "$LOG_DIR/xapp_energy.log" &
            
            echo ""
            log_ok "xApps iniciados em modo auto-restart!"
            echo ""
            echo "Para ver o status: ./run_xapps_auto.sh status"
            echo "Para parar: ./run_xapps_auto.sh stop"
            echo ""
            ;;
        stop)
            stop_xapps
            ;;
        status)
            status_xapps
            ;;
        *)
            echo "Uso: $0 [start|stop|status]"
            ;;
    esac
}

main "$@"
