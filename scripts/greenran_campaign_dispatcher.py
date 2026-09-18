#!/usr/bin/env python3
"""Consume one validated local GreenRAN job at a time."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from greenran_campaign_jobs import (  # noqa: E402
    JobValidationError,
    atomic_json_write,
    normalize_job,
    prepare_queue,
    queue_layout,
    runtime_environment,
)
from greenran_infra_budget import (  # noqa: E402
    GROUPS,
    InfraBudgetError,
    assert_cgroup_delegation,
    cgroup_delegation_status,
    probe_cgroup_attach,
    resolve_cgroup_root,
)


DEFAULT_QUEUE = ROOT / "runs" / "agent_jobs"


def _host_cgroup_probe() -> dict[str, Any]:
    """Run the attach proof in the dispatcher host context.

    Codex may see a read-only cgroup mount in its sandbox.  The user service is
    the authority that actually launches campaigns, so its probe is the only
    result that can approve a job.
    """
    checked_at = int(time.time())
    delegation = cgroup_delegation_status()
    result: dict[str, Any] = {
        "schema": "greenran.cgroup_host_probe.v1",
        "checked_at": checked_at,
        "delegation": delegation,
        "attach_probe": None,
        "valid": False,
    }
    if not delegation.get("valid"):
        result["reason"] = "delegation_invalid"
        return result
    try:
        attach_probe = probe_cgroup_attach()
    except (InfraBudgetError, OSError, RuntimeError) as exc:
        result["reason"] = f"attach_probe_error:{exc}"
        return result
    result["attach_probe"] = attach_probe
    result["valid"] = bool(attach_probe.get("valid"))
    if not result["valid"]:
        result["reason"] = str(attach_probe.get("reason") or "child_attach_failed")
    return result


def _write_host_cgroup_probe(layout: dict[str, Path], job_id: str, probe: dict[str, Any]) -> None:
    atomic_json_write(layout["status"] / f"{job_id}.cgroup_host_probe.json", probe)


def _active_cgroup_pids() -> dict[str, list[int]]:
    root = resolve_cgroup_root()
    active: dict[str, list[int]] = {}
    for group in GROUPS:
        try:
            values = [int(value) for value in (root / group / "cgroup.procs").read_text(encoding="ascii").split()]
        except (OSError, ValueError):
            values = []
        if values:
            active[group] = values
    return active


def _job_process_groups(campaign_dir: Path, process_group: int) -> set[int]:
    """Collect process groups recorded by the just-finished campaign only."""
    groups: set[int] = set()
    if int(process_group) > 0:
        groups.add(int(process_group))
    # The outer online-economic campaign owns one or more nested arms. Their
    # PID files remain the exact ownership boundary, but they are below the
    # campaign root rather than next to it. Search only inside this campaign;
    # historical campaigns and every other queued job remain out of scope.
    for pid_file in campaign_dir.rglob("*.pid"):
        try:
            value = int(pid_file.read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            continue
        if value > 0:
            # Launchers intentionally use start_new_session for some helpers.
            # Their leader PID is therefore also the process-group ID.
            groups.add(value)
    return groups


def _pids_in_process_groups(pids: dict[str, list[int]], process_groups: set[int]) -> dict[str, list[int]]:
    """Return only live cgroup PIDs that still belong to this dispatcher's job.

    Each command is launched in a new session, so its PID is also its process
    group.  This lets the dispatcher distinguish a child accidentally left by
    the launcher from a process that predates the job.  The latter is never
    signalled automatically.
    """
    owned: dict[str, list[int]] = {}
    for group, values in pids.items():
        matches: list[int] = []
        for pid in values:
            try:
                if os.getpgid(pid) in process_groups:
                    matches.append(pid)
            except ProcessLookupError:
                continue
            except OSError:
                continue
        if matches:
            owned[group] = matches
    return owned


def _flatten_pids(pids: dict[str, list[int]]) -> list[int]:
    return sorted({pid for values in pids.values() for pid in values})


def _signal_known_job_pids(pids: dict[str, list[int]], signum: int) -> list[int]:
    """Signal only verified members of the finished job's process group."""
    signalled: list[int] = []
    for pid in _flatten_pids(pids):
        try:
            os.kill(pid, signum)
            signalled.append(pid)
        except ProcessLookupError:
            continue
        except PermissionError:
            # A process outside the dispatcher account cannot be a valid child
            # of a non-privileged job.  Leave it untouched and report it.
            continue
    return signalled


