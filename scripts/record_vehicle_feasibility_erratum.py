#!/usr/bin/env python3
"""Record an immutable assessment erratum without rewriting a campaign."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path


def sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign-dir", type=Path, required=True)
    parser.add_argument("--reason", default="decision_target_reached")
    args = parser.parse_args()
    campaign = args.campaign_dir.resolve()
    result_path = campaign / "interval_4000us" / "feasibility_result.json"
    arm_path = campaign / "interval_4000us" / "arm_manifest.json"
    selected_path = campaign / "selected_vehicle_profile.json"
    if not result_path.is_file() or not arm_path.is_file() or not selected_path.is_file():
        raise SystemExit("campanha incompleta: resultado, arm_manifest ou seleção ausente")
    result = json.loads(result_path.read_text(encoding="utf-8"))
    arm = json.loads(arm_path.read_text(encoding="utf-8"))
    payload = {
        "schema": "greenran.vehicle_feasibility_assessment_erratum.v1",
        "created_at": int(time.time()),
        "campaign_dir": str(campaign),
        "original_assessment": {
            "selected_vehicle_profile_sha256": sha256(selected_path),
            "feasibility_result_sha256": sha256(result_path),
            "arm_manifest_sha256": sha256(arm_path),
            "status": json.loads(selected_path.read_text(encoding="utf-8")).get("status"),
            "classification": result.get("classification"),
        },
        "correction": {
            "status": "metric_invalid",
            "promotion_eligible": False,
            "reason": args.reason,
            "observed_sim_time_s": (arm.get("simulation_performance") or {}).get("sim_time_observed_s"),
            "required_sim_time_s": sum((
                float(result.get("evidence", {}).get("warmup_seconds", 30.0)),
                float(result.get("evidence", {}).get("required_scored_windows", 1))
                * float(result.get("evidence", {}).get("window_seconds", 10.0)),
                1.0,
                0.5,
            )),
            "explanation": "The decision target stopped the arm before the scientific PDCP cohort and drain completed.",
        },
        "preservation": {
            "original_files_edited": False,
            "original_artifacts_preserved": True,
        },
    }
    output = campaign / "assessment_erratum_v1.json"
    output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
