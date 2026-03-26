#!/bin/bash
# GreenRAN O-RAN - Script para Parar Todos os Processos
# ===========================================

echo "Parando todos os processos GreenRAN O-RAN..."

# Parar rApp
pkill -9 -f "rapp_orchestrator" 2>/dev/null && echo "rApp parado" || true

# Parar csv_to_metrics.py
pkill -9 -f "csv_to_metrics" 2>/dev/null && echo "csv_to_metrics.py parado" || true

# Parar xApps
pkill -9 -f "xapp_slicer" 2>/dev/null && echo "xApp Slicer parado" || true
pkill -9 -f "xapp_energy" 2>/dev/null && echo "xApp Energy parado" || true
pkill -9 -f "xapp" 2>/dev/null && echo "Outros xApps parados" || true

# Parar ns-3
pkill -9 -f "ns3.42" 2>/dev/null && echo "ns-3 parado" || true

# Parar nearRT-RIC
pkill -9 -f "nearRT-RIC" 2>/dev/null && echo "nearRT-RIC parado" || true

# Parar watchdog e dashboard
pkill -9 -f "watchdog_xapps" 2>/dev/null && echo "Watchdog parado" || true
pkill -9 -f "rapp_dashboard" 2>/dev/null && echo "Dashboard parado" || true

# Limpar processos zumbis (defunct)
echo "Limpando processos zumbis..."
ZOMBIES=$(ps aux | grep -E "defunct|zombie" | grep -v grep | awk '{print $2}' | wc -l)
if [ $ZOMBIES -gt 0 ]; then
    ps aux | grep -E "defunct|zombie" | grep -v grep | awk '{print $2}' | xargs -r kill -9 2>/dev/null || true
    echo "$ZOMBIES processos zumbis eliminados"
fi

# Limpar arquivos PID antigos
echo "Limpando arquivos PID antigos..."
rm -f /tmp/xapp_slicer.pid
rm -f /tmp/xapp_energy_saver.pid
rm -f /tmp/xapp_energy.pid
rm -f /tmp/ns3.pid
rm -f /tmp/ric.pid
rm -f /tmp/csv_metrics.pid
rm -f /tmp/rapp.pid
rm -f /tmp/dashboard.pid
rm -f /tmp/watchdog.pid

# Limpar sessões tmux se existirem
tmux kill-session -t greenran 2>/dev/null || true
tmux kill-session -t slicer 2>/dev/null || true
tmux kill-session -t energy 2>/dev/null || true

# Aguardar processos terminarem
sleep 2

# Verificar se ainda há processos rodando
REMAINING=$(ps aux | grep -E "nearRT-RIC|xapp|ns3.42|rapp|csv_to_metrics" | grep -v grep | wc -l)
if [ $REMAINING -gt 0 ]; then
    echo "AVISO: $REMAINING processos ainda rodando!"
    ps aux | grep -E "nearRT-RIC|xapp|ns3.42|rapp|csv_to_metrics" | grep -v grep
else
    echo "Todos os processos foram eliminados!"
fi

echo "Pronto!"
