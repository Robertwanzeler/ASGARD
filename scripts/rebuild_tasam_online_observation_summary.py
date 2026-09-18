#!/usr/bin/env python3
"""Rebuild a derived observation report without mutating campaign evidence."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from run_tasam_online_economic_campaign import (  # noqa: E402
    DEFAULT_CALIBRATION,
    _observation_summary,
    _write,
)


def _local_campaign(path: Path) -> Path:
    resolved = path.resolve()
    runs_root = (ROOT / "runs").resolve()
    try:
        resolved.relative_to(runs_root)
    except ValueError as exc:
        raise SystemExit(f"campanha fora de runs/: {resolved}") from exc
    return resolved


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-dir", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--calibration", type=Path, default=DEFAULT_CALIBRATION)
    parser.add_argument("--min-applied-actions", type=int, default=180)
    args = parser.parse_args()

    campaign = _local_campaign(args.campaign_dir)
    adaptation = campaign / "adaptation_online"
    checkpoint = (args.checkpoint or campaign / "initial_checkpoint_applied_action_v2").resolve()
    calibration = args.calibration.resolve()
    report = campaign / "online_observation_summary.json"
    summary = _observation_summary(
        campaign,
        adaptation,
        checkpoint,
        calibration,
        min_applied_actions=args.min_applied_actions,
    )
    summary["report_reconciled_at"] = int(time.time())
    summary["report_reconciliation"] = (
        "campos econômicos reconstruídos do feedback_json e economic_action_json; "
        "SQLite bruto não foi alterado"
    )
    _write(report, summary)

    manifest_path = campaign / "campaign_manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["observation_report"] = str(report)
        manifest["observation_report_reconciled_at"] = summary["report_reconciled_at"]
        _write(manifest_path, manifest)
    print(json.dumps({
        "report": str(report),
        "decision_count": summary.get("decision_count"),
        "applied_decisions": summary.get("applied_decisions"),
        "economic_transition_eligible": summary.get("economic_transition_eligible"),
        "candidate_promoted": summary.get("candidate_promoted"),
        "valid": summary.get("valid"),
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
