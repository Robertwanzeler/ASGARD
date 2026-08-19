#!/usr/bin/env python3
"""
Promote validated rerun scenarios into the canonical GraphSAGE protocol root.

This script copies only the known improved scenario trees from the secondary
protocol folders into runs/graphsage_article00_protocol and then regenerates
the canonical protocol summary.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROTOCOL_ROOT = PROJECT_ROOT / "runs" / "graphsage_article00_protocol"
DEFAULT_PROTOCOL_CLEAN_REMAINING_ROOT = PROJECT_ROOT / "runs" / "graphsage_article00_protocol_clean_remaining"
DEFAULT_PROTOCOL_LAST_TWO_ROOT = PROJECT_ROOT / "runs" / "graphsage_article00_protocol_last_two"
DEFAULT_PROTOCOL_VEHICLE_CLEAN_ROOT = PROJECT_ROOT / "runs" / "graphsage_article00_protocol_vehicle_clean"
DEFAULT_SUMMARIZER = PROJECT_ROOT / "scripts" / "summarize_greenran_graphsage_article00_protocol.py"

PROMOTION_PLAN = {
    "protocol_clean_remaining": {
        "root": DEFAULT_PROTOCOL_CLEAN_REMAINING_ROOT,
        "scenarios": ("app1_latencia", "app2_degradado_critico", "app2_degradado_leve"),
    },
    "protocol_last_two": {
        "root": DEFAULT_PROTOCOL_LAST_TWO_ROOT,
        "scenarios": ("app1_throughput",),
    },
    "protocol_vehicle_clean": {
        "root": DEFAULT_PROTOCOL_VEHICLE_CLEAN_ROOT,
        "scenarios": ("vehicle_critical", "vehicle_implicito"),
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Consolidate improved GraphSAGE protocol reruns into the canonical protocol root.")
    parser.add_argument("--protocol-root", type=Path, default=DEFAULT_PROTOCOL_ROOT)
    parser.add_argument("--summarizer", type=Path, default=DEFAULT_SUMMARIZER)
    parser.add_argument("--plan-only", action="store_true", help="show the copy plan without writing files")
    return parser.parse_args()


def iter_promotions(source_root: Path, scenarios: tuple[str, ...]):
    for threshold_dir in sorted(source_root.glob("threshold_*")):
        if not threshold_dir.is_dir():
            continue
        for seed_dir in sorted(threshold_dir.glob("seed_*")):
            if not seed_dir.is_dir():
                continue
            for scenario in scenarios:
                source_dir = seed_dir / scenario
                if source_dir.exists():
                    yield source_dir


def main() -> int:
    args = parse_args()
    protocol_root = args.protocol_root.resolve()
    promotions: list[dict] = []

    for source_name, config in PROMOTION_PLAN.items():
        source_root = Path(config["root"]).resolve()
        scenarios = tuple(config["scenarios"])
        for source_dir in iter_promotions(source_root, scenarios):
            rel = source_dir.relative_to(source_root)
            target_dir = protocol_root / rel
            promotions.append(
                {
                    "source_name": source_name,
                    "source_dir": str(source_dir),
                    "target_dir": str(target_dir),
                }
            )
            if args.plan_only:
                continue
            if target_dir.exists():
                shutil.rmtree(target_dir)
            shutil.copytree(source_dir, target_dir)

    summary_result = None
    if not args.plan_only:
        summary_result = subprocess.run(
            [sys.executable, str(args.summarizer.resolve()), "--output-root", str(protocol_root)],
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
            check=True,
        )

    payload = {
        "protocol_root": str(protocol_root),
        "promotions": promotions,
        "promotion_count": len(promotions),
        "plan_only": args.plan_only,
    }
    if summary_result is not None and summary_result.stdout.strip():
        try:
            payload["summary"] = json.loads(summary_result.stdout)
        except json.JSONDecodeError:
            payload["summary_stdout"] = summary_result.stdout
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
