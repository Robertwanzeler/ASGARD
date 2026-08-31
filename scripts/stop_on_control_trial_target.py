#!/usr/bin/env python3
"""Stop one GreenRAN state directory after its control-trial target is met.

This is intentionally scoped to PID files inside the supplied state directory;
it never uses broad process-name matching or stops other GreenRAN runs.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import time
from pathlib import Path


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
    "wall_clock_supervisor.pid",
)


def read_state(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def read_pid(path: Path) -> int | None:
    try:
        value = int(path.read_text(encoding="utf-8").strip())
        return value if value > 1 else None
    except (OSError, ValueError):
        return None


def stop_run(state_dir: Path) -> list[dict]:
    results = []
    pids: dict[int, str] = {}
    for name in PID_NAMES:
        path = state_dir / name
        pid = read_pid(path)
        if pid is not None and pid != os.getpid():
            pids.setdefault(pid, name)
    for pid, name in pids.items():
        try:
            os.kill(pid, signal.SIGTERM)
            results.append({"pid": pid, "pid_file": name, "signal": "SIGTERM"})
        except ProcessLookupError:
            results.append({"pid": pid, "pid_file": name, "signal": "already_stopped"})
        except PermissionError:
            results.append({"pid": pid, "pid_file": name, "signal": "permission_denied"})
    time.sleep(1.0)
    for pid, name in pids.items():
        try:
            os.kill(pid, 0)
            os.kill(pid, signal.SIGKILL)
            results.append({"pid": pid, "pid_file": name, "signal": "SIGKILL"})
        except (ProcessLookupError, PermissionError):
            pass
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Stop one GreenRAN run at its control-trial target")
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--target", type=int, required=True)
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    args = parser.parse_args()
    state_dir = Path(args.state_dir).resolve()
    state_path = state_dir / "control_trial_state.json"
    while True:
        state = read_state(state_path)
        eligible = int(state.get("eligible_decisions", 0) or 0)
        if bool(state.get("rollback", False)) or eligible >= max(1, args.target):
            results = stop_run(state_dir)
            print(json.dumps({
                "state_dir": str(state_dir),
                "target": args.target,
                "eligible_decisions": eligible,
                "rollback": bool(state.get("rollback", False)),
                "stopped": results,
            }, ensure_ascii=False))
            return 0
        time.sleep(max(0.1, args.poll_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
