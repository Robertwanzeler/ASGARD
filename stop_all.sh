#!/bin/bash
# GreenRAN O-RAN - Script para Parar Todos os Processos
# ===========================================

echo "Parando todos os processos GreenRAN O-RAN..."

# Parar rApp
pkill -9 -f "rapp_orchestrator" 2>/dev/null && echo "rApp parado" || true

# Parar csv_to_metrics.py
pkill -9 -f "csv_to_metrics" 2>/dev/null && echo "csv_to_metrics.py parado" || true

# Parar xApps
pkill -9 -f "xapp" 2>/dev/null && echo "xApps parados" || true

# Parar ns-3
pkill -9 -f "ns3.42" 2>/dev/null && echo "ns-3 parado" || true

# Parar nearRT-RIC
pkill -9 -f "nearRT-RIC" 2>/dev/null && echo "nearRT-RIC parado" || true

echo "Pronto!"
