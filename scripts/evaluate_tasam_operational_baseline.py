#!/usr/bin/env python3
"""Evaluate one matched real-PDCP baseline run without ARMD or TA-SAM."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from evaluate_tasam_network_campaign import read_network_run
from evaluate_tasam_operational_run import _decision_integrity, _runtime_flags, _stage_counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    network = read_network_run(run_dir)
    integrity = _decision_integrity(run_dir)
    criteria = {
        "real_pdcp_only": bool(network["valid_real_only"]),
        "no_proxy_rows": int(network["proxy_metric_rows"]) == 0,
        "armd_disabled": integrity.get("armd_enabled", 0) == 0,
        "tasam_not_applied": integrity.get("tasam_actuation_applied", 0) == 0,
        "no_assistant_only_mode": "assistant_only_control" not in integrity.get("control_trial_modes", {}),
    }
    report = {
        "schema": "greenran.tasam_operational_baseline.v1",
        "run_dir": str(run_dir),
        "runtime": _runtime_flags(run_dir),
        "stage_counts": _stage_counts(run_dir),
        "decision_integrity": integrity,
        "network_metrics": network,
        "acceptance": {"criteria": criteria, "valid": all(criteria.values())},
        "interpretation": {
            "comparison_target": "rApp baseline without ARMD and TA-SAM",
            "energy": "calibrated_ru_mmwave_power_model; direct hardware power meter unavailable",
        },
    }
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "valid": report["acceptance"]["valid"], "criteria": criteria}, ensure_ascii=False, indent=2))
    return 0 if report["acceptance"]["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
