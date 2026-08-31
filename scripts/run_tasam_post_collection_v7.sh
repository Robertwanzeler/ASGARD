#!/usr/bin/env bash
set -euo pipefail

# Pós-coleta oficial da v7. Aguarda o gate completo antes de exportar/treinar.
ROOT=/home/robert/orange_nuclear
STATE_DIR=/home/robert/orange_nuclear/runs/greenran_tasam_e2_active_20260827_v7
TARGETED_SUMMARY="$STATE_DIR/targeted_export/.work/latest_raw_summary.json"
FINAL_DIR="$STATE_DIR/final_dataset"
EXPORTED_TRACE="$FINAL_DIR/tasam_article_trace_exported.jsonl"
TRACE="$FINAL_DIR/tasam_article_trace_final.jsonl"
EXPORT_SUMMARY="$FINAL_DIR/tasam_article_trace_final_summary.json"
QUALITY_REPORT="$FINAL_DIR/tasam_dataset_quality.json"
STRICT_GATE="$FINAL_DIR/tasam_final_gate.json"
TRAIN_ROOT=/home/robert/orange_nuclear/runs/tasam_greenran_v7_postcollection_train
POST_LOG="$STATE_DIR/post_collection_supervisor.log"

mkdir -p "$FINAL_DIR" "$TRAIN_ROOT"
exec >>"$POST_LOG" 2>&1

echo "[post-v7] supervisor iniciado em $(date --iso-8601=seconds)"
if [[ -e "$STATE_DIR/post_collection_supervisor.done" ]]; then
  echo "[post-v7] já finalizado; não duplicando execução"
  exit 0
fi

while :; do
  ready=0
  if [[ -s "$TARGETED_SUMMARY" ]]; then
    ready=$(/usr/bin/python3 - "$TARGETED_SUMMARY" <<'PY'
import json
import sys
s=json.load(open(sys.argv[1], encoding="utf-8"))
stages=("allowed_bootstrap","allowed_stable","camera_conditional","camera_blocked",
        "vehicle_conditional","vehicle_blocked","app2_conditional","app2_blocked",
        "allowed_recovery")
print("1" if all(int((s.get("stage_counts") or {}).get(x,0) or 0)>=500 for x in stages) else "0")
PY
)
    /usr/bin/python3 - "$TARGETED_SUMMARY" <<'PY'
import json
import sys
s=json.load(open(sys.argv[1], encoding="utf-8"))
print("[post-v7] written=%s stages=%s" % (s.get("written_transitions"), s.get("stage_counts")))
PY
  else
    echo "[post-v7] aguardando resumo do monitor"
  fi
  [[ "$ready" == "1" ]] && break
  sleep 30
done

echo "[post-v7] meta de estágios atingida; encerrando somente a v7"
GREENRAN_STATE_DIR="$STATE_DIR" bash "$ROOT/scripts/stop_tasam_article_ns3_collection.sh"

if [[ -f "$STATE_DIR/targeted_monitor.pid" ]]; then
  monitor_pid=$(tr -dc '0-9' < "$STATE_DIR/targeted_monitor.pid" || true)
  if [[ -n "$monitor_pid" ]] && kill -0 "$monitor_pid" 2>/dev/null; then
    kill "$monitor_pid" 2>/dev/null || true
    sleep 1
    kill -KILL "$monitor_pid" 2>/dev/null || true
  fi
fi

echo "[post-v7] exportando trace final real-only"
/usr/bin/python3 "$ROOT/scripts/export_tasam_article_dataset.py" \
  --db "$STATE_DIR/rapp_data_lake.db" \
  --output-jsonl "$EXPORTED_TRACE" \
  --summary-json "$EXPORT_SUMMARY" \
  --max-step-gap-s 20

echo "[post-v7] congelando exatamente 500 válidas por estágio"
/usr/bin/python3 - "$EXPORTED_TRACE" "$TRACE" <<'PY'
import json
import sys
from collections import defaultdict
from pathlib import Path

