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
DEFAULT_VEHICLE_ROOT = ROOT / "runs/tasam_vehicle_feasibility_seed47_20260917_balanced_v4_v2x_gbr_r2"
DEFAULT_QUEUE_ROOT = ROOT / "runs/agent_jobs"
DEFAULT_CAMPAIGN = ROOT / "runs/tasam_asgard_adaptation_seed47_20260917_v10_gbr_r2"
DEFAULT_CHECKPOINT = ROOT / "runs/tasam_economic_checkpoint_v10_seed47_20260917"
DEFAULT_CALIBRATION = ROOT / "config/energy_calibration_sim_v3_sleep.json"
JOB_ID = "tasam-asgard-online-economic-seed47-20260917-v10-gbr-r2"
VEHICLE_PROFILE = "tasam_training_balanced_v4_v2x_gbr"


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


def run(args: argparse.Namespace) -> int:
    vehicle_root = args.vehicle_root.resolve()
    queue_root = args.queue_root.resolve()
    manifest_path = vehicle_root / "selected_vehicle_profile.json"
    status_path = queue_root / "status" / "tasam-vehicle-feasibility-seed47-balanced-v4-v2x-gbr-20260917-r2.json"
    while True:
        if _job_exists(queue_root):
            return 0
        if manifest_path.is_file():
            manifest = _read(manifest_path)
            if manifest.get("status") != "passed":
                return _blocked(vehicle_root, f"baseline_infeasible:{manifest.get('status') or 'invalid_manifest'}")
            if manifest.get("profile") != VEHICLE_PROFILE:
                return _blocked(vehicle_root, "vehicle_profile_mismatch")
            if int(manifest.get("selected_interval_us") or 0) not in {4000, 6000, 8000, 12000, 16000}:
                return _blocked(vehicle_root, "selected_interval_invalid")
            topology = manifest.get("topology") or {}
            if int(topology.get("ue_count", 0)) != 20 or int(topology.get("du_count", 0)) != 3:
                return _blocked(vehicle_root, "vehicle_topology_invalid")
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
                "profile": VEHICLE_PROFILE,
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
        status = _read(status_path)
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
    parser.add_argument("--poll-seconds", type=float, default=15.0)
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
