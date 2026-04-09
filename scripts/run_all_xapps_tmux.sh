#!/bin/bash

# Script Master para executar O-RAN com tmux
# Abre 3 terminais: RIC, Slicer, Energy Saver
# Uso: ./run_all_xapps_tmux.sh

# Cores para output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${BLUE}=========================================="
echo -e "  O-RAN xApps Manager (tmux)"
echo -e "==========================================${NC}"
echo ""

# Verificar se tmux está instalado
if ! command -v tmux &> /dev/null; then
    echo -e "${RED}ERRO: tmux não está instalado${NC}"
    echo "Instale com: sudo apt-get install tmux"
    exit 1
fi

# Verificar se sessão já existe
SESSION_NAME="oran_xapps"

# Função para limpar sessão existente
cleanup() {
    echo -e "${YELLOW}Limpando sessões anteriores...${NC}"
    tmux kill-session -t $SESSION_NAME 2>/dev/null
    sleep 1
}

# Função para iniciar sessão
start_session() {
    # Iniciar nova sessão detached
    tmux new-session -d -s $SESSION_NAME -n "RIC"
    
    # Configurar janela 1 - RIC
    tmux send-keys -t $SESSION_NAME:0 "cd /home/robert/orange_nuclear" C-m
    tmux send-keys -t $SESSION_NAME:0 "export LD_LIBRARY_PATH=/home/robert/orange_nuclear/flexric/build_e2ap_v1/src/ric:/home/robert/orange_nuclear/flexric_lib:/home/robert/orange_nuclear/flexric/build_e2ap_v1/src/x:\$LD_LIBRARY_PATH" C-m
    tmux send-keys -t $SESSION_NAME:0 "./flexric/build_e2ap_v1/examples/ric/nearRT-RIC -c flexric/flexric.conf -p flexric_lib/" C-m
    
    # Criar janela 2 - xApp Slicer
    tmux new-window -t $SESSION_NAME -n "SLICER"
    tmux send-keys -t $SESSION_NAME:1 "cd /home/robert/orange_nuclear" C-m
    tmux send-keys -t $SESSION_NAME:1 "export LD_LIBRARY_PATH=/home/robert/orange_nuclear/flexric/build_e2ap_v1/src/ric:/home/robert/orange_nuclear/flexric_lib:/home/robert/orange_nuclear/flexric/build_e2ap_v1/src/x:\$LD_LIBRARY_PATH" C-m
    tmux send-keys -t $SESSION_NAME:1 "./flexric/build_e2ap_v1/examples/xApp/c/xapp_slicer -c flexric/flexric.conf -p flexric_lib/" C-m
    
    # Criar janela 3 - xApp Energy Saver
    tmux new-window -t $SESSION_NAME -n "ENERGY"
    tmux send-keys -t $SESSION_NAME:2 "cd /home/robert/orange_nuclear" C-m
    tmux send-keys -t $SESSION_NAME:2 "export LD_LIBRARY_PATH=/home/robert/orange_nuclear/flexric/build_e2ap_v1/src/ric:/home/robert/orange_nuclear/flexric_lib:/home/robert/orange_nuclear/flexric/build_e2ap_v1/src/x:\$LD_LIBRARY_PATH" C-m
    tmux send-keys -t $SESSION_NAME:2 "./flexric/build_e2ap_v1/examples/xApp/c/xapp_energy_saver -c flexric/flexric.conf -p flexric_lib/" C-m
}

# Função para mostrar status
show_status() {
    echo ""
    echo -e "${GREEN}Sessão '$SESSION_NAME' iniciada com sucesso!${NC}"
    echo ""
    echo "Janelas disponíveis:"
    echo "  0: RIC (Near-RT RIC)"
    echo "  1: SLICER (xApp Slicer - Segurança/Vigilância)"
    echo "  2: ENERGY (xApp Energy Saver)"
    echo ""
    echo -e "${YELLOW}Para conectar à sessão:${NC}"
    echo "  tmux attach -t $SESSION_NAME"
    echo ""
    echo -e "${YELLOW}Para navegar entre janelas:${NC}"
    echo "  Ctrl+b , depois número da janela (0, 1, 2)"
    echo "  ou: tmux select-window -t $SESSION_NAME:<número>"
    echo ""
    echo -e "${YELLOW}Para sair sem fechar:${NC}"
    echo "  Ctrl+b , depois d (detach)"
    echo ""
    echo -e "${RED}Para encerrar tudo:${NC}"
    echo "  tmux kill-session -t $SESSION_NAME"
    echo ""
}

# Menu
case "${1:-start}" in
    start)
        cleanup
        start_session
        show_status
        ;;
    stop)
        echo -e "${RED}Encerrando sessões...${NC}"
        tmux kill-session -t $SESSION_NAME 2>/dev/null
        echo -e "${GREEN}Encerrado!${NC}"
        ;;
    attach)
        tmux attach -t $SESSION_NAME
        ;;
    status)
        tmux list-windows -t $SESSION_NAME
        ;;
    *)
        echo "Uso: $0 {start|stop|attach|status}"
        echo ""
        echo "Comandos:"
        echo "  start   - Iniciar sessão tmux com RIC, Slicer e Energy Saver"
        echo "  stop    - Encerrar sessão tmux"
        echo "  attach  - Conectar à sessão tmux"
        echo "  status  - Ver status das janelas"
        exit 1
        ;;
esac
