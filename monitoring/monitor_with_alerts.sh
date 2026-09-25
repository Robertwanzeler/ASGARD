#!/bin/bash
#
# =============================================================================
# GreenRAN O-RAN - Monitor de Coleta com Alertas
# =============================================================================
# Monitora a coleta de dados até atingir o dataset mínimo para ML
# Mostra alertas quando SLA está sendo violado
# Salva snapshot do dataset automaticamente
#
# Uso: ./monitor_with_alerts.sh [--continuous] [--target-records N]
# =============================================================================

# Configurações
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ORANGE_DIR="${GREENRAN_PROJECT_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
DB_FILE="/tmp/rapp_data_lake.db"
METRICS_FILE="/tmp/xapp_metrics/extended_metrics.json"
TARGET_RECORDS=${1:-5000}
CONTINUOUS=${2:-false}
SLA_THRESHOLD_MS=100

# Cores
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

# Arquivos de controle
SNAPSHOT_DIR="/tmp/rapp_snapshots"
ALERT_LOG="/tmp/rapp_alerts_monitor.log"

# Criar diretório de snapshots
mkdir -p "$SNAPSHOT_DIR"

# Funções
log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
log_ok() { echo -e "${GREEN}[OK]${NC} $1"; }
log_alert() { 
    echo -e "${RED}[ALERT]${NC} $1"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [ALERT] $1" >> "$ALERT_LOG"
}

separator() {
    echo "================================================================"
}

# Verificar se sistema está rodando
check_system() {
    local csv_pid=$(pgrep -f "csv_to_metrics" | head -1)
    local rapp_pid=$(pgrep -f "rapp_orchestrator" | head -1)
    local ns3_pid=$(pgrep -f "ns3.42-scenario" | head -1)
    
    if [ -z "$csv_pid" ] || [ -z "$rapp_pid" ] || [ -z "$ns3_pid" ]; then
        log_error "Sistema não está completamente rodando!"
        echo "  csv_to_metrics: $([ -n "$csv_pid" ] && echo "✓" || echo "✗")"
        echo "  rapp_orchestrator: $([ -n "$rapp_pid" ] && echo "✓" || echo "✗")"
        echo "  ns-3: $([ -n "$ns3_pid" ] && echo "✓" || echo "✗")"
        return 1
    fi
    
    return 0
}

# Obter contagem de registros
get_record_count() {
    if [ ! -f "$DB_FILE" ]; then
        echo "0"
        return
    fi
    
    python3 - << 'PYEOF'
import sqlite3
import sys

try:
    conn = sqlite3.connect('/tmp/rapp_data_lake.db')
    c = conn.cursor()
    c.execute('SELECT COUNT(*) FROM extended_metrics')
    count = c.fetchone()[0]
    conn.close()
    print(count)
except:
    print("0")
PYEOF
}

# Obter métricas em tempo real
get_realtime_metrics() {
    if [ ! -f "$METRICS_FILE" ]; then
        echo "{}"
        return
    fi
    
    python3 - << 'PYEOF'
import json
import sys

try:
    with open('/tmp/xapp_metrics/extended_metrics.json') as f:
        data = json.load(f)
        gm = data.get('global_metrics', {})
        
        worst_latency_us = gm.get('global_worst_latency_us', 0)
        avg_latency_us = gm.get('global_avg_latency_us', 0)
        throughput = gm.get('throughput_kbps', 0)
        active_ues = gm.get('total_active_ues', 0)
        active_cameras = data.get('active_cameras', 0)
        critical_cameras = data.get('critical_cameras', 0)
        
        print(json.dumps({
            'worst_latency_us': worst_latency_us,
            'worst_latency_ms': worst_latency_us / 1000,
            'avg_latency_ms': avg_latency_us / 1000,
            'throughput_kbps': throughput,
            'active_ues': active_ues,
            'active_cameras': active_cameras,
            'critical_cameras': critical_cameras
        }))
except:
    print("{}")
PYEOF
}

# Verificar SLA
check_sla() {
    local worst_latency_ms=$1
    local sla_threshold=$2
    local violations=0
    
    if (( $(echo "$worst_latency_ms > $sla_threshold" | bc -l) )); then
        violations=$((violations + 1))
    fi
    
    echo $violations
}

# Calcular percentis
get_percentiles() {
    if [ ! -f "$DB_FILE" ]; then
        echo "N/A N/A"
        return
    fi
    
    python3 - << 'PYEOF'
import sqlite3
import json

try:
    conn = sqlite3.connect('/tmp/rapp_data_lake.db')
    c = conn.cursor()
    
    # P5 (5% melhor)
    c.execute('SELECT latency_p5_us FROM extended_metrics WHERE latency_p5_us > 0 ORDER BY latency_p5_us LIMIT 1')
    p5 = c.fetchone()
    p5_val = p5[0] / 1000 if p5 else 0
    
    # P95 (5% pior)
    c.execute('SELECT latency_p95_us FROM extended_metrics ORDER BY latency_p95_us DESC LIMIT 1')
    p95 = c.fetchone()
    p95_val = p95[0] / 1000 if p95 else 0
    
    conn.close()
    print(f"{p5_val:.2f} {p95_val:.2f}")
except:
    print("N/A N/A")
PYEOF
}

