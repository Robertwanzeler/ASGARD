#!/usr/bin/env python3
"""Safely terminate only GreenRAN processes owned by explicit state dirs.

This tool is intentionally narrower than ``pkill``: every PID must come from a
PID file below one of the supplied state directories or from a delegated
GreenRAN ``cgroup.procs`` file, expose the same ``GREENRAN_STATE_DIR`` in
``/proc/<pid>/environ``, and belong to that delegated cgroup.  The dispatcher
is always refused.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import shutil
import socket
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DISPATCHER_MARKER = "greenran_campaign_dispatcher.py"
DEFAULT_PORTS = (36421, 36422, 36431, 36432, 38470, 38570)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_proc(pid: int, name: str) -> bytes:
    try:
        return (Path("/proc") / str(pid) / name).read_bytes()
    except OSError:
        return b""


def _cmdline(pid: int) -> str:
    return _read_proc(pid, "cmdline").replace(b"\0", b" ").decode(errors="replace").strip()


def _env(pid: int) -> dict[str, str]:
    raw = _read_proc(pid, "environ")
    result: dict[str, str] = {}
    for item in raw.split(b"\0"):
        if b"=" not in item:
            continue
        key, value = item.split(b"=", 1)
        result[key.decode(errors="replace")] = value.decode(errors="replace")
    return result


def _cgroup(pid: int) -> str:
    return _read_proc(pid, "cgroup").decode(errors="replace")


def _is_greenran_cgroup(cgroup: str) -> bool:
    """Accept the static delegated tree and versioned systemd user scopes."""
    return "/greenran/" in cgroup or (
        "/greenran-" in cgroup and ".scope" in cgroup
    )


def _pid_files(state_dir: Path) -> list[Path]:
    return sorted(p for p in state_dir.rglob("*.pid") if p.is_file())


def _pid_from_file(path: Path) -> int | None:
    try:
        value = int(path.read_text(encoding="ascii").strip().splitlines()[0])
    except (OSError, ValueError, IndexError):
        return None
    return value if value > 1 else None


def _delegated_cgroup_pids() -> list[tuple[int, Path]]:
    """Return live PIDs from GreenRAN delegated groups, including child workers.

    Services such as ``csv_to_metrics.py`` may be spawned without their own
    PID-file.  Enumerating only PID-files leaves those children behind and
    prevents the cgroup delegation service from safely reapplying ownership.
    """
    roots = (Path("/sys/fs/cgroup"),)
    result: list[tuple[int, Path]] = []
    seen: set[tuple[int, Path]] = set()
    for root in roots:
        for pattern in ("greenran/*/cgroup.procs", "greenran-*.scope/cgroup.procs"):
            try:
                proc_files = root.rglob(pattern)
            except OSError:
                continue
            for proc_file in proc_files:
                try:
                    pids = proc_file.read_text(encoding="ascii").split()
                except OSError:
                    continue
                for raw in pids:
                    try:
                        pid = int(raw)
                    except ValueError:
                        continue
                    key = (pid, proc_file.parent)
                    if pid > 1 and key not in seen:
                        seen.add(key)
                        result.append(key)
    return result


def validate_pid(pid: int, state_dir: Path) -> tuple[bool, str]:
    if pid <= 1 or not (Path("/proc") / str(pid)).exists():
        return False, "not_alive"
    command = _cmdline(pid)
    if DISPATCHER_MARKER in command:
        return False, "dispatcher_protected"
    actual = Path(_env(pid).get("GREENRAN_STATE_DIR", "")).resolve()
    if actual != state_dir.resolve():
        return False, f"state_dir_mismatch:{actual}"
    if not _is_greenran_cgroup(_cgroup(pid)):
        return False, "greenran_cgroup_missing"
    return True, "validated"


def _port_busy(port: int) -> bool:
    # A local connect catches listeners without relying on an external tool.
    for host in ("127.0.0.1", "::1"):
        family = socket.AF_INET6 if ":" in host else socket.AF_INET
        sock = socket.socket(family, socket.SOCK_STREAM)
        sock.settimeout(0.05)
        try:
            if sock.connect_ex((host, port)) == 0:
                return True
        except OSError:
            pass
        finally:
            sock.close()
    return False


def reconcile_wall_status(state_dir: Path, reason: str) -> list[str]:
    changed: list[str] = []
    for path in sorted(state_dir.rglob("wall_clock_status.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict) or payload.get("phase") != "running":
            continue
        payload.update({
            "phase": "cancelled",
            "status": "cancelled",
            "cancelled_at": _now(),
            "cancel_reason": reason,
            "reconciled_by": "cleanup_greenran_orphaned_run.py",
            "traces_edited": False,
        })
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        temporary.replace(path)
        changed.append(str(path))
    return changed


def run(args: argparse.Namespace) -> int:
    state_dirs = [p.resolve() for p in args.state_dir]
    if not state_dirs:
        raise SystemExit("informe ao menos um --state-dir")
    if any(not p.is_dir() for p in state_dirs):
        missing = [str(p) for p in state_dirs if not p.is_dir()]
        raise SystemExit("state-dir inexistente: " + ", ".join(missing))

    entries: list[dict[str, Any]] = []
    validated: dict[int, Path] = {}
    known_pids: set[int] = set()
    for state_dir in state_dirs:
        for pid_file in _pid_files(state_dir):
            pid = _pid_from_file(pid_file)
            if pid is None:
                entries.append({"pid_file": str(pid_file), "status": "invalid_pid_file"})
                continue
            ok, reason = validate_pid(pid, state_dir)
            item = {"pid": pid, "pid_file": str(pid_file), "state_dir": str(state_dir), "validation": reason}
            if ok:
                validated[pid] = state_dir
                known_pids.add(pid)
                item["status"] = "validated"
            else:
                item["status"] = "protected_or_gone"
            entries.append(item)

    # Include child workers that have no PID-file but are still attached to a
    # delegated GreenRAN cgroup.  The exact state-dir check remains mandatory.
    for pid, cgroup_dir in _delegated_cgroup_pids():
        if pid in known_pids:
            continue
        env_state = Path(_env(pid).get("GREENRAN_STATE_DIR", ""))
        for state_dir in state_dirs:
            if env_state.resolve() != state_dir.resolve():
                continue
            ok, reason = validate_pid(pid, state_dir)
            item = {
                "pid": pid,
                "pid_file": None,
                "source": "delegated_cgroup_procs",
                "cgroup": str(cgroup_dir),
                "state_dir": str(state_dir),
                "validation": reason,
                "status": "validated" if ok else "protected_or_gone",
            }
            entries.append(item)
            if ok:
                validated[pid] = state_dir
                known_pids.add(pid)
            break

    action = "dry_run"
    if args.execute:
        action = "term_then_validated_kill"
        for pid in sorted(validated):
            try:
                os.kill(pid, signal.SIGTERM)
                for item in entries:
                    if item.get("pid") == pid:
                        item["signal"] = "SIGTERM"
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            live = [pid for pid, state in validated.items() if validate_pid(pid, state)[0]]
            if not live:
                break
            time.sleep(0.25)
        for pid, state_dir in sorted(validated.items()):
            if not validate_pid(pid, state_dir)[0]:
                continue
            try:
                os.kill(pid, signal.SIGKILL)
                for item in entries:
                    if item.get("pid") == pid:
                        item["signal"] = "SIGKILL"
            except ProcessLookupError:
                pass

    reconciled = []
    if args.execute:
        for state_dir in state_dirs:
            reconciled.extend(reconcile_wall_status(state_dir, args.reason))

    ports = {str(port): _port_busy(port) for port in args.port}
    free_gib = shutil.disk_usage(PROJECT_ROOT).free / (1024 ** 3)
    audit_dir = args.audit_dir.resolve()
    audit_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema": "greenran.cleanup_manifest.v2",
        "created_at": _now(),
        "action": action,
        "reason": args.reason,
        "state_dirs": [str(p) for p in state_dirs],
        "entries": entries,
        "reconciled_wall_clock_status": reconciled,
        "dispatcher_preserved": True,
        "ports": ports,
        "free_gib": round(free_gib, 3),
        "minimum_free_gib": 20.0,
        "ready_for_pilot": not any(ports.values()) and free_gib >= 20.0 and not any(
            validate_pid(pid, state)[0] for pid, state in validated.items()
        ),
        "traces_edited": False,
    }
    (audit_dir / "cleanup_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, indent=2, ensure_ascii=False))
    return 0 if manifest["ready_for_pilot"] or not args.execute else 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", action="append", type=Path, required=True)
    parser.add_argument("--audit-dir", type=Path, required=True)
    parser.add_argument("--reason", default="pilot_window90_preparation")
    parser.add_argument("--port", action="append", type=int, default=list(DEFAULT_PORTS))
    parser.add_argument("--execute", action="store_true", help="envia sinais somente a PIDs validados")
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
