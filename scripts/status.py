#!/usr/bin/env python3
"""Status em tempo real: coleta GreenRAN ou TA-SAM online fiel ao artigo."""

import json, os, re, sqlite3, subprocess, sys, time
from pathlib import Path

from run_rapp_online_retrain import (
    DEFAULT_REQUIRED_DECISION_TRANSITIONS,
    DEFAULT_REQUIRED_SCENARIO_FAMILIES,
    evaluate_collection_quality,
    evaluate_readiness,
)

DIR = Path("/home/robert/orange_nuclear/runs/sac_bootstrap/online_tasam_marl")
LOG = DIR / "online_tasam_marl_history.jsonl"
SUMMARY = DIR / "online_tasam_marl_summary.json"
CKPT = DIR / "online_tasam_marl_resume.pt"
MANIFEST = Path("/home/robert/orange_nuclear/runs/sac_bootstrap/tasam_candidate_evaluation_latest.json")
SHADOW_DIR = Path("/home/robert/orange_nuclear/runs/sac_bootstrap/tasam_marl_v4")
TOTAL = 20000
PROJECT_ROOT = Path("/home/robert/orange_nuclear")
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from network_quality import evaluate_network_quality

DEFAULT_COLLECTION_STATE = Path(
    os.environ.get(
        "GREENRAN_TASAM_ACTIVE_STATE_DIR",
        str(PROJECT_ROOT / "runs" / "tasam_article_ns3_collection"),
    )
)

def bar(pct, w=22):
    fill = int(pct / 100 * w)
    return "[" + "\u2588" * fill + "\u2591" * (w - fill) + f"] {pct:5.1f}%"

def spark(vals, w=20):
    if not vals:
        return "\u2591" * w
    lo = max(min(vals) - 0.5, 175)
    hi = min(max(vals) + 0.5, 195)
    rng = max(hi - lo, 1)
    blocks = "\u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"
    out = []
    for v in vals[-w:]:
        i = int((v - lo) / rng * (len(blocks) - 1))
        out.append(blocks[min(max(i, 0), len(blocks) - 1)])
    return "".join(out)

def read_log():
    if not LOG.exists(): return None
    raw = LOG.read_text(encoding="utf-8").splitlines()
    ep, step, ret50, eval_ret, alpha, rho, actor_loss, critic_loss, td_var = 0, 0, 0, 0, 0, 0, 0, 0, 0
    eval_hist = []
    returns = []
    for line in raw:
        line = line.strip()
        if not line: continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        ep = int(payload.get("episode", 0) or 0)
        step = int(payload.get("global_step", 0) or 0)
        returns.append(float(payload.get("episode_return", 0.0) or 0.0))
        alpha = float(payload.get("alpha", 0.0) or 0.0)
        rho = float(payload.get("rho_actor", 0.0) or 0.0)
        actor_loss = float(payload.get("actor_loss", 0.0) or 0.0)
        critic_loss = float(payload.get("critic_loss", 0.0) or 0.0)
        td_var = float(payload.get("effective_td_var_threshold", 0.0) or 0.0)
    if returns:
        ret50 = sum(returns[-50:]) / len(returns[-50:])
    if SUMMARY.exists():
        try:
            summary = json.loads(SUMMARY.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            summary = {}
        for item in summary.get("eval_history", []) or []:
            eval_hist.append((
                int(item.get("episode", 0) or 0),
                float(item.get("mean_return", 0.0) or 0.0),
                float(item.get("mean_embb_completion", 0.0) or 0.0),
                float(item.get("mean_mmtc_completion", 0.0) or 0.0),
                float(item.get("mean_urllc_completion", 0.0) or 0.0),
            ))
        if eval_hist:
            eval_ret = float(eval_hist[-1][1])
    return dict(ep=ep, step=step, ret50=ret50, eval_ret=eval_ret,
                alpha=alpha, rho=rho, actor_loss=actor_loss, critic_loss=critic_loss,
                td_var=td_var, eval_hist=eval_hist)


def latest_run(hist):
    if not hist:
        return []
    run = []
    for ep, er, embb, mmtc, urllc in hist:
        if run and ep < run[-1][0] and ep < 5000:
            run = []
        run.append((ep, er, embb, mmtc, urllc))
    return run

def proc_info():
    try:
        r = subprocess.run(["pgrep", "-af", "train_online_tasam_marl.py"], capture_output=True, text=True, timeout=2)
        lines = [line.strip() for line in r.stdout.splitlines() if line.strip()]
        if not lines:
            return "\u2717 PARADO"
        pid = lines[0].split()[0]
        return f"\u2713 PID {pid}  online_tasam_marl ativo"
    except: return "\u2717 PARADO"

def eta(ep):
    if ep < 100: return "calculando..."
    try:
        if not LOG.exists():
            return "?h"
        total_sec = max(0.0, time.time() - LOG.stat().st_mtime)
        if total_sec <= 0:
            return "?h"
        eps_per_sec = ep / total_sec
        rem = TOTAL - ep
        sec = rem / eps_per_sec
        fin = time.strftime("%d/%b %H:%M", time.localtime(time.time() + sec))
        hh, rr = divmod(int(sec), 3600)
        mm, _ = divmod(rr, 60)
        return f"~{hh}h{mm:02d}m  ({fin})"
    except: return "?h"

def shadow_status():
    try:
        m = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}
        br = m.get("best_run", {})
        run = br.get("run_dir", "")
        rd = br.get("readiness", "?")
        var = br.get("final_metrics", {}).get("action_var_mean", 0)
        return f"{Path(run).name}  ({rd})  action_var={var:.4f}"
    except: return "N/A"

