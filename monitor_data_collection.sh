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
MAGENTA='\033[0;35m'
BOLD='\033[1m'
NC='\033[0m'

# Garante que números usem ponto como decimal (evita erro em pt_BR)
export LC_NUMERIC=C

DB_PATH="/tmp/rapp_data_lake.db"
METRICS_FILE="/tmp/xapp_metrics/extended_metrics.json"

# Função para query SQLite via Python
query_sqlite() {
    python3 -c "import sqlite3; conn=sqlite3.connect('$DB_PATH'); c=conn.execute(\"\"\"$1\"\"\"); r=c.fetchone(); print(r[0] if r else 'N/A')" 2>/dev/null || echo "N/A"
}

# Contadores
EXTENDED_COUNT=$(query_sqlite "SELECT COUNT(*) FROM extended_metrics")
UE_COUNT=$(query_sqlite "SELECT COUNT(*) FROM ue_metrics")
METRICS_COUNT=$(query_sqlite "SELECT COUNT(*) FROM metrics_history")
DECISIONS_COUNT=$(query_sqlite "SELECT COUNT(*) FROM decisions_history")

echo ""
echo -e "${BOLD}╔════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║${NC}        ${CYAN}rApp-ResourceOptimizer - Monitor de Coleta${NC}              ${BOLD}║${NC}"
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
echo -e "${CYAN}${BOLD}[1] REGISTROS NO DATA LAKE${NC}"
echo -e "${CYAN}----------------------------------------${NC}"

echo -e "  Métricas Estendidas: ${BOLD}$EXTENDED_COUNT${NC} registros"
echo -e "  Métricas de Usuários: ${BOLD}$UE_COUNT${NC} registros"
echo -e "  Histórico de Rede:    ${BOLD}$METRICS_COUNT${NC} registros"
echo -e "  Histórico Decisões:   ${BOLD}$DECISIONS_COUNT${NC} registros"

# ============================================
# 2. Período de Coleta
# ============================================
echo ""
echo -e "${BLUE}${BOLD}[2] PERÍODO DE COLETA${NC}"
echo -e "${BLUE}----------------------------------------${NC}"

PRIMEIRO=$(query_sqlite "SELECT MIN(datetime) FROM extended_metrics")
ULTIMO=$(query_sqlite "SELECT MAX(datetime) FROM extended_metrics")

if [ ! -z "$PRIMEIRO" ]; then
    echo -e "  Primeiro registro: ${BOLD}$PRIMEIRO${NC}"
    echo -e "  Último registro:  ${BOLD}$ULTIMO${NC}"
    
    # Tempo decorrido
    PRIMEIRO_EPOCH=$(query_sqlite "SELECT MIN(timestamp) FROM extended_metrics")
    AGORA=$(date +%s)
    DECORRIDO=$((AGORA - PRIMEIRO_EPOCH))
    HORAS=$((DECORRIDO / 3600))
    MINUTOS=$(((DECORRIDO % 3600) / 60))
    SEGUNDOS=$((DECORRIDO % 60))
    echo -e "  Tempo de coleta: ${GREEN}${BOLD}${HORAS}h ${MINUTOS}m ${SEGUNDOS}s${NC}"
else
    echo -e "  ${RED}Nenhum registro encontrado${NC}"
fi

# ============================================
# 3. Métricas Atuais
# ============================================
echo ""
echo -e "${YELLOW}${BOLD}[3] MÉTRICAS DE LATÊNCIA (MÉDIA GERAL)${NC}"
echo -e "${YELLOW}----------------------------------------${NC}"

# Métricas básicas
LAT_AVG=$(query_sqlite "SELECT AVG(global_avg_latency_us)/1000 FROM extended_metrics")
LAT_WORST=$(query_sqlite "SELECT MAX(global_worst_latency_us)/1000 FROM extended_metrics")
LAT_MIN=$(query_sqlite "SELECT MIN(global_min_latency_us)/1000 FROM extended_metrics WHERE global_min_latency_us > 0")
JITTER=$(query_sqlite "SELECT AVG(global_jitter_us)/1000 FROM extended_metrics")
THROUGHPUT=$(query_sqlite "SELECT AVG(throughput_kbps) FROM extended_metrics")

# Métricas robustas (percentis)
LAT_P5=$(query_sqlite "SELECT AVG(latency_p5_us)/1000 FROM extended_metrics WHERE latency_p5_us > 0")
LAT_P95=$(query_sqlite "SELECT AVG(latency_p95_us)/1000 FROM extended_metrics WHERE latency_p95_us > 0")
LAT_MINNZ=$(query_sqlite "SELECT AVG(latency_min_nonzero_us)/1000 FROM extended_metrics WHERE latency_min_nonzero_us > 0")

# Formatação com cores para métricas críticas
COLOR_LAT_AVG="${YELLOW}"
COLOR_LAT_WORST="${RED}"
COLOR_LAT_MIN="${GREEN}"
COLOR_THROUGHPUT="${CYAN}"

