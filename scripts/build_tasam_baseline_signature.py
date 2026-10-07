#!/usr/bin/env python3
"""Build a strict-pair SLA signature for an already completed baseline arm."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from evaluate_tasam_strict_pair import _violation_keys, evaluate_ue_windows


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--warmup-s", type=int, default=30)
    parser.add_argument("--duration-s", type=int, default=120)
    args = parser.parse_args()

    baseline = args.baseline.resolve()
    manifest_path = baseline / "arm_manifest.json"
    if not baseline.is_dir() or not manifest_path.is_file():
        raise SystemExit(f"baseline or arm_manifest.json ausente: {baseline}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if int(manifest.get("seed", -1)) != args.seed:
        raise SystemExit("seed do baseline diverge da assinatura")
    if str(manifest.get("profile", "")) != args.profile:
        raise SystemExit("perfil do baseline diverge da assinatura")

    sla = evaluate_ue_windows(
        baseline, warmup_s=args.warmup_s, duration_s=args.duration_s
    )
    keys = sorted(_violation_keys(sla))
    payload = {
        "schema": "greenran.tasam.strict_pair.v1",
        "seed": args.seed,
        "profile": args.profile,
        "warmup_s": args.warmup_s,
        "duration_s": args.duration_s,
        "experiment_contract": {
            "baseline_manifest": str(manifest_path),
            "baseline_manifest_sha256": sha256_file(manifest_path),
            "profile": args.profile,
            "seed": args.seed,
            "same_schedule": True,
            "same_binary_required": True,
        },
        "baseline": {
            "run_dir": str(baseline),
            "sla": sla,
        },
        "sla_signature": {
            "baseline_violation_keys": keys,
            "baseline_violation_count": len(keys),
        },
    }
    args.output.resolve().parent.mkdir(parents=True, exist_ok=True)
    args.output.resolve().write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "output": str(args.output.resolve()),
        "sla_valid": sla.get("valid", False),
        "expected_ue_windows": sla.get("expected_ue_windows", 0),
        "observed_ue_windows": sla.get("observed_ue_windows", 0),
        "violation_count": len(keys),
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
