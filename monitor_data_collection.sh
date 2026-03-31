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

# Threshold para filtrar dados aberrantes (bug do tempo de simulação)
# NOTA: 100ms é o SLA! Violações reais (>100ms) devem ser visíveis.
# O bug do ns-3 gera valores absurdos (>5000ms), então usamos 500ms como teto.
MAX_VALID_LATENCY_US=500000  # 500ms em microsegundos (filtra bug, mantém violações reais)

# Função para query SQLite via Python
query_sqlite() {
    python3 -c "
import sqlite3
import sys
try:
    conn = sqlite3.connect('$DB_PATH')
    c = conn.execute('''$1''')
    r = c.fetchone()
    print(r[0] if r and r[0] is not None else '0')
    conn.close()
except Exception as e:
    print('0')
" 2>/dev/null || echo "0"
}

# Contadores
EXTENDED_COUNT=$(query_sqlite "SELECT COUNT(*) FROM extended_metrics")
UE_COUNT=$(query_sqlite "SELECT COUNT(*) FROM ue_metrics")
METRICS_COUNT=$(query_sqlite "SELECT COUNT(*) FROM metrics_history")
DECISIONS_COUNT=$(query_sqlite "SELECT COUNT(*) FROM decisions_history")

# Forçar inteiros para operações matemáticas
EXTENDED_COUNT=${EXTENDED_COUNT:-0}
UE_COUNT=${UE_COUNT:-0}
METRICS_COUNT=${METRICS_COUNT:-0}
DECISIONS_COUNT=${DECISIONS_COUNT:-0}

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

# Validar que há dados antes de calcular tempo
if [ ! -z "$PRIMEIRO" ] && [ "$PRIMEIRO" != "None" ] && [ "$PRIMEIRO" != "0" ]; then
    echo -e "  Primeiro registro: ${BOLD}$PRIMEIRO${NC}"
    echo -e "  Último registro:  ${BOLD}$ULTIMO${NC}"
    
    # Tempo decorrido
    PRIMEIRO_EPOCH=$(query_sqlite "SELECT MIN(timestamp) FROM extended_metrics")
    if [ ! -z "$PRIMEIRO_EPOCH" ] && [ "$PRIMEIRO_EPOCH" != "None" ]; then
        AGORA=$(date +%s)
        DECORRIDO=$((AGORA - PRIMEIRO_EPOCH))
        HORAS=$((DECORRIDO / 3600))
        MINUTOS=$(((DECORRIDO % 3600) / 60))
        SEGUNDOS=$((DECORRIDO % 60))
        echo -e "  Tempo de coleta: ${GREEN}${BOLD}${HORAS}h ${MINUTOS}m ${SEGUNDOS}s${NC}"
    else
        echo -e "  Tempo de coleta: ${YELLOW}N/A${NC}"
    fi
    
    # Mostrar informação sobre zeros nos dados
    ZERO_COUNT=$(query_sqlite "SELECT SUM(zero_samples) FROM extended_metrics")
    if [ ! -z "$ZERO_COUNT" ] && [ "$ZERO_COUNT" != "None" ] && [ "$ZERO_COUNT" -gt 0 ] 2>/dev/null; then
        if [[ "$ZERO_COUNT" =~ ^[0-9]+$ ]] && [ "$ZERO_COUNT" -gt 0 ]; then
            echo ""
            echo -e "  ${YELLOW}Nota: ${ZERO_COUNT} samples com delay=0 detectados${NC}"
            echo -e "  ${YELLOW}(valores filtrados das métricas Min/P5)${NC}"
        fi
    fi
else
    echo -e "  Primeiro registro: ${YELLOW}N/A${NC}"
    echo -e "  Último registro:  ${YELLOW}N/A${NC}"
    echo -e "  Tempo de coleta:  ${YELLOW}Aguardando dados...${NC}"
fi

# ============================================
# 3. QUALIDADE DOS DADOS
# ============================================
echo ""
echo -e "${RED}${BOLD}[3] QUALIDADE DOS DADOS (FILTRAGEM)${NC}"
echo -e "${RED}----------------------------------------${NC}"

# Contar dados aberrantes (bug do tempo de simulação)
TOTAL_RAW=$(query_sqlite "SELECT COUNT(*) FROM extended_metrics")
VALID_COUNT=$(query_sqlite "SELECT COUNT(*) FROM extended_metrics WHERE global_avg_latency_us < $MAX_VALID_LATENCY_US")
INVALID_COUNT=$(query_sqlite "SELECT COUNT(*) FROM extended_metrics WHERE global_avg_latency_us >= $MAX_VALID_LATENCY_US")

# Garantir inteiros
TOTAL_RAW=${TOTAL_RAW:-0}
VALID_COUNT=${VALID_COUNT:-0}
INVALID_COUNT=${INVALID_COUNT:-0}

