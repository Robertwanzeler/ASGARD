#!/usr/bin/env python3
"""Queue ASGARD v10 only after the protected V2X baseline passes.

This is a local, fail-closed bridge for the autonomous dispatcher.  It never
starts a campaign directly: it writes one validated ``online_economic`` job
only after ``selected_vehicle_profile.json`` is complete and passed.  A
baseline failure produces a durable diagnostic and no training job.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VEHICLE_ROOT = ROOT / "runs/tasam_vehicle_feasibility_seed47_20260919_gbr_priority_matrix_r1"
DEFAULT_QUEUE_ROOT = ROOT / "runs/agent_jobs"
DEFAULT_CAMPAIGN = ROOT / "runs/tasam_asgard_adaptation_seed47_20260919_v10_gbr_priority"
DEFAULT_CHECKPOINT = ROOT / "runs/tasam_economic_checkpoint_v10_seed47_20260917"
DEFAULT_CALIBRATION = ROOT / "config/energy_calibration_sim_v3_sleep.json"
JOB_ID = "tasam-asgard-online-economic-seed47-20260919-v10-gbr-priority"
VEHICLE_PROFILE = "tasam_training_balanced_v4_v2x_gbr_priority"


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def _job_exists(queue_root: Path) -> bool:
    return any((queue_root / part / f"{JOB_ID}.json").exists() for part in ("queued", "running", "finished", "failed"))


def _blocked(vehicle_root: Path, reason: str) -> int:
    _write_atomic(
        vehicle_root / "asgard_v10_autochain_blocked.json",
        {
            "schema": "greenran.asgard.v10.autochain.v1",
            "status": "blocked",
            "reason": reason,
            "vehicle_feasibility_root": str(vehicle_root),
            "created_at": int(time.time()),
        },
    )
    return 3


def _strict_matrix_baseline_reason(manifest: dict[str, Any], profile: str) -> str | None:
    """Return a fail-closed reason unless the current V2X matrix is promotable."""
    if manifest.get("schema") != "greenran.autonomous_vehicle_feasibility.v4":
        return "baseline_matrix_manifest_v4_required"
    if manifest.get("status") != "passed":
        return f"baseline_infeasible:{manifest.get('status') or 'invalid_manifest'}"
    if manifest.get("scientific_decision") != "approved" or manifest.get("promotion_eligible") is not True:
        return "baseline_not_scientifically_approved"
    if manifest.get("profile") != profile:
        return "vehicle_profile_mismatch"
    provenance = manifest.get("provenance") or {}
    metric_contract = provenance.get("metric_contract") or {}
    if (
        manifest.get("metric_contract") != "per_pdu_cohort_v1"
        or metric_contract.get("collector_mode") != "pdcp_real"
        or metric_contract.get("pdcp_source") != "native_pdcp_pdu_tx_rx"
        or metric_contract.get("proxy_allowed") is not False
    ):
        return "baseline_real_only_contract_invalid"
    matrix = manifest.get("multi_seed_validation") or {}
    if (
        matrix.get("valid") is not True
        or tuple(matrix.get("required_seeds") or []) != (45, 46, 47)
        or tuple(matrix.get("complete_seeds") or []) != (45, 46, 47)
        or matrix.get("seed47_reused_from_phase1") is not True
        or matrix.get("provenance_compatible") is not True
    ):
        return "baseline_multi_seed_validation_invalid"
    if int(manifest.get("selected_interval_us") or 0) not in {4000, 6000, 8000, 12000, 16000}:
        return "selected_interval_invalid"
    topology = manifest.get("topology") or {}
    if int(topology.get("ue_count", 0)) != 20 or int(topology.get("du_count", 0)) != 3:
        return "vehicle_topology_invalid"
    return None


def run(args: argparse.Namespace) -> int:
    vehicle_root = args.vehicle_root.resolve()
    queue_root = args.queue_root.resolve()
    manifest_path = vehicle_root / "selected_vehicle_profile.json"
    status_path = queue_root / "status" / f"{args.vehicle_job_id}.json" if args.vehicle_job_id else None
    while True:
        if _job_exists(queue_root):
            return 0
        if manifest_path.is_file():
            manifest = _read(manifest_path)
            reason = _strict_matrix_baseline_reason(manifest, args.vehicle_profile)
            if reason is not None:
                return _blocked(vehicle_root, reason)
            campaign = args.campaign_dir.resolve()
            if campaign.exists() and any(campaign.iterdir()):
                return _blocked(vehicle_root, f"adaptation_campaign_not_empty:{campaign}")
            payload = {
                "schema": "greenran.agent_campaign_job.v1",
                "job_id": JOB_ID,
                "kind": "online_economic",
                "campaign_dir": str(campaign),
                "checkpoint": str(args.checkpoint.resolve()),
                "calibration": str(args.calibration.resolve()),
                "vehicle_profile_manifest": str(manifest_path),
                "profile": args.vehicle_profile,
                "seed": 47,
                "observe_only": False,
                "warm_start": True,
                "warm_start_parent": str(args.checkpoint.resolve()),
                "parent_was_promoted": False,
                "submitted_at": int(time.time()),
                "requested_by": "v2x-baseline-autochain",
            }
            _write_atomic(queue_root / "queued" / f"{JOB_ID}.json", payload)
            _write_atomic(
                vehicle_root / "asgard_v10_autochain_queued.json",
                {"schema": "greenran.asgard.v10.autochain.v1", "status": "queued", "job_id": JOB_ID, "payload": payload},
            )
            return 0
        status = _read(status_path) if status_path is not None else {}
        if status.get("state") in {"failed", "finished"} and status.get("exit_code") not in (None, 0):
            return _blocked(vehicle_root, f"vehicle_feasibility_job_failed:{status.get('exit_code')}")
        time.sleep(max(1.0, float(args.poll_seconds)))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vehicle-root", type=Path, default=DEFAULT_VEHICLE_ROOT)
    parser.add_argument("--queue-root", type=Path, default=DEFAULT_QUEUE_ROOT)
    parser.add_argument("--campaign-dir", type=Path, default=DEFAULT_CAMPAIGN)
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    parser.add_argument("--calibration", type=Path, default=DEFAULT_CALIBRATION)
    parser.add_argument("--vehicle-profile", default=VEHICLE_PROFILE)
    parser.add_argument("--vehicle-job-id", default=None)
    parser.add_argument("--poll-seconds", type=float, default=15.0)
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
