#!/usr/bin/env python3
"""Status em tempo real: coleta GreenRAN ou SAC Online + Shadow TA-SAM v4."""

import json, os, re, sqlite3, subprocess, sys, time
from pathlib import Path

DIR = Path("/home/robert/orange_nuclear/runs/sac_bootstrap/online_sam_v1")
LOG = DIR / "training_log.txt"
CKPT = DIR / "online_sam_checkpoint.pt"
MANIFEST = Path("/home/robert/orange_nuclear/runs/sac_bootstrap/tasam_candidate_evaluation_latest.json")
SHADOW_DIR = Path("/home/robert/orange_nuclear/runs/sac_bootstrap/tasam_marl_v4")
TOTAL = 20000
PROJECT_ROOT = Path("/home/robert/orange_nuclear")
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
    raw = LOG.read_text().splitlines()
    ep, step, ret50, eval_ret, qos, alpha, rho, actor_loss = 0, 0, 0, 0, 0, 0, 0, 0
    eval_hist = []
    # First pass: find latest heartbeat and collect eval history
    last_eval_line = None
    for line in raw:
        line = line.strip()
        if not line: continue
        if "return=" in line:
            last_eval_line = line
            m = re.search(r"ep=\s*(\d+).*?return=\s*([\d.-]+).*?eval_ret=\s*([\d.-]+).*?qos=([+-]\d\.\d+).*?actor_loss=([\d.-]+).*?rho=([\d.]+).*?alpha=([\d.]+)", line)
            if m:
                ep_e, ret, er, q, al, r, a = int(m.group(1)), float(m.group(2)), float(m.group(3)), float(m.group(4)), float(m.group(5)), float(m.group(6)), float(m.group(7))
                if ep_e > 0:
                    eval_ret, qos, alpha, rho, actor_loss = er, q, a, r, al
                    eval_hist.append((ep_e, er, q, a, r))
        elif "ret_avg50=" in line and "return=" not in line:
            m = re.search(r"ep=\s*(\d+).*?step=\s*(\d+).*?ret_avg50=\s*([\d.-]+)", line)
            if m:
                ep = int(m.group(1))
                step = int(m.group(2))
                ret50 = float(m.group(3))
                for tok in line.split():
                    if tok.startswith("alpha="): alpha = float(tok.split("=")[1])
                    if tok.startswith("rho="): rho = float(tok.split("=")[1])
    return dict(ep=ep, step=step, ret50=ret50, eval_ret=eval_ret, qos=qos,
                alpha=alpha, rho=rho, actor_loss=actor_loss, eval_hist=eval_hist)


def latest_run(hist):
    if not hist:
        return []
    run = []
    for ep, er, q, a, r in hist:
        if run and ep < run[-1][0] and ep < 5000:
            run = []
        run.append((ep, er, q, a, r))
    return run

def proc_info():
    try:
        r = subprocess.run(["ps", "-p", "4123913", "-o", "etime,%cpu,%mem,stat", "--no-headers"],
                           capture_output=True, text=True, timeout=2)
        data = r.stdout.strip()
        if data:
            parts = data.split()
            et, cpu, mem, stat = parts[0], parts[1], parts[2], parts[3] if len(parts) == 4 else ("", "", "", parts[0])
            icon = "\u2713" if "R" in stat else "\u26a0"
            return f"{icon} PID 4123913  {et}  CPU {cpu}%  MEM {mem}%"
        return "\u2717 MORTO"
    except: return "\u2717 MORTO"

