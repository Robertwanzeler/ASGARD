#!/bin/bash

# Script para executar Near-RT RIC em terminal dedicado
# Uso: ./run_ric_terminal.sh

echo "Iniciando Near-RT RIC..."
echo ""

cd /home/robert/orange_nuclear

export LD_LIBRARY_PATH=/home/robert/orange_nuclear/flexric/build_e2ap_v1/src/ric:/home/robert/orange_nuclear/flexric_lib:/home/robert/orange_nuclear/flexric/build_e2ap_v1/src/xApp:$LD_LIBRARY_PATH

# Executar RIC
./flexric/build_e2ap_v1/examples/ric/nearRT-RIC -c flexric/flexric.conf -p flexric_lib/
