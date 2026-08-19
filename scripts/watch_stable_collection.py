#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from greenran_paths import STATE_DIR  # type: ignore

DEFAULT_STATE_DIR = Path("/tmp/greenran_marl_stable_ready")
DEFAULT_GOAL_SIM_TIME = 90.0
DEFAULT_ROW_TARGET = 5000
DEFAULT_SHADOW_WINDOW = 300
CORE_PID_FILES = [
    "ns3.pid",
    "csv_metrics.pid",
    "rapp.pid",
    "dashboard.pid",
    "app1_vigilancia.pid",
    "app2_monitoramento.pid",
    "app3_veicular.pid",
    "carla_bridge.pid",
    "carla_ns3_mapper.pid",
    "marl_runtime_gate_watch.pid",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Simple live monitor for the stable GreenRAN collection")
    parser.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR), help="Runtime state directory")
    parser.add_argument("--goal-sim-time", type=float, default=DEFAULT_GOAL_SIM_TIME, help="Target sim time for progress display")
    parser.add_argument("--row-target", type=int, default=DEFAULT_ROW_TARGET, help="Target row count for progress display")
    parser.add_argument("--shadow-window", type=int, default=DEFAULT_SHADOW_WINDOW, help="Recent shadow rows to summarize")
    parser.add_argument("--refresh", type=float, default=2.0, help="Refresh interval seconds")
    parser.add_argument("--plain-log", action="store_true", help="Print snapshots continuously instead of rendering a fixed terminal panel")
    parser.add_argument("--once", action="store_true", help="Print one snapshot and exit")
    return parser.parse_args()


def safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def pct(done: float, total: float) -> float:
    if total <= 0:
        return 0.0
    return max(0.0, min(100.0, (done / total) * 100.0))


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def process_alive(pid_path: Path) -> bool:
    if not pid_path.exists():
        return False
    try:
        pid = int(pid_path.read_text().strip())
    except Exception:
        return False
    return Path(f"/proc/{pid}").exists()


def core_alive(state_dir: Path) -> bool:
    return all(process_alive(state_dir / name) for name in CORE_PID_FILES)


def summarize_shadow(rows: list[tuple]) -> dict:
    if not rows:
        return {}
    sample_count = len(rows)
    score_deltas = [safe_float(row[3]) for row in rows]
    recommends = [safe_int(row[4]) for row in rows]
    positives = sum(1 for value in score_deltas if value > 0.01)
    latest = rows[0]
    return {
        "sample_count": sample_count,
        "policy_id": latest[0] or "",
        "source": latest[1] or "",
        "checkpoint_readiness": latest[2] or "",
        "latest_score_delta": safe_float(latest[3]),
        "latest_recommend_shadow": bool(latest[4]),
        "latest_live_score": safe_float(latest[5]),
        "latest_shadow_score": safe_float(latest[6]),
        "avg_score_delta": sum(score_deltas) / max(sample_count, 1),
        "positive_rate": positives / max(sample_count, 1),
        "recommend_rate": sum(recommends) / max(sample_count, 1),
    }


def table_exists(cur: sqlite3.Cursor, name: str) -> bool:
    row = cur.execute("select name from sqlite_master where type='table' and name=?", (name,)).fetchone()
    return row is not None


def query_db(db_path: Path, shadow_window: int) -> dict:
    if not db_path.exists():
        return {"db_exists": False}
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    out = {"db_exists": True}
    try:
        out["rows"] = cur.execute("select count(*) from resource_allocation_history").fetchone()[0] if table_exists(cur, "resource_allocation_history") else 0
        out["shadow_rows"] = cur.execute("select count(*) from marl_shadow_comparison_history").fetchone()[0] if table_exists(cur, "marl_shadow_comparison_history") else 0
        out["latest_alloc"] = cur.execute(
            """
            select datetime, controller_id, d_ran, d_ai, r_ran, r_ai,
                   ran_completion_ratio, ai_completion_ratio, utilization_ratio
            from resource_allocation_history
            order by timestamp desc limit 1
            """
        ).fetchone() if table_exists(cur, "resource_allocation_history") else None
        out["latest_decision"] = cur.execute(
            """
            select datetime, decision, reason
            from decisions_history
            order by timestamp desc limit 1
            """
        ).fetchone() if table_exists(cur, "decisions_history") else None
        out["latest_ext"] = cur.execute(
            """
            select datetime, sim_time_s, throughput_kbps, cvar_per_ue_us
            from extended_metrics
            order by timestamp desc limit 1
            """
        ).fetchone() if table_exists(cur, "extended_metrics") else None
        shadow = cur.execute(
            """
            select policy_id, source, checkpoint_readiness, score_delta, recommend_shadow,
                   live_score, shadow_score
            from marl_shadow_comparison_history
            order by timestamp desc limit ?
            """,
            (int(shadow_window),),
        ).fetchall() if table_exists(cur, "marl_shadow_comparison_history") else []
        out["shadow_summary"] = summarize_shadow(shadow)
    finally:
        conn.close()
    return out


def clear_screen() -> None:
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()


def move_home() -> None:
    sys.stdout.write("\033[H\033[J")
    sys.stdout.flush()


def enter_alt_screen() -> None:
    sys.stdout.write("\033[?1049h\033[?25l")
    sys.stdout.flush()


