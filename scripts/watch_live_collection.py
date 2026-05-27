#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATUS = REPO_ROOT / "runs" / "sac_bootstrap" / "fixed_awac_pressure_smoke_clean_v5_status.json"
DEFAULT_CHECKPOINT = REPO_ROOT / "runs" / "sac_bootstrap" / "fixed_awac_pressure_smoke_clean_v5_status_checkpoints" / "latest_checkpoint.json"
DEFAULT_APP1 = Path("/tmp/greenran_fixed_awac_pressure_smoke_clean_v5/app1_vigilancia/monitoring_snapshot.json")
DEFAULT_STATE_DIR = Path("/tmp/greenran_fixed_awac_pressure_smoke_clean_v5")
DEFAULT_CSV = REPO_ROOT / "runs" / "sac_bootstrap" / "workload_trace_fixed_baseline_v1_pressure_smoke_clean_v5.csv"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Live dashboard for GreenRAN controlled collection")
    parser.add_argument("--status-json", default=str(DEFAULT_STATUS), help="Path to collection status.json")
    parser.add_argument("--latest-checkpoint", default=str(DEFAULT_CHECKPOINT), help="Path to latest checkpoint JSON")
    parser.add_argument("--app1-json", default=str(DEFAULT_APP1), help="Path to App1 monitoring snapshot")
    parser.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR), help="Runtime state directory used by the probe refresh")
    parser.add_argument("--csv-path", default=str(DEFAULT_CSV), help="Workload CSV path used by the probe refresh")
    parser.add_argument("--collection-profile", default="drl_article_conflict_smoke_v1", help="Collection profile passed to the probe refresh")
    parser.add_argument("--goal-sim-time", type=float, default=120.0, help="Goal sim_time passed to the probe refresh")
    parser.add_argument("--runtime-sim-time", type=int, default=120, help="Runtime sim_time passed to the probe refresh")
    parser.add_argument("--row-target", type=int, default=5000, help="Row target passed to the probe refresh")
    parser.add_argument("--min-rows", type=int, default=300, help="Minimum rows passed to the probe refresh")
    parser.add_argument("--app2-wait-seconds", type=int, default=15, help="App2 wait passed to the probe refresh")
    parser.add_argument("--probe-refresh", action=argparse.BooleanOptionalAction, default=True, help="Refresh the status/checkpoint with a live --no-start --once probe before each render")
    parser.add_argument("--refresh", type=float, default=2.0, help="Refresh interval in seconds")
    parser.add_argument("--once", action="store_true", help="Print one snapshot and exit")
    return parser.parse_args()


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


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


def clear_screen() -> None:
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()