def _cleanup_finished_job(
    campaign_dir: Path, process_group: int, *, grace_seconds: float = 10.0
) -> dict[str, Any]:
    """Drain only child processes proven to belong to a completed job.

    Launchers occasionally leave a collector's worker alive after their parent
    exits.  This is a normal shutdown defect, not authority to kill arbitrary
    processes in the shared cgroup.  Membership in the unique Popen process
    group is the ownership proof used here.
    """
    process_groups = _job_process_groups(campaign_dir, process_group)
    initial = _active_cgroup_pids()
    owned_before = _pids_in_process_groups(initial, process_groups)
    terminated = _signal_known_job_pids(owned_before, signal.SIGTERM)
    deadline = time.monotonic() + max(0.0, grace_seconds)
    active = initial
    owned = owned_before
    while owned and time.monotonic() < deadline:
        time.sleep(0.2)
        active = _active_cgroup_pids()
        owned = _pids_in_process_groups(active, process_groups)
    killed: list[int] = []
    if owned:
        killed = _signal_known_job_pids(owned, signal.SIGKILL)
        # Give the kernel a short, bounded interval to reap cgroup membership.
        for _ in range(10):
            time.sleep(0.1)
            active = _active_cgroup_pids()
            owned = _pids_in_process_groups(active, process_groups)
            if not owned:
                break
    final_active = _active_cgroup_pids()
    return {
        "process_group": process_group,
        "verified_process_groups": sorted(process_groups),
        "initial_active_cgroup_pids": initial,
        "verified_job_pids": owned_before,
        "terminated_pids": terminated,
        "killed_pids": killed,
        "remaining_verified_job_pids": _pids_in_process_groups(final_active, process_groups),
        "remaining_active_cgroup_pids": final_active,
    }


def _status(layout: dict[str, Path], job_id: str, payload: dict[str, Any]) -> None:
    payload.setdefault("dispatcher_source_sha256", _sha256(Path(__file__)))
    jobs_source = ROOT / "src" / "greenran_campaign_jobs.py"
    if jobs_source.is_file():
        payload.setdefault("campaign_jobs_source_sha256", _sha256(jobs_source))
    atomic_json_write(layout["status"] / f"{job_id}.json", payload)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _claim(layout: dict[str, Path]) -> Path | None:
    for source in sorted(layout["queued"].glob("*.json")):
        destination = layout["running"] / source.name
        try:
            os.replace(source, destination)
            return destination
        except FileNotFoundError:
            continue
    return None


def _recovery_campaign_path(raw: dict[str, Any]) -> Path:
    """Resolve a previously validated job's campaign path without rerunning it."""
    value = raw.get("campaign_dir")
    if not isinstance(value, str) or not value:
        raise JobValidationError("job em execução não tem campaign_dir recuperável")
    candidate = Path(value).resolve()
    try:
        candidate.relative_to(ROOT / "runs")
    except ValueError as exc:
        raise JobValidationError("campaign_dir de recuperação fora de runs/") from exc
    if candidate == DEFAULT_QUEUE or DEFAULT_QUEUE in candidate.parents:
        raise JobValidationError("campaign_dir de recuperação não pode ser a fila")
    return candidate


