#!/bin/bash
#
# =============================================================================
# GreenRAN xApp Auto-Restart Wrapper - VERSÃO MELHORADA
# =============================================================================
# Wrapper que inicia e mantém os xApps rodando automaticamente
# Reinicia os xApps quando eles param (timeout de 600s)
#
# Melhorias:
# - Log de restarts em /tmp/xapp_restarts.log
# - Health tracking em /tmp/xapp_health.json
# - Detecção de tipo de falha (timeout vs erro)
# - Heartbeat para monitoramento
#
# Uso: ./run_xapps_auto.sh [start|stop|status|health]
# =============================================================================

BASE_DIR="/home/robert/orange_nuclear"
RIC_DIR="$BASE_DIR/flexric/build_e2ap_v1"
LOG_DIR="/tmp"

PID_FILE="/tmp/xapps_wrapper.pid"
RESTART_LOG="/tmp/xapp_restarts.log"
HEALTH_FILE="/tmp/xapp_health.json"
HEARTBEAT_INTERVAL=30
HEARTBEAT_TIMEOUT=90

# Cores
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

# =============================================================================
# FUNÇÕES DE LOG
# =============================================================================

log_info() { 
    echo -e "${BLUE}[INFO]${NC} $(date '+%Y-%m-%d %H:%M:%S') $1"
    echo "[INFO] $(date '+%Y-%m-%d %H:%M:%S') $1" >> "$RESTART_LOG"
}

log_ok() { 
    echo -e "${GREEN}[OK]${NC} $(date '+%Y-%m-%d %H:%M:%S') $1"
}

log_warn() { 
    echo -e "${YELLOW}[WARN]${NC} $(date '+%Y-%m-%d %H:%M:%S') $1"
    echo "[WARN] $(date '+%Y-%m-%d %H:%M:%S') $1" >> "$RESTART_LOG"
}

log_error() { 
    echo -e "${RED}[ERROR]${NC} $(date '+%Y-%m-%d %H:%M:%S') $1"
    echo "[ERROR] $(date '+%Y-%m-%d %H:%M:%S') $1" >> "$RESTART_LOG"
}

log_restart() {
    local xapp=$1
    local pid=$2
    local exit_code=$3
    local restart_count=$4
    local reason=$5
    
    echo "$(date '+%Y-%m-%d %H:%M:%S'),$xapp,$pid,$exit_code,$restart_count,$reason" >> "$RESTART_LOG"
}

# =============================================================================
# FUNÇÕES DE HEALTH TRACKING
# =============================================================================

update_health() {
    local xapp_name=$1
    local status=$2
    local pid=$3
    local last_cycle=$4
    
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    
    # Atualiza JSON de health
    if [ -f "$HEALTH_FILE" ]; then
        # Usa python para update atômico do JSON
        python3 - "$xapp_name" "$status" "$pid" "$last_cycle" "$timestamp" << 'PYTHON_EOF'
import json
import sys

xapp_name = sys.argv[1]
status = sys.argv[2]
pid = sys.argv[3]
last_cycle = sys.argv[4]
timestamp = sys.argv[5]

health_file = "/tmp/xapp_health.json"

try:
    with open(health_file, 'r') as f:
        health = json.load(f)
except:
    health = {}

if xapp_name not in health:
    health[xapp_name] = {
        'total_restarts': 0,
        'restarts_today': 0,
        'last_restart': None,
        'last_reason': None
    }

health[xapp_name]['status'] = status
health[xapp_name]['pid'] = int(pid) if pid.isdigit() else None
health[xapp_name]['last_heartbeat'] = timestamp
health[xapp_name]['last_cycle'] = last_cycle

if status == 'RESTARTED':
    health[xapp_name]['total_restarts'] += 1
    health[xapp_name]['restarts_today'] += 1
    health[xapp_name]['last_restart'] = timestamp

with open(health_file, 'w') as f:
    json.dump(health, f, indent=2)
PYTHON_EOF
    else
        # Cria arquivo inicial
        cat > "$HEALTH_FILE" << EOF
{
  "$xapp_name": {
    "status": "$status",
    "pid": $pid,
    "last_heartbeat": "$timestamp",
    "last_cycle": "$last_cycle",
    "total_restarts": 0,
    "restarts_today": 0
  }
}
EOF
    fi
}