echo -e "  ${CYAN}Métricas Básicas:${NC}"
echo -e "    Latência Média:    ${BOLD}${COLOR_LAT_AVG}${LAT_AVG:-N/A}${NC} ms"
echo -e "    Latência Pior:     ${BOLD}${COLOR_LAT_WORST}${LAT_WORST:-N/A}${NC} ms \033[1;30m(SLA: 100ms)\033[0m"
echo -e "    Latência Melhor:   ${BOLD}${COLOR_LAT_MIN}${LAT_MIN:-N/A}${NC} ms"
echo -e "    Jitter Médio:      ${BOLD}${JITTER:-N/A}${NC} ms"
echo -e "    Vazão Média:       ${BOLD}${COLOR_THROUGHPUT}${THROUGHPUT:-N/A}${NC} kbps"

echo ""
echo -e "  ${GREEN}Métricas Robustas (Percentis):${NC}"
echo -e "    Latência P5:       ${BOLD}${GREEN}${LAT_P5:-N/A}${NC} ms \033[1;30m(5% melhor)${NC}"
echo -e "    Latência P95:      ${BOLD}${YELLOW}${LAT_P95:-N/A}${NC} ms \033[1;30m(5% pior)${NC}"
echo -e "    Latência Min>0:    ${BOLD}${GREEN}${LAT_MINNZ:-N/A}${NC} ms \033[1;30m(menor valor real)${NC}"

# ============================================
# 4. UEs e Câmeras
# ============================================
echo ""
echo -e "${MAGENTA}${BOLD}[4] UEs E CÂMERAS${NC}"
echo -e "${MAGENTA}----------------------------------------${NC}"

CAM_ATIVAS=$(query_sqlite "SELECT AVG(total_active_cameras) FROM extended_metrics")
UE_ATIVAS=$(query_sqlite "SELECT AVG(total_active_ues) FROM extended_metrics")
UE_CRITICAS=$(query_sqlite "SELECT AVG(total_critical_ues) FROM extended_metrics")

echo -e "  Câmeras Ativas (média): ${BOLD}${CAM_ATIVAS:-N/A}${NC}"
echo -e "  UEs Ativas (média):     ${BOLD}${UE_ATIVAS:-N/A}${NC}"
echo -e "  UEs Críticas (média):   ${BOLD}${RED}${UE_CRITICAS:-N/A}${NC}"

# ============================================
# 5. Decisões do rApp
# ============================================
echo ""
echo -e "${CYAN}${BOLD}[5] DECISÕES DO RAPP${NC}"
echo -e "${CYAN}----------------------------------------${NC}"

BLOCKED=$(query_sqlite "SELECT COUNT(*) FROM decisions_history WHERE decision = 'BLOCKED'" | grep -E '^[0-9]+$' || echo "0")
ALLOWED=$(query_sqlite "SELECT COUNT(*) FROM decisions_history WHERE decision = 'ALLOWED'" | grep -E '^[0-9]+$' || echo "0")
CONDITIONAL=$(query_sqlite "SELECT COUNT(*) FROM decisions_history WHERE decision = 'CONDITIONAL'" | grep -E '^[0-9]+$' || echo "0")
UNKNOWN=$(query_sqlite "SELECT COUNT(*) FROM decisions_history WHERE decision NOT IN ('BLOCKED', 'ALLOWED', 'CONDITIONAL')" | grep -E '^[0-9]+$' || echo "0")
TOTAL_DEC=$((BLOCKED + ALLOWED + CONDITIONAL + UNKNOWN))

echo -e "  BLOQUEADO (BLOCKED):     ${RED}${BOLD}$BLOCKED${NC}"
echo -e "  PERMITIDO (ALLOWED):     ${GREEN}${BOLD}$ALLOWED${NC}"
echo -e "  CONDICIONAL:             ${YELLOW}${BOLD}$CONDITIONAL${NC}"
echo -e "  INICIAL/OUTROS:          ${MAGENTA}${BOLD}$UNKNOWN${NC}"
echo -e "  Total de Decisões:       ${BOLD}$TOTAL_DEC${NC}"

if [ "$TOTAL_DEC" -gt 0 ]; then
    echo ""
    echo "  Distribuição:"
    echo -n "    BLOQUEADO:   "
    printf "%.1f%%" $(echo "scale=2; $BLOCKED * 100 / $TOTAL_DEC" | bc 2>/dev/null || echo "0")
    echo ""
    echo -n "    PERMITIDO:   "
    printf "%.1f%%" $(echo "scale=2; $ALLOWED * 100 / $TOTAL_DEC" | bc 2>/dev/null || echo "0")
    echo ""
    echo -n "    OUTROS:      "
    printf "%.1f%%" $(echo "scale=2; $UNKNOWN * 100 / $TOTAL_DEC" | bc 2>/dev/null || echo "0")
    echo ""
fi

# ============================================
# 6. Tamanho do Database
# ============================================
echo ""
echo -e "${BOLD}[6] TAMANHO DO DATABASE${NC}"
echo "----------------------------------------"

