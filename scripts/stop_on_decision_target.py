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
    # Stop the wall-clock owner first.  Otherwise it can keep the launcher
    # alive until the full wall budget even after the decision target is met.
    "wall_clock_supervisor.pid",
    "decision_target_supervisor.pid",
    "rapp.pid",
    "csv_metrics.pid",
    "xapp_slicer.pid",
    "xapp_energy.pid",
    "xapp_vehicle.pid",
    "xapp_tasam_actuator.pid",
    "ns3.pid",
    "ns3_supervisor.pid",
    "ric.pid",
    "db_snapshot.pid",
    "tasam_article_export.pid",
    "tasam_true_online_real.pid",
    "rapp_online_retrain.pid",
    "collection_event.pid",
    "collection_event_alternator.pid",
)


def read_pid(path: Path) -> int | None:
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
        return pid if pid > 1 else None
    except (OSError, ValueError):
        return None


def _protected_process_groups() -> set[int]:
    """Find process groups that must survive target cleanup.

    The target watcher runs beside ``run_tasam_online_arm.py``.  Depending on
    how systemd/bash created the background jobs, a worker PID can share the
    arm's process group.  Killing that group would prevent the arm from
    writing its final manifest.  Protect those groups and signal only the
    worker PID in that case.
    """
    protected: set[int] = set()
    for entry in Path("/proc").glob("[0-9]*"):
        try:
            raw = (entry / "cmdline").read_bytes()
            command = raw.replace(b"\0", b" ").decode("utf-8", errors="replace")
            if "run_tasam_online_arm.py" not in command and "run_tasam_local_causal_campaign.sh" not in command:
                continue
            protected.add(os.getpgid(int(entry.name)))
        except (OSError, ValueError, ProcessLookupError, PermissionError):
            continue
    return protected


def signal_scoped_process(pid: int, sig: int, *, use_group: bool = True) -> str:
    """Signal a state-scoped process and its setsid children when available."""
    try:
        pgid = os.getpgid(pid)
    except ProcessLookupError:
        return "already_stopped"
    # Collection services are started with setsid, so their process group is
    # the safest scope for stopping the supervisor plus its collector child.
    # Never signal our own process group as a broad fallback.
    if use_group and pgid > 1 and pgid != os.getpgrp() and pgid not in _protected_process_groups():
        try:
            os.killpg(pgid, sig)
            return "SIGTERM" if sig == signal.SIGTERM else "SIGKILL"
        except ProcessLookupError:
            return "already_stopped"
    try:
        os.kill(pid, sig)
        return "SIGTERM" if sig == signal.SIGTERM else "SIGKILL"
    except ProcessLookupError:
        return "already_stopped"
    except PermissionError:
        return "permission_denied"


