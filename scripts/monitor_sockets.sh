#!/bin/bash
# GreenRAN Socket Monitor - Loop continuo
echo "Monitorando sockets GreenRAN..."
while true; do
    clear
    echo "=== Monitoramento de Sockets GreenRAN ==="
    echo "Data: $(date)"
    echo ""
    
    state_dir="${GREENRAN_STATE_DIR:-/tmp}"
    SOCKETS=(
        "${GREENRAN_SLICER_SOCKET_PATH:-$state_dir/sockets/slicer.sock}"
        "${GREENRAN_ENERGY_SOCKET_PATH:-$state_dir/sockets/energy_saver.sock}"
    )
    
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
