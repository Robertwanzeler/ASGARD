#!/bin/bash
# GreenRAN O-RAN - Data Collection Monitor
# ========================================
# Monitora o progresso da coleta de dados do rApp

# Cores
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

DB_PATH="/tmp/rapp_data_lake.db"
METRICS_FILE="/tmp/xapp_metrics/extended_metrics.json"

# Função para query SQLite via Python
query_sqlite() {
    python3 -c "import sqlite3; conn=sqlite3.connect('$DB_PATH'); print(conn.execute('$1').fetchone()[0])" 2>/dev/null || echo "N/A"
}

# Contadores
EXTENDED_COUNT=$(query_sqlite "SELECT COUNT(*) FROM extended_metrics")
UE_COUNT=$(query_sqlite "SELECT COUNT(*) FROM ue_metrics")
METRICS_COUNT=$(query_sqlite "SELECT COUNT(*) FROM metrics_history")
DECISIONS_COUNT=$(query_sqlite "SELECT COUNT(*) FROM decisions_history")

echo ""
echo -e "${BOLD}╔════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║${NC}        ${CYAN}rApp-ResourceOptimizer - Data Collection Monitor${NC}        ${BOLD}║${NC}"
echo -e "${BOLD}╚════════════════════════════════════════════════════════════════╝${NC}"
echo ""

# Verificar se o Data Lake existe
if [ ! -f "$DB_PATH" ]; then
    echo -e "${RED}[ERRO]${NC} Data Lake não encontrado: $DB_PATH"
    echo "Execute o sistema primeiro: ./run_greenran_complete.sh"
    exit 1
fi

# ============================================
# 1. Estatísticas de Registros
# ============================================
echo -e "${BOLD}[1] REGISTROS NO DATA LAKE${NC}"
echo "----------------------------------------"

echo "  extended_metrics: $EXTENDED_COUNT registros"
echo "  ue_metrics:       $UE_COUNT registros"
echo "  metrics_history:   $METRICS_COUNT registros"
echo "  decisions:         $DECISIONS_COUNT registros"

# ============================================
# 2. Período de Coleta
# ============================================
echo ""
echo -e "${BOLD}[2] PERÍODO DE COLETA${NC}"
echo "----------------------------------------"

PRIMEIRO=$(sqlite3 "$DB_PATH" "SELECT MIN(datetime) FROM extended_metrics" 2>/dev/null)
ULTIMO=$(sqlite3 "$DB_PATH" "SELECT MAX(datetime) FROM extended_metrics" 2>/dev/null)

if [ ! -z "$PRIMEIRO" ]; then
    echo "  Primeiro registro: $PRIMEIRO"
    echo "  Último registro:  $ULTIMO"
    
    # Tempo decorrido
    PRIMEIRO_EPOCH=$(sqlite3 "$DB_PATH" "SELECT MIN(timestamp) FROM extended_metrics" 2>/dev/null)
    AGORA=$(date +%s)
    DECORRIDO=$((AGORA - PRIMEIRO_EPOCH))
    HORAS=$((DECORRIDO / 3600))
    MINUTOS=$(((DECORRIDO % 3600) / 60))
    SEGUNDOS=$((DECORRIDO % 60))
    echo "  Tempo de coleta: ${HORAS}h ${MINUTOS}m ${SEGUNDOS}s"
else
    echo "  Nenhum registro encontrado"
fi

# ============================================
# 3. Métricas Atuais
# ============================================
echo ""
echo -e "${BOLD}[3] MÉTRICAS ATUAIS${NC}"
echo "----------------------------------------"

