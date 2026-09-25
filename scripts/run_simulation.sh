#!/bin/bash
set -euo pipefail

. "$(dirname "$0")/core_runtime.sh"
load_greenran_runtime

echo "=========================================="
echo "  O-RAN Simulation - Integrated Runtime"
echo "=========================================="

BASE_DIR="${BASE_DIR:-$PROJECT_DIR}"

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

resolve_xapp_binary() {
    local name="$1"
    find "$RIC_DIR/examples/xApp" -type f -name "$name" | sort | head -n 1
}

require_started() {
    local pid="$1"
    local name="$2"
    local log_file="$3"
    sleep 1
    if ! kill -0 "$pid" 2>/dev/null; then
        echo ""
        echo "ERRO: $name encerrou durante o startup."
        [ -f "$log_file" ] && tail -n 40 "$log_file"
        exit 1
    fi
}

wait_for_file() {
    local path="$1"
    local timeout_s="$2"
    local label="$3"
    local i
    for ((i=0; i<timeout_s; i++)); do
        if [ -s "$path" ]; then
            return 0
        fi
        sleep 1
    done

    echo ""
    echo "ERRO: $label não foi gerado em ${timeout_s}s."
    return 1
}

wait_for_e2_ready() {
    local timeout_s="${1:-20}"
    local i
    for ((i=0; i<timeout_s; i++)); do
        if grep -Eq "E2 node connected|SETUP-REQUEST|SETUP-RESPONSE|E2setupResponse|Registered E2 Nodes" /tmp/ric.log /tmp/ns3.log 2>/dev/null; then
            return 0
        fi
        sleep 1
    done
    return 1
}

# Configura LD_LIBRARY_PATH
export LD_LIBRARY_PATH="$RIC_DIR/src/ric:$BASE_DIR/flexric_lib:$RIC_DIR/src/xApp:${LD_LIBRARY_PATH:-}"

RIC_BIN="$(resolve_ric_binary || true)"

if [ -z "$RIC_BIN" ]; then
    echo "ERRO: nearRT-RIC não encontrado em $RIC_DIR"
    exit 1
fi

# Mata processos antigos
echo ""
echo "===清理 processos antigos ==="
pkill -f "nearRT-RIC" 2>/dev/null || true
pkill -f "xapp_slicer" 2>/dev/null || true
pkill -f "xapp_energy" 2>/dev/null || true
pkill -f "python3.*rapp_orchestrator" 2>/dev/null || true
pkill -f "python3.*csv_to_metrics.py" 2>/dev/null || true
pkill -f "ns3.42-scenario-zero" 2>/dev/null || true
sleep 2

mkdir -p "$STATE_DIR/xapp_metrics" "$STATE_DIR/xapp_intents" "$STATE_DIR/rapp_policies"
rm -f "$GREENRAN_XAPP_SLICER_PID" "$GREENRAN_XAPP_ENERGY_PID" "$GREENRAN_RAPP_PID" "$GREENRAN_CSV_PID"
rm -f "$STATE_DIR/xapp_metrics/metrics.json" "$STATE_DIR/xapp_metrics/extended_metrics.json"

echo ""
echo "=== 1. Iniciando nearRT-RIC (E2AP v1) ==="
cd $BASE_DIR
stdbuf -oL -eL "$RIC_BIN" -c flexric/flexric.conf -p flexric_lib/ > /tmp/ric.log 2>&1 &
RIC_PID=$!
echo "RIC iniciado (PID: $RIC_PID)"
sleep 3
require_started "$RIC_PID" "nearRT-RIC" /tmp/ric.log

echo ""
echo "=== 2. Iniciando ns3 (scenario-zero com E2) ==="
cd $BASE_DIR/ns-O-RAN-flexric/mmwave-LENA-oran
stdbuf -oL -eL $BASE_DIR/ns-O-RAN-flexric/mmwave-LENA-oran/build/scratch/ns3.42-scenario-zero-default --e2TermIp=127.0.0.1 --e2lteEnabled=true --e2nrEnabled=true --simTime=60 > /tmp/ns3.log 2>&1 &
NS3_PID=$!
echo "ns3 iniciado (PID: $NS3_PID)"
sleep 5
require_started "$NS3_PID" "ns3" /tmp/ns3.log

echo ""
echo "=== 3. Validando handshake E2 ==="
if wait_for_e2_ready 20; then
    echo "Handshake E2 detectado"
