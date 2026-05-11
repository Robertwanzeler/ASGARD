#!/bin/bash

set -euo pipefail

. "$(dirname "$0")/core_runtime.sh"
load_greenran_runtime

PROJECT_ROOT="$GREENRAN_PROJECT_DIR"
OUTPUT_ROOT="$PROJECT_ROOT/runs/experimentos_conflitos"
SCENARIO="conflito_implicito"
ROUNDS=10
DURATION=120
EPOCHS="50,100,200,400,600,800,1000"
SUBSET_SIZES="50,150,450"
TARGET_ROWS=0
KEEP_RUNTIME=0

usage() {
    cat <<EOF
Uso:
  $(basename "$0") [opções]

Opções:
  --scenario <slug>         cenário de coleta (default: conflito_implicito)
  --rounds <n>              rodadas por cenário (default: 10)
  --duration <s>            duração de cada rodada em segundos (default: 120)
  --epochs <csv>            épocas do treino GraphSAGE (default: $EPOCHS)
  --subset-sizes <csv>      subsets do treino (default: $SUBSET_SIZES)
  --target-rows <n>         meta de linhas por cenário (default: 0)
  --output-root <dir>       raiz dos experimentos (default: $OUTPUT_ROOT)
  --keep-runtime            não encerra o runtime no final
  -h, --help                mostra esta ajuda
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --scenario)
            SCENARIO="$2"
            shift 2
            ;;
        --rounds)
            ROUNDS="$2"
            shift 2
            ;;
        --duration)
            DURATION="$2"
            shift 2
            ;;
        --epochs)
            EPOCHS="$2"
            shift 2
            ;;
        --subset-sizes)
            SUBSET_SIZES="$2"
            shift 2
            ;;
        --target-rows)
            TARGET_ROWS="$2"
            shift 2
            ;;
        --output-root)
            OUTPUT_ROOT="$2"
            shift 2
            ;;
        --keep-runtime)
            KEEP_RUNTIME=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Opção desconhecida: $1" >&2
            usage >&2
            exit 1
            ;;
    esac
done

cleanup() {
    if [[ "$KEEP_RUNTIME" -eq 0 ]]; then
        "$PROJECT_ROOT/scripts/stop_all.sh" >/dev/null 2>&1 || true
    fi
}

trap cleanup EXIT

echo "=== [1/4] Iniciando runtime GreenRAN + ns-3 ==="
"$PROJECT_ROOT/scripts/run_greenran_v2.sh"

echo "=== [2/4] Aguardando banco e métricas do runtime ==="
for _ in $(seq 1 60); do
    if [[ -f "$GREENRAN_DB_PATH" && -f "$STATE_DIR/xapp_metrics/extended_metrics.json" ]]; then
        break
    fi
    sleep 2
done

if [[ ! -f "$GREENRAN_DB_PATH" ]]; then
    echo "Banco de dados não encontrado em $GREENRAN_DB_PATH" >&2
    exit 1
fi

mkdir -p "$OUTPUT_ROOT"

echo "=== [3/4] Coletando conflitos do cenário '$SCENARIO' ==="
COLLECT_CMD=(
    ./drlexp/.venv/bin/python
    scripts/run_conflict_experiments.py
    --scenario "$SCENARIO"
    --rounds "$ROUNDS"
    --duration "$DURATION"
    --auto
    --auto-switch
    --output-root "$OUTPUT_ROOT"
    --subset-sizes "$SUBSET_SIZES"
)

if [[ "$TARGET_ROWS" -gt 0 ]]; then
    COLLECT_CMD+=(--target-rows-per-scenario "$TARGET_ROWS")
fi

"${COLLECT_CMD[@]}"

EXPERIMENT_DIR="$(find "$OUTPUT_ROOT" -maxdepth 1 -type d -name '*_conflict_protocol' | sort | tail -1)"
if [[ -z "$EXPERIMENT_DIR" ]]; then
    echo "Nenhum diretório de experimento foi criado em $OUTPUT_ROOT" >&2
    exit 1
fi

echo "=== [4/4] Treinando GraphSAGE no experimento coletado ==="
./drlexp/.venv/bin/python training/train_graphsage_conflicts.py \
    --experiment-dir "$EXPERIMENT_DIR" \
    --scenario "$SCENARIO" \
    --epochs "$EPOCHS" \
    --subset-sizes "$SUBSET_SIZES"

echo
echo "Pipeline concluído."
echo "Experimento: $EXPERIMENT_DIR"
echo "Treino: $EXPERIMENT_DIR/graphsage_training"
if [[ "$KEEP_RUNTIME" -eq 1 ]]; then
    echo "Runtime mantido ativo."
else
    echo "Runtime será encerrado pelo wrapper."
fi