source=Path(sys.argv[1])
target=Path(sys.argv[2])
stages=("allowed_bootstrap","allowed_stable","camera_conditional","camera_blocked",
        "vehicle_conditional","vehicle_blocked","app2_conditional","app2_blocked",
        "allowed_recovery")
kept=defaultdict(list)
for line in source.open(encoding="utf-8"):
    if not line.strip():
        continue
    row=json.loads(line)
    stage=str(row.get("scenario_stage") or "")
    if stage in stages and len(kept[stage]) < 500:
        kept[stage].append(row)
rows=sorted(
    (row for stage in stages for row in kept[stage]),
    key=lambda row: int(row.get("timestamp") or 0),
)
if len(rows)!=4500 or any(len(kept[stage])!=500 for stage in stages):
    raise SystemExit("não foi possível congelar 500 transições válidas por estágio")
target.write_text("".join(json.dumps(row,ensure_ascii=False)+"\n" for row in rows),encoding="utf-8")
print(json.dumps({"rows":len(rows),"stage_counts":{stage:len(kept[stage]) for stage in stages}},ensure_ascii=False))
PY

echo "[post-v7] validando dataset geral"
 /usr/bin/python3 "$ROOT/scripts/validate_tasam_dataset.py" \
  --trace-jsonl "$TRACE" \
  --output-json "$QUALITY_REPORT" \
  --expected-topology-id greenran_fixed_marl_v1 \
  --expected-du-count 3 \
  --expected-du-state-dim 10 \
  --expected-action-dim 3 \
  --min-transitions 4500

echo "[post-v7] validando contrato estrito"
 /usr/bin/python3 - "$TRACE" "$STRICT_GATE" <<'PY'
import json
import sys
from collections import Counter
from pathlib import Path

trace=Path(sys.argv[1])
out=Path(sys.argv[2])
stages=("allowed_bootstrap","allowed_stable","camera_conditional","camera_blocked",
        "vehicle_conditional","vehicle_blocked","app2_conditional","app2_blocked",
        "allowed_recovery")
rows=[json.loads(line) for line in trace.open(encoding="utf-8") if line.strip()]
errors=[]
counts=Counter(str(r.get("scenario_stage") or "") for r in rows)
if len(rows)!=4500:
    errors.append(f"rows={len(rows)}, expected=4500")
for stage in stages:
    if counts[stage]!=500:
        errors.append(f"stage={stage} count={counts[stage]}, expected=500")
for i,r in enumerate(rows,1):
    q=r.get("collection_quality") or {}
    d=r.get("decision") or {}
    a=r.get("action") or {}
    if not q.get("valid_for_training"): errors.append(f"line {i}: invalid_for_training")
    if str(q.get("collector_mode") or "").lower()!="pdcp_real" or not q.get("pdcp_real"):
        errors.append(f"line {i}: not_pdcp_real")
    if float(q.get("proxy_latency_sample_count") or 0)!=0: errors.append(f"line {i}: proxy")
    if not r.get("metrics") or not r.get("next_metrics"): errors.append(f"line {i}: missing_metrics")
    if abs(float(q.get("current_metric_skew_s") or 0))>6 or abs(float(q.get("next_metric_skew_s") or 0))>6:
        errors.append(f"line {i}: metric_skew")
    if q.get("sim_reset"): errors.append(f"line {i}: sim_reset")
    if int(a.get("per_ue_floor_violation_count") or 0)!=0: errors.append(f"line {i}: floor_violation")
    if not d.get("armd_proposal_present") or not d.get("armd_proposal_valid"):
        errors.append(f"line {i}: invalid_armd")
    if not d.get("tasam_proposal_present") or not d.get("tasam_proposal_valid"):
        errors.append(f"line {i}: invalid_tasam")
    if not r.get("judge_feedback_observed"): errors.append(f"line {i}: missing_judge")
payload={"schema":"greenran.tasam_v7_strict_gate.v1","trace":str(trace.resolve()),
         "rows":len(rows),"stage_counts":dict(counts),"error_count":len(errors),
         "errors":errors[:100],"training_ready":not errors}
