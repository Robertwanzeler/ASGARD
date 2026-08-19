#!/bin/bash

# Script de Teste de Mitigação O-RAN
# Objetivo: Verificar se o RIC bloqueia CONTROL do Energy Saver quando Slicer está em modo crítico

# Cores para output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}=========================================="
echo -e "  TESTE DE MITIGAÇÃO O-RAN"
echo -e "==========================================${NC}"
echo ""

# Função para verificar se tmux está instalado
check_tmux() {
    if ! command -v tmux &> /dev/null; then
        echo -e "${RED}ERRO: tmux não está instalado${NC}"
        echo "Instale com: sudo apt-get install tmux"
        exit 1
    fi
}

# Função para verificar se o RIC está rodando
check_ric_running() {
    if pgrep -f "nearRT-RIC" > /dev/null; then
        echo -e "${GREEN}[✓] RIC está rodando${NC}"
        return 0
    else
        echo -e "${RED}[✗] RIC NÃO está rodando${NC}"
        echo "Inicie o RIC primeiro com: ./run_ric.sh"
        return 1
    fi
}

# Função para ativar modo crítico do Slicer
activate_critical_mode() {
    echo "CRITICAL" > /tmp/slicer_critical_mode
    echo -e "${YELLOW}[ATUALIZADO] Modo crítico ATIVADO${NC}"
    echo "Arquivo /tmp/slicer_critical_mode criado"
}

# Função para desativar modo crítico do Slicer
deactivate_critical_mode() {
    rm -f /tmp/slicer_critical_mode
    echo -e "${GREEN}[ATUALIZADO] Modo crítico DESATIVADO${NC}"
    echo "Arquivo /tmp/slicer_critical_mode removido"
}

# Função para verificar status do modo crítico
check_critical_mode() {
    if [ -f /tmp/slicer_critical_mode ]; then
        echo -e "${RED}[STATUS] Modo crítico: ATIVO${NC}"
    else
        echo -e "${GREEN}[STATUS] Modo crítico: INATIVO${NC}"
    fi
}

# Menu principal
show_menu() {
    echo ""
    echo "Escolha uma opção:"
    echo "  1) Ativar modo crítico do Slicer (simular congestão)"
    echo "  2) Desativar modo crítico do Slicer (rede normal)"
    echo "  3) Verificar status do modo crítico"
    echo "  4) Iniciar teste completo (ativo -> verific -> desativo)"
    echo "  5) Verificar se RIC está rodando"
    echo "  0) Sair"
    echo ""
    echo -n "Opção: "
}

# Teste completo
run_full_test() {
    echo ""
    echo -e "${BLUE}=== INICIANDO TESTE COMPLETO DE MITIGAÇÃO ===${NC}"
    echo ""

    # Verificar se RIC está rodando
    if ! check_ric_running; then
        echo -e "${RED}Execute o RIC primeiro em outro terminal:${NC}"
        echo "  Terminal 1: ./run_ric.sh"
        echo ""
        return 1
    fi

    echo ""
    echo "Passo 1: Ativando modo crítico do Slicer..."
    activate_critical_mode
    sleep 2

    echo ""
    echo "Passo 2: Verificando status..."
    check_critical_mode

    echo ""
    echo "Passo 3: Agora inicie o xApp Energy Saver em outro terminal:"
    echo "  Terminal 2: ./run_energy.sh"
    echo ""
    echo "Verifique os logs do RIC - deve aparecer:"
    echo -e "  ${RED}[MITIGATION] CONTROL from xApp X BLOCKED - Slicer in CRITICAL mode!${NC}"
    echo ""

    read -p "Pressione ENTER quando quiser desativar o modo crítico..."

    echo ""
    echo "Passo 4: Desativando modo crítico do Slicer..."
    deactivate_critical_mode

    echo ""
    echo -e "${GREEN}=== TESTE COMPLETO FINALIZADO ===${NC}"
    echo ""
    echo "Para verificar os logs do RIC, procure por mensagens:"
    echo "  - [MITIGATION] CONTROL...BLOCKED (quando crítico)"
    echo "  - [MITIGATION] Slicer returned to normal mode (quando desativado)"
}

# Verificar tmux
check_tmux

# Loop principal
while true; do
    show_menu
    read -r opt

    case $opt in
        1)
            activate_critical_mode
            ;;
        2)
            deactivate_critical_mode
            ;;
        3)
            check_critical_mode
            ;;
        4)
            run_full_test
            ;;
        5)
            check_ric_running
            ;;
        0)
            echo "Saindo..."
            exit 0
            ;;
        *)
            echo "Opção inválida"
            ;;
    esac
done