def _recover_interrupted_jobs(layout: dict[str, Path]) -> None:
    """Close jobs left in ``running`` after dispatcher restart/reboot.

    A dispatcher restart ends its direct child, but launcher helpers may have
    started independent sessions.  PID files in that specific campaign are the
    scope boundary for cleanup.  Jobs are recorded as failed rather than
    silently promoted: their supervising process did not finish normally.
    """
    for claimed in sorted(layout["running"].glob("*.json")):
        started_at = int(time.time())
        job_id = claimed.stem
        try:
            raw = json.loads(claimed.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise JobValidationError("job em execução não é um objeto JSON")
            supplied_id = raw.get("job_id")
            if isinstance(supplied_id, str) and supplied_id:
                job_id = supplied_id
            if claimed.name != f"{job_id}.json":
                raise JobValidationError("nome de job em execução não corresponde ao job_id")
            campaign_dir = _recovery_campaign_path(raw)
            cleanup = _cleanup_finished_job(campaign_dir, 0)
            result = {
                "schema": "greenran.agent_campaign_status.v1",
                "job_id": job_id,
                "kind": raw.get("kind"),
                "state": "failed",
                "started_at": started_at,
                "finished_at": int(time.time()),
                "campaign_dir": str(campaign_dir),
                "reason": "dispatcher_restarted_or_crashed_before_job_reaped",
                "post_exit_cleanup": cleanup,
            }
        except (JobValidationError, OSError, json.JSONDecodeError) as exc:
            result = {
                "schema": "greenran.agent_campaign_status.v1",
                "job_id": job_id,
                "state": "failed",
                "started_at": started_at,
                "finished_at": int(time.time()),
                "reason": f"recovery_failed:{exc}",
            }
        _status(layout, job_id, result)
        claimed.replace(layout["failed"] / claimed.name)


def run_one(queue_root: Path) -> bool:
    layout = prepare_queue(queue_root)
    claimed = _claim(layout)
    if claimed is None:
        return False
    started_at = int(time.time())
    raw: dict[str, Any] = {}
    job_id = claimed.stem
    host_cgroup_probe: dict[str, Any] | None = None
    try:
        raw = json.loads(claimed.read_text(encoding="utf-8"))
        spec = normalize_job(raw)
        job_id = spec.job_id
        host_cgroup_probe = _host_cgroup_probe()
        _write_host_cgroup_probe(layout, job_id, host_cgroup_probe)
        if not host_cgroup_probe.get("valid"):
            raise InfraBudgetError(
                "pré-voo cgroup do host falhou: "
                + str(host_cgroup_probe.get("reason") or "delegação/attach inválido")
            )
        if claimed.name != f"{job_id}.json":
            raise JobValidationError("nome do arquivo não corresponde ao job_id")
        assert_cgroup_delegation()
        active = _active_cgroup_pids()
        if active:
            raise JobValidationError(f"cgroup GreenRAN ainda possui processos ativos: {active}")
        _status(layout, job_id, {
            "schema": "greenran.agent_campaign_status.v1", "job_id": job_id,
            "kind": spec.kind, "state": "running", "started_at": started_at,
            "command": spec.command, "campaign_dir": str(spec.campaign_path),
            "cgroup_host_probe": host_cgroup_probe,
        })
        log_path = layout["logs"] / f"{job_id}.log"
        with log_path.open("a", encoding="utf-8") as log:
            log.write("$ " + " ".join(spec.command) + "\n")
            process = subprocess.Popen(
                spec.command, cwd=ROOT, env=runtime_environment(), stdout=log,
                stderr=subprocess.STDOUT, start_new_session=True,
            )
            exit_code = process.wait()
        cleanup = _cleanup_finished_job(spec.campaign_path, process.pid)
        active_after = cleanup["remaining_active_cgroup_pids"]
        campaign_manifest = {}
        manifest_path = spec.campaign_path / "campaign_manifest.json"
        if manifest_path.is_file():
            try:
                campaign_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                campaign_manifest = {}
        experiment_status = str(campaign_manifest.get("status") or "")
        baseline_infeasible = (
            spec.kind == "vehicle_feasibility"
            and exit_code == 3
            and experiment_status == "baseline_infeasible"
            and not active_after
        )
        blocked_after_valid_run = (
            experiment_status == "adaptation_blocked"
            and not active_after
            and bool(campaign_manifest.get("observation_report") or campaign_manifest.get("block_reason"))
        )
        success = (exit_code == 0 and not active_after) or blocked_after_valid_run or baseline_infeasible
        result = {
            "schema": "greenran.agent_campaign_status.v1", "job_id": job_id,
            "kind": spec.kind, "state": "finished" if success else "failed",
            "execution_state": "finished" if not active_after else "failed_cleanup",
            "experiment_status": experiment_status or ("finished" if success else "failed"),
            "result_reason": (
                "baseline_infeasible" if baseline_infeasible else
                campaign_manifest.get("block_reason", "") if blocked_after_valid_run else ""
            ),
            "started_at": started_at, "finished_at": int(time.time()), "exit_code": exit_code,
            "campaign_dir": str(spec.campaign_path), "command": spec.command,
            "log": str(log_path), "active_cgroup_pids": active_after,
            "post_exit_cleanup": cleanup,
            "cgroup_host_probe": host_cgroup_probe,
        }
        _status(layout, job_id, result)
        claimed.replace((layout["finished"] if success else layout["failed"]) / claimed.name)
    except (JobValidationError, InfraBudgetError, OSError, json.JSONDecodeError) as exc:
        result = {
            "schema": "greenran.agent_campaign_status.v1", "job_id": job_id,
            "state": "rejected", "started_at": started_at, "finished_at": int(time.time()),
            "reason": str(exc),
            "cgroup_host_probe": host_cgroup_probe,
        }
        _status(layout, job_id, result)
        claimed.replace(layout["failed"] / claimed.name)
    return True


def serve(queue_root: Path, poll_seconds: float) -> int:
    layout = prepare_queue(queue_root)
    lock_path = queue_root / "dispatcher.lock"
    with lock_path.open("a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("dispatcher já está ativo", file=sys.stderr)
            return 2
        try:
            assert_cgroup_delegation()
            _recover_interrupted_jobs(layout)
        except InfraBudgetError:
            # Keep stale jobs untouched until the cgroup contract is usable;
            # otherwise recovery could falsely claim a clean shutdown.
            pass
        while True:
            handled = run_one(queue_root)
            if not handled:
                time.sleep(poll_seconds)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue-root", type=Path, default=DEFAULT_QUEUE)
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.poll_seconds <= 0:
        parser.error("poll-seconds precisa ser positivo")
    if args.once:
        run_one(args.queue_root.resolve())
        return 0
    return serve(args.queue_root.resolve(), args.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
