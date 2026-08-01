#!/bin/bash
# GreenRAN Socket Monitor - Loop continuo
echo "Monitorando sockets GreenRAN..."
while true; do
    clear
    echo "=== Monitoramento de Sockets GreenRAN ==="
    echo "Data: $(date)"
    echo ""
    
    SOCKETS=("/tmp/slicer.sock" "/tmp/energy_saver.sock")
    
    for s in "${SOCKETS[@]}"; do
        if [ -S "$s" ]; then
            echo "Socket $s: [OK] Ativo"
        else
            echo "Socket $s: [MISSING] Não encontrado"
        fi
    done
    
    echo ""
    echo "Pressione Ctrl+C para sair."
    sleep 2
done