if [ "$TOTAL_RAW" -gt 0 ] 2>/dev/null; then
    VALID_PCT=$(python3 -c "print(f'{$VALID_COUNT/$TOTAL_RAW*100:.1f}')")
    INVALID_PCT=$(python3 -c "print(f'{$INVALID_COUNT/$TOTAL_RAW*100:.1f}')")
    
    echo -e "  Registros Válidos (<500ms):    ${GREEN}${BOLD}$VALID_COUNT${NC} (${GREEN}${VALID_PCT}%${NC})"
    echo -e "  Registros Aberrantes (>500ms): ${RED}${BOLD}$INVALID_COUNT${NC} (${RED}${INVALID_PCT}%${NC})"
    echo -e "  Total Bruto:                   ${BOLD}$TOTAL_RAW${NC}"
    
    if [ "$INVALID_COUNT" -gt 0 ] 2>/dev/null; then
        echo ""
        echo -e "  ${YELLOW}⚠ NOTA: Dados aberrantes são bugs do tempo de simulação (ns-3)${NC}"
        echo -e "  ${YELLOW}  (latências >500ms são bugs, não violações reais de SLA)${NC}"
        echo -e "  ${YELLOW}  Filtrando automaticamente para métricas corretas.${NC}"
        echo -e "  ${YELLOW}  Violações reais de SLA (>100ms) são mantidas!${NC}"
    fi
fi

# ============================================
# 4. Métricas de Latência (DADOS FILTRADOS)
# ============================================
echo ""
echo -e "${YELLOW}${BOLD}[4] MÉTRICAS DE LATÊNCIA (DADOS VÁLIDOS)${NC}"
echo -e "${YELLOW}----------------------------------------${NC}"

if [ "$VALID_COUNT" -gt 0 ] 2>/dev/null; then
    # Métricas básicas (FILTRADAS - excluindo dados aberrantes)
    LAT_AVG=$(query_sqlite "SELECT AVG(global_avg_latency_us)/1000 FROM extended_metrics WHERE global_avg_latency_us < $MAX_VALID_LATENCY_US")
    LAT_WORST=$(query_sqlite "SELECT MAX(global_worst_latency_us)/1000 FROM extended_metrics WHERE global_avg_latency_us < $MAX_VALID_LATENCY_US")
    LAT_MIN=$(query_sqlite "SELECT MIN(global_min_latency_us)/1000 FROM extended_metrics WHERE global_min_latency_us > 0 AND global_avg_latency_us < $MAX_VALID_LATENCY_US")
    JITTER=$(query_sqlite "SELECT AVG(global_jitter_us)/1000 FROM extended_metrics WHERE global_avg_latency_us < $MAX_VALID_LATENCY_US")
    THROUGHPUT=$(query_sqlite "SELECT AVG(throughput_kbps) FROM extended_metrics WHERE global_avg_latency_us < $MAX_VALID_LATENCY_US")

    # Métricas robustas (percentis) - FILTRADAS
    LAT_MINNZ=$(query_sqlite "SELECT AVG(latency_min_nonzero_us)/1000 FROM extended_metrics WHERE global_avg_latency_us < $MAX_VALID_LATENCY_US")
    LAT_P95=$(query_sqlite "SELECT AVG(latency_p95_us)/1000 FROM extended_metrics WHERE global_avg_latency_us < $MAX_VALID_LATENCY_US")

    # Fallback: usar latency_min_nonzero_us se global_min_latency_us for 0 ou vazio
    LAT_MIN_FALLBACK=$(query_sqlite "SELECT AVG(latency_min_nonzero_us)/1000 FROM extended_metrics WHERE global_avg_latency_us < $MAX_VALID_LATENCY_US")
    if [ -z "$LAT_MIN" ] || [ "$LAT_MIN" = "None" ]; then
        LAT_MIN="$LAT_MIN_FALLBACK"
    fi

    # Fallback P5: usar latency_min_nonzero_us
    LAT_P5="$LAT_MIN_FALLBACK"

    # Formatação com cores para métricas críticas
    COLOR_LAT_AVG="${YELLOW}"
    COLOR_LAT_WORST="${RED}"
    COLOR_LAT_MIN="${GREEN}"
    COLOR_THROUGHPUT="${CYAN}"

    echo -e "  ${CYAN}Métricas Básicas (filtradas):${NC}"
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
else
    echo -e "  ${YELLOW}    Aguardando dados...${NC}"
fi

# ============================================
# 5. UEs e Câmeras
# ============================================
echo ""
echo -e "${MAGENTA}${BOLD}[5] UEs E CÂMERAS${NC}"
echo -e "${MAGENTA}----------------------------------------${NC}"

