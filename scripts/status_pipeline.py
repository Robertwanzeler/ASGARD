#!/usr/bin/env python3
"""Status da pipeline article_v1: coleta, shadow, ETA."""

import json, os, sqlite3, subprocess, sys, time
from pathlib import Path

DB = Path("/tmp/greenran_tasam_article_v1/rapp_data_lake.db")
EXT_JSON = Path("/tmp/greenran_tasam_article_v1/xapp_metrics/extended_metrics.json")
NS3_PID = None
TARGET = 10000

def find_ns3_pid():
    try:
        r = subprocess.run(["ps", "-eo", "pid,args"], capture_output=True, text=True, timeout=3)
        for line in r.stdout.splitlines():
            if "Energy_saving" in line and "build/scratch" in line:
                return line.split(None, 1)[0]
    except: pass
    return None

def proc_uptime(pid):
    try:
        r = subprocess.run(["ps", "-p", pid, "-o", "etime,%cpu,%mem", "--no-headers"],
                           capture_output=True, text=True, timeout=2)
        data = r.stdout.strip()
        if data:
            parts = data.split()
            et, cpu, mem = parts[0], parts[1], parts[2]
            return et, cpu, mem
        return None, None, None
    except: return None, None, None

def bar(val, total, w=24):
    pct = val / total * 100 if total > 0 else 0
    fill = int(pct / 100 * w)
    return "\u2588" * fill + "\u2591" * (w - fill) + f" {pct:5.1f}%"

def fmt_bytes(b):
    for u in ("B", "KB", "MB", "GB"):
        if b < 1024: return f"{b:.0f} {u}"
        b /= 1024
    return f"{b:.1f} TB"

def fmt_eta(rem_sec):
    if rem_sec is None or rem_sec < 0: return "--"
    h, r = divmod(int(rem_sec), 3600)
    m, s = divmod(r, 60)
    fin = time.strftime("%d/%b %H:%M", time.localtime(time.time() + rem_sec))
    return f"~{h}h{m:02d}m  ({fin})"

def screen():
    global NS3_PID
    if NS3_PID is None:
        NS3_PID = find_ns3_pid()
    pid_alive = NS3_PID and os.path.exists(f"/proc/{NS3_PID}")

    et, cpu, mem = proc_uptime(NS3_PID) if pid_alive else (None, None, None)
    db_sz = DB.stat().st_size if DB.exists() else 0

    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        global_count = con.execute("SELECT COUNT(*) FROM marl_global_state_history").fetchone()[0]
        du_count     = con.execute("SELECT COUNT(*) FROM marl_du_state_history").fetchone()[0]
        ext_count    = con.execute("SELECT COUNT(*) FROM extended_metrics").fetchone()[0]
        shadow_count = con.execute("SELECT COUNT(*) FROM marl_shadow_comparison_history").fetchone()[0]

        cur = con.execute("SELECT MIN(timestamp), MAX(timestamp) FROM extended_metrics")
        ts_min, ts_max = cur.fetchone()

        cur = con.execute(
            "SELECT score_delta, shadow_score, live_score, "
            "live_ai_completion_est, shadow_ai_completion_est "
            "FROM marl_shadow_comparison_history ORDER BY id DESC LIMIT 1"
        )
        shadow_row = cur.fetchone()
        # Compare over last 100 for consistency
        cur = con.execute(
            "SELECT score_delta FROM marl_shadow_comparison_history "
            "ORDER BY id DESC LIMIT 100"
        )
        deltas = [r[0] for r in cur.fetchall() if r[0] is not None]
        avg_delta = sum(deltas) / len(deltas) if deltas else 0
    finally:
        con.close()

    # Collector mode from extended_metrics.json
    collector_mode = "?"
    real_lat = 0
    try:
        with open(EXT_JSON) as f:
            d = json.load(f)
        g = d.get("global_metrics", {})
        collector_mode = g.get("collector_mode", "?")
        real_lat = g.get("real_latency_sample_count", 0)
        proxy_lat = g.get("proxy_latency_sample_count", 0)
    except: pass

    # Rate
    elapsed_h = None
    if ts_min and ts_max and ts_max > ts_min:
        elapsed_h = (ts_max - ts_min) / 3600
    rate_h = ext_count / elapsed_h if elapsed_h and elapsed_h > 0 else 0

    # ETA to target
    rem = max(0, TARGET - ext_count)
    eta_sec = rem / rate_h * 3600 if rate_h > 0 else None

    # Shadow verdict
    verdict = "---"
    if shadow_row:
        sd = shadow_row[0]
        if sd is not None:
            if sd > 0.01:
                verdict = "BEATING_LIVE"
            elif sd > -0.01:
                verdict = "tie"
            else:
                verdict = "not_beating_live"

    os.system("clear")
    print("\033[1m" + "=" * 58 + "\033[0m")
    print(f"  Pipeline article_v1          {time.strftime('%H:%M:%S')}")
    print("\033[1m" + "=" * 58 + "\033[0m")
    print("")

    if pid_alive:
        print(f"  ns-3: \033[1mPID {NS3_PID}\033[0m  {et}  CPU {cpu}%  MEM {mem}%")
    else:
        print(f"  ns-3: \033[31mDOWN\033[0m")
    print(f"  DB:   {fmt_bytes(db_sz)}  ({fmt_bytes(db_sz // max(ext_count, 1))}/ts)")
    print(f"  Collect: {collector_mode}  (real_lat={real_lat}, proxy_lat={proxy_lat})")
    print("")

    # Progress bar (against target)
    progress = min(ext_count, TARGET)
    print(f"  {bar(progress, TARGET)}")
    print(f"  \033[1m{ext_count}\033[0m / {TARGET} timesteps   {du_count} DU states")
    print(f"  Shadow: {shadow_count} comparacoes  avg_delta={avg_delta:+.5f}")
    print("")

    print(f"  Rate: \033[1m{rate_h:.0f}\033[0m ts/h  ({elapsed_h:.1f}h decorridos)")
    print(f"  ETA: \033[1m{fmt_eta(eta_sec)}\033[0m")
    print("")

    if shadow_row:
        sd, ss, ls, lai, sai = [shadow_row[i] for i in range(5)]
        print(f"  \033[1mShadow vs Live\033[0m")
        print(f"    score_delta:       {sd:+.5f}  ({verdict})")
        print(f"    shadow_score:      {ss:.4f}")
        print(f"    live_score:        {ls:.4f}")
        print(f"    shadow_ai_comp:    {sai:.3f}")
        print(f"    live_ai_comp:      {lai:.3f}")

    print("\033[1m" + "=" * 58 + "\033[0m")
    sys.stdout.flush()

if __name__ == "__main__":
    interval = int(sys.argv[1]) if len(sys.argv) > 1 else 15
    try:
        while True:
            screen()
            time.sleep(interval)
    except KeyboardInterrupt:
        screen()
        print("")