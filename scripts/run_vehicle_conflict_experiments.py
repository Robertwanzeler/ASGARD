#!/usr/bin/env python3
"""Convenience wrapper for collecting real App3 vehicle conflict scenarios."""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNNER = PROJECT_ROOT / "scripts" / "run_conflict_experiments.py"
DEFAULT_SCENARIOS = (
    "vehicle_warning",
    "vehicle_critical",
    "vehicle_implicito",
    "vehicle_recovery",
)
DEFAULT_TARGET_ROWS_PER_SCENARIO = 450
DEFAULT_TARGET_TOTAL_ROWS = DEFAULT_TARGET_ROWS_PER_SCENARIO * len(DEFAULT_SCENARIOS)
DEFAULT_ESTIMATED_ROWS_PER_ROUND = 100
DEFAULT_MAX_ROUND_BUFFER = 1
SCENARIO_SUBDIVISION = {
    "vehicle_warning": {
        "title": "Vehicle warning",
        "target_rows": DEFAULT_TARGET_ROWS_PER_SCENARIO,
        "degradation_band": "guard",
        "thresholds": {
            "ego_latency_ms_min": 50.0,
            "ego_packet_loss_percent_min": 2.0,
            "medium_risk_vehicles_min": 1,
        },
        "control_mode": "vehicle_warning",
        "notes": [
            "App1 e App2 permanecem saudáveis.",
            "Treina o estado de guarda veicular antes do bloqueio crítico.",
        ],
    },
    "vehicle_critical": {
        "title": "Vehicle critical",
        "target_rows": DEFAULT_TARGET_ROWS_PER_SCENARIO,
        "degradation_band": "critical",
        "thresholds": {
            "ego_latency_ms_min": 100.0,
            "ego_packet_loss_percent_min": 5.0,
            "high_risk_vehicles_min": 1,
            "degraded_autonomy_vehicles_min": 1,
        },
        "control_mode": "vehicle_critical",
        "notes": [
            "Força bloqueio por segurança veicular.",
            "É o cenário de degradação dura do domínio App3.",
        ],
    },
    "vehicle_implicito": {
        "title": "Vehicle implícito",
        "target_rows": DEFAULT_TARGET_ROWS_PER_SCENARIO,
        "degradation_band": "implicit_local",
        "thresholds": {
            "global_system_healthy": True,
            "ego_latency_ms_min": 50.0,
            "ego_packet_loss_percent_min": 2.0,
            "medium_risk_vehicles_min": 1,
        },
        "control_mode": "vehicle_implicit",
        "notes": [
            "CVaR/P95 global saudável com degradação apenas local no ego vehicle.",
            "Treina conflito implícito específico do domínio veicular.",
        ],
    },
    "vehicle_recovery": {
        "title": "Vehicle recovery",
        "target_rows": DEFAULT_TARGET_ROWS_PER_SCENARIO,
        "degradation_band": "critical_to_healthy",
        "thresholds": {
            "start_ego_latency_ms_min": 100.0,
            "start_ego_packet_loss_percent_min": 5.0,
            "start_high_risk_vehicles_min": 1,
            "start_degraded_autonomy_vehicles_min": 1,
            "mid_round_recovery": True,
        },
        "control_mode": "vehicle_recovery_start",
        "notes": [
            "Começa crítico e recupera no meio da rodada.",
            "Treina histerese e estabilização do domínio veicular.",
        ],
    },
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run only the App3/vehicle conflict collection scenarios."
    )
    parser.add_argument(
        "--rounds",
        type=int,
        default=1,
        help="minimum rounds per vehicle scenario before the target-row stop condition may end collection",
    )
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
        default=DEFAULT_TARGET_ROWS_PER_SCENARIO,
        help="keep collecting until this many App3 rows are reached per vehicle scenario",
    )
    parser.add_argument(
        "--target-total-rows",
        type=int,
        default=DEFAULT_TARGET_TOTAL_ROWS,
        help="global vehicle-row target across all configured vehicle scenarios",
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
        "--plan-only",
        action="store_true",
        help="print the computed vehicle collection plan and exit without running collection",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="force a new vehicle experiment instead of resuming the latest incomplete one",
    )
    return parser.parse_args(argv)


def resolve_target_rows_per_scenario(args: argparse.Namespace) -> int:
    scenario_count = len(DEFAULT_SCENARIOS)
    target_total_rows = int(args.target_total_rows or 0)
    target_rows_per_scenario = int(args.target_rows_per_scenario or 0)

    if target_total_rows > 0:
        if target_total_rows % scenario_count != 0:
            raise SystemExit(
                f"--target-total-rows must be divisible by {scenario_count} vehicle scenarios; "
                f"got {target_total_rows}"
            )
        derived = target_total_rows // scenario_count
        if target_rows_per_scenario not in {0, derived}:
            raise SystemExit(
                "--target-rows-per-scenario conflicts with --target-total-rows: "
                f"expected {derived}, got {target_rows_per_scenario}"
            )
        return derived

    if target_rows_per_scenario <= 0:
        raise SystemExit("either --target-total-rows or --target-rows-per-scenario must be > 0")
    return target_rows_per_scenario


def resolve_max_rounds_per_scenario(
    args: argparse.Namespace,
    target_rows_per_scenario: int,
) -> int:
    if int(args.max_rounds_per_scenario or 0) > 0:
        return int(args.max_rounds_per_scenario)

    estimated_rounds = math.ceil(
        target_rows_per_scenario / float(DEFAULT_ESTIMATED_ROWS_PER_ROUND)
    )
    return max(int(args.rounds or 1), estimated_rounds + DEFAULT_MAX_ROUND_BUFFER)


def build_collection_plan(target_rows_per_scenario: int, max_rounds_per_scenario: int) -> dict:
    total_target_rows = target_rows_per_scenario * len(DEFAULT_SCENARIOS)
    scenarios = []
    for slug in DEFAULT_SCENARIOS:
        payload = dict(SCENARIO_SUBDIVISION[slug])
        payload["slug"] = slug
        payload["target_rows"] = target_rows_per_scenario
        scenarios.append(payload)
    return {
        "collection_scope": "vehicle_only",
        "target_total_rows": total_target_rows,
        "target_rows_per_scenario": target_rows_per_scenario,
        "max_rounds_per_scenario": max_rounds_per_scenario,
        "scenario_count": len(DEFAULT_SCENARIOS),
        "scenarios": scenarios,
    }


def build_command(
    args: argparse.Namespace,
    target_rows_per_scenario: int,
    max_rounds_per_scenario: int,
) -> list[str]:
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
        str(target_rows_per_scenario),
        "--max-rounds-per-scenario",
        str(max_rounds_per_scenario),
        "--round-gap",
        str(args.round_gap),
        "--scenario-gap",
        str(args.scenario_gap),
        "--auto-switch",
    ]
    if not args.fresh:
        command.append("--continue")
    if not args.manual:
        command.append("--auto")
    for scenario in DEFAULT_SCENARIOS:
        command.extend(["--scenario", scenario])
    return command


def main() -> int:
    args = parse_args()
    target_rows_per_scenario = resolve_target_rows_per_scenario(args)
    max_rounds_per_scenario = resolve_max_rounds_per_scenario(args, target_rows_per_scenario)
    plan = build_collection_plan(target_rows_per_scenario, max_rounds_per_scenario)
    print(json.dumps(plan, indent=2, ensure_ascii=False))
    if args.plan_only:
        return 0

    command = build_command(args, target_rows_per_scenario, max_rounds_per_scenario)
    completed = subprocess.run(command, check=False)
    return int(completed.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
