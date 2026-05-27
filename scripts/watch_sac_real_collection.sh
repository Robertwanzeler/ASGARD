#!/bin/bash
set -euo pipefail

BASE_DIR="/home/robert/orange_nuclear"
STATE_DIR="${GREENRAN_STATE_DIR:-/tmp}"
DB_PATH="${GREENRAN_SAC_DB_PATH:-$STATE_DIR/rapp_data_lake.db}"
CSV_PATH="${GREENRAN_SAC_WORKLOAD_CSV:-$BASE_DIR/runs/sac_bootstrap/workload_trace_real.csv}"
INTERVAL_S="${1:-20}"

cd "$BASE_DIR"

while true; do
  clear
  echo "GreenRAN SAC Real Collection Watch"
  echo "=================================="
  echo "Atualizado em: $(date '+%Y-%m-%d %H:%M:%S')"
  echo

  python3 scripts/export_sac_workload_trace.py \
    --db "$DB_PATH" \
    --output-csv "$CSV_PATH" >/dev/null 2>&1 || true

  python3 - <<'PY'
import csv
import json
import os
import sqlite3
from collections import Counter
from pathlib import Path

db_path = os.environ.get("GREENRAN_SAC_DB_PATH") or os.path.join(
    os.environ.get("GREENRAN_STATE_DIR", "/tmp"),
    "rapp_data_lake.db",
)
csv_path = Path(
    os.environ.get("GREENRAN_SAC_WORKLOAD_CSV")
    or "/home/robert/orange_nuclear/runs/sac_bootstrap/workload_trace_real.csv"
)
ext_path = Path(os.environ.get("GREENRAN_STATE_DIR", "/tmp")) / "xapp_metrics" / "extended_metrics.json"

if not csv_path.exists():
    print("CSV ainda nao encontrado:")
    print(f"  {csv_path}")
    raise SystemExit(0)

rows = list(csv.DictReader(csv_path.open()))
if not rows:
    print("Nenhuma amostra SAC exportada ainda.")
    raise SystemExit(0)

conn = sqlite3.connect(db_path)
c = conn.cursor()
alloc_count = c.execute("select count(*) from resource_allocation_history").fetchone()[0]
latest_alloc = c.execute(
    """
    select datetime, controller_id, d_ran, d_ai, r_ran, r_ai, ran_completion_ratio, ai_completion_ratio, utilization_ratio
    from resource_allocation_history
    order by timestamp desc
    limit 1
    """
).fetchone()
latest_ext = c.execute(
    """
    select datetime, sim_time_s, throughput_kbps, cvar_per_ue_us
    from extended_metrics
    order by timestamp desc
    limit 1
    """
).fetchone()
conn.close()

d_ran = [float(r["d_ran"]) for r in rows]
d_ai = [float(r["d_ai"]) for r in rows]
r_ran = [float(r["r_ran"]) for r in rows]
r_ai = [float(r["r_ai"]) for r in rows]
ran_comp = [float(r["ran_completion_ratio"]) for r in rows]
ai_comp = [float(r["ai_completion_ratio"]) for r in rows]
util = [float(r["utilization_ratio"]) for r in rows]
ctrl = Counter(r.get("controller_id", "") or "unknown" for r in rows)

ext = {}
if ext_path.exists():
    try:
        ext = json.loads(ext_path.read_text())
    except Exception:
        ext = {}

sim_time_range = ext.get("sim_time_range", {})

def avg(values):
    return sum(values) / len(values) if values else 0.0

print(f"CSV rows: {len(rows)}")
print(f"DB resource_allocation_history rows: {alloc_count}")
print()
print("sim_time")
print(f"  file: {sim_time_range}")
if latest_ext:
    print(f"  db  : datetime={latest_ext[0]} sim_time={latest_ext[1]} throughput_kbps={latest_ext[2]:.2f} cvar_us={latest_ext[3]:.2f}")
print()
print("resource allocation")
if latest_alloc:
    print(
        "  latest: datetime={} controller={} d_ran={:.4f} d_ai={:.4f} r_ran={:.4f} r_ai={:.4f} "
        "ran_completion={:.4f} ai_completion={:.4f} utilization={:.4f}".format(
            latest_alloc[0],
            latest_alloc[1],
            latest_alloc[2],
            latest_alloc[3],
            latest_alloc[4],
            latest_alloc[5],
            latest_alloc[6],
            latest_alloc[7],
            latest_alloc[8],
        )
    )
print(f"  controllers: {dict(ctrl)}")
print()
print("dataset coverage")
print("  d_ran           : min={:.4f} avg={:.4f} max={:.4f} unique={}".format(min(d_ran), avg(d_ran), max(d_ran), len({round(v, 6) for v in d_ran})))
print("  d_ai            : min={:.4f} avg={:.4f} max={:.4f} unique={}".format(min(d_ai), avg(d_ai), max(d_ai), len({round(v, 6) for v in d_ai})))
print("  r_ran           : min={:.4f} avg={:.4f} max={:.4f} unique={}".format(min(r_ran), avg(r_ran), max(r_ran), len({round(v, 6) for v in r_ran})))
print("  r_ai            : min={:.4f} avg={:.4f} max={:.4f} unique={}".format(min(r_ai), avg(r_ai), max(r_ai), len({round(v, 6) for v in r_ai})))
print("  ran_completion  : min={:.4f} avg={:.4f} max={:.4f} unique={}".format(min(ran_comp), avg(ran_comp), max(ran_comp), len({round(v, 6) for v in ran_comp})))
print("  ai_completion   : min={:.4f} avg={:.4f} max={:.4f} unique={}".format(min(ai_comp), avg(ai_comp), max(ai_comp), len({round(v, 6) for v in ai_comp})))
print("  utilization     : min={:.4f} avg={:.4f} max={:.4f} unique={}".format(min(util), avg(util), max(util), len({round(v, 6) for v in util})))
print()

milestones = []
if len(rows) < 1000:
    milestones.append("dataset ainda pequeno para treino forte")
if len({round(v, 6) for v in ran_comp}) <= 1:
    milestones.append("ran_completion ainda sem diversidade real")
if len({round(v, 6) for v in d_ran}) < 20:
    milestones.append("d_ran ainda com pouca cobertura")
if len({round(v, 6) for v in util}) < 50:
    milestones.append("utilization ainda com pouca cobertura")

if milestones:
    print("leitura rapida")
    for item in milestones:
        print(f"  - {item}")
else:
    print("leitura rapida")
    print("  - coleta ja tem diversidade suficiente para uma nova rodada de treino offline")
PY

  echo
  echo "CSV exportado em:"
  echo "  $CSV_PATH"
  echo
  echo "Proxima atualizacao em ${INTERVAL_S}s. Ctrl+C para sair."
  sleep "$INTERVAL_S"
done