def eta(ep):
    if ep < 100: return "calculando..."
    # Use wall-clock rate since process start (from ps etime)
    try:
        r = subprocess.run(["ps", "-p", "4123913", "-o", "etime", "--no-headers"],
                           capture_output=True, text=True, timeout=2)
        et = r.stdout.strip()
        # Parse etime format: [[dd-]hh:mm:ss or mm:ss]
        if "-" in et:
            d, rest = et.split("-")
            d = int(d)
        else:
            d = 0; rest = et
        parts = rest.split(":")
        if len(parts) == 3:
            h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
        elif len(parts) == 2:
            h, m, s = 0, int(parts[0]), int(parts[1])
        else:
            return "?h"
        total_sec = d * 86400 + h * 3600 + m * 60 + s
        if total_sec <= 0: return "?h"
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
    print("  \033[1mSAC Online + SAM\033[0m          %s" % proc)
    print("\033[1m" + "=" * 56 + "\033[0m")
    print("")
    print("  " + bar(pct))
    print("  \033[1m%5d\033[0m / %d episodios      step %s" % (ep, TOTAL, f"{info['step']:,}"))
    print("")
    print("   ret_avg50   eval_ret    QoS    actor     alpha     rho")
    r50 = info["ret50"]
    er = info["eval_ret"] if info["eval_ret"] else info["ret50"]
    q = info["qos"] if info["qos"] else 0.94
    a = info["alpha"]
    r = info["rho"]
    al = info["actor_loss"]
    print("  %s    %s   %.3f   %7.2f   %.3f   %.4f" % (
        fmt_diff(r50), f"{er:.2f}".rjust(8) if er else "     ?", q, al or 0, a, r))
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
    lines = ps_lines()

    ns3 = (
        process_from_pid_file(state / "ns3.pid")
        or find_process(["ns3.42", "Energy_saving"], lines)
        or find_process(["ns3.42"], lines)
    )
    collector = process_from_pid_file(state / "csv_metrics.pid") or find_process(["csv_to_metrics.py", str(state)], lines) or find_process(["csv_to_metrics.py"], lines)
    rapp = process_from_pid_file(state / "rapp.pid") or find_process(["rapp_orchestrator.py", str(state)], lines) or find_process(["rapp_orchestrator.py"], lines)
    alternator = process_from_pid_file(state / "collection_event_alternator.pid") or find_process(["collection_event_alternator.py"], lines)

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

    if training_export.get("manifest_exists"):
        train_decisions = training_export.get("decision_counts") or {}
        print("  \033[1mTreino online\033[0m")
        print(
            "    raw/trainable:     "
            f"{fmt_num(training_export.get('raw_rows', 0))} / {fmt_num(training_export.get('trainable_rows', 0))}"
        )
        print(
            "    profile/descarte:  "
            f"{training_export.get('profile', '?')} / {fmt_float(training_export.get('drop_ratio', 0.0) * 100.0, 1)}%"
        )
        print(
            "    classes trainable: "
            "ALLOWED={allowed}  BLOCKED={blocked}  CONDITIONAL={conditional}".format(
                allowed=fmt_num(train_decisions.get("ALLOWED", 0)),
                blocked=fmt_num(train_decisions.get("BLOCKED", 0)),
                conditional=fmt_num(train_decisions.get("CONDITIONAL", 0)),
            )
        )
        if retrain_status.get("exists"):
            if retrain_status.get("target_trainable", 0) > 0:
                print(
                    "    meta/coleta:       "
                    f"{fmt_num(training_export.get('trainable_rows', 0))} / {fmt_num(retrain_status.get('target_trainable', 0))}"
                )
            if retrain_status.get("next_retrain_rows", 0) > 0:
                print(
                    "    próx checkpoint:   "
                    f"{fmt_num(retrain_status.get('next_retrain_rows', 0))}"
                    f"  (+{fmt_num(retrain_status.get('min_new_rows', 0))})"
                )
            print(
                "    retrain:           "
                f"{retrain_status.get('status', 'unknown')}"
                f"  clf={retrain_status.get('selected_classifier', '') or '-'}"
            )
            if retrain_status.get("metric_gate_status"):
                print(
                    "    gate métricas:     "
                    f"{retrain_status.get('metric_gate_status')}  "
                    f"acc={fmt_float(retrain_status.get('rf_accuracy'), 3)}  "
                    f"r2={fmt_float(retrain_status.get('regressor_r2'), 3)}"
                )
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
        print("  \033[33mAtenção:\033[0m PDCP travado/stale; snapshot atual deve ser filtrado antes do treino.")
    elif collector_mode != "pdcp_real" or int(proxy or 0) > 0:
        print("  \033[33mAtenção:\033[0m dado atual não está 100% PDCP real. Filtrar antes do treino.")
    elif ext_count < 10_000:
        print("  Leitura: coleta válida, mas ainda pequena para treino forte.")
    else:
        print("  Leitura: coleta válida para treino inicial; continue para mais diversidade.")
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
