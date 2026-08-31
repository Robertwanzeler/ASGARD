#!/usr/bin/env python3
"""Stop one GreenRAN run after a fixed number of rApp decisions.

The watcher is deliberately scoped to PID files inside one state directory.
It is used for paired baseline/assistant experiments where the baseline has no
control-trial counter but both sides must stop at the same decision count.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


PID_NAMES = (
    "rapp.pid",
    "csv_metrics.pid",
    "xapp_slicer.pid",
    "xapp_energy.pid",
    "xapp_vehicle.pid",
    "ns3.pid",
    "ns3_supervisor.pid",
    "ric.pid",
    "db_snapshot.pid",
    "tasam_article_export.pid",
    "tasam_true_online_real.pid",
    "rapp_online_retrain.pid",
    "collection_event.pid",
    "collection_event_alternator.pid",
    "wall_clock_supervisor.pid",
)


def read_pid(path: Path) -> int | None:
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
        return pid if pid > 1 else None
    except (OSError, ValueError):
        return None


def signal_scoped_process(pid: int, sig: int) -> None:
    """Signal a state-scoped process and its setsid children when available."""
    try:
        pgid = os.getpgid(pid)
    except ProcessLookupError:
        return
    # Collection services are started with setsid, so their process group is
    # the safest scope for stopping the supervisor plus its collector child.
    # Never signal our own process group as a broad fallback.
    if pgid > 1 and pgid != os.getpgrp():
        try:
            os.killpg(pgid, sig)
            return
        except ProcessLookupError:
            return
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        pass


def decision_count(db_path: Path) -> int:
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0) as conn:
            row = conn.execute("SELECT COUNT(*) FROM decisions_history").fetchone()
            return int(row[0] or 0) if row else 0
    except (OSError, sqlite3.Error):
        return 0


def real_metric_count(db_path: Path) -> int:
    """Count only fresh, non-proxy PDCP metric rows."""
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0) as conn:
            row = conn.execute(
                """SELECT COUNT(*) FROM extended_metrics
                   WHERE collector_mode='pdcp_real'
                     AND COALESCE(real_latency_sample_count, 0) > 0
                     AND COALESCE(proxy_latency_sample_count, 0)=0
                     AND COALESCE(pdcp_stale, 0)=0"""
            ).fetchone()
            return int(row[0] or 0) if row else 0
    except (OSError, sqlite3.Error):
        return 0


def assistant_only_invalid(db_path: Path) -> bool:
    """Return true when the rApp stopped without applying a fallback policy."""
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0) as conn:
            row = conn.execute(
                "SELECT decision, reason FROM decisions_history ORDER BY id DESC LIMIT 1"
            ).fetchone()
            return bool(
                row
                and (
                    str(row[0] or "") == "ASSISTANT_ONLY_INVALID"
                    or str(row[1] or "").startswith("assistant-only run invalid:")
                )
            )
    except (OSError, sqlite3.Error):
        return False


def stop_run(state_dir: Path) -> list[dict]:
    results: list[dict] = []
    pids: dict[int, str] = {}
    for name in PID_NAMES:
        pid = read_pid(state_dir / name)
        if pid is not None and pid != os.getpid():
            pids.setdefault(pid, name)
    for pid, name in pids.items():
        try:
            signal_scoped_process(pid, signal.SIGTERM)
            results.append({"pid": pid, "pid_file": name, "signal": "SIGTERM"})
        except ProcessLookupError:
            results.append({"pid": pid, "pid_file": name, "signal": "already_stopped"})
        except PermissionError:
            results.append({"pid": pid, "pid_file": name, "signal": "permission_denied"})
    time.sleep(1.0)
    for pid, name in pids.items():
        try:
            os.kill(pid, 0)
            signal_scoped_process(pid, signal.SIGKILL)
            results.append({"pid": pid, "pid_file": name, "signal": "SIGKILL"})
        except (ProcessLookupError, PermissionError):
            pass
    return results


def stop_rapp_only(state_dir: Path) -> list[dict]:
    """Stop only the rApp so the PDCP collector can finish its metric window."""
    pid = read_pid(state_dir / "rapp.pid")
    if pid is None or pid == os.getpid():
        return []
    result: list[dict] = []
    try:
        signal_scoped_process(pid, signal.SIGTERM)
        result.append({"pid": pid, "pid_file": "rapp.pid", "signal": "SIGTERM"})
    except ProcessLookupError:
        result.append({"pid": pid, "pid_file": "rapp.pid", "signal": "already_stopped"})
    except PermissionError:
        result.append({"pid": pid, "pid_file": "rapp.pid", "signal": "permission_denied"})
    time.sleep(1.0)
    try:
        os.kill(pid, 0)
        signal_scoped_process(pid, signal.SIGKILL)
        result.append({"pid": pid, "pid_file": "rapp.pid", "signal": "SIGKILL"})
    except (ProcessLookupError, PermissionError):
        pass
    return result


def stop_pid_file(state_dir: Path, name: str) -> dict | None:
    pid = read_pid(state_dir / name)
    if pid is None or pid == os.getpid():
        return None
    result = {"pid": pid, "pid_file": name, "signal": "SIGTERM"}
    try:
        signal_scoped_process(pid, signal.SIGTERM)
    except ProcessLookupError:
        result["signal"] = "already_stopped"
        return result
    except PermissionError:
        result["signal"] = "permission_denied"
        return result
    time.sleep(1.0)
    try:
        os.kill(pid, 0)
        signal_scoped_process(pid, signal.SIGKILL)
        result["signal"] = "SIGKILL"
    except (ProcessLookupError, PermissionError):
        pass
    return result


def finalize_real_metrics(state_dir: Path) -> dict:
    """Parse the completed local PDCP/RLC traces once and import the snapshot."""
    trace_dir = state_dir / "ns3_traces"
    metrics_dir = state_dir / "xapp_metrics"
    metrics_dir.mkdir(parents=True, exist_ok=True)
    extended_path = metrics_dir / "extended_metrics.json"
    standard_path = metrics_dir / "metrics.json"
    log_path = state_dir / "finalize_metrics.log"
    command = [
        sys.executable,
        str(PROJECT_ROOT / "src" / "csv_to_metrics.py"),
        "--input-dir", str(trace_dir),
        "--output", str(standard_path),
        "--extended-output", str(extended_path),
        "--poll-interval", "0",
        "--once",
    ]
    env = os.environ.copy()
    env["GREENRAN_STATE_DIR"] = str(state_dir)
    env["GREENRAN_REQUIRE_REAL_PDCP"] = "1"
    # The run has just been stopped; wall-clock age is not evidence that the
    # completed PDCP trace is invalid.  Keep strict real-only parsing while
    # disabling only the post-run freshness cutoff for this final snapshot.
    env["GREENRAN_PDCP_STALE_SECONDS"] = str(365 * 24 * 60 * 60)
    try:
        with log_path.open("w", encoding="utf-8") as log:
            completed = subprocess.run(command, cwd=str(PROJECT_ROOT), env=env,
                                       stdout=log, stderr=subprocess.STDOUT, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "error": str(exc), "extended_output": str(extended_path)}
    if completed.returncode != 0 or not extended_path.is_file():
        return {"ok": False, "returncode": completed.returncode, "extended_output": str(extended_path)}
    try:
        from src.rapp_data_lake import DataLake
        payload = json.loads(extended_path.read_text(encoding="utf-8"))
        lake = DataLake(str(state_dir / "rapp_data_lake.db"))
        lake.record_extended_from_json(payload)
        return {"ok": True, "extended_output": str(extended_path), "standard_output": str(standard_path)}
    except (OSError, ValueError, TypeError, sqlite3.Error) as exc:
        return {"ok": False, "error": str(exc), "extended_output": str(extended_path)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Stop one GreenRAN run at a decision target")
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--db", default="")
    parser.add_argument("--target", type=int, required=True)
    parser.add_argument(
        "--metrics-target",
        type=int,
        default=0,
        help="After the decision target, wait for this many fresh real-PDCP rows before stopping the collector.",
    )
    parser.add_argument(
        "--metrics-min-target",
        type=int,
        default=0,
        help="Minimum acceptable fresh real-PDCP rows. Defaults to --metrics-target; useful for startup warm-up gaps.",
    )
    parser.add_argument(
        "--metrics-timeout-seconds",
        type=float,
        default=60.0,
        help="Maximum wait for the real-PDCP target after stopping the rApp.",
    )
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument(
        "--finalize-real-metrics",
        action="store_true",
        help="After stopping the decision loop, parse/import one final real-PDCP snapshot.",
    )
    args = parser.parse_args()
    state_dir = Path(args.state_dir).resolve()
    db_path = Path(args.db).resolve() if args.db else state_dir / "rapp_data_lake.db"
    target = max(1, args.target)
    while True:
        count = decision_count(db_path)
        if assistant_only_invalid(db_path):
            stopped = stop_run(state_dir)
            print({
                "state_dir": str(state_dir),
                "db": str(db_path),
                "target": target,
                "decisions": count,
                "assistant_only_invalid": True,
                "stopped": stopped,
            })
            return 2
        if count >= target:
            metrics_target = max(0, args.metrics_target)
            if metrics_target <= 0:
                stopped = stop_run(state_dir)
                finalization = finalize_real_metrics(state_dir) if args.finalize_real_metrics else None
                metrics = real_metric_count(db_path)
                print({
                    "state_dir": str(state_dir), "db": str(db_path),
                    "target": target, "decisions": count,
                    "real_pdcp_metrics": metrics,
                    "rapp_stopped": False, "stopped": stopped,
                    "finalization": finalization,
                })
                return 0

            metrics_min_target = max(0, args.metrics_min_target or metrics_target)
            metrics_min_target = min(metrics_min_target, metrics_target)
            rapp_stopped = stop_rapp_only(state_dir)
            finalization = None
            # Let the independent PDCP/CSV collectors flush their last
            # snapshots while ns-3 is still alive.  Previously ns-3 and CSV
            # were killed before this wait, which systematically left a
            # 2-3-row gap between decisions and real metrics in paired runs.
            deadline = time.monotonic() + max(1.0, args.metrics_timeout_seconds)
            metrics = real_metric_count(db_path)
            while metrics < metrics_target and time.monotonic() < deadline:
                if assistant_only_invalid(db_path):
                    stopped = stop_run(state_dir)
                    print({
                        "state_dir": str(state_dir), "db": str(db_path),
                        "target": target, "decisions": count,
                        "metrics_target": metrics_target, "real_pdcp_metrics": metrics,
                        "assistant_only_invalid": True, "rapp_stopped": rapp_stopped,
                        "stopped": stopped,
                    })
                    return 2
                time.sleep(max(0.1, args.poll_seconds))
                metrics = real_metric_count(db_path)

            if args.finalize_real_metrics:
                for pid_name in ("ns3.pid", "ns3_supervisor.pid", "csv_metrics.pid"):
                    stop_pid_file(state_dir, pid_name)
                finalization = finalize_real_metrics(state_dir)
                metrics = real_metric_count(db_path)
            if metrics < metrics_min_target:
                stopped = stop_run(state_dir)
                print({
                    "state_dir": str(state_dir), "db": str(db_path),
                    "target": target, "decisions": count,
                    "metrics_target": metrics_target, "metrics_min_target": metrics_min_target,
                    "real_pdcp_metrics": metrics,
                    "metrics_timeout": True, "rapp_stopped": rapp_stopped,
                    "finalization": finalization,
                    "stopped": stopped,
                })
                return 3
            stopped = stop_run(state_dir)
            print({
                "state_dir": str(state_dir), "db": str(db_path),
                "target": target, "decisions": count,
                "metrics_target": metrics_target, "metrics_min_target": metrics_min_target,
                "real_pdcp_metrics": metrics,
                "metrics_gap": max(0, metrics_target - metrics),
                "rapp_stopped": rapp_stopped, "stopped": stopped,
                "finalization": finalization,
            })
            return 0
        time.sleep(max(0.1, args.poll_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
