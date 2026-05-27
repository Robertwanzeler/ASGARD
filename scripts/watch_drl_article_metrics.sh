#!/bin/bash
set -euo pipefail

BASE_DIR="/home/robert/orange_nuclear"
OUTPUT_DIR="$BASE_DIR/runs/eedrl_greenran_final/article_metrics_real"
TRACE_FILE="$OUTPUT_DIR/drl_predictor_trace.jsonl"
DB_PATH="/tmp/rapp_data_lake.db"
INTERVAL_S="${1:-15}"

mkdir -p "$OUTPUT_DIR"

cd "$BASE_DIR"

while true; do
  clear
  echo "GreenRAN DRL Article Metrics Watch"
  echo "=================================="
  echo "Atualizado em: $(date '+%Y-%m-%d %H:%M:%S')"
  echo

  if [ ! -f "$TRACE_FILE" ]; then
    echo "Trace ainda não encontrado:"
    echo "  $TRACE_FILE"
    echo
    echo "Suba o cenário com:"
    echo "  GREENRAN_DRL_TRACE=1 GREENRAN_DRL_TRACE_FILE=$TRACE_FILE ./scripts/run_greenran_v2.sh"
    sleep "$INTERVAL_S"
    continue
  fi

  python3 scripts/export_drl_isolation_confidence_dataset.py \
    --db "$DB_PATH" \
    --trace-file "$TRACE_FILE" \
    --output-dir "$OUTPUT_DIR" >/dev/null

  python3 - <<'PY'
import csv
import json
from collections import Counter
from pathlib import Path

base = Path("/home/robert/orange_nuclear/runs/eedrl_greenran_final/article_metrics_real")
timeseries = base / "drl_isolation_timeseries.csv"
summary_path = base / "drl_isolation_summary.json"

rows = list(csv.DictReader(timeseries.open())) if timeseries.exists() else []
summary = json.loads(summary_path.read_text()) if summary_path.exists() else {}

if not rows:
    print("Nenhuma amostra exportada ainda.")
    raise SystemExit(0)

action_conf = [float(r["drl_actor_action_confidence_pct"]) for r in rows]
decision_conf = [float(r["drl_actor_decision_confidence_pct"]) for r in rows]
final_prob = [float(r["drl_final_decision_probability_pct"]) for r in rows]
iso = [float(r["isolation_degree"]) for r in rows]
camera = Counter(r["camera_status"] for r in rows)
app2 = Counter(r["app2_status"] for r in rows)
app3 = Counter(r["app3_status"] for r in rows)
decision = Counter(r["drl_final_decision"] for r in rows)
actor_decision = Counter(r["drl_actor_decision"] for r in rows)
policy = Counter(r["drl_policy_action"] for r in rows)

print(f"Amostras: {len(rows)}")
print(
    "Action confidence min/avg/max: "
    f"{min(action_conf):.3f}% / {sum(action_conf)/len(action_conf):.3f}% / {max(action_conf):.3f}%"
)
print(
    "Decision confidence min/avg/max: "
    f"{min(decision_conf):.3f}% / {sum(decision_conf)/len(decision_conf):.3f}% / {max(decision_conf):.3f}%"
)
print(
    "Final decision probability min/avg/max: "
    f"{min(final_prob):.3f}% / {sum(final_prob)/len(final_prob):.3f}% / {max(final_prob):.3f}%"
)
print("Isolation values:", sorted(set(round(v, 6) for v in iso)))
print()
print("Thresholds (decision confidence)")
for item in summary.get("confidence_thresholds", []):
    print(
        "  >= {:>2}%: {:>4} amostras | iso_avg={:.6f}".format(
            item["confidence_threshold_pct"],
            item["samples"],
            item["avg_isolation_degree"],
        )
    )
print()
print("Thresholds (action confidence)")
for item in summary.get("action_confidence_thresholds", []):
    print(
        "  >= {:>2}%: {:>4} amostras | iso_avg={:.6f}".format(
            item["confidence_threshold_pct"],
            item["samples"],
            item["avg_isolation_degree"],
        )
    )
print()
print("Status counts")
print("  camera :", dict(camera))
print("  app2   :", dict(app2))
print("  app3   :", dict(app3))
print()
print("Decisions")
print("  actor  :", dict(actor_decision))
print("  final  :", dict(decision))
print("  policy :", dict(policy))
print()

threshold_60 = next((x for x in summary.get("confidence_thresholds", []) if x["confidence_threshold_pct"] == 60), None)
threshold_80 = next((x for x in summary.get("confidence_thresholds", []) if x["confidence_threshold_pct"] == 80), None)

print("Leitura rápida")
if threshold_60 and threshold_60["samples"] == 0:
    print("  - Ainda faltam amostras com decision confidence >= 60%.")
if threshold_80 and threshold_80["samples"] == 0:
    print("  - Ainda faltam amostras com decision confidence >= 80%.")
if len(set(round(v, 6) for v in iso)) <= 2:
    print("  - O isolation degree ainda tem pouca variação.")
if camera.get("violated", 0) == len(rows):
    print("  - App1/câmeras continua violado em todas as amostras.")
PY

  echo
  echo "Arquivos:"
  echo "  $OUTPUT_DIR/drl_isolation_timeseries.csv"
  echo "  $OUTPUT_DIR/drl_isolation_by_confidence_threshold.csv"
  echo "  $OUTPUT_DIR/drl_isolation_summary.json"
  echo
  echo "Próxima atualização em ${INTERVAL_S}s. Ctrl+C para sair."
  sleep "$INTERVAL_S"
done