if [ "$VALID_COUNT" -gt 0 ] 2>/dev/null; then
    CAM_ATIVAS=$(query_sqlite "SELECT AVG(total_active_cameras) FROM extended_metrics WHERE global_avg_latency_us < $MAX_VALID_LATENCY_US")
    UE_ATIVAS=$(query_sqlite "SELECT AVG(total_active_ues) FROM extended_metrics WHERE global_avg_latency_us < $MAX_VALID_LATENCY_US")
    UE_CRITICAS=$(query_sqlite "SELECT AVG(total_critical_ues) FROM extended_metrics WHERE global_avg_latency_us < $MAX_VALID_LATENCY_US")

    echo -e "  Câmeras Ativas (média):  ${BOLD}${GREEN}${CAM_ATIVAS:-N/A}${NC}"
    echo -e "  UEs Ativas (média):     ${BOLD}${UE_ATIVAS:-N/A}${NC}"
    echo -e "  UEs Críticas (média):   ${BOLD}${RED}${UE_CRITICAS:-N/A}${NC}"

    # Mostrar proporção
    if [ ! -z "$CAM_ATIVAS" ] && [ ! -z "$UE_ATIVAS" ] && [ "$CAM_ATIVAS" != "N/A" ] && [ "$UE_ATIVAS" != "N/A" ]; then
        TOTAL=$(python3 -c "print($UE_ATIVAS + $CAM_ATIVAS)")
        echo ""
        echo -e "  ${CYAN}Proporção: ${UE_ATIVAS} UEs + ${CAM_ATIVAS} Câmeras = ${TOTAL} total${NC}"
    fi
else
    echo -e "  Câmeras Ativas (média):  ${YELLOW}N/A${NC}"
    echo -e "  UEs Ativas (média):     ${YELLOW}N/A${NC}"
    echo -e "  UEs Críticas (média):   ${YELLOW}N/A${NC}"
fi

# ============================================
# 6. Decisões do rApp
# ============================================
echo ""
echo -e "${CYAN}${BOLD}[6] DECISÕES DO RAPP${NC}"
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
# 7. Tamanho do Database
# ============================================
echo ""
echo -e "${BOLD}[7] TAMANHO DO DATABASE${NC}"
echo "----------------------------------------"

TAMANHO=$(du -h "$DB_PATH" 2>/dev/null | cut -f1 || echo "N/A")
echo -e "  Arquivo: ${BOLD}$DB_PATH${NC}"
echo -e "  Tamanho: ${BOLD}$TAMANHO${NC}"

# ============================================
# 8. Métricas em Tempo Real
# ============================================
echo ""
echo -e "${BOLD}${CYAN}[8] MÉTRICAS EM TEMPO REAL${NC}"
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
# 9. Status dos Processos
# ============================================
echo ""
echo -e "${BOLD}${BLUE}[9] STATUS DOS PROCESSOS${NC}"
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
# 10. Economia de Energia
# ============================================
echo ""
echo -e "${BOLD}${GREEN}[10] ECONOMIA DE ENERGIA${NC}"
echo -e "${GREEN}----------------------------------------${NC}"