# Obter estatísticas de decisões
get_decision_stats() {
    if [ ! -f "$DB_FILE" ]; then
        echo "0 0 0"
        return
    fi
    
    python3 - << 'PYEOF'
import sqlite3

try:
    conn = sqlite3.connect('/tmp/rapp_data_lake.db')
    c = conn.cursor()
    
    c.execute("SELECT COUNT(*) FROM decisions_history WHERE energy_saver = 'BLOCKED'")
    blocked = c.fetchone()[0]
    
    c.execute("SELECT COUNT(*) FROM decisions_history WHERE energy_saver = 'ALLOWED'")
    allowed = c.fetchone()[0]
    
    c.execute("SELECT COUNT(*) FROM decisions_history")
    total = c.fetchone()[0]
    
    conn.close()
    print(f"{blocked} {allowed} {total}")
except:
    print("0 0 0")
PYEOF
}

# Salvar snapshot
save_snapshot() {
    local timestamp=$(date '+%Y%m%d_%H%M%S')
    local snapshot_file="${SNAPSHOT_DIR}/snapshot_${timestamp}.db"
    
    if [ -f "$DB_FILE" ]; then
        cp "$DB_FILE" "$snapshot_file"
        echo "$snapshot_file"
    fi
}

# Obter período de coleta
get_collection_period() {
    if [ ! -f "$DB_FILE" ]; then
        echo "N/A N/A"
        return
    fi
    
    python3 - << 'PYEOF'
import sqlite3
from datetime import datetime

try:
    conn = sqlite3.connect('/tmp/rapp_data_lake.db')
    c = conn.cursor()
    
    c.execute('SELECT MIN(timestamp), MAX(timestamp) FROM extended_metrics')
    result = c.fetchone()
    
    if result and result[0] and result[1]:
        start = datetime.fromtimestamp(result[0]).strftime('%Y-%m-%d %H:%M:%S')
        end = datetime.fromtimestamp(result[1]).strftime('%Y-%m-%d %H:%M:%S')
        duration_sec = result[1] - result[0]
        hours = int(duration_sec // 3600)
        mins = int((duration_sec % 3600) // 60)
        secs = int(duration_sec % 60)
        duration = f"{hours}h {mins}m {secs}s"
        print(f"{start}|{end}|{duration}")
    else:
        print("N/A|N/A|N/A")
    
    conn.close()
except:
    print("N/A|N/A|N/A")
PYEOF
}

# Display principal
display_status() {
    local records=$(get_record_count)
    local percent=0
    if [ "$records" -gt 0 ] && [ "$TARGET_RECORDS" -gt 0 ]; then
        percent=$((records * 100 / TARGET_RECORDS))
        if [ "$percent" -gt 100 ]; then
            percent=100
        fi
    fi
    local metrics=$(get_realtime_metrics)
    local period=$(get_collection_period)
    local stats=($(get_decision_stats))
    local percentiles=($(get_percentiles))
    
    # Parse período
    IFS='|' read -r start_time end_time duration <<< "$period"
    
    # Parse métricas
    local worst_latency=$(echo "$metrics" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('worst_latency_ms',0))")
    local avg_latency=$(echo "$metrics" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('avg_latency_ms',0))")
    local active_ues=$(echo "$metrics" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('active_ues',0))")
    local active_cameras=$(echo "$metrics" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('active_cameras',0))")
    local critical_cameras=$(echo "$metrics" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('critical_cameras',0))")
    local throughput=$(echo "$metrics" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('throughput_kbps',0))")
    
    clear
    separator
    echo -e "${CYAN}        GreenRAN O-RAN - Monitor de Coleta${NC}"
    separator
    
    # Barra de progresso
    echo ""
    echo -e "${BLUE}[PROGRESSO]${NC} Dataset: $records / $TARGET_RECORDS registros"
    
    # Barra visual
    local bar_len=50
    local filled_len=0
    if [ "$records" -gt 0 ] && [ "$TARGET_RECORDS" -gt 0 ]; then
        filled_len=$((records * bar_len / TARGET_RECORDS))
        if [ "$filled_len" -gt "$bar_len" ]; then
            filled_len=$bar_len
        fi
    fi
    
    printf "  ["
    for ((i=0; i<bar_len; i++)); do
        if [ $i -lt $filled_len ]; then
            printf "█"
        else
            printf "░"
        fi
    done
    printf "] %d%%" "$percent"
    
    # Mostrar quanto falta
    if [ "$records" -lt "$TARGET_RECORDS" ]; then
        local remaining=$((TARGET_RECORDS - records))
        local eta_mins=$((remaining * 30 / 60))
        echo " (faltam: $remaining | ~${eta_mins}min)"
    else
        echo " ✓ COMPLETO"
    fi
    echo ""
    
    # SLA Status
    echo ""
    echo -e "${BLUE}[SLA STATUS]${NC} Threshold: ${SLA_THRESHOLD_MS}ms"
    sla_ok=true
    if (( $(echo "$worst_latency > $SLA_THRESHOLD_MS" | bc -l 2>/dev/null || echo 0) )); then
        sla_ok=false
        log_alert "SLA VIOLADO! Latência: ${worst_latency}ms > ${SLA_THRESHOLD_MS}ms"
        echo -e "  Latência Pior: ${RED}${worst_latency}ms${NC} ⚠️ VIOLADO"
    else
        echo -e "  Latência Pior: ${GREEN}${worst_latency}ms${NC} ✓ OK"
    fi
    echo -e "  Latência Média: ${avg_latency}ms"
    
    # Métricas atuais
    echo ""
    echo -e "${BLUE}[MÉTRICAS ATUAIS]${NC}"
    echo "  UEs Ativos:       $active_ues"
    echo "  Câmeras Ativas:   $active_cameras"
    echo "  Câmeras Críticas: $critical_cameras"
    echo "  Throughput:       ${throughput} kbps"
    
    # Período de coleta
    echo ""
    echo -e "${BLUE}[COLETA]${NC}"
    echo "  Início: $start_time"
    echo "  Fim:    $end_time"
    echo "  Duração: ${duration}"
    
    # Percentis
    echo ""
    echo -e "${BLUE}[PERCENTIS]${NC}"
    echo "  P5 (melhor):  ${percentiles[0]} ms"
    echo "  P95 (pior):   ${percentiles[1]} ms"
    
    # Decisões
    echo ""
    echo -e "${BLUE}[DECISÕES]${NC}"
    blocked=${stats[0]}
    allowed=${stats[1]}
    total=${stats[2]}
    if [ "$total" -gt 0 ]; then
        blocked_pct=$((blocked * 100 / total))
        allowed_pct=$((allowed * 100 / total))
        echo "  BLOCKED: ${blocked} (${blocked_pct}%)"
        echo "  ALLOWED: ${allowed} (${allowed_pct}%)"
    else
        echo "  Sem decisões registradas"
    fi
    
    # Alertas recentes
    echo ""
    echo -e "${BLUE}[ALERTAS RECENTES]${NC}"
    if [ -f "$ALERT_LOG" ]; then
        tail -3 "$ALERT_LOG" 2>/dev/null | while read line; do
            echo "  $line"
        done
    else
        echo "  Nenhum alerta"
    fi
    
    # Próximos passos
    echo ""
    separator
    if [ "$records" -ge "$TARGET_RECORDS" ]; then
        echo -e "${GREEN}✓ DATASET PRONTO!${NC} $records registros coletados"
        echo ""
        echo "  Snapshot salvo em: $(save_snapshot)"
        echo ""
        echo "  Para análise, execute:"
        echo "    python3 -c \"import sqlite3; conn=sqlite3.connect('$DB_FILE'); print(conn.execute('SELECT COUNT(*) FROM extended_metrics').fetchone())\""
    else
        local remaining=$((TARGET_RECORDS - records))
        echo -e "${YELLOW}⏳ Coletando dados...${NC} Faltam $remaining registros"
        echo ""
        echo "  Para monitorar continuamente: watch -n 30 ./monitor_with_alerts.sh"
        echo "  Para parar: Ctrl+C"
    fi
    separator
}

# Main loop
main() {
    # Criar log de alertas
    touch "$ALERT_LOG"
    
    echo ""
    log_info "Monitor de Coleta com Alertas"
    echo "  Target: $TARGET_RECORDS registros"
    echo "  SLA: $SLA_THRESHOLD_MS ms"
    echo "  Snapshots: $SNAPSHOT_DIR"
    echo "  Alertas: $ALERT_LOG"
    echo ""
    
    # Verificar sistema
    if ! check_system; then
        echo ""
        log_error "Inicie o sistema primeiro:"
        echo "  ./run_greenran_complete.sh --all"
        exit 1
    fi
    
    log_ok "Sistema rodando!"
    echo ""
    
    # Loop principal
    while true; do
        display_status
        
        # Verificar se atingiu target
        records=$(get_record_count)
        if [ "$records" -ge "$TARGET_RECORDS" ]; then
            echo ""
            log_ok "Dataset completo! Parando monitoramento."
            break
        fi
        
        # Aguardar 30 segundos
        sleep 30
    done
    
    echo ""
    log_info "Monitoramento encerrado."
}

main
