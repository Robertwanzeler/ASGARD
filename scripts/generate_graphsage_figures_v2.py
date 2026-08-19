#!/usr/bin/env python3
"""
Generate GraphSAGE comparison figures from a completed training run.

This version is closer to conflitos:

- reconstruction F1 vs epochs
- reconstruction F1 vs threshold
- direct-edge recovery F1 vs epochs
- direct-edge recovery F1 vs threshold
- indirect-edge recovery F1 vs epochs
- indirect-edge recovery F1 vs threshold
- implicit-edge recovery F1 vs epochs
- implicit-edge recovery F1 vs threshold

It also keeps a direct comparison between GraphSAGE and the current heuristic
support metric for each scenario.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib
import random

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import torch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXPERIMENT_ROOT = PROJECT_ROOT / "runs" / "experimentos_conflitos"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from training.train_graphsage_conflicts import (  # noqa: E402
    GraphSAGEReconstructor,
    TrainingCase,
    build_conflict_type_target_adjacency,
    compute_metrics,
    load_case_tensors,
)


SCENARIO_TITLES = {
    "baseline_saude": "Baseline",
    "app1_throughput": "App1 Throughput",
    "app1_latencia": "App1 Latency",
    "app2_degradado_leve": "App2 Light",
    "app2_degradado_critico": "App2 Critical",
    "conflito_implicito": "Implicit",
    "recuperacao": "Recovery",
}

SCENARIO_ORDER = [
    "baseline_saude",
    "app1_throughput",
    "app1_latencia",
    "app2_degradado_leve",
    "app2_degradado_critico",
    "conflito_implicito",
    "recuperacao",
]

SUBSET_ORDER = [50, 150, 450]
THRESHOLD_SWEEP = [0.0, 0.2, 0.5, 0.9]
PAPER_THRESHOLD_CURVES = [
    ("No Threshold", 0.0),
    ("Threshold 0.1", 0.1),
    ("Threshold 0.3", 0.3),
    ("Threshold 0.5", 0.5),
]
FIGSIZE = (10.5, 6.2)
DEFAULT_RANDOM_BASELINE_TRIALS = 256
DEFAULT_RANDOM_SEED = 42
CONFLICT_TYPE_ORDER = ("direct", "indirect", "implicit")
CONFLICT_TYPE_SEED_OFFSET = {"direct": 0, "indirect": 500, "implicit": 1000}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate GraphSAGE comparison figures from a completed training run."
    )
    parser.add_argument(
        "--experiment-dir",
        help="completed experiment directory; defaults to the latest one under runs/experimentos_conflitos",
    )
    parser.add_argument(
        "--training-dir",
        help="GraphSAGE training directory; defaults to <experiment>/graphsage_training",
    )
    parser.add_argument(
        "--scenario",
        action="append",
        dest="scenarios",
        default=[],
        help="scenario slug to render; may be passed multiple times",
    )
    parser.add_argument(
        "--output-dir",
        help="directory where PNG/SVG figures will be written; defaults to <experiment>/graphsage_figures_v2",
    )
    parser.add_argument(
        "--compare-only",
        action="store_true",
        help="generate only the comparison F1 curves and skip extra baseline bar charts",
    )
    parser.add_argument(
        "--paper-layout",
        action="store_true",
        help="use paper-style layout: dataset-size curves vs epochs plus threshold curves vs epochs",
    )
    parser.add_argument(
        "--random-baseline-trials",
        type=int,
        default=DEFAULT_RANDOM_BASELINE_TRIALS,
        help="number of Monte Carlo trials for the comparison random baseline",
    )
    parser.add_argument(
        "--random-seed",
        type=int,
        default=DEFAULT_RANDOM_SEED,
        help="base seed used for reproducible random-baseline sampling",
    )
    return parser.parse_args()


def latest_experiment_dir(root: Path) -> Path:
    candidates = sorted(root.glob("*/experiment_report.json"))
    if not candidates:
        raise FileNotFoundError(f"no completed experiment_report.json found under {root}")
    return candidates[-1].parent


def load_json(path: Path) -> dict:
    with path.open() as f:
        return json.load(f)


def read_csv_rows(path: Path) -> list[dict]:
    with path.open() as f:
        return list(csv.DictReader(f))


def ensure_output_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def save_figure(fig: plt.Figure, output_dir: Path, stem: str) -> dict:
    png_path = output_dir / f"{stem}.png"
    svg_path = output_dir / f"{stem}.svg"
    fig.savefig(png_path, dpi=180, bbox_inches="tight")
    fig.savefig(svg_path, bbox_inches="tight")
    plt.close(fig)
    return {"png": str(png_path), "svg": str(svg_path)}


def scenario_label(slug: str) -> str:
    return SCENARIO_TITLES.get(slug, slug)


def load_training_cases(training_dir: Path) -> dict[str, dict[int, dict]]:
    aggregate = load_json(training_dir / "aggregate_report.json")
    cases: dict[str, dict[int, dict]] = defaultdict(dict)
    for case in aggregate.get("cases", []):
        scenario = case["scenario"]
        subset = int(case["subset_size"])
        history = read_csv_rows(Path(case["output_dir"]) / "history.csv")
        for row in history:
            for field in ("epoch", "tp", "fp", "fn", "tn", "predicted_edges", "target_edges"):
                row[field] = int(row[field])
            for field in ("loss", "precision", "recall", "f1", "threshold"):
                row[field] = float(row[field])
        cases[scenario][subset] = {
            "summary": case,
            "history": history,
        }
    return cases


def filter_cases(
    cases: dict[str, dict[int, dict]],
    scenarios: list[str],
) -> dict[str, dict[int, dict]]:
    if not scenarios:
        return cases
    missing = [slug for slug in scenarios if slug not in cases]
    if missing:
        raise SystemExit(f"requested scenarios not found in training dir: {', '.join(missing)}")
    return {slug: cases[slug] for slug in scenarios}


def load_heuristic_subset_metrics(experiment_dir: Path) -> dict[str, dict[int, float]]:
    experiment_report = load_json(experiment_dir / "experiment_report.json")
    metrics: dict[str, dict[int, float]] = defaultdict(dict)
    for scenario_report in experiment_report.get("scenario_reports", []):
        scenario = scenario_report["scenario"]
        for subset in scenario_report.get("subsets", []):
            requested = int(subset.get("requested_rows", 0) or 0)
            summary = subset.get("summary", {}) or {}
            baseline_edges = int(summary.get("baseline_edges", 0) or 0)
            confirmed = int(summary.get("confirmed_by_data", 0) or 0)
            metrics[scenario][requested] = confirmed / max(baseline_edges, 1)
    return metrics


def build_case_from_summary(summary: dict) -> TrainingCase:
    graph_path = Path(summary["graph_path"])
    return TrainingCase(
        scenario=summary["scenario"],
        subset_size=int(summary["subset_size"]),
        dataset_path=Path(summary["dataset_path"]),
        graph_path=graph_path,
        scenario_dir=graph_path.parents[2],
        output_dir=Path(summary["output_dir"]),
    )


def load_model_logits(case_summary: dict, checkpoint_path: Path) -> tuple[dict, list[str], torch.Tensor, torch.Tensor]:
    checkpoint = torch.load(checkpoint_path, map_location="cpu")
    case = build_case_from_summary(case_summary)
    graph, rows, node_ids, features, support, target = load_case_tensors(case)

    model = GraphSAGEReconstructor(
        in_dim=int(checkpoint["feature_dim"]),
        hidden_dim=int(checkpoint["hidden_dim"]),
        embed_dim=int(checkpoint["embed_dim"]),
        dropout=float(checkpoint.get("dropout", 0.10)),
    )
    model.load_state_dict(checkpoint["state_dict"])
    model.eval()
    with torch.no_grad():
        _, logits = model(features, support)
    return graph, node_ids, logits.cpu(), target.cpu()


def compute_type_metrics(
    graph: dict,
    node_ids: list[str],
    logits: torch.Tensor,
    conflict_type: str,
    threshold: float,
) -> dict:
    node_index = {node_id: idx for idx, node_id in enumerate(node_ids)}
    target = build_conflict_type_target_adjacency(graph, node_ids, node_index, conflict_type)
    return compute_metrics(logits, target, threshold)


def line_plot_series(
    series_map: dict[str, list[tuple[float, float]]],
    *,
    title: str,
    xlabel: str,
    ylabel: str,
    output_dir: Path,
    stem: str,
    ylim: tuple[float, float] | None = None,
) -> dict:
    fig, ax = plt.subplots(figsize=FIGSIZE)
    for label, points in series_map.items():
        if not points:
            continue
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        ax.plot(xs, ys, marker="o", linewidth=1.8, markersize=4, label=label)
    ax.set_title(title)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.grid(True, linestyle="--", alpha=0.35)
    if ylim is not None:
        ax.set_ylim(*ylim)
    ax.legend(loc="best", fontsize=8)
    return save_figure(fig, output_dir, stem)


def plot_dataset_size_vs_epochs(
    scenario: str,
    metric_label: str,
    case_map: dict[int, dict],
    *,
    output_dir: Path,
    stem: str,
    random_points: list[tuple[float, float]] | None = None,
) -> dict:
    series_map = {}
    if random_points:
        series_map["Random"] = random_points
    for subset in SUBSET_ORDER:
        case = case_map.get(subset)
        if not case:
            continue
        series_map[f"Dataset size = {subset} samples"] = [
            (row["epoch"], row["f1"]) for row in case["history"]
        ]
    return line_plot_series(
        series_map,
        title=f"F1 {metric_label} vs Epochs for Threshold=0.5",
        xlabel="# Epochs",
        ylabel="F1 Score",
        output_dir=output_dir,
        stem=stem,
        ylim=(0.0, 1.05),
    )


def plot_threshold_vs_epochs(
    points_by_threshold: dict[str, list[tuple[float, float]]],
    metric_label: str,
    *,
    output_dir: Path,
    stem: str,
) -> dict:
    return line_plot_series(
        points_by_threshold,
        title=f"F1 {metric_label} vs Epochs for Dataset Size=450",
        xlabel="# Epochs",
        ylabel="F1 Score",
        output_dir=output_dir,
        stem=stem,
        ylim=(0.0, 1.05),
    )


def mask_without_diagonal(size: int) -> torch.Tensor:
    return ~torch.eye(size, dtype=torch.bool)


def random_binary_matrix_like(
    target: torch.Tensor,
    edge_count: int,
    *,
    seed: int,
) -> torch.Tensor:
    size = target.shape[0]
    mask = mask_without_diagonal(size)
    flat_target = torch.zeros((size * size,), dtype=torch.float32)
    allowed = torch.nonzero(mask.reshape(-1), as_tuple=False).squeeze(1).tolist()
    edge_count = max(0, min(edge_count, len(allowed)))
    rng = random.Random(seed)
    for index in rng.sample(allowed, edge_count):
        flat_target[index] = 1.0
    return flat_target.reshape(size, size)


def average_metrics(metrics_list: list[dict], threshold: float) -> dict:
    if not metrics_list:
        return {
            "threshold": threshold,
            "tp": 0.0,
            "fp": 0.0,
            "fn": 0.0,
            "tn": 0.0,
            "precision": 0.0,
            "recall": 0.0,
            "f1": 0.0,
            "predicted_edges": 0.0,
            "target_edges": 0.0,
        }
    numeric_keys = ("tp", "fp", "fn", "tn", "precision", "recall", "f1", "predicted_edges", "target_edges")
    averaged = {"threshold": threshold}
    for key in numeric_keys:
        averaged[key] = round(sum(float(item[key]) for item in metrics_list) / len(metrics_list), 6)
    return averaged


def random_baseline_at_threshold(
    target: torch.Tensor,
    threshold: float,
    *,
    trials: int,
    seed: int,
) -> dict:
    mask = mask_without_diagonal(target.shape[0])
    possible_edges = int(mask.sum().item())
    if threshold <= 0.0:
        edge_count = possible_edges
    elif threshold >= 1.0:
        edge_count = 0
    else:
        edge_count = int(round((1.0 - threshold) * possible_edges))
    metrics_list = []
    for trial in range(trials):
        pred = random_binary_matrix_like(target, edge_count, seed=seed + trial)
        metrics_list.append(compute_metrics(torch.logit(pred.clamp(1e-6, 1.0 - 1e-6)), target, 0.5))
    return average_metrics(metrics_list, threshold)


def random_baseline_fixed_edge_count(
    target: torch.Tensor,
    *,
    trials: int,
    seed: int,
) -> dict:
    mask = mask_without_diagonal(target.shape[0])
    edge_count = int(target[mask].sum().item())
    metrics_list = []
    for trial in range(trials):
        pred = random_binary_matrix_like(target, edge_count, seed=seed + trial)
        metrics_list.append(compute_metrics(torch.logit(pred.clamp(1e-6, 1.0 - 1e-6)), target, 0.5))
    return average_metrics(metrics_list, 0.5)


def random_type_baseline_fixed_edge_count(
    graph: dict,
    node_ids: list[str],
    conflict_type: str,
    *,
    trials: int,
    seed: int,
) -> dict:
    node_index = {node_id: idx for idx, node_id in enumerate(node_ids)}
    target = build_conflict_type_target_adjacency(graph, node_ids, node_index, conflict_type)
    return random_baseline_fixed_edge_count(target, trials=trials, seed=seed)


def random_type_baseline_at_threshold(
    graph: dict,
    node_ids: list[str],
    conflict_type: str,
    threshold: float,
    *,
    trials: int,
    seed: int,
) -> dict:
    node_index = {node_id: idx for idx, node_id in enumerate(node_ids)}
    target = build_conflict_type_target_adjacency(graph, node_ids, node_index, conflict_type)
    return random_baseline_at_threshold(target, threshold, trials=trials, seed=seed)


def grouped_bar_compare_heuristic(
    cases: dict[str, dict[int, dict]],
    heuristic: dict[str, dict[int, float]],
    *,
    scenario: str,
    output_dir: Path,
    stem: str,
) -> dict:
    fig, ax = plt.subplots(figsize=(9.8, 5.8))
    categories = [str(size) for size in SUBSET_ORDER if size in cases.get(scenario, {})]
    x_positions = list(range(len(categories)))
    width = 0.34

    graphsage = []
    heuristic_values = []
    for size_text in categories:
        subset = int(size_text)
        graphsage.append(float(cases[scenario][subset]["summary"]["best_metrics"]["f1"]))
        heuristic_values.append(float(heuristic.get(scenario, {}).get(subset, 0.0)))

    ax.bar([x - width / 2 for x in x_positions], graphsage, width=width, label="GraphSAGE F1")
    ax.bar([x + width / 2 for x in x_positions], heuristic_values, width=width, label="Heuristic support")
    ax.set_title(f"{scenario_label(scenario)}: GraphSAGE vs Heuristic")
    ax.set_xlabel("Dataset size")
    ax.set_ylabel("Score")
    ax.set_xticks(x_positions)
    ax.set_xticklabels(categories)
    ax.set_ylim(0.0, 1.05)
    ax.grid(True, axis="y", linestyle="--", alpha=0.35)
    ax.legend(loc="best", fontsize=8)
    return save_figure(fig, output_dir, stem)


def grouped_bar_compare_baselines(
    cases: dict[str, dict[int, dict]],
    heuristic: dict[str, dict[int, float]],
    random_baseline: dict[str, dict[int, float]],
    *,
    scenario: str,
    output_dir: Path,
    stem: str,
) -> dict:
    fig, ax = plt.subplots(figsize=(10.4, 5.8))
    categories = [str(size) for size in SUBSET_ORDER if size in cases.get(scenario, {})]
    x_positions = list(range(len(categories)))
    width = 0.24

    graphsage = []
    heuristic_values = []
    random_values = []
    for size_text in categories:
        subset = int(size_text)
        graphsage.append(float(cases[scenario][subset]["summary"]["best_metrics"]["f1"]))
        heuristic_values.append(float(heuristic.get(scenario, {}).get(subset, 0.0)))
        random_values.append(float(random_baseline.get(scenario, {}).get(subset, 0.0)))

    ax.bar([x - width for x in x_positions], graphsage, width=width, label="GraphSAGE F1")
    ax.bar(x_positions, heuristic_values, width=width, label="Heuristic support")
    ax.bar([x + width for x in x_positions], random_values, width=width, label="Random baseline F1")
    ax.set_title(f"{scenario_label(scenario)}: GraphSAGE vs Heuristic vs Random")
    ax.set_xlabel("Dataset size")
    ax.set_ylabel("Score")
    ax.set_xticks(x_positions)
    ax.set_xticklabels(categories)
    ax.set_ylim(0.0, 1.05)
    ax.grid(True, axis="y", linestyle="--", alpha=0.35)
    ax.legend(loc="best", fontsize=8)
    return save_figure(fig, output_dir, stem)


def write_summary(output_dir: Path, payload: dict) -> None:
    path = output_dir / "graphsage_figures_summary.json"
    with path.open("w") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)


def main() -> int:
    args = parse_args()
    experiment_dir = Path(args.experiment_dir) if args.experiment_dir else latest_experiment_dir(DEFAULT_EXPERIMENT_ROOT)
    training_dir = Path(args.training_dir) if args.training_dir else experiment_dir / "graphsage_training"
    output_dir = Path(args.output_dir) if args.output_dir else experiment_dir / "graphsage_figures_v2"
    ensure_output_dir(output_dir)

    cases = filter_cases(load_training_cases(training_dir), args.scenarios)
    heuristic = load_heuristic_subset_metrics(experiment_dir)
    random_baseline_summary: dict[str, dict[int, float]] = defaultdict(dict)
    figures = {}

    for scenario in [slug for slug in SCENARIO_ORDER if slug in cases]:
        case_map = cases[scenario]
        scenario_random_epoch_lines = {}

        reconstruction_epochs = {}
        random_reconstruction_points = None
        for subset in SUBSET_ORDER:
            case = case_map.get(subset)
            if not case:
                continue
            reconstruction_epochs[f"{subset} samples"] = [
                (row["epoch"], row["f1"]) for row in case["history"]
            ]
            _, _, _, _, _, target = load_case_tensors(build_case_from_summary(case["summary"]))
            random_metrics = random_baseline_fixed_edge_count(
                target,
                trials=args.random_baseline_trials,
                seed=args.random_seed + subset,
            )
            random_baseline_summary[scenario][subset] = float(random_metrics["f1"])
            if args.paper_layout:
                random_reconstruction_points = [
                    (row["epoch"], random_metrics["f1"]) for row in case["history"]
                ]
            elif not args.compare_only:
                scenario_random_epoch_lines[f"Random {subset}"] = [
                    (row["epoch"], random_metrics["f1"]) for row in case["history"]
                ]
        if args.paper_layout:
            figures[f"{scenario}_reconstruction_f1_vs_epochs"] = plot_dataset_size_vs_epochs(
                scenario,
                "Reconstruction",
                case_map,
                output_dir=output_dir,
                stem=f"{scenario}_reconstruction_f1_vs_epochs",
                random_points=random_reconstruction_points,
            )
        else:
            reconstruction_epochs.update(scenario_random_epoch_lines)
            figures[f"{scenario}_reconstruction_f1_vs_epochs"] = line_plot_series(
                reconstruction_epochs,
                title=f"{scenario_label(scenario)}: Reconstruction F1 vs Epochs",
                xlabel="Epochs",
                ylabel="F1 score",
                output_dir=output_dir,
                stem=f"{scenario}_reconstruction_f1_vs_epochs",
                ylim=(0.0, 1.05),
            )

        if 450 in case_map:
            case450 = case_map[450]
            best_model_path = Path(case450["summary"]["output_dir"]) / "best_model.pt"
            graph, node_ids, best_logits, target = load_model_logits(case450["summary"], best_model_path)
            if args.paper_layout:
                threshold_epoch_points = {label: [] for label, _ in PAPER_THRESHOLD_CURVES}
                checkpoint_dir = Path(case450["summary"]["output_dir"]) / "checkpoints"
                for row in case450["history"]:
                    checkpoint_path = checkpoint_dir / f"epoch_{row['epoch']}.pt"
                    if not checkpoint_path.exists():
                        continue
                    _, _, epoch_logits, _ = load_model_logits(case450["summary"], checkpoint_path)
                    for label, threshold in PAPER_THRESHOLD_CURVES:
                        threshold_epoch_points[label].append(
                            (row["epoch"], compute_metrics(epoch_logits, target, threshold)["f1"])
                        )
                figures[f"{scenario}_reconstruction_f1_vs_threshold"] = plot_threshold_vs_epochs(
                    threshold_epoch_points,
                    "Reconstruction",
                    output_dir=output_dir,
                    stem=f"{scenario}_reconstruction_f1_vs_threshold",
                )
            else:
                reconstruction_thresholds = {
                    f"{scenario_label(scenario)} 450": [
                        (threshold, compute_metrics(best_logits, target, threshold)["f1"])
                        for threshold in THRESHOLD_SWEEP
                    ]
                }
                if not args.compare_only:
                    reconstruction_thresholds["Random baseline"] = [
                        (
                            threshold,
                            random_baseline_at_threshold(
                                target,
                                threshold,
                                trials=args.random_baseline_trials,
                                seed=args.random_seed + 450,
                            )["f1"],
                        )
                        for threshold in THRESHOLD_SWEEP
                    ]
                figures[f"{scenario}_reconstruction_f1_vs_threshold"] = line_plot_series(
                    reconstruction_thresholds,
                    title=f"{scenario_label(scenario)}: Reconstruction F1 vs Threshold",
                    xlabel="Threshold",
                    ylabel="F1 score",
                    output_dir=output_dir,
                    stem=f"{scenario}_reconstruction_f1_vs_threshold",
                    ylim=(0.0, 1.05),
                )

            for conflict_type in CONFLICT_TYPE_ORDER:
                if conflict_type not in (graph.get("stats", {}).get("by_conflict_type", {}) or {}):
                    continue

                type_epochs = {}
                random_type_points = None
                for subset in SUBSET_ORDER:
                    case = case_map.get(subset)
                    if not case:
                        continue
                    checkpoint_dir = Path(case["summary"]["output_dir"]) / "checkpoints"
                    points = []
                    for row in case["history"]:
                        checkpoint_path = checkpoint_dir / f"epoch_{row['epoch']}.pt"
                        if not checkpoint_path.exists():
                            continue
                        epoch_graph, epoch_node_ids, epoch_logits, _ = load_model_logits(case["summary"], checkpoint_path)
                        metrics = compute_type_metrics(epoch_graph, epoch_node_ids, epoch_logits, conflict_type, 0.5)
                        points.append((row["epoch"], metrics["f1"]))
                    if points:
                        if args.paper_layout:
                            type_epochs[f"Dataset size = {subset} samples"] = points
                        else:
                            type_epochs[f"{subset} samples"] = points
                        if args.paper_layout:
                            baseline_metrics = random_type_baseline_fixed_edge_count(
                                epoch_graph,
                                epoch_node_ids,
                                conflict_type,
                                trials=args.random_baseline_trials,
                                seed=args.random_seed + subset + CONFLICT_TYPE_SEED_OFFSET.get(conflict_type, 0),
                            )
                            random_type_points = [
                                (row["epoch"], baseline_metrics["f1"]) for row in case["history"]
                            ]
                        elif not args.compare_only:
                            baseline_metrics = random_type_baseline_fixed_edge_count(
                                epoch_graph,
                                epoch_node_ids,
                                conflict_type,
                                trials=args.random_baseline_trials,
                                seed=args.random_seed + subset + CONFLICT_TYPE_SEED_OFFSET.get(conflict_type, 0),
                            )
                            type_epochs[f"Random {subset}"] = [
                                (row["epoch"], baseline_metrics["f1"]) for row in case["history"]
                            ]

                if type_epochs:
                    if args.paper_layout and random_type_points:
                        type_epochs = {"Random": random_type_points, **type_epochs}
                    figures[f"{scenario}_{conflict_type}_f1_vs_epochs"] = line_plot_series(
                        type_epochs,
                        title=(
                            f"F1 {conflict_type.title()} vs Epochs for Threshold=0.5"
                            if args.paper_layout
                            else f"{scenario_label(scenario)}: {conflict_type.title()} F1 vs Epochs"
                        ),
                        xlabel="# Epochs" if args.paper_layout else "Epochs",
                        ylabel="F1 Score" if args.paper_layout else "F1 score",
                        output_dir=output_dir,
                        stem=f"{scenario}_{conflict_type}_f1_vs_epochs",
                        ylim=(0.0, 1.05),
                    )

                if args.paper_layout:
                    threshold_epoch_points = {label: [] for label, _ in PAPER_THRESHOLD_CURVES}
                    checkpoint_dir = Path(case450["summary"]["output_dir"]) / "checkpoints"
                    for row in case450["history"]:
                        checkpoint_path = checkpoint_dir / f"epoch_{row['epoch']}.pt"
                        if not checkpoint_path.exists():
                            continue
                        epoch_graph, epoch_node_ids, epoch_logits, _ = load_model_logits(case450["summary"], checkpoint_path)
                        for label, threshold in PAPER_THRESHOLD_CURVES:
                            threshold_epoch_points[label].append(
                                (
                                    row["epoch"],
                                    compute_type_metrics(epoch_graph, epoch_node_ids, epoch_logits, conflict_type, threshold)["f1"],
                                )
                            )
                    figures[f"{scenario}_{conflict_type}_f1_vs_threshold"] = plot_threshold_vs_epochs(
                        threshold_epoch_points,
                        conflict_type.title(),
                        output_dir=output_dir,
                        stem=f"{scenario}_{conflict_type}_f1_vs_threshold",
                    )
                else:
                    type_thresholds = {
                        f"{scenario_label(scenario)} 450": [
                            (
                                threshold,
                                compute_type_metrics(graph, node_ids, best_logits, conflict_type, threshold)["f1"],
                            )
                            for threshold in THRESHOLD_SWEEP
                        ]
                    }
                    if not args.compare_only:
                        type_thresholds["Random baseline"] = [
                            (
                                threshold,
                                random_type_baseline_at_threshold(
                                    graph,
                                    node_ids,
                                    conflict_type,
                                    threshold,
                                    trials=args.random_baseline_trials,
                                    seed=args.random_seed + 450 + CONFLICT_TYPE_SEED_OFFSET.get(conflict_type, 0),
                                )["f1"],
                            )
                            for threshold in THRESHOLD_SWEEP
                        ]
                    figures[f"{scenario}_{conflict_type}_f1_vs_threshold"] = line_plot_series(
                        type_thresholds,
                        title=f"{scenario_label(scenario)}: {conflict_type.title()} F1 vs Threshold",
                        xlabel="Threshold",
                        ylabel="F1 score",
                        output_dir=output_dir,
                        stem=f"{scenario}_{conflict_type}_f1_vs_threshold",
                        ylim=(0.0, 1.05),
                    )

        if not args.compare_only:
            figures[f"{scenario}_graphsage_vs_heuristic"] = grouped_bar_compare_heuristic(
                cases,
                heuristic,
                scenario=scenario,
                output_dir=output_dir,
                stem=f"{scenario}_graphsage_vs_heuristic",
            )
            figures[f"{scenario}_graphsage_vs_baselines"] = grouped_bar_compare_baselines(
                cases,
                heuristic,
                random_baseline_summary,
                scenario=scenario,
                output_dir=output_dir,
                stem=f"{scenario}_graphsage_vs_baselines",
            )

    write_summary(
        output_dir,
        {
            "experiment_dir": str(experiment_dir),
            "training_dir": str(training_dir),
            "scenarios": args.scenarios,
            "compare_only": args.compare_only,
            "threshold_sweep": THRESHOLD_SWEEP,
            "random_baseline_trials": args.random_baseline_trials,
            "random_seed": args.random_seed,
            "note": (
                "These figures approximate conflitos more closely: reconstruction plus direct-, indirect-, "
                "and implicit-edge F1 curves vs epochs and vs threshold. Random-baseline curves are "
                "Monte Carlo references added only in post-processing, so they do not alter the collected "
                "scenarios or GraphSAGE training artifacts. Conflict-type figures are computed over "
                "edge subsets whose baseline graph edges carry those conflict_type tags."
            ),
            "figures": figures,
            "random_baseline_f1_by_scenario_subset": {
                scenario: {str(subset): score for subset, score in subset_scores.items()}
                for scenario, subset_scores in random_baseline_summary.items()
            },
        },
    )

    print(f"Figures written to: {output_dir}")
    for key, paths in figures.items():
        if isinstance(paths, dict) and "png" in paths:
            print(f"- {key}: {paths['png']}")
    print(f"- summary: {output_dir / 'graphsage_figures_summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
