#!/usr/bin/env python3
"""Convenience wrapper for collecting App1/App2/App3 conflict scenarios."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNNER = PROJECT_ROOT / "scripts" / "run_conflict_experiments.py"
DEFAULT_SCENARIOS = (
    "app1_throughput",
    "app1_latencia",
    "app2_degradado_leve",
    "app2_degradado_critico",
    "conflito_implicito",
    "recuperacao",
    "vehicle_warning",
    "vehicle_critical",
    "vehicle_implicito",
    "vehicle_recovery",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the main App1/App2/App3 conflict collection scenarios."
    )
    parser.add_argument("--rounds", type=int, default=20, help="rounds per scenario")
    parser.add_argument("--duration", type=int, default=600, help="seconds per round")
    parser.add_argument(
        "--progress-step",
        type=int,
        default=60,
        help="countdown print step in seconds",
    )
    parser.add_argument(
        "--output-root",
        default=str(PROJECT_ROOT / "runs" / "experimentos_conflitos"),
        help="directory where experiment outputs will be stored",
    )
    parser.add_argument(
        "--target-rows-per-scenario",
        type=int,
        default=450,
        help="keep collecting until this many rows are reached per scenario",
    )
    parser.add_argument(
        "--max-rounds-per-scenario",
        type=int,
        default=0,
        help="hard cap for rounds per scenario when target rows are enabled",
    )
    parser.add_argument(
        "--round-gap",
        type=int,
        default=0,
        help="seconds to wait automatically between rounds",
    )
    parser.add_argument(
        "--scenario-gap",
        type=int,
        default=0,
        help="seconds to wait automatically between scenarios",
    )
    parser.add_argument(
        "--manual",
        action="store_true",
        help="disable automatic progression; useful when you want prompts between rounds/scenarios",
    )
    parser.add_argument(
        "--continue",
        dest="continue_experiment",
        action="store_true",
        help="continue from the last incomplete experiment instead of creating a new one",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    command = [
        sys.executable,
        str(RUNNER),
        "--rounds",
        str(args.rounds),
        "--duration",
        str(args.duration),
        "--progress-step",
        str(args.progress_step),
        "--output-root",
        str(args.output_root),
        "--target-rows-per-scenario",
        str(args.target_rows_per_scenario),
        "--max-rounds-per-scenario",
        str(args.max_rounds_per_scenario),
        "--round-gap",
        str(args.round_gap),
        "--scenario-gap",
        str(args.scenario_gap),
        "--auto-switch",
    ]
    if args.continue_experiment:
        command.append("--continue")
    if not args.manual:
        command.append("--auto")
    for scenario in DEFAULT_SCENARIOS:
        command.extend(["--scenario", scenario])
    completed = subprocess.run(command, check=False)
    return int(completed.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