else
    echo "ERRO: sem evidência de handshake E2 entre RIC e ns-3"
    tail -n 30 /tmp/ric.log 2>/dev/null || true
    tail -n 30 /tmp/ns3.log 2>/dev/null || true
    exit 1
fi

echo ""
echo "=== 4. Iniciando coletor de métricas ==="
cd $BASE_DIR
setsid env GREENRAN_STATE_DIR="$STATE_DIR" GREENRAN_DB_PATH="$GREENRAN_DB_PATH" \
    python3 ./src/csv_to_metrics.py \
    --input-dir "$NS3_DIR" \
    --output "$STATE_DIR/xapp_metrics/metrics.json" \
    --extended-output "$STATE_DIR/xapp_metrics/extended_metrics.json" \
    --poll-interval "$GREENRAN_COLLECTOR_POLL_INTERVAL" \
    > "$GREENRAN_CSV_LOG" 2>&1 &
CSV_PID=$!
echo "$CSV_PID" > "$GREENRAN_CSV_PID"
echo "Coletor iniciado (PID: $CSV_PID)"
sleep 3
require_started "$CSV_PID" "coletor de métricas" "$GREENRAN_CSV_LOG"

if ! wait_for_file "$STATE_DIR/xapp_metrics/metrics.json" 30 "metrics.json"; then
    tail -n 30 "$GREENRAN_CSV_LOG" 2>/dev/null || true
    exit 1
fi

if ! wait_for_file "$STATE_DIR/xapp_metrics/extended_metrics.json" 30 "extended_metrics.json"; then
    tail -n 30 "$GREENRAN_CSV_LOG" 2>/dev/null || true
    exit 1
fi

echo ""
echo "=== 5. Iniciando rApp Orchestrator ==="
setsid env GREENRAN_STATE_DIR="$STATE_DIR" GREENRAN_DB_PATH="$GREENRAN_DB_PATH" \
    python3 ./src/rapp_orchestrator.py \
    --synthetic "$GREENRAN_ORCHESTRATOR_SYNTHETIC_DAYS" \
    --interval "$GREENRAN_ORCHESTRATOR_INTERVAL" \
    > "$GREENRAN_RAPP_LOG" 2>&1 &
RAPP_PID=$!
echo "$RAPP_PID" > "$GREENRAN_RAPP_PID"
echo "rApp iniciado (PID: $RAPP_PID)"
sleep 5
require_started "$RAPP_PID" "rApp Orchestrator" "$GREENRAN_RAPP_LOG"

if [ -f "$GREENRAN_XAPP_SLICER_PID" ] && ps -p "$(cat "$GREENRAN_XAPP_SLICER_PID" 2>/dev/null)" >/dev/null 2>&1; then
    echo "xApp Slicer iniciado pelo rApp"
else
    echo "AVISO: xApp Slicer ainda não registrou PID; verifique $GREENRAN_RAPP_LOG"
fi

if [ -f "$GREENRAN_XAPP_ENERGY_PID" ] && ps -p "$(cat "$GREENRAN_XAPP_ENERGY_PID" 2>/dev/null)" >/dev/null 2>&1; then
    echo "xApp Energy Saver iniciado pelo rApp"
else
    echo "AVISO: xApp Energy Saver ainda não foi ativado pelo rApp"
fi

echo ""
echo "=== Verificação dos processos ==="
ps aux | grep -E "(nearRT-RIC|xapp_slicer|xapp_energy|ns3.42-scenario-zero|rapp_orchestrator|csv_to_metrics)" | grep -v grep

echo ""
echo "=========================================="
echo "  Simulação em execução!"
echo "=========================================="
echo ""
echo "Para monitorar os logs:"
echo "  tail -f $GREENRAN_XAPP_SLICER_LOG   # xApp Slicer"
echo "  tail -f $GREENRAN_XAPP_ENERGY_LOG   # xApp Energy Saver"
echo "  tail -f $GREENRAN_CSV_LOG           # Coletor"
echo "  tail -f $GREENRAN_RAPP_LOG          # rApp"
echo "  tail -f /tmp/ns3.log                # ns3"
echo "  tail -f /tmp/ric.log                # RIC"
echo ""
echo "Pressione Ctrl+C para encerrar"
echo "=========================================="

# Espera infinita
wait