# Última métrica estendida
LAT_AVG=$(sqlite3 "$DB_PATH" "SELECT AVG(global_avg_latency_us)/1000 FROM extended_metrics" 2>/dev/null | head -1)
LAT_WORST=$(sqlite3 "$DB_PATH" "SELECT MAX(global_worst_latency_us)/1000 FROM extended_metrics" 2>/dev/null | head -1)
LAT_MIN=$(sqlite3 "$DB_PATH" "SELECT MIN(global_min_latency_us)/1000 FROM extended_metrics" 2>/dev/null | head -1)
JITTER=$(sqlite3 "$DB_PATH" "SELECT AVG(global_jitter_us)/1000 FROM extended_metrics" 2>/dev/null | head -1)
THROUGHPUT=$(sqlite3 "$DB_PATH" "SELECT AVG(throughput_kbps) FROM extended_metrics" 2>/dev/null | head -1)

echo "  Latência Média:    ${LAT_AVG:-N/A} ms"
echo "  Latência Pior:     ${LAT_WORST:-N/A} ms"
echo "  Latência Melhor:   ${LAT_MIN:-N/A} ms"
echo "  Jitter Médio:      ${JITTER:-N/A} ms"
echo "  Throughput Médio:  ${THROUGHPUT:-N/A} kbps"

# ============================================
# 4. UEs e Câmeras
# ============================================
echo ""
echo -e "${BOLD}[4] UEs E CÂMERAS${NC}"
echo "----------------------------------------"

CAM_ATIVAS=$(sqlite3 "$DB_PATH" "SELECT AVG(total_active_cameras) FROM extended_metrics" 2>/dev/null | head -1)
UE_ATIVAS=$(sqlite3 "$DB_PATH" "SELECT AVG(total_active_ues) FROM extended_metrics" 2>/dev/null | head -1)
UE_CRITICAS=$(sqlite3 "$DB_PATH" "SELECT AVG(total_critical_ues) FROM extended_metrics" 2>/dev/null | head -1)

echo "  Câmeras Ativas (média): ${CAM_ATIVAS:-N/A}"
echo "  UEs Ativas (média):     ${UE_ATIVAS:-N/A}"
echo "  UEs Críticas (média):   ${UE_CRITICAS:-N/A}"

# ============================================
# 5. Decisões do rApp
# ============================================
echo ""
echo -e "${BOLD}[5] DECISÕES DO RAPP${NC}"
echo "----------------------------------------"

BLOCKED=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM decisions_history WHERE decision = 'BLOCKED'" 2>/dev/null || echo "0")
ALLOWED=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM decisions_history WHERE decision = 'ALLOWED'" 2>/dev/null || echo "0")
CONDITIONAL=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM decisions_history WHERE decision = 'CONDITIONAL'" 2>/dev/null || echo "0")
TOTAL_DEC=$((BLOCKED + ALLOWED + CONDITIONAL))

echo "  BLOCKED:      $BLOCKED"
echo "  ALLOWED:      $ALLOWED"
echo "  CONDITIONAL:  $CONDITIONAL"
echo "  Total:        $TOTAL_DEC"

if [ "$TOTAL_DEC" -gt 0 ]; then
    echo ""
    echo "  Distribuição:"
    echo -n "    BLOCKED:     "
    printf "%.1f%%" $(echo "scale=2; $BLOCKED * 100 / $TOTAL_DEC" | bc 2>/dev/null || echo "0")
    echo ""
    echo -n "    ALLOWED:     "
    printf "%.1f%%" $(echo "scale=2; $ALLOWED * 100 / $TOTAL_DEC" | bc 2>/dev/null || echo "0")
    echo ""
    echo -n "    CONDITIONAL: "
    printf "%.1f%%" $(echo "scale=2; $CONDITIONAL * 100 / $TOTAL_DEC" | bc 2>/dev/null || echo "0")
    echo ""
fi

# ============================================
# 6. Tamanho do Database
# ============================================
echo ""
echo -e "${BOLD}[6] TAMANHO DO DATABASE${NC}"
echo "----------------------------------------"

TAMANHO=$(du -h "$DB_PATH" 2>/dev/null | cut -f1 || echo "N/A")
LINHAS=$(wc -l < "$DB_PATH" 2>/dev/null || echo "N/A")
echo "  Arquivo: $DB_PATH"
echo "  Tamanho: $TAMANHO"

