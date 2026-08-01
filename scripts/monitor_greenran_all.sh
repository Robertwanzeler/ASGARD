#!/bin/bash
# GreenRAN All-in-One Monitor
# Este script automatiza o monitoramento de todos os componentes críticos.

SESSION="greenran_monitor"

# Verifica se a sessão já existe, se sim, conecta a ela
if tmux has-session -t $SESSION 2>/dev/null; then
    echo "Sessão já existe. Conectando..."
    tmux attach -t $SESSION
    exit 0
fi

# Inicia nova sessão em background
tmux new-session -d -s "$SESSION" "watch -n 2 cat /home/robert/orange_nuclear/runs/tasam_article_ns3_collection/tasam_true_online_real/true_online_status.json"

# Divide a tela para os logs
tmux split-window -h "tail -f /home/robert/orange_nuclear/runs/tasam_article_ns3_collection/ns3.log"
tmux split-window -v "tail -f /home/robert/orange_nuclear/runs/tasam_article_ns3_collection/rapp.log"
tmux select-pane -t 0
tmux split-window -v "/home/robert/orange_nuclear/scripts/monitor_sockets.sh && read -p 'Pressione enter para fechar...'"

# Seleciona o primeiro painel e anexa
tmux select-pane -t 0
tmux attach -t "$SESSION"
