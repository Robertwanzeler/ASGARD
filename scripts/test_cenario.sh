#!/bin/bash
set -euo pipefail

. "$(dirname "$0")/core_runtime.sh"
load_greenran_runtime
# GreenRAN - Teste Rápido de Cenário
# Testa se o cenário está gerando congestionamento
# ======================================

cd "${BASE_DIR:-$HOME/orange_nuclear}"

resolve_ric_binary() {
    local candidate
    for candidate in \
        "$RIC_DIR/examples/ric/nearRT-RIC" \
        "$BASE_DIR/flexric/build/examples/ric/nearRT-RIC"; do
        if [ -x "$candidate" ]; then
            echo "$candidate"
            return 0
        fi
    done
    return 1
}

resolve_ns3_test_binary() {
    local candidates=(
        "$NS3_DIR/build/scratch/ns3.42-scenario-greenran-debug"
        "$NS3_DIR/build/scratch/ns3.42-scenario-greenran-default"
        "$NS3_DIR/build/scratch/ns3.42-Energy_saving_with_cell_utilization_scenario-default"
        "$NS3_DIR/build/scratch/ns3.42-Energy_Saving_with_load_balancing_scenario-default"
    )
    local candidate
    for candidate in "${candidates[@]}"; do
        if [ -x "$candidate" ]; then
            echo "$candidate"
            return 0
        fi
    done
    return 1
}

echo "=========================================="
echo "  Teste Rápido: Cenário com Congestionamento"
echo "=========================================="
echo ""

# Limpar processos
pkill -9 -f "nearRT-RIC" 2>/dev/null || true
pkill -9 -f "xapp" 2>/dev/null || true
pkill -9 -f "ns3.42" 2>/dev/null || true
sleep 2

# LD_LIBRARY_PATH
export LD_LIBRARY_PATH="$RIC_DIR/src/ric:$BASE_DIR/flexric_lib:$RIC_DIR/src/xApp:${LD_LIBRARY_PATH:-}"

: > /tmp/ric_test.log
: > /tmp/ns3_test.log

RIC_BIN="$(resolve_ric_binary || true)"
NS3_BIN="$(resolve_ns3_test_binary || true)"

if [ -z "$RIC_BIN" ]; then
    echo "ERRO: nearRT-RIC não encontrado em $RIC_DIR"
    exit 1
fi

if [ -z "$NS3_BIN" ]; then
    echo "ERRO: binário ns-3 de teste não encontrado em $NS3_DIR/build/scratch"
    exit 1
fi

# Iniciar RIC
echo "[1] Iniciando RIC..."
stdbuf -oL -eL "$RIC_BIN" -c ./flexric/flexric.conf -p ./flexric_lib/ > /tmp/ric_test.log 2>&1 &
RIC_PID=$!
sleep 5
if ! pgrep -f "$(basename "$RIC_BIN")" >/dev/null 2>&1; then
    echo "ERRO: nearRT-RIC encerrou durante o startup."
    [ -f /tmp/ric_test.log ] && tail -n 40 /tmp/ric_test.log
    exit 1
fi

# Iniciar ns-3 (simTime=60s para teste rápido)
echo "[2] Iniciando ns-3 (60s de teste)..."
cd ns-O-RAN-flexric/mmwave-LENA-oran
stdbuf -oL -eL "$NS3_BIN" \
    --e2TermIp=127.0.0.1 \
    --e2lteEnabled=true \
    --e2nrEnabled=true \
    --simTime=60 > /tmp/ns3_test.log 2>&1 &
NS3_PID=$!
sleep 3
if ! pgrep -f "$(basename "$NS3_BIN")" >/dev/null 2>&1; then
    echo "ERRO: ns-3 encerrou durante o startup."
    [ -f /tmp/ns3_test.log ] && tail -n 40 /tmp/ns3_test.log
    exit 1
fi

echo "[3] Aguardando 30s para coleta de métricas..."
sleep 30

echo ""
echo "=========================================="
echo "  Verificando logs..."
echo "=========================================="

# Ver logs do ns-3
echo ""
echo "--- ns-3 Logs (últimas 30 linhas) ---"
tail -30 /tmp/ns3_test.log 2>/dev/null || true

echo ""
echo "--- Verificando handshake E2 ---"
if grep -Eq "E2 node connected|SETUP-REQUEST|SETUP-RESPONSE|Registered E2 Nodes" /tmp/ric_test.log /tmp/ns3_test.log 2>/dev/null; then
    echo "Handshake E2 detectado"
else
    echo "ERRO: não houve evidência de handshake E2 nos logs"
    echo ""
    echo "--- RIC Logs (últimas 30 linhas) ---"
    tail -30 /tmp/ric_test.log 2>/dev/null || true
    exit 1
fi

if ! kill -0 "$RIC_PID" 2>/dev/null; then
    echo "ERRO: nearRT-RIC encerrou durante a coleta"
    tail -30 /tmp/ric_test.log 2>/dev/null || true
    exit 1
fi

if ! kill -0 "$NS3_PID" 2>/dev/null; then
    echo "ERRO: ns-3 encerrou antes do esperado"
    tail -30 /tmp/ns3_test.log 2>/dev/null || true
    exit 1
fi

# Verificar se há rajadas
echo ""
echo "--- Verificando rajadas ---"
CAMERA_COUNT="$(grep -c "CAMERA" /tmp/ns3_test.log 2>/dev/null || true)"
echo "Ocorrências de CAMERA no log: ${CAMERA_COUNT}"

echo ""
echo "=========================================="
echo "  Validando geração de traços..."
echo "=========================================="
echo ""
PDCP_TRACE="$NS3_DIR/DlPdcpStats.txt"
MAC_TRACE="$NS3_DIR/DlMacStats.txt"

if [ ! -s "$PDCP_TRACE" ] && [ ! -s "$MAC_TRACE" ]; then
    echo "ERRO: ns-3 não gerou traços PDCP/MAC após a coleta"
    ls -l "$NS3_DIR"/Dl*Stats.txt 2>/dev/null || true
    exit 1
fi

echo "Traços detectados:"
[ -s "$PDCP_TRACE" ] && echo "  - $PDCP_TRACE"
[ -s "$MAC_TRACE" ] && echo "  - $MAC_TRACE"

kill "$NS3_PID" 2>/dev/null || true
sleep 1

kill "$RIC_PID" 2>/dev/null || true

echo ""
echo "Teste rápido concluído com sucesso."