def fmt_diff(v, ref=187, w=8):
    d = v - ref
    s = f"{v:.1f}".rjust(w)
    if d > 0.5: return f"{s} \u2191"
    if d < -0.5: return f"{s} \u2193"
    return s

def training_screen():
    info = read_log()
    proc = proc_info()
    if not info: print("Nenhum dado de treino encontrado."); return
    ep = info["ep"]
    pct = ep / TOTAL * 100
    ev_hist = latest_run(info["eval_hist"])
    latest_ers = [x[1] for x in ev_hist] if ev_hist else []

    os.system("clear")
    print("\033[1m" + "=" * 56 + "\033[0m")
    print("  \033[1mTA-SAM Online MARL\033[0m       %s" % proc)
    print("\033[1m" + "=" * 56 + "\033[0m")
    print("")
    print("  " + bar(pct))
    print("  \033[1m%5d\033[0m / %d episodios      step %s" % (ep, TOTAL, f"{info['step']:,}"))
    print("")
    print("   ret_avg50   eval_ret    td_var  actor     alpha     rho")
    r50 = info["ret50"]
    er = info["eval_ret"] if info["eval_ret"] else info["ret50"]
    a = info["alpha"]
    r = info["rho"]
    al = info["actor_loss"]
    td = info["td_var"]
    print("  %s    %s   %.4f   %7.2f   %.3f   %.4f" % (
        fmt_diff(r50), f"{er:.2f}".rjust(8) if er else "     ?", td, al or 0, a, r))
    print("  critic_loss=%7.4f" % (info["critic_loss"] or 0))
    if latest_ers:
        print("  eval_ret: " + spark(latest_ers))
        print("            " + "".join(f"{x:6.1f}" for x in latest_ers[-8:]))
    print("")
    eta_str = eta(ep)
    print("  ETA: \033[1m%s\033[0m" % eta_str)
    if CKPT.exists():
        ckpt_age = (time.time() - CKPT.stat().st_mtime) / 3600
        print("  Checkpoint: %.1fh ago" % ckpt_age)
    print("")
    print("  \033[1mShadow:\033[0m " + shadow_status())
    print("\033[1m" + "=" * 56 + "\033[0m")
    sys.stdout.flush()


def fmt_age(path):
    if not path.exists():
        return "--"
    age = max(0, time.time() - path.stat().st_mtime)
    return fmt_seconds(age)


def fmt_seconds(age):
    if age is None:
        return "--"
    age = max(0, float(age))
    if age < 60:
        return f"{age:.1f}s"
    if age < 3600:
        return f"{age / 60:.1f}m"
    return f"{age / 3600:.1f}h"


def trace_status_from_metrics(state, gm):
    status = gm.get("trace_file_status") or {}
    if status:
        return status
    ns3_dir = PROJECT_ROOT / "ns-O-RAN-flexric" / "mmwave-LENA-oran"
    fallback = {}
    for key, filename in (("pdcp", "DlPdcpStats.txt"), ("rlc", "DlRlcStats.txt"), ("mac", "DlMacStats.txt")):
        path = ns3_dir / filename
        if path.exists():
            age = max(0, time.time() - path.stat().st_mtime)
            fallback[key] = {"path": str(path), "exists": True, "age_s": age, "stale": age > 30.0}
        else:
            fallback[key] = {"path": str(path), "exists": False, "age_s": None, "stale": False}
    return fallback


def fmt_num(value):
    try:
        value = int(value)
    except Exception:
        return "0"
    return f"{value:,}".replace(",", ".")


def fmt_float(value, digits=2):
    try:
        return f"{float(value):.{digits}f}"
    except Exception:
        return "0.00"