get_health() {
    if [ -f "$HEALTH_FILE" ]; then
        cat "$HEALTH_FILE"
    else
        echo "{}"
    fi
}

# =============================================================================
# VERIFICAÇÃO DE STATUS
# =============================================================================

is_running() {
    local pid=$1
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        return 0
    fi
    return 1
}

get_pid_from_name() {
    local name=$1
    pgrep -f "$name" | head -1
}

# =============================================================================
# INICIAR XAPP COM AUTO-RESTART
# =============================================================================

start_xapp() {
    local name=$1
    local binary=$2
    local logfile=$3
    local max_restarts=100
    local restart_count=0
    local last_cycle="N/A"
    
    log_info "Iniciando $name com auto-restart..."
    echo "==========================================" >> "$RESTART_LOG"
    log_info "Wrapper iniciado para $name"
    
    while [ $restart_count -lt $max_restarts ]; do
        # Verifica se binary existe
        if [ ! -f "$binary" ]; then
            log_error "Binary não encontrado: $binary"
            log_restart "$name" "0" "255" "$restart_count" "BINARY_NOT_FOUND"
            sleep 60
            continue
        fi
        
        # Executar xApp
        export LD_LIBRARY_PATH="$RIC_DIR/src/ric:$BASE_DIR/flexric_lib:$RIC_DIR/src/xApp:$LD_LIBRARY_PATH"
        $binary > "$logfile" 2>&1 &
        local pid=$!
        
        log_ok "$name iniciado (PID: $pid)"
        update_health "$name" "RUNNING" "$pid" "$last_cycle"
        
        # Aguardar até o processo morrer
        local exit_code=0
        while kill -0 "$pid" 2>/dev/null; do
            sleep 5
            
            # Atualizar heartbeat periodicamente
            if [ $((SECONDS % HEARTBEAT_INTERVAL)) -eq 0 ]; then
                update_health "$name" "RUNNING" "$pid" "$last_cycle"
            fi
        done
        
        # Processo morreu - obter exit code
        wait $pid
        exit_code=$?
        last_cycle=$(grep -oE "Cycle: [0-9]+" "$logfile" 2>/dev/null | tail -1 | grep -oE "[0-9]+" || echo "N/A")
        
        # Determinar motivo da parada
        local reason="UNKNOWN"
        if [ $exit_code -eq 0 ]; then
            reason="NORMAL_EXIT"
        elif [ $exit_code -eq 137 ] || [ $exit_code -eq 143 ]; then
            reason="KILLED_BY_SIGNAL"
        elif [ $exit_code -gt 128 ]; then
            local signal_num=$((exit_code - 128))
            case $signal_num in
                15) reason="TIMEOUT_KILL" ;;
                9) reason="FORCE_KILL" ;;
                *) reason="SIGNAL_$signal_num" ;;
            esac
        else
            reason="EXIT_CODE_$exit_code"
        fi
        
        restart_count=$((restart_count + 1))
        
        # Log detalhado
        log_warn "$name parou - Exit code: $exit_code, Reason: $reason, Cycle: $last_cycle"
        log_restart "$name" "$pid" "$exit_code" "$restart_count" "$reason"
        
        update_health "$name" "RESTARTED" "0" "$last_cycle"
        
        if [ $restart_count -lt $max_restarts ]; then
            log_info "Reiniciando $name em 2s... (tentativa $restart_count/$max_restarts)"
            sleep 2
        else
            log_error "$name excedeu número máximo de reinicializações ($max_restarts)"
            update_health "$name" "FAILED" "0" "$last_cycle"
        fi
    done
}