def refresh_probe(args: argparse.Namespace) -> None:
    cmd = [
        sys.executable,
        str(REPO_ROOT / "scripts" / "run_fixed_awac_controlled_collection.py"),
        "--state-dir",
        str(args.state_dir),
        "--csv-path",
        str(args.csv_path),
        "--status-json",
        str(args.status_json),
        "--launcher-log",
        "/tmp/greenran_watch_live_launcher.log",
        "--checkpoint-dir",
        str(Path(args.latest_checkpoint).parent),
        "--collection-profile",
        str(args.collection_profile),
        "--row-target",
        str(args.row_target),
        "--min-rows",
        str(args.min_rows),
        "--goal-sim-time",
        str(args.goal_sim_time),
        "--runtime-sim-time",
        str(args.runtime_sim_time),
        "--app2-wait-seconds",
        str(args.app2_wait_seconds),
        "--no-start",
        "--once",
    ]
    subprocess.run(cmd, cwd=REPO_ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


def build_lines(status: dict, checkpoint: dict, app1: dict, refresh: float, probe_refresh: bool) -> list[str]:
    updated_at = status.get("updated_at", "unknown")
    scenario_id = status.get("scenario_id", "unknown")
    profile = status.get("collection_profile", "unknown")
    state_dir = status.get("state_dir", "unknown")

    rows = safe_int(status.get("rows"), 0)
    row_target = safe_int(status.get("row_target"), 0)
    row_remaining = safe_int(status.get("row_target_remaining"), max(row_target - rows, 0))
    min_rows = safe_int(status.get("min_rows"), 0)
    min_remaining = safe_int(status.get("min_rows_remaining"), max(min_rows - rows, 0))

    sim_time = safe_float(status.get("sim_time_end"), 0.0)
    goal_sim_time = safe_float(status.get("goal_sim_time"), 0.0)
    sim_remaining = safe_float(status.get("sim_time_remaining"), max(goal_sim_time - sim_time, 0.0))

    ran = status.get("ran_pressure") or {}
    latest = status.get("latest_alloc") or {}
    latest_ext = status.get("latest_ext") or {}
    coverage = status.get("coverage") or {}
    controllers = status.get("controllers") or {}
    verdict = checkpoint.get("verdict") or {}
    snapshot = checkpoint.get("status_snapshot") or {}
    app1_net = app1.get("network") or {}
    app1_sla = app1.get("camera_sla") or {}
    app1_obs = app1_sla.get("observed") or {}

    return [
        "GreenRAN Live Collection Monitor",
        "=" * 32,
        f"updated_at      : {updated_at}",
        f"scenario_id     : {scenario_id}",
        f"profile         : {profile}",
        f"state_dir       : {state_dir}",
        f"refresh         : {refresh:.1f}s",
        f"probe_refresh   : {probe_refresh}",
        "",
        "progress",
        f"  rows          : {rows} / {row_target} ({pct(rows, row_target):5.1f}%) | faltam {row_remaining}",
        f"  min_rows      : {rows} / {min_rows} ({pct(rows, min_rows):5.1f}%) | faltam {min_remaining}",
        f"  sim_time      : {sim_time:.3f} / {goal_sim_time:.3f} ({pct(sim_time, goal_sim_time):5.1f}%) | faltam {sim_remaining:.3f}s",
        f"  pressure      : stage={ran.get('stage', 'unknown')} cycle={safe_int(ran.get('cycle'), 0)} elapsed={safe_float(ran.get('stage_elapsed'), 0.0):.3f}s remaining={safe_float(ran.get('stage_remaining'), 0.0):.3f}s",
        "",
        "allocation",
        f"  controller    : {latest.get('controller_id', 'unknown')}",
        f"  d_ran / d_ai  : {safe_float(latest.get('d_ran'), 0.0):.4f} / {safe_float(latest.get('d_ai'), 0.0):.4f}",
        f"  r_ran / r_ai  : {safe_float(latest.get('r_ran'), 0.0):.4f} / {safe_float(latest.get('r_ai'), 0.0):.4f}",
        f"  completion    : ran={safe_float(latest.get('ran_completion_ratio'), 0.0):.4f} ai={safe_float(latest.get('ai_completion_ratio'), 0.0):.4f}",
        f"  utilization   : {safe_float(latest.get('utilization_ratio'), 0.0):.4f}",
        "",
        "telemetry",
        f"  throughput    : {safe_float(latest_ext.get('throughput_kbps'), 0.0):.2f} kbps",
        f"  cvar_per_ue   : {safe_float(latest_ext.get('cvar_per_ue_us'), 0.0):.2f} us",
        f"  alloc_rows_db : {safe_int(status.get('resource_allocation_rows'), 0)}",
        "",
        "coverage",
        f"  d_ran_unique  : {safe_int(coverage.get('d_ran_unique'), 0)}",
        f"  d_ai_unique   : {safe_int(coverage.get('d_ai_unique'), 0)}",
        f"  ran_completion: {safe_int(coverage.get('ran_completion_unique'), 0)}",
        f"  ai_completion : {safe_int(coverage.get('ai_completion_unique'), 0)}",
        f"  utilization   : {safe_int(coverage.get('utilization_unique'), 0)}",
        f"  controllers   : {controllers}",
        "",
        "app1 cameras",
        f"  throughput    : min={safe_float(app1_obs.get('min_throughput_mbps'), safe_float(app1_net.get('min_camera_throughput_mbps'), 0.0)):.2f} avg={safe_float(app1_obs.get('avg_throughput_mbps'), safe_float(app1_net.get('avg_camera_throughput_mbps'), 0.0)):.2f} Mbps",
        f"  latency       : max={safe_float(app1_obs.get('max_latency_ms'), safe_float(app1_net.get('max_camera_latency_ms'), 0.0)):.2f} avg={safe_float(app1_obs.get('avg_latency_ms'), safe_float(app1_net.get('avg_camera_latency_ms'), 0.0)):.2f} ms",
        f"  status        : runtime={app1_sla.get('runtime_status', 'unknown')} proposal={app1_sla.get('proposal_status', 'unknown')}",
        f"  reason        : {app1_sla.get('reason', 'n/a')}",
        "",
        "checkpoint",
        f"  latest        : {checkpoint.get('checkpoint_key', 'missing')}",
        f"  readiness     : {verdict.get('readiness', 'unknown')}",
        f"  ran_conflict  : {verdict.get('ran_conflict_observed', False)}",
        f"  recommendation: {verdict.get('recommendation', 'n/a')}",
        "",
        "runtime",
        f"  core_alive    : {bool(status.get('runtime_core_alive'))}",
        f"  launcher_alive: {bool(status.get('launcher_alive'))}",
        f"  checkpoint_ts : {snapshot.get('updated_at', 'n/a')}",
        "",
        "Ctrl+C para sair.",
    ]


def render(args: argparse.Namespace, status_path: Path, checkpoint_path: Path, app1_path: Path, refresh: float, once: bool) -> int:
    while True:
        if args.probe_refresh:
            refresh_probe(args)
        status = load_json(status_path)
        checkpoint = load_json(checkpoint_path)
        app1 = load_json(app1_path)
        lines = build_lines(status, checkpoint, app1, refresh, args.probe_refresh)
        if once:
            print("\n".join(lines))
            return 0
        clear_screen()
        print("\n".join(lines), flush=True)
        time.sleep(max(refresh, 0.25))


def main() -> int:
    args = parse_args()
    return render(args, Path(args.status_json), Path(args.latest_checkpoint), Path(args.app1_json), args.refresh, args.once)


if __name__ == "__main__":
    raise SystemExit(main())