# Query energy commands via Python
ENERGY_STATS=$(python3 -c "
import sqlite3
import json

conn = sqlite3.connect('$DB_PATH')
c = conn.cursor()

c.execute('''
    SELECT 
        command,
        COUNT(*) as count,
        AVG(power_percent) as avg_power
    FROM energy_commands
    GROUP BY command
    ORDER BY count DESC
''')

results = {'total': 0, 'commands': {}, 'avg_power': 0, 'savings': 0}
total_power = 0
total_count = 0

for row in c.fetchall():
    cmd = row[0]
    count = row[1]
    avg_power = row[2] or 100
    results['commands'][cmd] = {'count': count, 'avg_power': avg_power}
    total_power += avg_power * count
    total_count += count

if total_count > 0:
    results['total'] = total_count
    results['avg_power'] = round(total_power / total_count, 1)
    results['savings'] = round(100 - results['avg_power'], 1)

print(json.dumps(results))
conn.close()
" 2>/dev/null)

if [ ! -z "$ENERGY_STATS" ] && [ "$ENERGY_STATS" != "null" ]; then
    TOTAL_CMDS=$(echo "$ENERGY_STATS" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('total',0))" 2>/dev/null)
    AVG_POWER=$(echo "$ENERGY_STATS" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('avg_power',0))" 2>/dev/null)
    SAVINGS=$(echo "$ENERGY_STATS" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('savings',0))" 2>/dev/null)
    
    if [ "$TOTAL_CMDS" -gt 0 ] 2>/dev/null; then
        echo -e "  Total de comandos:    ${BOLD}$TOTAL_CMDS${NC}"
        echo -e "  Potência média:      ${BOLD}${AVG_POWER}%${NC}"
        echo -e "  Economia média:      ${GREEN}${BOLD}${SAVINGS}%${NC}"
        echo ""
        echo -e "  ${CYAN}Distribuição por modo:${NC}"
        
        # Full Power
        FP_COUNT=$(echo "$ENERGY_STATS" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['commands'].get('FULL_POWER',{}).get('count',0))" 2>/dev/null)
        FP_PCT=$(python3 -c "print(round($FP_COUNT/$TOTAL_CMDS*100,1))" 2>/dev/null || echo "0")
        echo -n "    FULL_POWER:         $FP_COUNT ($FP_PCT%)"
        if [ "$FP_PCT" -gt 50 ]; then echo " ⚠️ ALTA POTÊNCIA"; else echo ""; fi
        
        # CONDITIONAL_REDUCE (70%)
        CR_COUNT=$(echo "$ENERGY_STATS" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['commands'].get('CONDITIONAL_REDUCE',{}).get('count',0))" 2>/dev/null)
        if [ ! -z "$CR_COUNT" ] && [ "$CR_COUNT" -gt 0 ]; then
            CR_PCT=$(python3 -c "print(round($CR_COUNT/$TOTAL_CMDS*100,1))" 2>/dev/null || echo "0")
            echo -e "    CONDITIONAL:        $CR_COUNT ($CR_PCT%) - 70% potência"
        fi
        
        # POWER_DOWN (50%)
        PD_COUNT=$(echo "$ENERGY_STATS" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['commands'].get('POWER_DOWN',{}).get('count',0))" 2>/dev/null)
        if [ ! -z "$PD_COUNT" ] && [ "$PD_COUNT" -gt 0 ]; then
            PD_PCT=$(python3 -c "print(round($PD_COUNT/$TOTAL_CMDS*100,1))" 2>/dev/null || echo "0")
            echo -e "    POWER_DOWN:         $PD_COUNT ($PD_PCT%) - 50% potência"
        fi
        
        # POWER_DOWN_ECO (25%)
        PE_COUNT=$(echo "$ENERGY_STATS" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['commands'].get('POWER_DOWN_ECO',{}).get('count',0))" 2>/dev/null)
        if [ ! -z "$PE_COUNT" ] && [ "$PE_COUNT" -gt 0 ]; then
            PE_PCT=$(python3 -c "print(round($PE_COUNT/$TOTAL_CMDS*100,1))" 2>/dev/null || echo "0")
            echo -e "    POWER_DOWN_ECO:     $PE_COUNT ($PE_PCT%) - 25% potência"
        fi
        
        # POWER_DOWN_ECO
        PE_COUNT=$(echo "$ENERGY_STATS" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['commands'].get('POWER_DOWN_ECO',{}).get('count',0))" 2>/dev/null)
        if [ ! -z "$PE_COUNT" ] && [ "$PE_COUNT" -gt 0 ]; then
            PE_PCT=$(python3 -c "print(round($PE_COUNT/$TOTAL_CMDS*100,1))" 2>/dev/null || echo "0")
            echo -e "    POWER_DOWN_ECO:     $PE_COUNT ($PE_PCT%) - 10% potência"
        fi
    else
        echo -e "  ${YELLOW}Nenhum comando de energia registrado ainda${NC}"
    fi
else
    echo -e "  ${YELLOW}Nenhum comando de energia registrado ainda${NC}"
fi

# ============================================
# Resumo Final
# ============================================
echo ""
echo -e "${BOLD}╔════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║${NC}                        ${BOLD}RESUMO DA COLETA${NC}                        ${BOLD}║${NC}"
echo -e "${BOLD}╚════════════════════════════════════════════════════════════════╝${NC}"
echo ""

# Verificar se há dados suficientes para ML (usando dados válidos)
if [ "$VALID_COUNT" -gt 0 ] 2>/dev/null; then
    if [ "$VALID_COUNT" -lt 60 ]; then
        echo -e "${YELLOW}${BOLD}[COLETANDO]${NC} Dados sendo acumulados... (${VALID_COUNT} registros válidos)"
        echo "             Mínimo recomendado para ML: 60 registros"
    elif [ "$VALID_COUNT" -lt 300 ]; then
        echo -e "${YELLOW}${BOLD}[TREINAMENTO]${NC} Base suficiente para análise básica (${VALID_COUNT} registros válidos)"
        echo "               Para melhor acurácia, continue a coleta."
    else
        echo -e "${GREEN}${BOLD}[PRONTO]${NC} Dataset robusto para Machine Learning completo (${VALID_COUNT} registros válidos)"
    fi
else
    echo -e "${YELLOW}${BOLD}[AGUARDANDO]${NC} Execute uma simulação para iniciar a coleta de dados"
    echo "             Exemplo: ./run_greenran_v2.sh"
fi

echo ""
echo "Para monitorar continuamente:"
echo "  watch -n 5 ./monitor_data_collection.sh"
echo ""
