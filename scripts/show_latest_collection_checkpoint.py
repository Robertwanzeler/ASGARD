#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LATEST_CANDIDATES = [
    REPO_ROOT / "runs" / "sac_bootstrap" / "fixed_awac_pressure_forced_fast_clean_status_checkpoints" / "latest_checkpoint.json",
    REPO_ROOT / "runs" / "sac_bootstrap" / "fixed_awac_pressure_forced_clean_status_checkpoints" / "latest_checkpoint.json",
    REPO_ROOT / "runs" / "sac_bootstrap" / "fixed_awac_pressure_clean_status_checkpoints" / "latest_checkpoint.json",
    REPO_ROOT / "runs" / "sac_bootstrap" / "fixed_awac_pressure_status_checkpoints" / "latest_checkpoint.json",
]


def default_latest_path() -> Path:
    for candidate in DEFAULT_LATEST_CANDIDATES:
        if candidate.exists():
            return candidate
    return DEFAULT_LATEST_CANDIDATES[0]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Show the latest automated GreenRAN collection checkpoint verdict")
    parser.add_argument("--latest-json", default=str(default_latest_path()), help="Path to latest_checkpoint.json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    latest_path = Path(args.latest_json)
    if not latest_path.exists():
        print(f"checkpoint=missing path={latest_path}")
        return 1

    try:
        payload = json.loads(latest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"checkpoint=error path={latest_path} reason={exc}")
        return 1

    verdict = payload.get("verdict") or {}
    snapshot = payload.get("status_snapshot") or {}
    ran_pressure = snapshot.get("ran_pressure") or {}
    line = (
        f"checkpoint={payload.get('checkpoint_key', 'unknown')} "
        f"readiness={verdict.get('readiness', 'unknown')} "
        f"rows={snapshot.get('rows', '?')} "
        f"sim_time={snapshot.get('sim_time_end', '?')} "
        f"ran_stage={ran_pressure.get('stage', 'unknown')} "
        f"ran_conflict={verdict.get('ran_conflict_observed', False)} "
        f"recommendation={verdict.get('recommendation', 'n/a')}"
    )
    print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
