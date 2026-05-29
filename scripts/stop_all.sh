#!/bin/bash
# GreenRAN O-RAN - Script para Parar Todos os Processos
# ===========================================

. "$(dirname "$0")/core_runtime.sh"
load_greenran_runtime

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
pkill -9 -f "watch_marl_runtime_gate.py" 2>/dev/null && echo "Watcher MARL gate parado" || true
pkill -9 -f "app1_vigilancia/backend/app.py" 2>/dev/null && echo "App1-Vigilancia parada" || true
pkill -9 -f "simulate_cameras.py" 2>/dev/null && echo "Simulador App1 parado" || true
pkill -9 -f "app2_monitoramento/backend/app.py" 2>/dev/null && echo "App2-Monitoramento parada" || true
pkill -9 -f "app3_veicular/backend/app.py" 2>/dev/null && echo "App3-Veicular parado" || true
pkill -9 -f "simulate_sensors.py" 2>/dev/null && echo "Simulador App2 parado" || true
pkill -9 -f "push_stats_to_influx.py" 2>/dev/null && echo "Push Stats parado" || true
pkill -9 -f "push_cvar_to_influx.py" 2>/dev/null && echo "Push CVaR parado" || true
pkill -9 -f "push_app1_to_influx.py" 2>/dev/null && echo "Push App1 parado" || true
pkill -9 -f "push_app2_to_influx.py" 2>/dev/null && echo "Push App2 parado" || true

# Limpar processos zumbis (defunct)
echo "Limpando processos zumbis..."
ZOMBIES=$(ps aux | grep -E "defunct|zombie" | grep -v grep | awk '{print $2}' | wc -l)
if [ $ZOMBIES -gt 0 ]; then
    ps aux | grep -E "defunct|zombie" | grep -v grep | awk '{print $2}' | xargs -r kill -9 2>/dev/null || true
    echo "$ZOMBIES processos zumbis eliminados"
fi

# Limpar arquivos PID antigos
echo "Limpando arquivos PID antigos..."
rm -f "$GREENRAN_XAPP_SLICER_PID"
rm -f "$STATE_DIR/xapp_energy_saver.pid"
rm -f "$GREENRAN_XAPP_ENERGY_PID"
rm -f "$GREENRAN_NS3_PID"
rm -f "$GREENRAN_RIC_PID"
rm -f "$GREENRAN_CSV_PID"
rm -f "$GREENRAN_RAPP_PID"
rm -f "$GREENRAN_DASHBOARD_PID"
rm -f "$GREENRAN_WATCHDOG_PID"
rm -f "$GREENRAN_APP1_PID"
rm -f "$GREENRAN_APP1_SIMULATOR_PID"
rm -f "$GREENRAN_PUSH_APP1_PID"
rm -f "$GREENRAN_APP2_PID"
rm -f "$GREENRAN_APP2_SIMULATOR_PID"
rm -f "$GREENRAN_APP3_PID"
rm -f "$GREENRAN_PUSH_APP2_PID"
rm -f "$GREENRAN_MARL_GATE_WATCH_PID"

# Limpar sessões tmux se existirem
tmux kill-session -t greenran 2>/dev/null || true
tmux kill-session -t slicer 2>/dev/null || true
tmux kill-session -t energy 2>/dev/null || true

# Aguardar processos terminarem
sleep 2

# Verificar se ainda há processos rodando
REMAINING=$(ps aux | grep -E "nearRT-RIC|xapp|ns3.42|rapp|csv_to_metrics|app1_vigilancia|app2_monitoramento|app3_veicular|simulate_cameras|simulate_sensors|push_stats_to_influx|push_cvar_to_influx|push_app1_to_influx|push_app2_to_influx|watch_marl_runtime_gate" | grep -v grep | wc -l)
if [ $REMAINING -gt 0 ]; then
    echo "AVISO: $REMAINING processos ainda rodando!"
    ps aux | grep -E "nearRT-RIC|xapp|ns3.42|rapp|csv_to_metrics|app1_vigilancia|app2_monitoramento|app3_veicular|simulate_cameras|simulate_sensors|push_stats_to_influx|push_cvar_to_influx|push_app1_to_influx|push_app2_to_influx|watch_marl_runtime_gate" | grep -v grep
else
    echo "Todos os processos foram eliminados!"
fi

echo "Pronto!"
