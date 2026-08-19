#!/bin/bash

# =============================================================================
# Monitor de Logs em Tempo Real
# =============================================================================
# Execute este script em um terminal separado enquanto a simulação roda
# Uso: ./monitor_logs.sh
# =============================================================================

# Cores
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
MAGENTA='\033[0;35m'
NC='\033[0m' # No Color

clear
echo -e "${CYAN}============================================${NC}"
echo -e "${CYAN}  Monitor de Logs - GreenRAN O-RAN${NC}"
echo -e "${CYAN}============================================${NC}"
echo ""

# Contador de linhas anteriores
SLICER_LINE=0
ENERGY_LINE=0
NS3_LINE=0

while true; do
    # Verificar arquivos
    if [ -f /tmp/xapp_slicer.log ]; then
        SLICER_TOTAL=$(wc -l < /tmp/xapp_slicer.log)
    else
        SLICER_TOTAL=0
    fi
    
    if [ -f /tmp/xapp_energy.log ]; then
        ENERGY_TOTAL=$(wc -l < /tmp/xapp_energy.log)
    else
        ENERGY_TOTAL=0
    fi
    
    if [ -f /tmp/ns3.log ]; then
        NS3_TOTAL=$(wc -l < /tmp/ns3.log)
    else
        NS3_TOTAL=0
    fi
    
    # Verificar se há novas linhas
    if [ $SLICER_TOTAL -gt $SLICER_LINE ] || [ $ENERGY_TOTAL -gt $ENERGY_LINE ]; then
        clear
        echo -e "${CYAN}============================================${NC}"
        echo -e "${CYAN}  Monitor de Logs - GreenRAN O-RAN${NC}"
        echo -e "${CYAN}  $(date '+%H:%M:%S')${NC}"
        echo -e "${CYAN}============================================${NC}"
        echo ""
        
        # SLICER - últimas métricas
        echo -e "${MAGENTA}>>> XAPP SLICER <<<${NC}"
        grep -E "CAMERA [0-9]+|UE [0-9]+|volume_dl|pacotes_dl|bitrate_dl_kbps|delay_dl_us|problema_principal|acao.*INTERVIR|acao.*NAO" /tmp/xapp_slicer.log 2>/dev/null | tail -8
        echo ""
        
        # ENERGY - últimas métricas
        echo -e "${GREEN}>>> XAPP ENERGY SAVER <<<${NC}"
        grep -E "CAMERA [0-9]+|UE [0-9]+|volume_dl|pacotes_dl|bitrate_dl_kbps|delay_dl_us|ENERGY.*acao" /tmp/xapp_energy.log 2>/dev/null | tail -8
        echo ""
        
        # ns3 status
        echo -e "${BLUE}>>> NS3 STATUS <<<${NC}"
        grep -E "Started|Simulator|seconds|connected|E2" /tmp/ns3.log 2>/dev/null | tail -3
    fi
    
    # Atualizar contadores
    SLICER_LINE=$SLICER_TOTAL
    ENERGY_LINE=$ENERGY_TOTAL
    NS3_LINE=$NS3_TOTAL
    
    # Verificar se processos ainda estão rodando
    if ! pgrep -f "ns3.42-scenario-base" > /dev/null; then
        echo ""
        echo -e "${RED}>>> SIMULAÇÃO TERMINOU <<<${NC}"
        break
    fi
    
    sleep 2
done
