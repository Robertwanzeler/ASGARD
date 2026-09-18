#!/usr/bin/env python3
"""Create one atomically queued GreenRAN job for the local dispatcher."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from greenran_campaign_jobs import (  # noqa: E402
    ALLOWED_KINDS,
    JOB_SCHEMA,
    JobValidationError,
    atomic_json_write,
    new_job_id,
    normalize_job,
    prepare_queue,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue-root", type=Path, default=ROOT / "runs" / "agent_jobs")
    parser.add_argument("--job", type=Path, help="JSON job document; cannot be combined with flags")
    parser.add_argument("--kind", choices=sorted(ALLOWED_KINDS))
    parser.add_argument("--job-id")
    parser.add_argument("--campaign-dir")
    parser.add_argument("--checkpoint")
    parser.add_argument("--calibration")
    parser.add_argument("--shadow-db")
    parser.add_argument("--control-gate")
    parser.add_argument("--baseline")
    parser.add_argument("--treatment")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--profile")
    parser.add_argument("--vehicle-profile-manifest")
    parser.add_argument("--benchmark-manifest")
    parser.add_argument("--observe-only", action="store_true")
    parser.add_argument("--warm-start", action="store_true")
    parser.add_argument("--warm-start-parent")
    parser.add_argument("--parent-was-promoted", action="store_true")
    parser.add_argument("--adaptation-dir")
    parser.add_argument("--min-free-gib", type=float)
    args = parser.parse_args()
    if args.job:
        if args.kind:
            parser.error("--job não pode ser combinado com flags")
        payload = json.loads(args.job.read_text(encoding="utf-8"))
    else:
        if not args.kind:
            parser.error("--kind é obrigatório sem --job")
        payload = {
            "schema": JOB_SCHEMA, "job_id": args.job_id or new_job_id(args.kind),
            "kind": args.kind, "campaign_dir": args.campaign_dir, "checkpoint": args.checkpoint,
            "calibration": args.calibration, "shadow_db": args.shadow_db,
            "control_gate": args.control_gate, "baseline": args.baseline,
            "treatment": args.treatment, "seed": args.seed, "limit": args.limit,
            "profile": args.profile,
            "vehicle_profile_manifest": args.vehicle_profile_manifest,
            "benchmark_manifest": args.benchmark_manifest,
            "observe_only": args.observe_only,
            "warm_start": args.warm_start,
            "warm_start_parent": args.warm_start_parent,
            "parent_was_promoted": args.parent_was_promoted,
            "adaptation_dir": args.adaptation_dir,
            "min_free_gib": args.min_free_gib,
            "submitted_at": int(time.time()), "requested_by": "codex",
        }
        payload = {key: value for key, value in payload.items() if value is not None}
    try:
        spec = normalize_job(payload)
    except JobValidationError as exc:
        parser.error(str(exc))
    layout = prepare_queue(args.queue_root.resolve())
    destination = layout["queued"] / f"{spec.job_id}.json"
    if destination.exists() or any((layout[name] / destination.name).exists() for name in ("running", "finished", "failed")):
        parser.error(f"job_id já existe: {spec.job_id}")
    atomic_json_write(destination, spec.payload)
    print(json.dumps({"job_id": spec.job_id, "kind": spec.kind, "queued": str(destination), "command": spec.command}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
