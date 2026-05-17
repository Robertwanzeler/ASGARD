#!/usr/bin/env python3
"""
Targeted calibration runner for hard GreenRAN GraphSAGE scenarios.

This runner consumes the completed ARTICLE00-aligned protocol summary and
focuses on the scenarios that still miss 1.0 at the official target epoch.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.run_greenran_graphsage_article00_protocol import (  # noqa: E402
    OFFICIAL_SCENARIOS,
    resolve_experiment_dir,
)


DEFAULT_PROTOCOL_OUTPUT = PROJECT_ROOT / "runs" / "graphsage_article00_protocol"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "runs" / "graphsage_article00_calibration"
DEFAULT_PREVIOUS_CALIBRATION_OUTPUT = PROJECT_ROOT / "runs" / "graphsage_article00_calibration"
DEFAULT_TRAINER_PYTHON = PROJECT_ROOT / "drlexp" / ".venv" / "bin" / "python"

SCENARIO_CONFIG = {
    "app1_throughput": {"domain_scope": "app1", "domain_scope_mode": "service"},
    "app1_latencia": {"domain_scope": "app1", "domain_scope_mode": "service"},
    "app2_degradado_leve": {"domain_scope": "app2", "domain_scope_mode": "service"},
    "app2_degradado_critico": {"domain_scope": "app2", "domain_scope_mode": "service"},
    "vehicle_warning": {"domain_scope": "app3", "domain_scope_mode": "service"},
    "vehicle_critical": {"domain_scope": "app3", "domain_scope_mode": "causal"},
    "vehicle_implicito": {"domain_scope": "app3", "domain_scope_mode": "causal"},
    "vehicle_recovery": {"domain_scope": "app3", "domain_scope_mode": "causal"},
    "conflito_implicito": {"domain_scope": "mixed", "domain_scope_mode": "auto"},
    "recuperacao": {"domain_scope": "mixed", "domain_scope_mode": "auto"},
}

PROFILES = {
    "app1_balanced_v1": {
        "hidden_dim": 32,
        "embed_dim": 32,
        "dropout": 0.05,
        "loss_mask_mode": "all",
        "negative_edge_weight": 1.25,
        "pos_weight_scale": 1.0,
        "max_pos_weight": 3.0,
        "fp_penalty_weight": 0.1,
        "fp_penalty_margin": 0.5,
        "selection_mode": "f1",
        "selection_fp_weight": 0.25,
        "selection_precision_weight": 0.1,
        "pair_mask_mode": "valid_types",
    },
    "app2_balanced_v1": {
        "hidden_dim": 32,
        "embed_dim": 32,
        "dropout": 0.05,
        "loss_mask_mode": "all",
        "negative_edge_weight": 2.0,
        "pos_weight_scale": 0.75,
        "max_pos_weight": 2.0,
        "fp_penalty_weight": 0.3,
        "fp_penalty_margin": 0.5,
        "selection_mode": "fp_penalized",
        "selection_fp_weight": 0.35,
        "selection_precision_weight": 0.15,
        "pair_mask_mode": "valid_types",
    },
    "app3_balanced_v1": {
        "hidden_dim": 48,
        "embed_dim": 32,
        "dropout": 0.05,
        "loss_mask_mode": "support",
        "negative_edge_weight": 2.5,
        "pos_weight_scale": 0.5,
        "max_pos_weight": 1.5,
        "fp_penalty_weight": 0.4,
        "fp_penalty_margin": 0.5,
        "selection_mode": "fp_penalized",
        "selection_fp_weight": 0.4,
        "selection_precision_weight": 0.2,
        "pair_mask_mode": "valid_types",
    },
    "vehicle_recovery_recall_v1": {
        "hidden_dim": 48,
        "embed_dim": 32,
        "dropout": 0.03,
        "loss_mask_mode": "all",
        "negative_edge_weight": 1.0,
        "pos_weight_scale": 1.5,
        "max_pos_weight": 4.0,
        "fp_penalty_weight": 0.0,
        "fp_penalty_margin": 0.5,
        "selection_mode": "f1",
        "selection_fp_weight": 0.0,
        "selection_precision_weight": 0.0,
        "pair_mask_mode": "valid_types",
    },
    "domain_precision_v1": {
        "hidden_dim": 32,
        "embed_dim": 32,
        "dropout": 0.05,
        "loss_mask_mode": "all",
        "negative_edge_weight": 2.0,
        "pos_weight_scale": 0.75,
        "max_pos_weight": 2.0,
        "fp_penalty_weight": 0.3,
        "fp_penalty_margin": 0.5,
        "selection_mode": "fp_penalized",
        "selection_fp_weight": 0.35,
        "selection_precision_weight": 0.15,
        "pair_mask_mode": "valid_types",
    },
    "domain_precision_support": {
        "hidden_dim": 32,
        "embed_dim": 32,
        "dropout": 0.05,
        "loss_mask_mode": "support",
        "negative_edge_weight": 2.0,
        "pos_weight_scale": 0.75,
        "max_pos_weight": 2.0,
        "fp_penalty_weight": 0.3,
        "fp_penalty_margin": 0.5,
        "selection_mode": "fp_penalized",
        "selection_fp_weight": 0.35,
        "selection_precision_weight": 0.15,
        "pair_mask_mode": "valid_types",
    },
    "domain_precision_v2": {
        "hidden_dim": 48,
        "embed_dim": 32,
        "dropout": 0.05,
        "loss_mask_mode": "support",
        "negative_edge_weight": 2.5,
        "pos_weight_scale": 0.5,
        "max_pos_weight": 1.5,
        "fp_penalty_weight": 0.4,
        "fp_penalty_margin": 0.5,
        "selection_mode": "fp_penalized",
        "selection_fp_weight": 0.4,
        "selection_precision_weight": 0.2,
        "pair_mask_mode": "valid_types",
    },
}

PROFILE_STRATEGIES = {
    "family_v1": {
        "app1_throughput": "app1_balanced_v1",
        "app1_latencia": "app1_balanced_v1",
        "app2_degradado_leve": "app2_balanced_v1",
        "app2_degradado_critico": "app2_balanced_v1",
        "vehicle_critical": "app3_balanced_v1",
        "vehicle_implicito": "app3_balanced_v1",
        "vehicle_recovery": "app3_balanced_v1",
        "recuperacao": "app2_balanced_v1",
    },
}


def parse_csv_ints(raw: str) -> tuple[int, ...]:
    values = tuple(sorted({int(piece.strip()) for piece in raw.split(",") if piece.strip()}))
    if not values:
        raise SystemExit("no integer values requested")
    return values


def parse_csv_floats(raw: str) -> tuple[float, ...]:
    values = tuple(sorted({float(piece.strip()) for piece in raw.split(",") if piece.strip()}))
    if not values:
        raise SystemExit("no float values requested")
    return values


def parse_csv_strings(raw: str) -> tuple[str, ...]:
    values = tuple(piece.strip() for piece in raw.split(",") if piece.strip())
    if not values:
        raise SystemExit("no values requested")
    return values


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run targeted calibration for hard GreenRAN GraphSAGE scenarios.")
    parser.add_argument(
        "--protocol-output-root",
        default=str(DEFAULT_PROTOCOL_OUTPUT),
        help="completed protocol output root; defaults to runs/graphsage_article00_protocol",
    )
    parser.add_argument(
        "--output-root",
        default=str(DEFAULT_OUTPUT_ROOT),
        help="directory where calibration artifacts will be written",
    )
    parser.add_argument(
        "--previous-calibration-root",
        default=str(DEFAULT_PREVIOUS_CALIBRATION_OUTPUT),
        help="optional previous calibration root used to exclude scenarios that are already solved",
    )
    parser.add_argument(
        "--trainer-python",
        default="",
        help="python executable used to invoke training/train_graphsage_conflicts.py",
    )
    parser.add_argument(
        "--profile",
        default="domain_precision_v1",
        choices=tuple(PROFILES.keys()),
        help="calibration profile",
    )
    parser.add_argument(
        "--strategy",
        default="single_profile",
        choices=("single_profile", *PROFILE_STRATEGIES.keys()),
        help="single_profile uses --profile for every scenario; family strategies assign profiles per scenario",
    )
    parser.add_argument(
        "--thresholds",
        default="0.5",
        help="comma-separated thresholds to calibrate",
    )
    parser.add_argument(
        "--seeds",
        default="42,43,44,45,46",
        help="comma-separated seeds",
    )
    parser.add_argument(
        "--subset-sizes",
        default="450",
        help="comma-separated subset sizes; defaults to the official 450",
    )
    parser.add_argument(
        "--epochs",
        default="200",
        help="comma-separated epochs; defaults to the official 200",
    )
    parser.add_argument(
        "--scenario",
        action="append",
        dest="scenarios",
        default=[],
        help="explicit scenario filter; when omitted, only scenarios that failed the official target are selected",
    )
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="only write the calibration manifest and print the planned runs",
    )
    return parser.parse_args()


def resolve_trainer_python(raw: str) -> str:
    if raw:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = (Path.cwd() / path).resolve()
        return str(path)
    if DEFAULT_TRAINER_PYTHON.exists():
        return str(DEFAULT_TRAINER_PYTHON)
    return sys.executable


def load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def scenario_experiment_map() -> dict[str, str]:
    return {item.scenario: item.experiment_dir for item in OFFICIAL_SCENARIOS}


def weak_scenarios_from_protocol(protocol_output_root: Path, thresholds: tuple[float, ...]) -> list[str]:
    summary = load_json(protocol_output_root / "protocol_summary.json")
    weak = set()
    for row in summary.get("rows", []):
        if int(row.get("subset_size", 0)) != 450:
            continue
        if float(row.get("threshold", 0.0)) not in thresholds:
            continue
        if not bool(row.get("target_met", False)):
            weak.add(row["scenario"])
    return sorted(weak)


def unresolved_scenarios_from_calibration(
    calibration_output_root: Path,
    thresholds: tuple[float, ...],
) -> set[str]:
    summary_path = calibration_output_root / "calibration_summary.json"
    if not summary_path.exists():
        return set()
    summary = load_json(summary_path)
    unresolved = set()
    aggregates = summary.get("aggregates", [])
    for row in aggregates:
        if float(row.get("threshold", 0.0)) not in thresholds:
            continue
        if int(row.get("target_hits", 0)) < int(row.get("completed_seeds", 0)):
            unresolved.add(row["scenario"])
    return unresolved


def training_complete(output_dir: Path, scenario: str, subset_sizes: tuple[int, ...]) -> bool:
    scenario_dir = output_dir / scenario
    for subset in subset_sizes:
        if not (scenario_dir / f"subset_{subset}" / "training_summary.json").exists():
            return False
    return True


def build_command(
    trainer_python: str,
    scenario: str,
    threshold: float,
    seed: int,
    subset_sizes: tuple[int, ...],
    epochs: tuple[int, ...],
    output_dir: Path,
    profile_name: str,
) -> list[str]:
    spec_map = {item.scenario: item for item in OFFICIAL_SCENARIOS}
    experiment_dir = resolve_experiment_dir(spec_map[scenario])
    scenario_cfg = SCENARIO_CONFIG[scenario]
    profile = PROFILES[profile_name]
    return [
        trainer_python,
        str(PROJECT_ROOT / "training" / "train_graphsage_conflicts.py"),
        "--experiment-dir",
        str(experiment_dir),
        "--scenario",
        scenario,
        "--split-by-rounds",
        "--epochs",
        ",".join(str(value) for value in epochs),
        "--subset-sizes",
        ",".join(str(value) for value in subset_sizes),
        "--threshold",
        str(threshold),
        "--seed",
        str(seed),
        "--hidden-dim",
        str(profile["hidden_dim"]),
        "--embed-dim",
        str(profile["embed_dim"]),
        "--dropout",
        str(profile["dropout"]),
        "--loss-mask-mode",
        str(profile["loss_mask_mode"]),
        "--negative-edge-weight",
        str(profile["negative_edge_weight"]),
        "--pos-weight-scale",
        str(profile["pos_weight_scale"]),
        "--max-pos-weight",
        str(profile["max_pos_weight"]),
        "--fp-penalty-weight",
        str(profile["fp_penalty_weight"]),
        "--fp-penalty-margin",
        str(profile["fp_penalty_margin"]),
        "--selection-mode",
        str(profile["selection_mode"]),
        "--selection-fp-weight",
        str(profile["selection_fp_weight"]),
        "--selection-precision-weight",
        str(profile["selection_precision_weight"]),
        "--pair-mask-mode",
        str(profile["pair_mask_mode"]),
        "--domain-scope",
        str(scenario_cfg["domain_scope"]),
        "--domain-scope-mode",
        str(scenario_cfg["domain_scope_mode"]),
        "--output-dir",
        str(output_dir),
    ]


def resolve_profile_for_scenario(strategy: str, profile: str, scenario: str) -> str:
    if strategy == "single_profile":
        return profile
    strategy_map = PROFILE_STRATEGIES[strategy]
    if scenario not in strategy_map:
        raise SystemExit(f"scenario '{scenario}' is not mapped in strategy '{strategy}'")
    return strategy_map[scenario]


def run_command(command: list[str]) -> None:
    result = subprocess.run(command, cwd=PROJECT_ROOT, text=True)
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def main() -> int:
    args = parse_args()
    protocol_output_root = Path(args.protocol_output_root).resolve()
    output_root = Path(args.output_root).resolve()
    previous_calibration_root = Path(args.previous_calibration_root).resolve()
    trainer_python = resolve_trainer_python(args.trainer_python)
    thresholds = parse_csv_floats(args.thresholds)
    seeds = parse_csv_ints(args.seeds)
    subset_sizes = parse_csv_ints(args.subset_sizes)
    epochs = parse_csv_ints(args.epochs)

    if args.scenarios:
        scenarios = sorted(set(args.scenarios))
    else:
        scenarios = weak_scenarios_from_protocol(protocol_output_root, thresholds)
        prior_unresolved = unresolved_scenarios_from_calibration(previous_calibration_root, thresholds)
        if prior_unresolved:
            scenarios = sorted(set(scenarios) & prior_unresolved)
    if not scenarios:
        raise SystemExit("no weak scenarios selected for calibration")

    output_root.mkdir(parents=True, exist_ok=True)
    profile_root_label = args.profile if args.strategy == "single_profile" else args.strategy
    manifest = {
        "schema": "greenran.graphsage_calibration.v1",
        "generated_at": int(time.time()),
        "generated_at_iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "protocol_output_root": str(protocol_output_root),
        "output_root": str(output_root),
        "previous_calibration_root": str(previous_calibration_root),
        "profile": args.profile,
        "strategy": args.strategy,
        "thresholds": list(thresholds),
        "seeds": list(seeds),
        "subset_sizes": list(subset_sizes),
        "epochs": list(epochs),
        "scenarios": scenarios,
        "runs": [],
    }

    for threshold in thresholds:
        threshold_slug = str(threshold).replace(".", "_")
        for seed in seeds:
            for scenario in scenarios:
                resolved_profile = resolve_profile_for_scenario(args.strategy, args.profile, scenario)
                run_output_dir = output_root / profile_root_label / f"threshold_{threshold_slug}" / f"seed_{seed}" / scenario
                manifest["runs"].append(
                    {
                        "scenario": scenario,
                        "threshold": threshold,
                        "seed": seed,
                        "resolved_profile": resolved_profile,
                        "output_dir": str(run_output_dir),
                        "command": build_command(
                            trainer_python=trainer_python,
                            scenario=scenario,
                            threshold=threshold,
                            seed=seed,
                            subset_sizes=subset_sizes,
                            epochs=epochs,
                            output_dir=run_output_dir,
                            profile_name=resolved_profile,
                        ),
                    }
                )

    manifest_path = output_root / "calibration_manifest.json"
    with manifest_path.open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, ensure_ascii=False)

    print(f"Manifesto de calibração: {manifest_path}")
    print(f"Profile: {args.profile}")
    print(f"Strategy: {args.strategy}")
    print(f"Scenarios: {', '.join(scenarios)}")
    print(f"Runs planejados: {len(manifest['runs'])}")

    for run in manifest["runs"]:
        print(
            "[plan] threshold={threshold} seed={seed} scenario={scenario} profile={profile}".format(
                threshold=run["threshold"],
                seed=run["seed"],
                scenario=run["scenario"],
                profile=run["resolved_profile"],
            )
        )
        if args.plan_only:
            continue
        output_dir = Path(run["output_dir"])
        if training_complete(output_dir, run["scenario"], subset_sizes):
            print(
                "[skip] threshold={threshold} seed={seed} scenario={scenario} já concluído".format(
                    threshold=run["threshold"],
                    seed=run["seed"],
                    scenario=run["scenario"],
                )
            )
            continue
        run_command(run["command"])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
