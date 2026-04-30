#!/usr/bin/env python3
"""
Experimental runner for the isolated article00 track.

This runner stays outside GreenRAN and orchestrates:

- dataset generation;
- temporal GraphSAGE training;
- optional multi-seed execution;
- aggregate reporting;
- optional figure generation.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUNS_ROOT = PROJECT_ROOT / "runs" / "article00"
EXPERIMENT_PYTHON = PROJECT_ROOT / "drlexp" / ".venv" / "bin" / "python"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the isolated article00 pipeline.")
    parser.add_argument("--samples", type=int, default=600)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--seeds", help="optional comma-separated list of seeds; overrides --seed")
    parser.add_argument("--epochs", default="50,100,200,400,600,800,1000")
    parser.add_argument("--thresholds", default="0.2,0.5,0.9")
    parser.add_argument("--selection-threshold", type=float, default=0.5)
    parser.add_argument("--hidden-dim", type=int, default=16)
    parser.add_argument("--embed-dim", type=int, default=16)
    parser.add_argument("--dropout", type=float, default=0.10)
    parser.add_argument("--learning-rate", type=float, default=0.001)
    parser.add_argument("--weight-decay", type=float, default=1e-4)
    parser.add_argument("--temporal-radius", type=int, default=1)
    parser.add_argument("--temporal-decay", type=float, default=1.0)
    parser.add_argument("--fp-penalty-weight", type=float, default=0.0)
    parser.add_argument("--fp-penalty-margin", type=float, default=0.10)
    parser.add_argument("--tp-reward-weight", type=float, default=0.0)
    parser.add_argument("--tp-reward-margin", type=float, default=0.5)
    parser.add_argument("--hard-positive-weight", type=float, default=0.0)
    parser.add_argument("--hard-positive-threshold", type=float, default=0.5)
    parser.add_argument("--hard-positive-focus-kpis", default="")
    parser.add_argument("--hard-negative-weight", type=float, default=0.0)
    parser.add_argument("--hard-negative-threshold", type=float, default=0.5)
    parser.add_argument("--hard-negative-focus-kpis", default="")
    parser.add_argument("--selection-mode", choices=("threshold_f1", "composite"), default="threshold_f1")
    parser.add_argument("--selection-weight-parameter", type=float, default=1.0)
    parser.add_argument("--selection-weight-indirect", type=float, default=1.0)
    parser.add_argument("--selection-weight-implicit", type=float, default=1.0)
    parser.add_argument("--smoothing", type=float, default=0.82)
    parser.add_argument("--skip-figures", action="store_true", help="do not call the figure generator at the end")
    return parser.parse_args()


def run(cmd: list[str]) -> None:
    result = subprocess.run(cmd, cwd=PROJECT_ROOT, text=True, capture_output=True)
    if result.returncode != 0:
        raise SystemExit(f"command failed: {' '.join(cmd)}\n{result.stdout}\n{result.stderr}")
    if result.stdout.strip():
        print(result.stdout.strip())


def experiment_python() -> str:
    return str(EXPERIMENT_PYTHON) if EXPERIMENT_PYTHON.exists() else sys.executable


def parse_seed_list(args: argparse.Namespace) -> list[int]:
    if args.seeds:
        values = sorted({int(piece.strip()) for piece in args.seeds.split(",") if piece.strip()})
        if not values:
            raise SystemExit("no seeds requested")
        return values
    return [args.seed]


def load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def mean_std(values: list[float]) -> tuple[float, float]:
    if not values:
        return 0.0, 0.0
    if len(values) == 1:
        return values[0], 0.0
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    return mean, variance ** 0.5


def aggregate_histories(training_summaries: list[dict]) -> dict:
    grouped: dict[int, dict[str, dict[str, list[float]]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for summary in training_summaries:
        for item in summary.get("history", []):
            epoch = int(item["epoch"])
            for threshold_label, metrics in item.get("threshold_metrics", {}).items():
                grouped[epoch][threshold_label]["parameter_kpi_f1"].append(
                    float(metrics["parameter_kpi_metrics"]["f1"])
                )
                grouped[epoch][threshold_label]["indirect_f1"].append(
                    float(metrics["conflict_metrics"]["indirect"]["f1"])
                )
                grouped[epoch][threshold_label]["implicit_f1"].append(
                    float(metrics["conflict_metrics"]["implicit"]["f1"])
                )

    payload: dict[str, dict[str, dict[str, float]]] = {}
    for epoch in sorted(grouped):
        payload[str(epoch)] = {}
        for threshold_label, metrics in grouped[epoch].items():
            payload[str(epoch)][threshold_label] = {}
            for metric_name, values in metrics.items():
                mean, std = mean_std(values)
                payload[str(epoch)][threshold_label][metric_name] = round(mean, 6)
                payload[str(epoch)][threshold_label][f"{metric_name}_std"] = round(std, 6)
    return payload


def main() -> None:
    args = parse_args()
    seeds = parse_seed_list(args)

    reports_dir = DEFAULT_RUNS_ROOT / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    training_summaries = []
    run_entries = []

    for seed in seeds:
        dataset_dir = DEFAULT_RUNS_ROOT / "datasets" / f"seed_{seed}"
        training_dir = DEFAULT_RUNS_ROOT / "training" / f"seed_{seed}"

        run(
            [
                experiment_python(),
                "scripts/generate_article00_dataset.py",
                "--samples",
                str(args.samples),
                "--seed",
                str(seed),
                "--smoothing",
                str(args.smoothing),
                "--output-dir",
                str(dataset_dir),
            ]
        )
        run(
            [
                experiment_python(),
                "training/train_graphsage_article00.py",
                "--dataset-dir",
                str(dataset_dir),
                "--output-dir",
                str(training_dir),
                "--epochs",
                args.epochs,
                "--thresholds",
                args.thresholds,
                "--selection-threshold",
                str(args.selection_threshold),
                "--hidden-dim",
                str(args.hidden_dim),
                "--embed-dim",
                str(args.embed_dim),
                "--dropout",
                str(args.dropout),
                "--learning-rate",
                str(args.learning_rate),
                "--weight-decay",
                str(args.weight_decay),
                "--temporal-radius",
                str(args.temporal_radius),
                "--temporal-decay",
                str(args.temporal_decay),
                "--fp-penalty-weight",
                str(args.fp_penalty_weight),
                "--fp-penalty-margin",
                str(args.fp_penalty_margin),
                "--tp-reward-weight",
                str(args.tp_reward_weight),
                "--tp-reward-margin",
                str(args.tp_reward_margin),
                "--hard-positive-weight",
                str(args.hard_positive_weight),
                "--hard-positive-threshold",
                str(args.hard_positive_threshold),
                "--hard-positive-focus-kpis",
                str(args.hard_positive_focus_kpis),
                "--hard-negative-weight",
                str(args.hard_negative_weight),
                "--hard-negative-threshold",
                str(args.hard_negative_threshold),
                "--hard-negative-focus-kpis",
                str(args.hard_negative_focus_kpis),
                "--selection-mode",
                str(args.selection_mode),
                "--selection-weight-parameter",
                str(args.selection_weight_parameter),
                "--selection-weight-indirect",
                str(args.selection_weight_indirect),
                "--selection-weight-implicit",
                str(args.selection_weight_implicit),
                "--seed",
                str(seed),
            ]
        )
        training_summary = load_json(training_dir / "training_summary.json")
        training_summaries.append(training_summary)
        run_entries.append(
            {
                "seed": seed,
                "dataset_dir": str(dataset_dir),
                "training_dir": str(training_dir),
                "best_epoch": training_summary.get("best_epoch"),
                "best_parameter_kpi_f1": training_summary.get("best_parameter_kpi_metrics", {}).get("f1"),
                "best_indirect_f1": training_summary.get("best_conflict_metrics", {}).get("indirect", {}).get("f1"),
                "best_implicit_f1": training_summary.get("best_conflict_metrics", {}).get("implicit", {}).get("f1"),
            }
        )

    manifest_name = "article00_run_seed_{}".format(seeds[0]) if len(seeds) == 1 else "article00_run_multiseed"
    manifest_path = reports_dir / f"{manifest_name}.json"
    aggregate_report_path = reports_dir / "aggregate_report.json"
    with manifest_path.open("w", encoding="utf-8") as handle:
        json.dump(
            {
                "schema": "greenran.article00_run_manifest.v1",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "seeds": seeds,
                "samples": args.samples,
                "epochs": args.epochs,
                "thresholds": args.thresholds,
                "selection_threshold": args.selection_threshold,
                "hidden_dim": args.hidden_dim,
                "embed_dim": args.embed_dim,
                "dropout": args.dropout,
                "learning_rate": args.learning_rate,
                "weight_decay": args.weight_decay,
                "temporal_radius": args.temporal_radius,
                "temporal_decay": args.temporal_decay,
                "fp_penalty_weight": args.fp_penalty_weight,
                "fp_penalty_margin": args.fp_penalty_margin,
                "tp_reward_weight": args.tp_reward_weight,
                "tp_reward_margin": args.tp_reward_margin,
                "hard_positive_weight": args.hard_positive_weight,
                "hard_positive_threshold": args.hard_positive_threshold,
                "hard_positive_focus_kpis": args.hard_positive_focus_kpis,
                "hard_negative_weight": args.hard_negative_weight,
                "hard_negative_threshold": args.hard_negative_threshold,
                "hard_negative_focus_kpis": args.hard_negative_focus_kpis,
                "selection_mode": args.selection_mode,
                "selection_weight_parameter": args.selection_weight_parameter,
                "selection_weight_indirect": args.selection_weight_indirect,
                "selection_weight_implicit": args.selection_weight_implicit,
                "smoothing": args.smoothing,
                "runs": run_entries,
                "status": "experimental_temporal_graphsage",
            },
            handle,
            indent=2,
        )

    with aggregate_report_path.open("w", encoding="utf-8") as handle:
        json.dump(
            {
                "schema": "greenran.article00_aggregate_report.v1",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "seeds": seeds,
                "samples": args.samples,
                "epochs": args.epochs,
                "thresholds": args.thresholds,
                "selection_threshold": args.selection_threshold,
                "temporal_radius": args.temporal_radius,
                "temporal_decay": args.temporal_decay,
                "fp_penalty_weight": args.fp_penalty_weight,
                "fp_penalty_margin": args.fp_penalty_margin,
                "tp_reward_weight": args.tp_reward_weight,
                "tp_reward_margin": args.tp_reward_margin,
                "hard_positive_weight": args.hard_positive_weight,
                "hard_positive_threshold": args.hard_positive_threshold,
                "hard_positive_focus_kpis": args.hard_positive_focus_kpis,
                "hard_negative_weight": args.hard_negative_weight,
                "hard_negative_threshold": args.hard_negative_threshold,
                "hard_negative_focus_kpis": args.hard_negative_focus_kpis,
                "selection_mode": args.selection_mode,
                "selection_weight_parameter": args.selection_weight_parameter,
                "selection_weight_indirect": args.selection_weight_indirect,
                "selection_weight_implicit": args.selection_weight_implicit,
                "history_aggregate": aggregate_histories(training_summaries),
                "runs": run_entries,
                "status": "experimental_temporal_graphsage",
            },
            handle,
            indent=2,
        )

    if not args.skip_figures:
        run(
            [
                experiment_python(),
                "scripts/generate_graphsage_article00_figures.py",
                "--training-root",
                str(DEFAULT_RUNS_ROOT / "training"),
                "--output-dir",
                str(DEFAULT_RUNS_ROOT / "figures"),
            ]
        )

    print(f"Article00 run manifest written to: {manifest_path}")
    print(f"Article00 aggregate report written to: {aggregate_report_path}")


if __name__ == "__main__":
    main()