def leave_alt_screen() -> None:
    sys.stdout.write("\033[?25h\033[?1049l")
    sys.stdout.flush()


def build_lines(state_dir: Path, goal_sim_time: float, row_target: int, data: dict, control: dict, app1: dict) -> list[str]:
    rows = safe_int(data.get("rows"), 0)
    latest_alloc = data.get("latest_alloc") or []
    latest_ext = data.get("latest_ext") or []
    latest_decision = data.get("latest_decision") or []
    shadow = data.get("shadow_summary") or {}
    sim_time = safe_float(latest_ext[1] if len(latest_ext) > 1 else 0.0)
    row_remaining = max(0, int(row_target - rows))
    sim_remaining = max(0.0, float(goal_sim_time - sim_time))
    stage = str(control.get("collection_event_stage_name") or control.get("scenario") or "unknown")
    cycle = safe_int(control.get("collection_event_cycle"), 0)
    stage_remaining = safe_float(control.get("stage_remaining_s"), 0.0)
    app1_sla = app1.get("camera_sla") or {}
    app1_obs = app1_sla.get("observed") or {}

    return [
        "GreenRAN Stable Collection Monitor",
        "=================================",
        f"state_dir       : {state_dir}",
        f"core_alive      : {core_alive(state_dir)}",
        "",
        "progress",
        f"  rows          : {rows} / {row_target} ({pct(rows, row_target):5.1f}%) | faltam {row_remaining}",
        f"  sim_time      : {sim_time:.3f} / {goal_sim_time:.3f} ({pct(sim_time, goal_sim_time):5.1f}%) | faltam {sim_remaining:.3f}s",
        f"  stage         : {stage} | cycle={cycle} | faltam {stage_remaining:.3f}s na fase",
        "",
        "allocation",
        f"  controller    : {latest_alloc[1] if len(latest_alloc) > 1 else 'unknown'}",
        f"  d_ran / d_ai  : {safe_float(latest_alloc[2] if len(latest_alloc) > 2 else 0.0):.4f} / {safe_float(latest_alloc[3] if len(latest_alloc) > 3 else 0.0):.4f}",
        f"  r_ran / r_ai  : {safe_float(latest_alloc[4] if len(latest_alloc) > 4 else 0.0):.4f} / {safe_float(latest_alloc[5] if len(latest_alloc) > 5 else 0.0):.4f}",
        f"  completion    : ran={safe_float(latest_alloc[6] if len(latest_alloc) > 6 else 0.0):.4f} ai={safe_float(latest_alloc[7] if len(latest_alloc) > 7 else 0.0):.4f}",
        f"  utilization   : {safe_float(latest_alloc[8] if len(latest_alloc) > 8 else 0.0):.4f}",
        "",
        "decision",
        f"  latest        : {latest_decision[1] if len(latest_decision) > 1 else 'unknown'}",
        f"  reason        : {latest_decision[2] if len(latest_decision) > 2 else 'n/a'}",
        "",
        "app1",
        f"  runtime       : {app1_sla.get('runtime_status', 'unknown')}",
        f"  proposal      : {app1_sla.get('proposal_status', 'unknown')}",
        f"  throughput    : min={safe_float(app1_obs.get('min_throughput_mbps'), 0.0):.2f} avg={safe_float(app1_obs.get('avg_throughput_mbps'), 0.0):.2f} Mbps",
        f"  latency       : max={safe_float(app1_obs.get('max_latency_ms'), 0.0):.2f} avg={safe_float(app1_obs.get('avg_latency_ms'), 0.0):.2f} ms",
        "",
        "shadow",
        f"  rows          : {safe_int(data.get('shadow_rows'), 0)}",
        f"  policy        : {shadow.get('policy_id', 'unknown')}",
        f"  readiness     : {shadow.get('checkpoint_readiness', 'unknown')}",
        f"  latest_delta  : {safe_float(shadow.get('latest_score_delta'), 0.0):.6f}",
        f"  avg_delta     : {safe_float(shadow.get('avg_score_delta'), 0.0):.6f}",
        f"  positive_rate : {safe_float(shadow.get('positive_rate'), 0.0):.3f}",
        f"  recommend_rate: {safe_float(shadow.get('recommend_rate'), 0.0):.3f}",
        "",
        "Ctrl+C para sair.",
    ]


def render(args: argparse.Namespace) -> int:
    state_dir = Path(args.state_dir)
    db_path = state_dir / "rapp_data_lake.db"
    control_path = state_dir / "article00_scenario_control.json"
    app1_path = state_dir / "app1_vigilancia" / "monitoring_snapshot.json"
    use_panel = not args.once and not args.plain_log
    if use_panel:
        enter_alt_screen()
    try:
        while True:
            data = query_db(db_path, args.shadow_window)
            control = load_json(control_path)
            app1 = load_json(app1_path)
            lines = build_lines(state_dir, args.goal_sim_time, args.row_target, data, control, app1)
            if args.once:
                print("\n".join(lines))
                return 0
            if use_panel:
                move_home()
                print("\n".join(lines), flush=True)
            else:
                clear_screen()
                print("\n".join(lines), flush=True)
            time.sleep(max(args.refresh, 0.25))
    except KeyboardInterrupt:
        return 0
    finally:
        if use_panel:
            leave_alt_screen()


def main() -> int:
    return render(parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
