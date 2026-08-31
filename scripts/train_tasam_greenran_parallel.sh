#!/usr/bin/env bash
set -euo pipefail

# Cria um snapshot fechado do trace atual e treina TA-SAM enquanto a coleta
# continua em outro conjunto de processos.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_DIR="${GREENRAN_TASAM_STATE_DIR:-$ROOT/runs/greenran_tasam_3du_article_adapted_20260730}"
SOURCE_TRACE="$STATE_DIR/tasam_article_export/tasam_article_trace.jsonl"
STAMP="$(date +%Y%m%d_%H%M%S)"
OUTPUT_ROOT="$ROOT/runs/tasam_greenran_article_train_$STAMP"
SNAPSHOT="$OUTPUT_ROOT/tasam_article_trace_snapshot.jsonl"
SUMMARY="$STATE_DIR/tasam_article_export/tasam_article_export_summary.json"

mkdir -p "$OUTPUT_ROOT"

if [[ ! -s "$SOURCE_TRACE" ]]; then
  echo "Trace TA-SAM não encontrado: $SOURCE_TRACE" >&2
  exit 1
fi

echo "[TA-SAM] Criando snapshot do trace atual..."
snapshot_ok=0
for attempt in 1 2 3 4 5; do
  cp "$SOURCE_TRACE" "$SNAPSHOT.tmp"
  mv "$SNAPSHOT.tmp" "$SNAPSHOT"
  if /usr/bin/python3 - "$SNAPSHOT" "$SUMMARY" <<'PY'
import json
import sys

trace_path, summary_path = sys.argv[1:]
summary = json.load(open(summary_path, encoding="utf-8"))
expected = int(summary.get("written_transitions", 0) or 0)
rows = 0
for line in open(trace_path, encoding="utf-8"):
    record = json.loads(line)
    if record.get("schema") != "greenran.tasam_article_transition.v1":
        raise ValueError("schema TA-SAM inesperado")
    if len(record.get("du_states") or []) != 3:
        raise ValueError("transição sem 3 DUs")
    if record.get("reward_hint") is None and record.get("reward_components") is None:
        raise ValueError("transição sem recompensa")
    rows += 1
if rows != expected:
    raise ValueError(f"snapshot em escrita: linhas={rows}, resumo={expected}")
print(f"validado: {rows} transições, 3 DUs, recompensas presentes")
PY
  then
    snapshot_ok=1
    break
  fi
  echo "[TA-SAM] Exportador estava regravando o trace; tentando novamente ($attempt/5)..."
  sleep 5
done

if [[ "$snapshot_ok" != 1 ]]; then
  echo "Não foi possível obter um snapshot estável. A coleta continua; tente novamente." >&2
  exit 1
fi

echo "[TA-SAM] Iniciando treino article-SAC / tasam_selective."
echo "[TA-SAM] Saída: $OUTPUT_ROOT"
exec /usr/bin/python3 "$ROOT/scripts/run_tasam_article_reproduction.py" \
  --skip-export \
  --trace-jsonl "$SNAPSHOT" \
  --output-root "$OUTPUT_ROOT" \
  --epochs 25 \
  --modes tasam_selective \
  --seed 42 \
  --train-python /usr/bin/python3
