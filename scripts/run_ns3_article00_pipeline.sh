#!/bin/bash

set -euo pipefail

. "$(dirname "$0")/core_runtime.sh"
load_greenran_runtime

PROJECT_ROOT="$GREENRAN_PROJECT_DIR"
OUTPUT_ROOT="$PROJECT_ROOT/runs/experimentos_conflitos"
SCENARIO="conflito_implicito"
ROUNDS=10
DURATION=120
SAMPLES=450
SEEDS="42,43,44,45,46,47"
EPOCHS="50,100,200,400,600,800,1000"
THRESHOLDS="0.2,0.5,0.9"
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
  --samples <n>             subset size para o dataset temporal (default: $SAMPLES; 0=full)
  --seeds <csv>             seeds de treino (default: $SEEDS)
  --epochs <csv>            épocas do treino temporal (default: $EPOCHS)
  --thresholds <csv>        thresholds avaliados (default: $THRESHOLDS)
  --target-rows <n>         meta de linhas por cenário (default: 0)
  --output-root <dir>       raiz dos experimentos coletados (default: $OUTPUT_ROOT)
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
        --samples)
            SAMPLES="$2"
            shift 2
            ;;
        --seeds)
            SEEDS="$2"
            shift 2
            ;;
        --epochs)
            EPOCHS="$2"
            shift 2
            ;;
        --thresholds)
            THRESHOLDS="$2"
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

case "$SAMPLES" in
    0|50|150|450)
        ;;
    *)
        echo "--samples must be one of: 0, 50, 150, 450" >&2
        exit 1
        ;;
esac

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
    --subset-sizes "50,150,450"
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

echo "=== [4/4] Treinando o pipeline temporal no estilo article00 ==="
./drlexp/.venv/bin/python scripts/run_ns3_article00_experiments.py \
    --experiment-dir "$EXPERIMENT_DIR" \
    --scenario "$SCENARIO" \
    --samples "$SAMPLES" \
    --seeds "$SEEDS" \
    --epochs "$EPOCHS" \
    --thresholds "$THRESHOLDS" \
    --selection-threshold 0.5 \
    --hidden-dim 32 \
    --embed-dim 32 \
    --dropout 0.05 \
    --learning-rate 0.001 \
    --weight-decay 1e-4 \
    --temporal-radius 3 \
    --temporal-decay 0.7 \
    --fp-penalty-weight 0.3 \
    --fp-penalty-margin 0.1 \
    --tp-reward-weight 0.2 \
    --tp-reward-margin 0.5 \
    --selection-mode composite \
    --selection-weight-parameter 1.0 \
    --selection-weight-indirect 1.0 \
    --selection-weight-implicit 1.0

echo
echo "Pipeline temporal ns-3 concluído."
echo "Experimento coletado: $EXPERIMENT_DIR"
echo "Artefatos article00-style: $PROJECT_ROOT/runs/ns3_article00/$SCENARIO"
if [[ "$KEEP_RUNTIME" -eq 1 ]]; then
    echo "Runtime mantido ativo."
else
    echo "Runtime será encerrado pelo wrapper."
fi