# ============================================
# 7. Métricas em Tempo Real
# ============================================
echo ""
echo -e "${BOLD}[7] MÉTRICAS EM TEMPO REAL${NC}"
echo "----------------------------------------"

if [ -f "$METRICS_FILE" ]; then
    LATENCY_REALTIME=$(grep -o '"global_worst_latency_us": [0-9.]*' "$METRICS_FILE" 2>/dev/null | head -1 | cut -d':' -f2 | xargs)
    CAMERAS_REALTIME=$(grep -o '"active_cameras": [0-9]*' "$METRICS_FILE" 2>/dev/null | head -1 | cut -d':' -f2 | xargs)
    CRITICAL_REALTIME=$(grep -o '"critical_cameras": [0-9]*' "$METRICS_FILE" 2>/dev/null | head -1 | cut -d':' -f2 | xargs)
    THROUGHPUT_REALTIME=$(grep -o '"throughput_kbps": [0-9.]*' "$METRICS_FILE" 2>/dev/null | head -1 | cut -d':' -f2 | xargs)
    
    echo "  Latência Pior:     ${LATENCY_REALTIME:-N/A} us ($(echo "scale=1; ${LATENCY_REALTIME:-0} / 1000" | bc 2>/dev/null || echo "N/A") ms)"
    echo "  Câmeras Ativas:    ${CAMERAS_REALTIME:-N/A}"
    echo "  Críticas:          ${CRITICAL_REALTIME:-N/A}"
    echo "  Throughput:         ${THROUGHPUT_REALTIME:-N/A} kbps"
else
    echo "  Arquivo não disponível: $METRICS_FILE"
fi

# ============================================
# 8. Status dos Processos
# ============================================
echo ""
echo -e "${BOLD}[8] STATUS DOS PROCESSOS${NC}"
echo "----------------------------------------"

CSV_PID=$(pgrep -f "csv_to_metrics" | head -1)
RAPP_PID=$(pgrep -f "rapp_orchestrator" | head -1)
NS3_PID=$(pgrep -f "ns3.42" | head -1)

echo -n "  csv_to_metrics.py:  "
if [ ! -z "$CSV_PID" ]; then
    echo -e "${GREEN}RUNNING${NC} (PID: $CSV_PID)"
else
    echo -e "${RED}STOPPED${NC}"
fi

echo -n "  rapp_orchestrator.py: "
if [ ! -z "$RAPP_PID" ]; then
    echo -e "${GREEN}RUNNING${NC} (PID: $RAPP_PID)"
else
    echo -e "${RED}STOPPED${NC}"
fi

echo -n "  ns3.42:             "
if [ ! -z "$NS3_PID" ]; then
    echo -e "${GREEN}RUNNING${NC} (PID: $NS3_PID)"
else
    echo -e "${YELLOW}NOT RUNNING${NC}"
fi

# ============================================
# Resumo Final
# ============================================
echo ""
echo -e "${BOLD}╔════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║${NC}                        RESUMO                                  ${BOLD}║${NC}"
echo -e "${BOLD}╚════════════════════════════════════════════════════════════════╝${NC}"
echo ""

# Verificar se há dados suficientes para ML
if [ "$EXTENDED_COUNT" -lt 60 ]; then
    echo -e "${YELLOW}[COLLECTING]${NC} Dados sendo coletados... (${EXTENDED_COUNT} registros)"
    echo "            Mínimo recomendado: 60 registros"
elif [ "$EXTENDED_COUNT" -lt 300 ]; then
    echo -e "${YELLOW}[TRAINING]${NC} Dados suficientes para análise básica (${EXTENDED_COUNT} registros)"
    echo "            Para melhor ML, colete mais dados"
else
    echo -e "${GREEN}[READY]${NC} Dados suficientes para ML completo (${EXTENDED_COUNT} registros)"
fi

echo ""
echo "Para monitorar continuamente:"
echo "  watch -n 5 ./monitor_data_collection.sh"
echo ""