out.write_text(json.dumps(payload,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
print(json.dumps(payload,indent=2,ensure_ascii=False))
if errors: raise SystemExit(2)
PY

touch "$STATE_DIR/post_collection_frozen.ok"
echo "[post-v7] dataset aprovado; iniciando treino multiseed"
 /usr/bin/python3 "$ROOT/scripts/run_tasam_greenran_multiseed.py" \
  --trace-jsonl "$TRACE" \
  --output-root "$TRAIN_ROOT" \
  --seeds 42,43,44,45,46,47 \
  --modes no_sam,tasam_selective \
  --epochs 200 \
  --min-transitions 4500 \
  --expected-topology-id greenran_fixed_marl_v1 \
  --expected-du-count 3 \
  --train-python /usr/bin/python3

echo "[post-v7] agregando resumos e avaliações"
for seed in 42 43 44 45 46 47; do
  seed_root="$TRAIN_ROOT/seed_$(printf '%04d' "$seed")"
  /usr/bin/python3 "$ROOT/scripts/summarize_tasam_runs.py" \
    --run-root "$seed_root" \
    --output-json "$seed_root/tasam_run_comparison.json" \
    --output-csv "$seed_root/tasam_run_comparison.csv"
  /usr/bin/python3 "$ROOT/scripts/evaluate_tasam_candidates.py" \
    --runs-root "$seed_root" \
    --output "$seed_root/tasam_candidate_evaluation.json"
done

echo "[post-v7] selecionando checkpoints sem promoção automática"
SELECTION="$TRAIN_ROOT/tasam_checkpoint_selection.json"
/usr/bin/python3 - "$TRAIN_ROOT" "$SELECTION" <<'PY'
import json
import sys
from pathlib import Path

root=Path(sys.argv[1])
out=Path(sys.argv[2])
rows=[]
for seed in (42,43,44,45,46,47):
    path=root/f"seed_{seed:04d}"/"tasam_selective"/"tasam_marl_summary.json"
    if not path.exists():
        rows.append({"seed":seed,"status":"missing_summary"})
        continue
    summary=json.loads(path.read_text(encoding="utf-8"))
    best=summary.get("best_checkpoint") or {}
    metrics=best.get("metrics") or summary.get("final_metrics") or {}
    checkpoint=Path(best.get("checkpoint_dir") or summary.get("checkpoint_root") or "")
    score=(float(metrics.get("eval_return") or 0.0)
           - 0.10*float(metrics.get("critic_loss") or 0.0)
           + 0.10*float(metrics.get("action_var_mean") or 0.0))
    rows.append({"seed":seed,"status":"selected" if checkpoint.exists() else "missing_checkpoint",
                 "checkpoint_dir":str(checkpoint.resolve()) if checkpoint.exists() else str(checkpoint),
                 "score":score,"metrics":metrics,"summary":str(path.resolve())})
selected=[r for r in rows if r.get("status")=="selected"]
payload={"schema":"greenran.tasam_v7_checkpoint_selection.v1","policy":"no_auto_promotion",
         "seeds":rows,"selected_count":len(selected),"selected":selected}
out.write_text(json.dumps(payload,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
print(json.dumps(payload,indent=2,ensure_ascii=False))
if len(selected)!=6:
    raise SystemExit("não há checkpoint tasam_selective válido para todas as seis seeds")
PY

echo "[post-v7] iniciando A/B operacional pareado: 100 decisões por seed"
AB_ROOT=/home/robert/orange_nuclear/runs/tasam_greenran_v7_operational_ab
mkdir -p "$AB_ROOT"
for seed in 42 43 44 45 46 47; do
  seed_label="seed_$(printf '%04d' "$seed")"
  ab_seed="$AB_ROOT/$seed_label"
  checkpoint=$(/usr/bin/python3 - "$SELECTION" "$seed" <<'PY'
import json
import sys
for row in json.load(open(sys.argv[1],encoding="utf-8")).get("selected",[]):
    if int(row.get("seed",-1))==int(sys.argv[2]):
        print(row.get("checkpoint_dir", ""))
        break
PY
)
  mkdir -p "$ab_seed"
  mkdir -p "$ab_seed/baseline" "$ab_seed/joint"
  echo "[post-v7] A/B seed=$seed checkpoint=$checkpoint"
  set +e
  env GREENRAN_DECISION_TARGET=100 GREENRAN_METRICS_TARGET=100 \
    GREENRAN_METRICS_MIN_TARGET=100 GREENRAN_WALL_TIME_LIMIT_SECONDS=600 \
    GREENRAN_START_RIC=0 GREENRAN_ALLOW_PDCP_WITHOUT_E2=1 \
    GREENRAN_NS3_E2NR_ENABLED=false GREENRAN_NS3_E2DU_ENABLED=false \
    bash "$ROOT/scripts/run_tasam_stress_ab_round.sh" baseline "$seed" "$ab_seed/baseline" \
    > "$ab_seed/baseline_launcher.log" 2>&1
  baseline_status=$?
  if [[ -f "$ab_seed/baseline/validation_readiness.json" ]]; then
    /usr/bin/python3 "$ROOT/scripts/evaluate_tasam_operational_baseline.py" \
      --run-dir "$ab_seed/baseline" --output "$ab_seed/baseline/baseline_summary.json" \
      >> "$ab_seed/baseline_launcher.log" 2>&1
  fi
  env GREENRAN_DECISION_TARGET=100 GREENRAN_METRICS_TARGET=100 \
    GREENRAN_METRICS_MIN_TARGET=100 GREENRAN_WALL_TIME_LIMIT_SECONDS=600 \
    GREENRAN_START_RIC=0 GREENRAN_ALLOW_PDCP_WITHOUT_E2=1 \
    GREENRAN_NS3_E2NR_ENABLED=false GREENRAN_NS3_E2DU_ENABLED=false \
    bash "$ROOT/scripts/run_tasam_stress_ab_round.sh" joint "$seed" "$ab_seed/joint" "$checkpoint" \
    > "$ab_seed/joint_launcher.log" 2>&1
  joint_status=$?
  if [[ -f "$ab_seed/joint/validation_readiness.json" ]]; then
    /usr/bin/python3 "$ROOT/scripts/evaluate_tasam_operational_run.py" \
      --run-dir "$ab_seed/joint" --output "$ab_seed/joint/operational_summary.json" \
      >> "$ab_seed/joint_launcher.log" 2>&1
  fi
  if [[ -f "$ab_seed/baseline/baseline_summary.json" && -f "$ab_seed/joint/operational_summary.json" ]]; then
    /usr/bin/python3 "$ROOT/scripts/compare_tasam_operational_runs.py" \
      --baseline "$ab_seed/baseline/baseline_summary.json" \
      --assistant "$ab_seed/joint/operational_summary.json" \
      --output "$ab_seed/operational_comparison.json" \
      >> "$ab_seed/comparison.log" 2>&1
  fi
  printf '%s\n' "[post-v7] A/B seed=$seed baseline_status=$baseline_status joint_status=$joint_status"
  set -e
done

echo "[post-v7] consolidando comparações operacionais"
/usr/bin/python3 - "$AB_ROOT" "$AB_ROOT/operational_multiseed_report.json" <<'PY'
import json
import sys
from pathlib import Path

root=Path(sys.argv[1])
rows=[]
for seed in (42,43,44,45,46,47):
    path=root/f"seed_{seed:04d}"/"operational_comparison.json"
    if path.exists():
        rows.append(json.loads(path.read_text(encoding="utf-8")))
payload={"schema":"greenran.tasam_v7_operational_multiseed.v1",
         "baseline":"rApp-only","assistant":"rApp Judge + ARMD + TA-SAM",
         "decisions_per_seed":100,"seeds_expected":[42,43,44,45,46,47],
         "completed_comparisons":len(rows),"comparisons":rows,
         "promotion":"blocked_until_manual_review"}
Path(sys.argv[2]).write_text(json.dumps(payload,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
print(json.dumps({"completed_comparisons":len(rows),"output":sys.argv[2]},indent=2))
PY

echo "[post-v7] treino e A/B concluídos; nenhum checkpoint foi promovido"
touch "$STATE_DIR/post_collection_supervisor.done"