def _pid_alive(pid: int) -> bool:
    """Return whether a PID still exists, without treating missing PIDs as errors."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # The privileged campaign normally owns these processes.  If it does
        # not, preserve the conservative answer and report the permission
        # problem to the caller instead of issuing an unscoped kill.
        return True


def _greenran_group_members(pgid: int) -> list[int]:
    """Return live members of a recorded GreenRAN process group only.

    A launcher can exit before a child such as ``csv_to_metrics.py`` or the
    snapshot sleep process.  The leader PID stored in the run directory is
    then gone, but its process group remains a precise ownership boundary.
    Restrict the recovery path to members still attached below ``/greenran/``;
    this never authorizes a signal to an unrelated process group.
    """
    members: list[int] = []
    for entry in Path("/proc").glob("[0-9]*"):
        try:
            candidate = int(entry.name)
            if os.getpgid(candidate) != pgid:
                continue
            cgroup = (entry / "cgroup").read_text(encoding="utf-8", errors="replace")
            if "/greenran/" in cgroup or cgroup.rstrip().endswith("/greenran"):
                members.append(candidate)
        except (OSError, ValueError, ProcessLookupError, PermissionError):
            continue
    return sorted(members)


def _stop_orphaned_greenran_group(pgid: int, *, grace_seconds: float) -> dict | None:
    """Drain a surviving recorded worker group after its leader has exited."""
    if pgid <= 1 or pgid == os.getpgrp() or pgid in _protected_process_groups():
        return None
    members = _greenran_group_members(pgid)
    if not members:
        return None
    result: dict = {"orphan_group": pgid, "orphan_members": members, "orphan_signal": "SIGTERM"}
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        result["orphan_exited"] = True
        return result
    deadline = time.monotonic() + max(0.0, float(grace_seconds))
    while _greenran_group_members(pgid) and time.monotonic() < deadline:
        time.sleep(0.1)
    if not _greenran_group_members(pgid):
        result["orphan_exited"] = True
        return result
    result["orphan_signal"] = "SIGKILL"
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    deadline = time.monotonic() + 5.0
    while _greenran_group_members(pgid) and time.monotonic() < deadline:
        time.sleep(0.1)
    result["orphan_exited"] = not bool(_greenran_group_members(pgid))
    return result


def _wait_pid_exit(pid: int, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + max(0.0, float(timeout))
    while _pid_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    return not _pid_alive(pid)


def _stop_pid(pid: int, name: str, *, grace_seconds: float = 10.0, use_group: bool = False) -> dict:
    """Stop one PID/process group and wait for its real exit.

    The operation is intentionally idempotent: stale PID files are reported
    as already stopped and never cause a broad process-name kill.
    """
    if not _pid_alive(pid):
        result = {"pid": pid, "pid_file": name, "signal": "already_stopped"}
        if use_group:
            orphan = _stop_orphaned_greenran_group(pid, grace_seconds=grace_seconds)
            if orphan is not None:
                result.update(orphan)
        return result
    result = {"pid": pid, "pid_file": name, "signal": "SIGTERM"}
    # The wall supervisor is started by the arm wrapper and can share the
    # wrapper's process group on some shells.  Signal only its PID so the arm
    # survives long enough to finalize arm_manifest.json; workers are still
    # stopped below through their own state-scoped process groups.
    result["term_result"] = signal_scoped_process(pid, signal.SIGTERM, use_group=use_group)
    if _wait_pid_exit(pid, grace_seconds):
        result["exited"] = True
        return result
    result["signal"] = "SIGKILL"
    result["kill_result"] = signal_scoped_process(pid, signal.SIGKILL, use_group=use_group)
    result["exited"] = _wait_pid_exit(pid, 5.0)
    return result


def _sqlite_decision_count(db_path: Path) -> int:
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0)
        row = conn.execute("SELECT COUNT(*) FROM decisions_history").fetchone()
        return int(row[0] or 0) if row else 0
    except (OSError, sqlite3.Error):
        return 0
    finally:
        if conn is not None:
            conn.close()


def _jsonl_decision_count(state_dir: Path) -> int:
    """Count completed rApp decisions while SQLite WAL is still draining.

    The rApp closes its decision loop before the DataLake writer necessarily
    checkpoints the final WAL pages.  The JSONL decision journal is written by
    that same loop and is therefore a safe progress signal for stopping the
    wall-clock owner.  SQLite remains the authoritative evidence source for
    the later gate/report.
    """
    path = state_dir / "rapp_decisions.jsonl"
    try:
        with path.open("r", encoding="utf-8") as handle:
            return sum(1 for line in handle if line.strip())
    except OSError:
        return 0


def decision_count(db_path: Path, state_dir: Path | None = None) -> int:
    sqlite_count = _sqlite_decision_count(db_path)
    journal_count = _jsonl_decision_count(state_dir) if state_dir is not None else 0
    return max(sqlite_count, journal_count)


def real_metric_count(db_path: Path) -> int:
    """Count only fresh, non-proxy PDCP metric rows."""
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0)
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
    finally:
        if conn is not None:
            conn.close()


def assistant_only_invalid(db_path: Path) -> bool:
    """Return true when the rApp stopped without applying a fallback policy."""
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0)
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
    finally:
        if conn is not None:
            conn.close()


def stop_run(state_dir: Path) -> list[dict]:
    results: list[dict] = []
    pids: dict[int, str] = {}
    for name in PID_NAMES:
        pid = read_pid(state_dir / name)
        if pid is not None and pid != os.getpid():
            pids.setdefault(pid, name)
    # The wall-clock supervisor is the parent lifetime owner.  Stop and wait
    # for it before the workers so run_tasam_online_arm.py can finalize its
    # manifest immediately after the target watcher returns.
    ordered = sorted(
        pids.items(),
        key=lambda item: (0 if item[1] == "wall_clock_supervisor.pid" else 1),
    )
    for pid, name in ordered:
        # PID files are the scope boundary.  Workers are launched in their own
        # sessions, so signalling that recorded process group also drains
        # their converter/snapshot children.  ``signal_scoped_process`` still
        # falls back to the individual PID if a worker shares the protected
        # arm-launcher group, which preserves final-manifest publication.
        results.append(
            _stop_pid(
                pid,
                name,
                use_group=name != "wall_clock_supervisor.pid",
            )
        )
    return results


def stop_wall_supervisor(state_dir: Path) -> list[dict]:
    """Request a cooperative wall-clock shutdown and wait for completion."""
    request_path = state_dir / "decision_target_stop.json"
    payload = {
        "schema": "greenran.decision_target_stop.v1",
        "reason": "decision_target_reached",
        "source": "stop_on_decision_target",
        "requested_at": time.time(),
    }
    temporary = request_path.with_suffix(request_path.suffix + ".tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
        temporary.replace(request_path)
        with (state_dir / "decision_target.log").open("a", encoding="utf-8") as log:
            log.write(json.dumps(payload, ensure_ascii=False) + "\n")
            log.flush()
    except OSError as exc:
        return [{"pid_file": "wall_clock_supervisor.pid", "signal": "request_failed", "error": str(exc)}]

    status_path = state_dir / "wall_clock_status.json"
    deadline = time.monotonic() + 60.0
    while time.monotonic() < deadline:
        try:
            status = json.loads(status_path.read_text(encoding="utf-8"))
            if status.get("phase") == "finished":
                result = [{"pid_file": "wall_clock_supervisor.pid", "signal": "cooperative_stop", "exited": True}]
                try:
                    with (state_dir / "decision_target.log").open("a", encoding="utf-8") as log:
                        log.write(json.dumps({
                            "source": "stop_on_decision_target",
                            "reason": "decision_target_reached",
                            "result": result,
                            "finished_at": time.time(),
                        }, ensure_ascii=False) + "\n")
                        log.flush()
                except OSError:
                    pass
                return result
        except (OSError, ValueError, TypeError):
            pass
        time.sleep(0.2)

    # Preserve a bounded fallback for a supervisor that cannot read the
    # shared stop request.  It remains PID-scoped and never uses a process
    # name or broad kill.
    pid = read_pid(state_dir / "wall_clock_supervisor.pid")
    if pid is None or pid == os.getpid():
        return [{"pid_file": "wall_clock_supervisor.pid", "signal": "request_timeout", "exited": False}]
    result = [_stop_pid(pid, "wall_clock_supervisor.pid", use_group=False)]
    try:
        with (state_dir / "decision_target.log").open("a", encoding="utf-8") as log:
            log.write(json.dumps({
                "source": "stop_on_decision_target",
                "reason": "decision_target_reached",
                "result": result,
                "finished_at": time.time(),
            }, ensure_ascii=False) + "\n")
            log.flush()
    except OSError:
        pass
    return result


def stop_rapp_only(state_dir: Path) -> list[dict]:
    """Stop only the rApp so the PDCP collector can finish its metric window."""
    pid = read_pid(state_dir / "rapp.pid")
    if pid is None or pid == os.getpid():
        return []
    result = {"pid": pid, "pid_file": "rapp.pid", "signal": "SIGTERM"}
    try:
        result["term_result"] = signal_scoped_process(pid, signal.SIGTERM, use_group=False)
    except ProcessLookupError:
        result["signal"] = "already_stopped"
    except PermissionError:
        result["signal"] = "permission_denied"
    time.sleep(1.0)
    try:
        if _pid_alive(pid):
            result["signal"] = "SIGKILL"
            result["kill_result"] = signal_scoped_process(pid, signal.SIGKILL, use_group=False)
            result["exited"] = _wait_pid_exit(pid, 5.0)
        else:
            result["exited"] = True
    except (ProcessLookupError, PermissionError):
        result["exited"] = not _pid_alive(pid)
    return [result]


def stop_pid_file(state_dir: Path, name: str) -> dict | None:
    pid = read_pid(state_dir / name)
    if pid is None or pid == os.getpid():
        return None
    result = {"pid": pid, "pid_file": name, "signal": "SIGTERM"}
    try:
        result["term_result"] = signal_scoped_process(pid, signal.SIGTERM, use_group=False)
    except ProcessLookupError:
        result["signal"] = "already_stopped"
        return result
    except PermissionError:
        result["signal"] = "permission_denied"
        return result
    time.sleep(1.0)
    try:
        if _pid_alive(pid):
            signal_scoped_process(pid, signal.SIGKILL, use_group=False)
            result["signal"] = "SIGKILL"
            result["exited"] = _wait_pid_exit(pid, 5.0)
        else:
            result["exited"] = True
    except (ProcessLookupError, PermissionError):
        result["exited"] = not _pid_alive(pid)
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
    parser.add_argument(
        "--wall-only",
        action="store_true",
        help="Stop only the wall-clock supervisor after the target; leave arm finalization to the caller.",
    )
    parser.add_argument(
        "--cleanup-only",
        action="store_true",
        help="Clean up a completed run immediately without waiting for a decision target.",
    )
    args = parser.parse_args()
    state_dir = Path(args.state_dir).resolve()
    db_path = Path(args.db).resolve() if args.db else state_dir / "rapp_data_lake.db"
    target = max(1, args.target)
    if args.cleanup_only:
        count = decision_count(db_path, state_dir)
        stopped = stop_run(state_dir)
        finalization = finalize_real_metrics(state_dir) if args.finalize_real_metrics else None
        print({
            "state_dir": str(state_dir), "db": str(db_path),
            "target": target, "decisions": count,
            "cleanup_only": True, "stopped": stopped,
            "finalization": finalization,
        }, flush=True)
        return 0 if count >= target else 3
    while True:
        count = decision_count(db_path, state_dir)
        if assistant_only_invalid(db_path):
            stopped = stop_run(state_dir)
            print({
                "state_dir": str(state_dir),
                "db": str(db_path),
                "target": target,
                "decisions": count,
                "assistant_only_invalid": True,
                "stopped": stopped,
            }, flush=True)
            return 2
        if count >= target:
            metrics_target = max(0, args.metrics_target)
            if metrics_target <= 0:
                stopped = stop_wall_supervisor(state_dir) if args.wall_only else stop_run(state_dir)
                finalization = finalize_real_metrics(state_dir) if args.finalize_real_metrics else None
                metrics = real_metric_count(db_path)
                print({
                    "state_dir": str(state_dir), "db": str(db_path),
                    "target": target, "decisions": count,
                    "real_pdcp_metrics": metrics,
                    "rapp_stopped": False, "wall_only": bool(args.wall_only), "stopped": stopped,
                    "finalization": finalization,
                }, flush=True)
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
                    }, flush=True)
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
                }, flush=True)
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
            }, flush=True)
            return 0
        time.sleep(max(0.1, args.poll_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