# =============================================================================
# STATUS
# =============================================================================

status_xapps() {
    echo ""
    echo -e "${CYAN}========================================${NC}"
    echo -e "${CYAN}    GreenRAN xApp Status${NC}"
    echo -e "${CYAN}========================================${NC}"
    echo ""
    
    local slicer_pid=$(get_pid_from_name "xapp_slicer")
    local energy_pid=$(get_pid_from_name "xapp_energy_saver")
    
    # SLICER
    echo -e "${BLUE}[SLICER]${NC}"
    if is_running "$slicer_pid"; then
        echo -e "  Status:   ${GREEN}RODANDO${NC} (PID: $slicer_pid)"
        
        # Tempo de vida
        local start_time=$(ps -o pid,etime | grep "$slicer_pid" | awk '{print $2}' || echo "N/A")
        echo -e "  Uptime:   $start_time"
        
        # Último cycle
        if [ -f "$LOG_DIR/xapp_slicer.log" ]; then
            local last_cycle=$(tail -100 "$LOG_DIR/xapp_slicer.log" 2>/dev/null | grep "Cycle:" | tail -1 | grep -oE "[0-9]+" || echo "N/A")
            echo -e "  Cycle:    $last_cycle"
        fi
    else
        echo -e "  Status:   ${RED}PARADO${NC}"
        
        # Verificar health
        local health=$(get_health)
        if echo "$health" | grep -q "SLICER"; then
            local total_restarts=$(echo "$health" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('SLICER',{}).get('total_restarts','N/A'))" 2>/dev/null || echo "N/A")
            local last_restart=$(echo "$health" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('SLICER',{}).get('last_restart','N/A'))" 2>/dev/null || echo "N/A")
            echo -e "  Restarts: $total_restarts (último: $last_restart)"
        fi
    fi
    echo ""
    
    # ENERGY
    echo -e "${BLUE}[ENERGY]${NC}"
    if is_running "$energy_pid"; then
        echo -e "  Status:   ${GREEN}RODANDO${NC} (PID: $energy_pid)"
        
        # Tempo de vida
        local start_time=$(ps -o pid,etime | grep "$energy_pid" | awk '{print $2}' || echo "N/A")
        echo -e "  Uptime:   $start_time"
        
        # Último cycle
        if [ -f "$LOG_DIR/xapp_energy.log" ]; then
            local last_cycle=$(tail -100 "$LOG_DIR/xapp_energy.log" 2>/dev/null | grep "Cycle:" | tail -1 | grep -oE "[0-9]+" || echo "N/A")
            echo -e "  Cycle:    $last_cycle"
        fi
    else
        echo -e "  Status:   ${RED}PARADO${NC}"
        
        # Verificar health
        local health=$(get_health)
        if echo "$health" | grep -q "ENERGY"; then
            local total_restarts=$(echo "$health" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('ENERGY',{}).get('total_restarts','N/A'))" 2>/dev/null || echo "N/A")
            local last_restart=$(echo "$health" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('ENERGY',{}).get('last_restart','N/A'))" 2>/dev/null || echo "N/A")
            echo -e "  Restarts: $total_restarts (último: $last_restart)"
        fi
    fi
    echo ""
    
    # Health summary
    if [ -f "$HEALTH_FILE" ]; then
        echo -e "${BLUE}[HEALTH SUMMARY]${NC}"
        echo "  Restart log: $RESTART_LOG"
        local today=$(date '+%Y-%m-%d')
        local restarts_today=$(grep "^$today" "$RESTART_LOG" 2>/dev/null | wc -l || echo "0")
        echo "  Restarts hoje: $restarts_today"
        echo ""
    fi
    
    # Últimas entradas do log
    echo -e "${BLUE}[ÚLTIMOS RESTARTS]${NC}"
    if [ -f "$RESTART_LOG" ]; then
        tail -5 "$RESTART_LOG" 2>/dev/null | while read line; do
            echo "  $line"
        done
    else
        echo "  (sem registros)"
    fi
    echo ""
}