TAMANHO=$(du -h "$DB_PATH" 2>/dev/null | cut -f1 || echo "N/A")
echo -e "  Arquivo: ${BOLD}$DB_PATH${NC}"
echo -e "  Tamanho: ${BOLD}$TAMANHO${NC}"

# ============================================
# 7. Métricas em Tempo Real
# ============================================
echo ""
echo -e "${BOLD}${CYAN}[7] MÉTRICAS EM TEMPO REAL${NC}"
echo -e "${CYAN}----------------------------------------${NC}"

if [ -f "$METRICS_FILE" ]; then
    LATENCY_REALTIME=$(grep -o '"global_worst_latency_us": [0-9.]*' "$METRICS_FILE" 2>/dev/null | head -1 | cut -d':' -f2 | xargs)
    CAMERAS_REALTIME=$(grep -o '"active_cameras": [0-9]*' "$METRICS_FILE" 2>/dev/null | head -1 | cut -d':' -f2 | xargs)
    CRITICAL_REALTIME=$(grep -o '"critical_cameras": [0-9]*' "$METRICS_FILE" 2>/dev/null | head -1 | cut -d':' -f2 | xargs)
    THROUGHPUT_REALTIME=$(grep -o '"throughput_kbps": [0-9.]*' "$METRICS_FILE" 2>/dev/null | head -1 | cut -d':' -f2 | xargs)
    UES_REALTIME=$(grep -o '"total_active_ues": [0-9]*' "$METRICS_FILE" 2>/dev/null | head -1 | cut -d':' -f2 | xargs)
    
    echo -e "  Latência Pior:     ${BOLD}${LATENCY_REALTIME:-N/A}${NC} us (\033[1;31m$(echo "scale=1; ${LATENCY_REALTIME:-0} / 1000" | bc 2>/dev/null || echo "N/A") ms\033[0m)"
    echo -e "  UEs Ativos Agora:  ${BOLD}${UES_REALTIME:-N/A}${NC}"
    echo -e "  Câmeras Ativas:    ${BOLD}${CAMERAS_REALTIME:-N/A}${NC}"
    echo -e "  Câmeras Críticas:  ${BOLD}${RED}${CRITICAL_REALTIME:-N/A}${NC}"
    echo -e "  Vazão Instantânea: ${BOLD}${CYAN}${THROUGHPUT_REALTIME:-N/A}${NC} kbps"
else
    echo -e "  ${RED}Arquivo não disponível: $METRICS_FILE${NC}"
fi

# ============================================
# 8. Status dos Processos
# ============================================
echo ""
echo -e "${BOLD}${BLUE}[8] STATUS DOS PROCESSOS${NC}"
echo -e "${BLUE}----------------------------------------${NC}"

CSV_PID=$(pgrep -f "csv_to_metrics" | head -1)
RAPP_PID=$(pgrep -f "rapp_orchestrator" | head -1)
NS3_PID=$(pgrep -f "ns3.42" | head -1)

echo -n "  Leitor de Métricas: "
if [ ! -z "$CSV_PID" ]; then
    echo -e "${GREEN}${BOLD}EM EXECUÇÃO${NC} (PID: $CSV_PID)"
else
    echo -e "${RED}${BOLD}PARADO${NC}"
fi

echo -n "  Orquestrador rApp:  "
if [ ! -z "$RAPP_PID" ]; then
    echo -e "${GREEN}${BOLD}EM EXECUÇÃO${NC} (PID: $RAPP_PID)"
else
    echo -e "${RED}${BOLD}PARADO${NC}"
fi

echo -n "  Simulador ns-3:     "
if [ ! -z "$NS3_PID" ]; then
    echo -e "${GREEN}${BOLD}EM EXECUÇÃO${NC} (PID: $NS3_PID)"
else
    echo -e "${YELLOW}${BOLD}NÃO INICIADO${NC}"
fi

# ============================================
# Resumo Final
# ============================================
echo ""
echo -e "${BOLD}╔════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║${NC}                        ${BOLD}RESUMO DA COLETA${NC}                        ${BOLD}║${NC}"
echo -e "${BOLD}╚════════════════════════════════════════════════════════════════╝${NC}"
echo ""

# Verificar se há dados suficientes para ML
if [ "$EXTENDED_COUNT" -lt 60 ]; then
    echo -e "${YELLOW}${BOLD}[COLETANDO]${NC} Dados sendo acumulados... (${EXTENDED_COUNT} registros)"
    echo "             Mínimo recomendado para ML: 60 registros"
elif [ "$EXTENDED_COUNT" -lt 300 ]; then
    echo -e "${YELLOW}${BOLD}[TREINAMENTO]${NC} Base suficiente para análise básica (${EXTENDED_COUNT} registros)"
    echo "               Para melhor acurácia, continue a coleta."
else
    echo -e "${GREEN}${BOLD}[PRONTO]${NC} Dataset robusto para Machine Learning completo (${EXTENDED_COUNT} registros)"
fi

echo ""
echo "Para monitorar continuamente:"
echo "  watch -n 5 ./monitor_data_collection.sh"
echo ""