def collection_bar(value, target, w=24):
    value = max(0, int(value or 0))
    target = max(1, int(target or 1))
    pct = min(100.0, value / target * 100.0)
    fill = int(pct / 100 * w)
    return "[" + "█" * fill + "░" * (w - fill) + f"] {pct:5.1f}%"


def find_collection_state():
    env_state = os.environ.get("GREENRAN_STATE_DIR")
    if env_state:
        return Path(env_state)

    candidates = [DEFAULT_COLLECTION_STATE]
    runs_dir = PROJECT_ROOT / "runs"
    if runs_dir.exists():
        candidates.extend(
            sorted(
                [p for p in runs_dir.iterdir() if p.is_dir() and (p / "xapp_metrics" / "extended_metrics.json").exists()],
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
        )

    for state in candidates:
        if (state / "xapp_metrics" / "extended_metrics.json").exists() or (state / "rapp_data_lake.db").exists():
            return state
    return DEFAULT_COLLECTION_STATE


def ps_lines():
    try:
        result = subprocess.run(
            ["ps", "-eo", "pid,ppid,stat,etime,pcpu,pmem,args"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        return result.stdout.splitlines()
    except Exception:
        return []


def find_process(patterns, lines=None):
    lines = lines if lines is not None else ps_lines()
    for line in lines:
        if all(pattern in line for pattern in patterns) and "scripts/status.py" not in line:
            parts = line.split(None, 6)
            if len(parts) >= 7:
                return {
                    "pid": parts[0],
                    "stat": parts[2],
                    "etime": parts[3],
                    "cpu": parts[4],
                    "mem": parts[5],
                    "args": parts[6],
                }
    return None


def process_from_pid_file(pid_file):
    try:
        pid = pid_file.read_text(encoding="utf-8").strip()
    except Exception:
        return None
    if not pid.isdigit():
        return None
    try:
        result = subprocess.run(
            ["ps", "-p", pid, "-o", "pid,ppid,stat,etime,pcpu,pmem,args", "--no-headers"],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except Exception:
        return None
    line = result.stdout.strip()
    if not line:
        return None
    parts = line.split(None, 6)
    if len(parts) < 7:
        return None
    return {
        "pid": parts[0],
        "stat": parts[2],
        "etime": parts[3],
        "cpu": parts[4],
        "mem": parts[5],
        "args": parts[6],
    }


def alive_text(proc):
    if not proc:
        return "\033[31mMORTO\033[0m"
    stat = proc.get("stat", "")
    icon = "✓" if "R" in stat or "S" in stat else "!"
    return f"\033[32m{icon} vivo\033[0m PID {proc['pid']} {proc['etime']} CPU {proc['cpu']}% MEM {proc['mem']}%"


def load_json(path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {} if default is None else default


def load_online_training_export(state):
    export_dir = state / "tasam_article_export"
    manifest = load_json(export_dir / "latest_export.json", {})
    raw = (manifest.get("raw_export") or {}) if isinstance(manifest, dict) else {}
    trainable = (manifest.get("trainable_export") or {}) if isinstance(manifest, dict) else {}
    raw_summary = raw.get("summary") or {}
    trainable_summary = trainable.get("summary") or {}
    trainable_rows = int(trainable_summary.get("rows_after", 0) or 0)
    raw_rows = int(raw_summary.get("written_transitions", 0) or 0)
    drop_ratio = trainable_summary.get("drop_ratio", 0.0)
    try:
        drop_ratio = float(drop_ratio or 0.0)
    except Exception:
        drop_ratio = 0.0
    return {
        "manifest_exists": bool(manifest),
        "profile": str(trainable_summary.get("profile") or ""),
        "raw_rows": raw_rows,
        "trainable_rows": trainable_rows,
        "drop_ratio": drop_ratio,
        "decision_counts": dict(trainable_summary.get("decision_counts_after") or {}),
        "stage_counts": dict(trainable_summary.get("stage_counts_after") or {}),
        "drop_reasons": dict(trainable_summary.get("drop_reasons") or {}),
    }


def load_online_retrain_status(state):
    manifest = load_json(state / "tasam_article_export" / "rapp_online_retrain_latest.json", {})
    if not manifest:
        return {"exists": False}
    report = manifest.get("training_report") or {}
    classifier = (report.get("classifier") or {}) if isinstance(report, dict) else {}
    checks = dict(manifest.get("metric_gate_checks") or {})
    return {
        "exists": True,
        "status": str(manifest.get("status") or "unknown"),
        "reasons": list(manifest.get("reasons") or []),
        "raw_manifest": manifest,
        "selected_classifier": str(classifier.get("selected_classifier") or ""),
        "rf_accuracy": classifier.get("random_forest_accuracy"),
        "xgb_accuracy": classifier.get("xgboost_accuracy"),
        "metric_gate_status": str(manifest.get("metric_gate_status") or ""),
        "regressor_r2": checks.get("regressor_r2"),
        "evaluation_valid": checks.get("evaluation_valid"),
        "target_trainable": int(manifest.get("target_trainable", 0) or 0),
        "last_trigger_rows": int(manifest.get("last_trigger_rows", 0) or 0),
        "new_rows_since_trigger": int(manifest.get("new_rows_since_trigger", 0) or 0),
        "next_retrain_rows": int(manifest.get("next_retrain_rows", 0) or 0),
        "min_new_rows": int(((manifest.get("retrain_cadence") or {}).get("min_new_rows", 0)) or 0),
    }


def load_training_readiness(state):
    training_export = load_online_training_export(state)
    if not training_export.get("manifest_exists"):
        return {"exists": False}

    export_dir = state / "tasam_article_export"
    retrain_manifest = load_json(export_dir / "rapp_online_retrain_latest.json", {})
    trainable_summary = {
        "rows_after": int(training_export.get("trainable_rows", 0) or 0),
        "decision_counts_after": dict(training_export.get("decision_counts") or {}),
    }
    min_trainable = 50
    min_class_count = 5
    required_classes = ["ALLOWED", "CONDITIONAL", "BLOCKED"]
    readiness_status, readiness_reasons = evaluate_readiness(
        trainable_summary,
        min_trainable=min_trainable,
        min_class_count=min_class_count,
        required_classes=list(required_classes),
    )

    quality_report = {}
    quality_gate = {}
    if retrain_manifest:
        quality_report = dict(retrain_manifest.get("collection_quality") or {})
        quality_gate = dict(retrain_manifest.get("collection_quality_gate") or {})
    if not quality_report:
        quality_report = load_json(export_dir / "rapp_online_collection_quality.json", {})
    quality_status = "unknown"
    quality_reasons = []
    quality_checks = {}
    if quality_report:
        quality_status, quality_reasons, quality_checks = evaluate_collection_quality(
            quality_report,
            max_class_dominance_ratio=0.55,
            min_decision_transitions=30,
            min_decision_transition_ratio=0.05,
            min_distinct_decision_transitions=4,
            required_decision_transitions=list(DEFAULT_REQUIRED_DECISION_TRANSITIONS),
            required_scenario_families=list(DEFAULT_REQUIRED_SCENARIO_FAMILIES),
            max_constant_feature_ratio=0.25,
            min_valid_training_ratio=1.0,
        )
        if quality_gate:
            quality_status = str(quality_gate.get("status") or quality_status)
            quality_reasons = list(quality_gate.get("reasons") or quality_reasons)
            quality_checks = dict(quality_gate.get("checks") or quality_checks)

    target_trainable = int(retrain_manifest.get("target_trainable", 0) or 0)
    next_retrain_rows = int(retrain_manifest.get("next_retrain_rows", 0) or 0)
    min_new_rows = int(((retrain_manifest.get("retrain_cadence") or {}).get("min_new_rows", 0)) or 0)
    trainable_rows = int(training_export.get("trainable_rows", 0) or 0)
    last_trigger_rows = int(retrain_manifest.get("last_trigger_rows", 0) or 0)
    new_rows_since_trigger = max(0, trainable_rows - last_trigger_rows)
    decision_counts = dict(training_export.get("decision_counts") or {})
    class_gaps = {
        cls: max(0, min_class_count - int(decision_counts.get(cls, 0) or 0))
        for cls in required_classes
    }
    rows_to_target = max(0, target_trainable - trainable_rows) if target_trainable > 0 else 0
    rows_to_cadence = max(0, next_retrain_rows - trainable_rows) if next_retrain_rows > 0 else 0

    final_status = str(retrain_manifest.get("status") or readiness_status)
    final_reasons = list(retrain_manifest.get("reasons") or readiness_reasons)
    if readiness_status == "ready" and quality_status == "ready":
        if final_status == "blocked_new_rows":
            if rows_to_cadence <= 0:
                final_status = "ready"
                final_reasons = []
            else:
                final_reasons = [f"new real trainable rows below cadence: {new_rows_since_trigger} < {min_new_rows}"]
        elif final_status in {"blocked", "blocked_quality"} and not final_reasons:
            final_status = "ready"
    elif final_status not in {"blocked_new_rows", "blocked", "blocked_quality"}:
        final_status = quality_status if quality_status != "ready" else readiness_status
        final_reasons = quality_reasons if quality_status != "ready" else readiness_reasons

    status_label_map = {
        "ready": "PRONTA",
        "blocked_new_rows": "AGUARDANDO_CADENCIA",
        "blocked_quality": "BLOQUEADA_QUALIDADE",
        "blocked": "BLOQUEADA_BASE",
        "trained": "TREINADA",
        "promoted": "PROMOVIDA",
        "rejected_metrics": "REJEITADA_METRICAS",
        "invalid_evaluation": "AVALIACAO_INVALIDA",
        "failed": "FALHOU",
    }
    status_label = status_label_map.get(final_status, str(final_status).upper() or "DESCONHECIDO")

    if final_status == "ready":
        action = "pode disparar o retrain agora"
    elif final_status == "blocked_new_rows":
        action = f"aguardar +{fmt_num(rows_to_cadence)} linhas trainable reais"
    elif final_reasons:
        action = str(final_reasons[0])
    else:
        action = "continuar a coleta e reavaliar"

    return {
        "exists": True,
        "status": final_status,
        "status_label": status_label,
        "reasons": final_reasons,
        "action": action,
        "trainable_rows": trainable_rows,
        "target_trainable": target_trainable,
        "rows_to_target": rows_to_target,
        "last_trigger_rows": last_trigger_rows,
        "new_rows_since_trigger": new_rows_since_trigger,
        "next_retrain_rows": next_retrain_rows,
        "rows_to_cadence": rows_to_cadence,
        "min_new_rows": min_new_rows,
        "min_trainable": min_trainable,
        "min_class_count": min_class_count,
        "required_classes": list(required_classes),
        "decision_counts": decision_counts,
        "class_gaps": class_gaps,
        "quality_status": quality_status,
        "quality_reasons": quality_reasons,
        "quality_checks": quality_checks,
        "scenario_family_counts": dict((quality_report.get("scenario_families") or {}).get("counts") or {}),
        "decision_transition_total": int(((quality_report.get("decision_transitions") or {}).get("total", 0)) or 0),
        "decision_transition_distinct": int(((quality_report.get("decision_transitions") or {}).get("distinct", 0)) or 0),
        "decision_transition_ratio": float(((quality_report.get("decision_transitions") or {}).get("ratio", 0.0)) or 0.0),
        "dominant_class": str(((quality_report.get("class_balance") or {}).get("dominant_class")) or ""),
        "dominant_ratio": float(((quality_report.get("class_balance") or {}).get("dominant_ratio", 0.0)) or 0.0),
        "valid_for_training_ratio": float(((quality_report.get("collection_purity") or {}).get("valid_for_training_ratio", 0.0)) or 0.0),
    }


def load_true_online_status(state):
    online_dir = state / "tasam_true_online_real"
    status = load_json(online_dir / "true_online_status.json", {})
    runner_state = load_json(online_dir / "true_online_state.json", {})
    summary = load_json(online_dir / "tasam_selective" / "tasam_marl_summary.json", {})
    if not status and not runner_state and not summary:
        return {"exists": False, "online_dir": str(online_dir)}

    final_metrics = dict(summary.get("final_metrics") or {})
    return {
        "exists": True,
        "online_dir": str(online_dir),
        "status": str(status.get("status") or "unknown"),
        "reason": str(status.get("reason") or ""),
        "written_transitions": int(status.get("written_transitions", 0) or 0),
        "target_epochs": int(status.get("target_epochs", status.get("target_epochs_next", 0)) or 0),
        "counts": dict(status.get("counts") or {}),
        "updates_completed": int(status.get("updates_completed", runner_state.get("updates_completed", 0)) or 0),
        "completed_epochs": int(summary.get("completed_epochs", 0) or 0),
        "summary_target_epochs": int(summary.get("target_epochs", 0) or 0),
        "last_written_transitions": int(runner_state.get("last_written_transitions", 0) or 0),
        "model_dir": str(status.get("model_dir") or summary.get("checkpoint_root") or (online_dir / "tasam_selective")),
        "eval_return": final_metrics.get("eval_return"),
        "cumulative_return": final_metrics.get("cumulative_return"),
        "summary_exists": bool(summary),
        "status_exists": bool(status),
        "state_exists": bool(runner_state),
    }


def parse_bool_env(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def load_runtime_ml_status():
    runtime = load_json(PROJECT_ROOT / "config" / "core" / "runtime.json", {})
    ml = (runtime.get("ml") or {}) if isinstance(runtime, dict) else {}
    ml_enabled = bool(ml.get("enabled", True))
    ml_retrain_enabled = bool(ml.get("retrain_enabled", True))

    if "GREENRAN_ML_ENABLED" in os.environ:
        ml_enabled = parse_bool_env(os.environ.get("GREENRAN_ML_ENABLED"))
    if "GREENRAN_ML_RETRAIN_ENABLED" in os.environ:
        ml_retrain_enabled = parse_bool_env(os.environ.get("GREENRAN_ML_RETRAIN_ENABLED"))

    return {
        "ml_enabled": ml_enabled,
        "ml_retrain_enabled": ml_retrain_enabled and ml_enabled,
    }


def load_shadow_runtime_status(db_path, window=300):
    out = {
        "available": False,
        "window": int(window),
        "sample_count": 0,
        "positive_score_rate": 0.0,
        "recommend_rate": 0.0,
        "avg_score_delta": 0.0,
        "latest_readiness": "",
        "latest_policy_id": "",
    }
    if not db_path.exists():
        return out

    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        cur = con.cursor()
        rows = cur.execute(
            """
            SELECT
                score_delta,
                recommend_shadow,
                checkpoint_readiness,
                policy_id
            FROM marl_shadow_comparison_history
            ORDER BY rowid DESC
            LIMIT ?
            """,
            (int(window),),
        ).fetchall()
        con.close()
    except Exception:
        return out

    if not rows:
        return out

    sample_count = len(rows)
    positive_rows = sum(1 for row in rows if float(row[0] or 0.0) > 0.01)
    recommend_rows = sum(1 for row in rows if int(row[1] or 0) == 1)
    avg_score_delta = sum(float(row[0] or 0.0) for row in rows) / sample_count
    latest = rows[0]
    out.update(
        {
            "available": True,
            "sample_count": sample_count,
            "positive_score_rate": positive_rows / sample_count,
            "recommend_rate": recommend_rows / sample_count,
            "avg_score_delta": avg_score_delta,
            "latest_readiness": str(latest[2] or ""),
            "latest_policy_id": str(latest[3] or ""),
        }
    )
    return out


def db_snapshot(db_path):
    out = {
        "counts": {},
        "decisions": {},
        "latest_decision": None,
        "latest_decision_improvement": None,
        "sim_min": None,
        "sim_max": None,
    }
    if not db_path.exists():
        return out

    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        cur = con.cursor()
        for table in (
            "extended_metrics",
            "decisions_history",
            "resource_allocation_history",
            "marl_global_state_history",
            "marl_du_state_history",
            "marl_shadow_comparison_history",
            "conflict_events",
        ):
            try:
                out["counts"][table] = cur.execute(f"select count(*) from {table}").fetchone()[0]
            except Exception:
                out["counts"][table] = 0

        try:
            out["decisions"] = dict(cur.execute(
                "select decision, count(*) from decisions_history group by decision"
            ).fetchall())
        except Exception:
            pass

        try:
            out["latest_decision"] = cur.execute(
                "select datetime, decision, reason from decisions_history order by timestamp desc limit 1"
            ).fetchone()
        except Exception:
            pass

        try:
            out["latest_decision_improvement"] = cur.execute(
                """
                select network_improvement_pct, cvar_improvement_pct, p95_improvement_pct, improvement_valid
                from decisions_history
                order by timestamp desc
                limit 1
                """
            ).fetchone()
        except Exception:
            pass

        try:
            out["sim_min"], out["sim_max"] = cur.execute(
                "select min(sim_time_s), max(sim_time_s) from extended_metrics"
            ).fetchone()
        except Exception:
            pass
        con.close()
    except Exception:
        pass
    return out


def collection_screen():
    state = find_collection_state()
    metrics_path = state / "xapp_metrics" / "extended_metrics.json"
    db_path = state / "rapp_data_lake.db"
    metrics = load_json(metrics_path)
    gm = metrics.get("global_metrics", {}) or {}
    sim = metrics.get("sim_time_range", {}) or {}
    db = db_snapshot(db_path)
    training_export = load_online_training_export(state)
    retrain_status = load_online_retrain_status(state)
    training_readiness = load_training_readiness(state)
    true_online = load_true_online_status(state)
    runtime_ml = load_runtime_ml_status()
    shadow_runtime = load_shadow_runtime_status(db_path)
    lines = ps_lines()

    ns3 = (
        process_from_pid_file(state / "ns3.pid")
        or find_process(["ns3.42", "Energy_saving"], lines)
        or find_process(["ns3.42"], lines)
    )
    collector = process_from_pid_file(state / "csv_metrics.pid") or find_process(["csv_to_metrics.py", str(state)], lines) or find_process(["csv_to_metrics.py"], lines)
    rapp = process_from_pid_file(state / "rapp.pid") or find_process(["rapp_orchestrator.py", str(state)], lines) or find_process(["rapp_orchestrator.py"], lines)
    alternator = process_from_pid_file(state / "collection_event_alternator.pid") or find_process(["collection_event_alternator.py"], lines)
    true_online_proc = process_from_pid_file(state / "tasam_true_online_real.pid") or find_process(["run_tasam_true_online_real.py", str(state / "tasam_true_online_real")], lines) or find_process(["run_tasam_true_online_real.py"], lines)

    ext_count = db["counts"].get("extended_metrics", 0)
    decisions = db["decisions"]
    sim_min = db.get("sim_min")
    sim_max = db.get("sim_max")
    sim_span = max(0.0, float(sim_max or 0) - float(sim_min or 0)) if sim_min is not None and sim_max is not None else 0.0

    os.system("clear")
    print("\033[1m" + "=" * 72 + "\033[0m")
    print(f"  \033[1mGREENRAN COLETA AO VIVO\033[0m        {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("\033[1m" + "=" * 72 + "\033[0m")
    print(f"  Run: {state}")
    print("")
    print(f"  ns-3:       {alive_text(ns3)}")
    print(f"  coletor:    {alive_text(collector)}")
    print(f"  rApp:       {alive_text(rapp)}")
    print(f"  alternador: {alive_text(alternator)}")
    if shadow_runtime.get("available"):
        print(
            "  Accuracy operacional: "
            f"{fmt_float(shadow_runtime.get('positive_score_rate', 0.0) * 100.0, 2)}%"
            f"  (TA-SAM shadow vence live no score, janela={shadow_runtime.get('sample_count', 0)})"
        )
    print("")

    collector_mode = gm.get("collector_mode", "?")
    real = gm.get("real_latency_sample_count", 0)
    proxy = gm.get("proxy_latency_sample_count", 0)
    source_counts = gm.get("latency_sample_source_counts", {}) or {}
    print("  \033[1mDados atuais\033[0m")
    print(f"    JSON idade:      {fmt_age(metrics_path)}")
    print(f"    collector_mode:  {collector_mode}")
    print(f"    latência:        real={real} proxy={proxy} fontes={source_counts}")
    print(f"    UEs:             {gm.get('total_active_ues', 0)}  cameras={metrics.get('active_cameras', 0)}  sensores={gm.get('total_active_sensors', 0)}  veiculos={gm.get('total_active_vehicles', 0)}")
    print(f"    sim_end:         {fmt_float(sim.get('end'), 2)}s  janela={fmt_float(sim.get('window_s'), 2)}s  span_db={fmt_float(sim_span, 2)}s")
    print(f"    throughput:      {fmt_float(gm.get('throughput_kbps'), 1)} kbps  fonte={gm.get('throughput_source', '?')}")
    print(f"    P95/CVaR:        {fmt_float(float(gm.get('latency_p95_us', 0) or 0)/1000, 2)} ms / {fmt_float(float(gm.get('cvar_per_ue_us', 0) or 0)/1000, 2)} ms")
    network_quality = evaluate_network_quality(metrics)
    print(
        "    qualidade rede:  "
        f"{fmt_float(network_quality.get('score', 0.0), 2)}/100  "
        f"({network_quality.get('label', 'DESCONHECIDA')})"
    )
    print(
        "    sinais rede:     "
        f"camera={fmt_float(network_quality.get('camera_score', 0.0), 1)}  "
        f"sensores={fmt_float(network_quality.get('sensor_score', 0.0), 1)}  "
        f"veiculos={fmt_float(network_quality.get('vehicle_score', 0.0), 1)}  "
        f"núcleo={fmt_float(network_quality.get('core_score', 0.0), 1)}"
    )
    if network_quality.get("reasons"):
        print(f"    alertas:          {'; '.join(network_quality.get('reasons', [])[:2])}")
    trace_status = trace_status_from_metrics(state, gm)
    pdcp_status = trace_status.get('pdcp', {}) or {}
    rlc_status = trace_status.get('rlc', {}) or {}
    mac_status = trace_status.get('mac', {}) or {}
    print(
        "    traces:          "
        f"PDCP={fmt_seconds(pdcp_status.get('age_s'))}"
        f"{' STALE' if pdcp_status.get('stale') else ''}  "
        f"RLC={fmt_seconds(rlc_status.get('age_s'))}"
        f"{' STALE' if rlc_status.get('stale') else ''}  "
        f"MAC={fmt_seconds(mac_status.get('age_s'))}"
        f"{' STALE' if mac_status.get('stale') else ''}"
    )
    print("")

    print("  \033[1mBanco / metas\033[0m")
    print(f"    extended_metrics:          {fmt_num(ext_count)}")
    print(f"    decisions_history:         {fmt_num(db['counts'].get('decisions_history', 0))}")
    print(f"    marl_global_state_history: {fmt_num(db['counts'].get('marl_global_state_history', 0))}")
    print(f"    marl_du_state_history:     {fmt_num(db['counts'].get('marl_du_state_history', 0))}")
    print(f"    shadow_comparison:         {fmt_num(db['counts'].get('marl_shadow_comparison_history', 0))}")
    print(f"    conflitos:                 {fmt_num(db['counts'].get('conflict_events', 0))}")
    print(f"    10k:  {collection_bar(ext_count, 10_000)}")
    print(f"    30k:  {collection_bar(ext_count, 30_000)}")
    print(f"    100k: {collection_bar(ext_count, 100_000)}")
    print("")

    if true_online.get("exists"):
        print("  \033[1mTA-SAM Online Real\033[0m")
        print(f"    runner:            {alive_text(true_online_proc)}")
        print(
            "    status:            "
            f"{true_online.get('status', 'unknown')}"
            + (f"  ({true_online.get('reason')})" if true_online.get("reason") else "")
        )
        if true_online.get("written_transitions", 0) > 0 or true_online.get("last_written_transitions", 0) > 0:
            print(
                "    transições:        "
                f"{fmt_num(true_online.get('written_transitions', 0) or true_online.get('last_written_transitions', 0))}"
            )
        if true_online.get("target_epochs", 0) > 0 or true_online.get("completed_epochs", 0) > 0:
            target_epochs = int(true_online.get("summary_target_epochs", 0) or true_online.get("target_epochs", 0) or 0)
            completed_epochs = int(true_online.get("completed_epochs", 0) or 0)
            print(
                "    epochs:            "
                f"{fmt_num(completed_epochs)} / {fmt_num(target_epochs)}"
            )
        print(f"    updates:           {fmt_num(true_online.get('updates_completed', 0))}")
        if true_online.get("summary_exists"):
            print(
                "    métricas:          "
                f"eval={fmt_float(true_online.get('eval_return'), 4)}  "
                f"cum={fmt_float(true_online.get('cumulative_return'), 2)}"
            )
        print(f"    diretório:         {true_online.get('online_dir')}")
        print("")

    print("  \033[1mDecisões\033[0m")
    print(
        "    ALLOWED={allowed}  BLOCKED={blocked}  CONDITIONAL={conditional}".format(
            allowed=fmt_num(decisions.get("ALLOWED", 0)),
            blocked=fmt_num(decisions.get("BLOCKED", 0)),
            conditional=fmt_num(decisions.get("CONDITIONAL", 0)),
        )
    )
    latest = db.get("latest_decision")
    latest_improvement = db.get("latest_decision_improvement")
    if latest:
        dt, decision, reason = latest
        print(f"    última: {dt} | {decision}")
        print(f"            {str(reason)[:110]}")
        if latest_improvement:
            improvement_pct, cvar_pct, p95_pct, improvement_valid = latest_improvement
            if improvement_valid:
                print(
                    "            melhora rede: "
                    f"{fmt_float(improvement_pct, 1)}% "
                    f"(CVaR={fmt_float(cvar_pct, 1)}% P95={fmt_float(p95_pct, 1)}%)"
                )
    else:
        print("    última: sem decisão ainda")
    print("")

    if collector_mode == "pdcp_stale" or bool(pdcp_status.get('stale')):
        print("  \033[33mAtenção:\033[0m PDCP travado/stale; snapshot atual deve ser filtrado antes de operar.")
    elif collector_mode != "pdcp_real" or int(proxy or 0) > 0:
        print("  \033[33mAtenção:\033[0m dado atual não está 100% PDCP real. Filtrar antes de operar.")
    elif ext_count < 10_000:
        print("  Leitura: coleta válida, mas ainda pequena para operar com mais confiança.")
    else:
        print("  Leitura: coleta válida para operação TA-SAM/ARMD; continue monitorando.")
    print("\033[1m" + "=" * 72 + "\033[0m")
    sys.stdout.flush()


def screen(mode="auto"):
    if mode == "training":
        return training_screen()
    if mode == "collection":
        return collection_screen()
    state = find_collection_state()
    if (state / "xapp_metrics" / "extended_metrics.json").exists() or (state / "rapp_data_lake.db").exists():
        return collection_screen()
    return training_screen()

if __name__ == "__main__":
    mode = "auto"
    interval = None
    args = sys.argv[1:]
    if args and args[0] in {"collection", "training", "auto"}:
        mode = args.pop(0)
    if args:
        interval = int(args[0])
    try:
        if interval is None:
            screen(mode)
        else:
            while True:
                screen(mode)
                time.sleep(interval)
    except KeyboardInterrupt:
        screen(mode)
        print("")