# =============================================================================
# SAÚDE (HEALTH) DETALHADO
# =============================================================================

health_check() {
    echo ""
    echo -e "${CYAN}========================================${NC}"
    echo -e "${CYAN}    GreenRAN xApp Health Check${NC}"
    echo -e "${CYAN}========================================${NC}"
    echo ""
    
    if [ -f "$HEALTH_FILE" ]; then
        python3 -c "
import json
with open('$HEALTH_FILE', 'r') as f:
    health = json.load(f)

for name, data in health.items():
    print(f'{name}:')
    for key, value in data.items():
        print(f'  {key}: {value}')
    print()
"
    else
        echo "Sem dados de health. Execute 'start' primeiro."
    fi
    
    echo -e "${BLUE}[TEMPO DE VIDA MÉDIO]${NC}"
    echo "  Calculando..."
    
    if [ -f "$RESTART_LOG" ]; then
        local today=$(date '+%Y-%m-%d')
        local entries=$(grep "^$today" "$RESTART_LOG" 2>/dev/null | wc -l || echo "0")
        echo "  Entradas no log hoje: $entries"
    fi
    echo ""
}

# =============================================================================
# PARAR XAPPS
# =============================================================================

stop_xapps() {
    log_info "Parando xApps..."
    pkill -f "xapp_slicer" 2>/dev/null || true
    pkill -f "xapp_energy_saver" 2>/dev/null || true
    sleep 2
    
    # Atualizar health
    update_health "SLICER" "STOPPED" "0" "N/A"
    update_health "ENERGY" "STOPPED" "0" "N/A"
    
    log_ok "xApps parados"
}

# =============================================================================
# LIMPAR LOGS
# =============================================================================

clean_logs() {
    log_info "Limpando logs..."
    
    # Backup do log de restarts
    if [ -f "$RESTART_LOG" ]; then
        local backup="${RESTART_LOG}.$(date '+%Y%m%d_%H%M%S').bak"
        cp "$RESTART_LOG" "$backup"
        echo > "$RESTART_LOG"
        log_ok "Log de restarts salvo em $backup"
    fi
    
    # Limpa health file mas mantém estrutura
    if [ -f "$HEALTH_FILE" ]; then
        python3 -c "
import json
with open('$HEALTH_FILE', 'r') as f:
    health = json.load(f)

for name in health:
    health[name]['restarts_today'] = 0

with open('$HEALTH_FILE', 'w') as f:
    json.dump(health, f, indent=2)
"
        log_ok "Health file resetado (contadores diários zerados)"
    fi
}

# =============================================================================
# MAIN
# =============================================================================

main() {
    local action=${1:-start}
    
    # Inicializa arquivo de log se não existir
    touch "$RESTART_LOG"
    
    case $action in
        start)
            echo ""
            echo "=========================================="
            echo "  GreenRAN xApp Auto-Restart Wrapper"
            echo "  $(date)"
            echo "=========================================="
            echo ""
            
            # Parar xApps antigos
            stop_xapps
            sleep 1
            
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
            echo "Para ver health:   ./run_xapps_auto.sh health"
            echo "Para parar:       ./run_xapps_auto.sh stop"
            echo "Logs:             tail -f $RESTART_LOG"
            echo ""
            ;;
        stop)
            stop_xapps
            ;;
        status)
            status_xapps
            ;;
        health)
            health_check
            ;;
        clean)
            clean_logs
            ;;
        restart)
            stop_xapps
            sleep 2
            main start
            ;;
        *)
            echo "Uso: $0 [start|stop|status|health|clean|restart]"
            echo ""
            echo "  start   - Iniciar xApps com auto-restart"
            echo "  stop    - Parar xApps"
            echo "  status  - Ver status dos xApps"
            echo "  health  - Ver detalhes de health"
            echo "  clean   - Limpar contadores de restart"
            echo "  restart - Reiniciar xApps"
            ;;
    esac
}

main "$@"
