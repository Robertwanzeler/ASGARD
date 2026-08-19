#!/bin/bash
# GreenRAN O-RAN - Monitor de Intenções
# ===========================================
# Monitora os arquivos de intenção dos xApps
# Útil para ver as decisões em tempo real

echo "=========================================="
echo "  GreenRAN O-RAN - Monitor de Intenções"
echo "=========================================="
echo ""
echo "Pressione Ctrl+C para sair"
echo ""

# Criar diretório se não existir
mkdir -p /tmp/xapp_intents

# Monitorar continuamente
watch -n 1 'echo "=== $(date) ===" && echo "" && echo "--- /tmp/xapp_intents/slicer.txt ---" && cat /tmp/xapp_intents/slicer.txt 2>/dev/null || echo "(não existe)" && echo "" && echo "--- /tmp/xapp_intents/energy_saver.txt ---" && cat /tmp/xapp_intents/energy_saver.txt 2>/dev/null || echo "(não existe)" && echo "" && echo "--- /tmp/xapp_intents/rapp_decision.txt ---" && cat /tmp/xapp_intents/rapp_decision.txt 2>/dev/null || echo "(não existe - rApp não implementado)"'
