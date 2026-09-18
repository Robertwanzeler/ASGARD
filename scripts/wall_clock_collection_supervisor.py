#!/usr/bin/env python3
"""Stop one GreenRAN collection after a wall-clock deadline.

The supervisor is deliberately state-directory scoped.  It never uses broad
process-name kills, and it keeps the shared RIC alive when requested.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


PROCESS_SPECS = (
    ("rapp.pid", ("rapp_orchestrator.py",)),
    ("csv_metrics.pid", ("csv_to_metrics.py",)),
    ("xapp_slicer.pid", ("xapp_slicer",)),
    ("xapp_energy.pid", ("xapp_energy_saver",)),
    ("xapp_vehicle.pid", ("xapp_vehicle",)),
    # Native-E2 campaigns use this actuator instead of the legacy energy
    # socket.  It belongs to the state-directory-scoped process tree and must
    # be drained with the simulator to avoid blocking the next dispatcher job.
    ("xapp_tasam_actuator.pid", ("xapp_tasam_actuator",)),
    ("ns3.pid", ("ns3.42-Energy_saving_with_cell_utilization_scenario",)),
    ("ns3_supervisor.pid", ("start_ns3_supervisor.sh",)),
    ("db_snapshot.pid", ("snapshot_sqlite_db.py",)),
    ("rapp_online_retrain.pid", ("run_rapp_online_retrain.py",)),
    ("tasam_true_online_real.pid", ("run_tasam_true_online_real.py",)),
    ("tasam_article_export.pid", ("run_tasam_article_export.py",)),
    ("collection_event_alternator.pid", ("collection_event_alternator.py",)),
)


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def read_pid(path: Path) -> int | None:
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    return pid if pid > 1 else None


def command_line(pid: int) -> str:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return ""
    return raw.replace(b"\0", b" ").decode("utf-8", errors="replace")


def is_expected_process(pid: int, state_dir: Path, tokens: tuple[str, ...]) -> bool:
    command = command_line(pid)
    if not command:
        return False
    return str(state_dir) in command or any(token in command for token in tokens)


def write_status(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def terminate_process(pid: int, *, grace_seconds: float = 8.0) -> str:
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return "already_stopped"
    except PermissionError:
        return "permission_denied"

    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline:
        if not Path(f"/proc/{pid}").exists():
            return "terminated_gracefully"
        time.sleep(0.2)

    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return "terminated_gracefully"
    except PermissionError:
        return "kill_permission_denied"
    return "terminated_forcefully"


def stop_collection(state_dir: Path, *, keep_ric: bool) -> dict[str, str]:
    results: dict[str, str] = {}
    for pid_name, tokens in PROCESS_SPECS:
        pid_path = state_dir / pid_name
        pid = read_pid(pid_path)
        if pid is None or not is_expected_process(pid, state_dir, tokens):
            continue
        results[pid_name] = terminate_process(pid)
    if not keep_ric:
        ric_path = state_dir / "ric.pid"
        ric_pid = read_pid(ric_path)
        if ric_pid is not None and is_expected_process(ric_pid, state_dir, ("nearRT-RIC",)):
            results["ric.pid"] = terminate_process(ric_pid)
    return results


def final_export(state_dir: Path) -> int:
    # Controlled online campaigns already persist their canonical replay in
    # SQLite.  The article export is an optional historical artifact and can
    # exceed the campaign budget by gigabytes.  Honor the same switch used by
    # the live exporter at shutdown; previously this unconditional final call
    # bypassed GREENRAN_TASAM_EXPORT_ENABLED=0.
    raw_enabled = os.environ.get("GREENRAN_TASAM_EXPORT_ENABLED", "1").strip().lower()
    if raw_enabled not in {"1", "true", "yes", "on"}:
        return 0
    db_path = state_dir / "rapp_data_lake.db"
    export_dir = state_dir / "tasam_article_export"
    if not db_path.exists():
        return 0
    command = [
        sys.executable,
        str(Path(__file__).resolve().parent / "run_tasam_article_export.py"),
        "--db",
        str(db_path),
        "--output-dir",
        str(export_dir),
    ]
    completed = subprocess.run(command, cwd=Path(__file__).resolve().parents[1], check=False)
    return int(completed.returncode)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--duration-seconds", type=float, default=600.0)
    parser.add_argument("--post-stop-grace-seconds", type=float, default=3.0)
    parser.add_argument("--keep-ric", action="store_true")
    args = parser.parse_args()

    state_dir = args.state_dir.resolve()
    state_dir.mkdir(parents=True, exist_ok=True)
    status_path = state_dir / "wall_clock_status.json"
    started_monotonic = time.monotonic()
    started_at = now_iso()
    duration = max(1.0, float(args.duration_seconds))

    write_status(
        status_path,
        {
            "schema": "greenran.wall_clock_run.v1",
            "phase": "running",
            "started_at": started_at,
            "deadline_at": None,
            "requested_duration_s": duration,
            "elapsed_s": 0.0,
            "remaining_s": duration,
            "keep_ric": bool(args.keep_ric),
        },
    )

    deadline = started_monotonic + duration
    while True:
        # The decision-target watcher requests a cooperative shutdown through
        # the state directory.  This keeps ownership of worker cleanup in the
        # wall supervisor and avoids PID/process-group ambiguity across the
        # privileged service boundary.
        if (state_dir / "decision_target_stop.json").exists():
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        write_status(
            status_path,
            {
                "schema": "greenran.wall_clock_run.v1",
                "phase": "running",
                "started_at": started_at,
                "deadline_at": None,
                "requested_duration_s": duration,
                "elapsed_s": round(duration - remaining, 3),
                "remaining_s": round(remaining, 3),
                "keep_ric": bool(args.keep_ric),
            },
        )
        time.sleep(min(1.0, max(0.1, remaining)))

    shutdown_started = now_iso()
    results = stop_collection(state_dir, keep_ric=bool(args.keep_ric))
    if args.post_stop_grace_seconds > 0:
        time.sleep(float(args.post_stop_grace_seconds))
    export_enabled = os.environ.get("GREENRAN_TASAM_EXPORT_ENABLED", "1").strip().lower() in {
        "1", "true", "yes", "on"
    }
    export_code = final_export(state_dir)
    elapsed = time.monotonic() - started_monotonic
    write_status(
        status_path,
        {
            "schema": "greenran.wall_clock_run.v1",
            "phase": "finished",
            "started_at": started_at,
            "shutdown_started_at": shutdown_started,
            "finished_at": now_iso(),
            "requested_duration_s": duration,
            "elapsed_s": round(elapsed, 3),
            "remaining_s": 0.0,
            "keep_ric": bool(args.keep_ric),
            "stop_results": results,
            "final_export_code": export_code,
            "final_export": "enabled" if export_enabled else "disabled",
        },
    )
    return 0 if export_code == 0 else export_code


if __name__ == "__main__":
    raise SystemExit(main())
