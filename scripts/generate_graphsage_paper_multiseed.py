#!/usr/bin/env python3
"""
Generate paper-like GraphSAGE comparison figures from multiple training seeds.

This script aggregates multiple training directories that share the same
scenario/subset structure and produces:

- reconstruction F1 vs epochs for threshold 0.5
- reconstruction F1 vs epochs for dataset size 450 with threshold curves
- direct F1 vs epochs for threshold 0.5
- direct F1 vs epochs for dataset size 450 with threshold curves
- indirect F1 vs epochs for threshold 0.5
- indirect F1 vs epochs for dataset size 450 with threshold curves
- implicit F1 vs epochs for threshold 0.5
- implicit F1 vs epochs for dataset size 450 with threshold curves

Each curve is rendered as mean +/- standard deviation across seeds.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib

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
    build_feature_matrix,
    build_support_matrix,
    build_target_adjacency,
    build_conflict_type_target_adjacency,
    compute_metrics,
    load_case_tensors,
    normalize_features,
    subset_rows_with_boundaries,
)


SCENARIO_TITLES = {
    "conflito_implicito": "Implicit",
    "recuperacao": "Recovery",
}
SUBSET_ORDER = [50, 150, 450]
INDIRECT_MIN_SUPPORT = 10
PAPER_THRESHOLD_CURVES = [
    ("No Threshold", 0.0),
    ("Threshold 0.2", 0.2),
    ("Threshold 0.5", 0.5),
    ("Threshold 0.9", 0.9),
]
DATASET_CURVE_STYLES = {
    "Random": {"color": "black", "marker": "o", "linestyle": "--"},
    "Dataset size: 50 samples": {"color": "#d62728", "marker": "o", "linestyle": "-"},
    "Dataset size: 150 samples": {"color": "#2ca02c", "marker": "o", "linestyle": "-"},
    "Dataset size: 450 samples": {"color": "#1f77b4", "marker": "o", "linestyle": "-"},
}
THRESHOLD_CURVE_STYLES = {
    "No Threshold": {"color": "#ff4dd2", "marker": "o", "linestyle": "-"},
    "Threshold 0.2": {"color": "#ff8c1a", "marker": "o", "linestyle": "-"},
    "Threshold 0.5": {"color": "#8c564b", "marker": "o", "linestyle": "-"},
    "Threshold 0.9": {"color": "#d62728", "marker": "o", "linestyle": "-"},
}
FIGSIZE = (4.35, 3.25)
DEFAULT_RANDOM_BASELINE_TRIALS = 256
DEFAULT_RANDOM_SEED = 42
CONFLICT_TYPE_ORDER = ("direct", "indirect", "implicit")
CONFLICT_TYPE_SEED_OFFSET = {"direct": 0, "indirect": 500, "implicit": 1000}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate paper-like GraphSAGE figures with mean/std across multiple seeds."
    )
    parser.add_argument(
        "--experiment-dir",
        help="completed experiment directory; defaults to the latest one under runs/experimentos_conflitos",
    )
    parser.add_argument(
        "--training-dir",
        action="append",
        dest="training_dirs",
        default=[],
        help="training directory for one seed; may be passed multiple times",
    )
    parser.add_argument(
        "--scenario",
        required=True,
        help="scenario slug to render",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="directory where PNG/SVG figures will be written",
    )
    parser.add_argument(
        "--random-baseline-trials",
        type=int,
        default=DEFAULT_RANDOM_BASELINE_TRIALS,
        help="number of Monte Carlo trials for the random baseline",
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


def scenario_label(slug: str) -> str:
    return SCENARIO_TITLES.get(slug, slug)


def build_case_from_summary(summary: dict) -> TrainingCase:
    graph_path = Path(summary["graph_path"])
    return TrainingCase(
        scenario=summary["scenario"],
        subset_size=int(summary["subset_size"]),
        dataset_path=Path(summary["dataset_path"]),
        graph_path=graph_path,
        scenario_dir=graph_path.parents[1],
        output_dir=Path(summary["output_dir"]),
    )


def split_rows_from_summary(case_summary: dict, case: TrainingCase) -> tuple[list[dict], list[dict]] | None:
    train_rounds = [int(item) for item in case_summary.get("train_rounds", [])]
    test_rounds = [int(item) for item in case_summary.get("test_rounds", [])]
    if not train_rounds or not test_rounds:
        return None

    subset_rows, _, round_boundaries = subset_rows_with_boundaries(case)
    train_set = set(train_rounds)
    test_set = set(test_rounds)
    train_rows: list[dict] = []
    test_rows: list[dict] = []
    for round_number, start, end in round_boundaries:
        rows = subset_rows[start:end]
        if round_number in train_set:
            train_rows.extend(rows)
        elif round_number in test_set:
            test_rows.extend(rows)
    if not train_rows or not test_rows:
        return None
    return train_rows, test_rows


def load_model_logits(case_summary: dict, checkpoint_path: Path) -> tuple[dict, list[str], torch.Tensor, torch.Tensor]:
    checkpoint = torch.load(checkpoint_path, map_location="cpu")
    case = build_case_from_summary(case_summary)
    graph = load_json(case.graph_path)
    split_rows = split_rows_from_summary(case_summary, case)
    if split_rows is None:
        graph, rows, node_ids, features, support, target = load_case_tensors(case)
    else:
        train_rows, test_rows = split_rows
        node_ids = [node["id"] for node in graph.get("nodes", [])]
        node_index = {node_id: idx for idx, node_id in enumerate(node_ids)}
        train_support = build_support_matrix(train_rows, node_index)
        test_support = build_support_matrix(test_rows, node_index)
        train_raw = build_feature_matrix(graph, train_rows, node_ids, node_index, train_support)
        train_features, feature_mean, feature_std = normalize_features(train_raw)
        test_raw = build_feature_matrix(graph, test_rows, node_ids, node_index, test_support)
        features, _, _ = normalize_features(test_raw, mean=feature_mean, std=feature_std)
        support = test_support
        target = build_target_adjacency(graph, node_ids, node_index)

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
    min_support: int = 1,
) -> dict:
    node_index = {node_id: idx for idx, node_id in enumerate(node_ids)}
    target = build_conflict_type_target_adjacency(
        graph,
        node_ids,
        node_index,
        conflict_type,
        min_support=min_support,
    )
    return compute_metrics(logits, target, threshold)


def metric_graph_path(case_summary: dict) -> Path:
    dataset_path = Path(case_summary["dataset_path"])
    subset_match = dataset_path.stem.split("_")[-1]
    adjacency_path = dataset_path.parent / f"conflict_adjacency_{subset_match}.json"
    if adjacency_path.exists():
        return adjacency_path
    return Path(case_summary["graph_path"])


def type_min_support(conflict_type: str) -> int:
    if conflict_type == "indirect":
        return INDIRECT_MIN_SUPPORT
    return 1


def available_conflict_types(case_summaries: list[dict]) -> list[str]:
    present = set()
    for case_summary in case_summaries:
        metric_graph = load_json(metric_graph_path(case_summary))
        present.update((metric_graph.get("stats", {}).get("by_conflict_type", {}) or {}).keys())
        if not present:
            for edge in metric_graph.get("edges", []):
                present.update((edge.get("conflict_types", {}) or {}).keys())
    return [conflict_type for conflict_type in CONFLICT_TYPE_ORDER if conflict_type in present]


def load_seed_cases(training_dir: Path, scenario: str) -> dict[int, dict]:
    aggregate = load_json(training_dir / "aggregate_report.json")
    cases = {}
    for case in aggregate.get("cases", []):
        if case["scenario"] != scenario:
            continue
        subset = int(case["subset_size"])
        history = read_csv_rows(Path(case["output_dir"]) / "history.csv")
        for row in history:
            row["epoch"] = int(row["epoch"])
            row["f1"] = float(row["f1"])
        cases[subset] = {"summary": case, "history": history}
    if not cases:
        raise SystemExit(f"scenario '{scenario}' not found in {training_dir}")
    return cases


def mask_without_diagonal(size: int) -> torch.Tensor:
    return ~torch.eye(size, dtype=torch.bool)


def random_binary_matrix_like(target: torch.Tensor, edge_count: int, *, seed: int) -> torch.Tensor:
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
    numeric_keys = ("tp", "fp", "fn", "tn", "precision", "recall", "f1", "predicted_edges", "target_edges")
    averaged = {"threshold": threshold}
    for key in numeric_keys:
        averaged[key] = sum(float(item[key]) for item in metrics_list) / max(len(metrics_list), 1)
    return averaged


def random_baseline_fixed_edge_count(target: torch.Tensor, *, trials: int, seed: int) -> dict:
    mask = mask_without_diagonal(target.shape[0])
    edge_count = int(target[mask].sum().item())
    metrics_list = []
    for trial in range(trials):
        pred = random_binary_matrix_like(target, edge_count, seed=seed + trial)
        metrics_list.append(compute_metrics(torch.logit(pred.clamp(1e-6, 1.0 - 1e-6)), target, 0.5))
    return average_metrics(metrics_list, 0.5)


def mean_std(values: list[float]) -> tuple[float, float]:
    if not values:
        return 0.0, 0.0
    mean = sum(values) / len(values)
    var = sum((value - mean) ** 2 for value in values) / len(values)
    return mean, math.sqrt(var)


def aggregate_epoch_series(seed_series: list[list[tuple[int, float]]]) -> list[tuple[int, float, float]]:
    buckets: dict[int, list[float]] = defaultdict(list)
    for series in seed_series:
        for epoch, value in series:
            buckets[int(epoch)].append(float(value))
    aggregated = []
    for epoch in sorted(buckets):
        mean, std = mean_std(buckets[epoch])
        aggregated.append((epoch, mean, std))
    return aggregated


def plot_errorbar_series(
    series_map: dict[str, list[tuple[int, float, float]]],
    *,
    title: str,
    caption: str,
    output_dir: Path,
    stem: str,
    styles: dict[str, dict],
    legend_variant: str,
) -> dict:
    plt.rcParams.update(
        {
            "font.family": "serif",
            "font.size": 7,
            "axes.titlesize": 8,
            "axes.labelsize": 7,
            "legend.fontsize": 4.6,
            "xtick.labelsize": 6,
            "ytick.labelsize": 6,
        }
    )
    fig, ax = plt.subplots(figsize=FIGSIZE)
    for label, points in series_map.items():
        if not points:
            continue
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        yerr = [point[2] for point in points]
        style = styles.get(label, {"color": "black", "marker": "o"})
        ax.errorbar(
            xs,
            ys,
            yerr=yerr,
            label=label,
            color=style["color"],
            marker=style.get("marker", "o"),
            linestyle=style.get("linestyle", "-"),
            linewidth=0.95,
            markersize=2.6,
            capsize=1.6,
            elinewidth=0.65,
        )
    ax.set_title(title)
    ax.set_xlabel("# Epochs")
    ax.set_ylabel("F1 Score")
    ax.set_ylim(0.0, 1.05)
    ax.set_xlim(left=0)
    xticks = sorted({0, *[point[0] for points in series_map.values() for point in points]})
    ax.set_xticks(xticks)
    ax.grid(True, linestyle="--", linewidth=0.45, alpha=0.28)
    for spine in ax.spines.values():
        spine.set_linewidth(0.8)
    if legend_variant == "dataset":
        legend_kwargs = {
            "loc": "center right",
            "bbox_to_anchor": (0.97, 0.53),
            "ncol": 1,
        }
    else:
        legend_kwargs = {
            "loc": "lower right",
            "bbox_to_anchor": (0.97, 0.035),
            "ncol": 1,
        }
    ax.legend(
        frameon=True,
        framealpha=0.95,
        fancybox=False,
        borderpad=0.18,
        labelspacing=0.16,
        columnspacing=0.7,
        handlelength=1.45,
        handletextpad=0.4,
        **legend_kwargs,
    )
    fig.subplots_adjust(bottom=0.27, left=0.12, right=0.985, top=0.88)
    fig.text(0.06, 0.05, caption, ha="left", va="center", fontsize=6.5)
    png_path = output_dir / f"{stem}.png"
    fig.savefig(png_path, dpi=260, bbox_inches="tight")
    plt.close(fig)
    return {"png": str(png_path)}


def build_dataset_series(
    seed_cases_list: list[dict[int, dict]],
    *,
    random_points: list[tuple[int, float, float]] | None = None,
) -> dict[str, list[tuple[int, float, float]]]:
    series_map = {}
    if random_points is not None:
        series_map["Random"] = random_points
    for subset in SUBSET_ORDER:
        seed_series = []
        for cases in seed_cases_list:
            case = cases.get(subset)
            if not case:
                continue
            seed_series.append([(row["epoch"], row["f1"]) for row in case["history"]])
        if seed_series:
            series_map[f"Dataset size: {subset} samples"] = aggregate_epoch_series(seed_series)
    return series_map


def build_threshold_series(
    case_summaries: list[dict],
    metric_kind: str,
    *,
    trials: int,
    random_seed: int,
) -> dict[str, list[tuple[int, float, float]]]:
    threshold_seed_map: dict[str, list[list[tuple[int, float]]]] = defaultdict(list)
    for case_summary in case_summaries:
        checkpoint_dir = Path(case_summary["output_dir"]) / "checkpoints"
        history = read_csv_rows(Path(case_summary["output_dir"]) / "history.csv")
        metric_graph = load_json(metric_graph_path(case_summary))
        min_support = type_min_support(metric_kind) if metric_kind != "reconstruction" else 1
        seed_points = {label: [] for label, _ in PAPER_THRESHOLD_CURVES}
        for row in history:
            epoch = int(row["epoch"])
            checkpoint_path = checkpoint_dir / f"epoch_{epoch}.pt"
            if not checkpoint_path.exists():
                continue
            _, node_ids, epoch_logits, target = load_model_logits(case_summary, checkpoint_path)
            for label, threshold in PAPER_THRESHOLD_CURVES:
                if metric_kind == "reconstruction":
                    value = compute_metrics(epoch_logits, target, threshold)["f1"]
                else:
                    value = compute_type_metrics(
                        metric_graph,
                        node_ids,
                        epoch_logits,
                        metric_kind,
                        threshold,
                        min_support=min_support,
                    )["f1"]
                seed_points[label].append((epoch, value))
        for label, points in seed_points.items():
            threshold_seed_map[label].append(points)
    return {label: aggregate_epoch_series(seed_series) for label, seed_series in threshold_seed_map.items()}


def build_type_dataset_series(
    seed_cases_list: list[dict[int, dict]],
    conflict_type: str,
    *,
    trials: int,
    random_seed: int,
) -> dict[str, list[tuple[int, float, float]]]:
    series_map = {}
    random_seed_series = []
    for subset in SUBSET_ORDER:
        per_seed_points = []
        baseline_seed_values = []
        for cases in seed_cases_list:
            case = cases.get(subset)
            if not case:
                continue
            checkpoint_dir = Path(case["summary"]["output_dir"]) / "checkpoints"
            metric_graph = load_json(metric_graph_path(case["summary"]))
            min_support = type_min_support(conflict_type)
            history_points = []
            for row in case["history"]:
                epoch = int(row["epoch"])
                checkpoint_path = checkpoint_dir / f"epoch_{epoch}.pt"
                if not checkpoint_path.exists():
                    continue
                _, node_ids, logits, _ = load_model_logits(case["summary"], checkpoint_path)
                metrics = compute_type_metrics(
                    metric_graph,
                    node_ids,
                    logits,
                    conflict_type,
                    0.5,
                    min_support=min_support,
                )
                history_points.append((epoch, metrics["f1"]))
            if history_points:
                per_seed_points.append(history_points)

            graph, rows, node_ids, features, support, target = load_case_tensors(build_case_from_summary(case["summary"]))
            node_index = {node_id: idx for idx, node_id in enumerate(node_ids)}
            type_target = build_conflict_type_target_adjacency(
                metric_graph,
                node_ids,
                node_index,
                conflict_type,
                min_support=min_support,
            )
            baseline_seed_values.append(random_baseline_fixed_edge_count(type_target, trials=trials, seed=random_seed + subset)["f1"])

        if per_seed_points:
            series_map[f"Dataset size: {subset} samples"] = aggregate_epoch_series(per_seed_points)
        if baseline_seed_values:
            random_seed_series.append((subset, sum(baseline_seed_values) / len(baseline_seed_values)))

    if random_seed_series:
        epochs = []
        for cases in seed_cases_list:
            case = cases.get(450) or next(iter(cases.values()))
            epochs = [int(row["epoch"]) for row in case["history"]]
            if epochs:
                break
        random_mean = sum(value for _, value in random_seed_series) / len(random_seed_series)
        series_map = {
            "Random": [(epoch, random_mean, 0.0) for epoch in epochs],
            **series_map,
        }
    return series_map


def main() -> int:
    args = parse_args()
    experiment_dir = Path(args.experiment_dir) if args.experiment_dir else latest_experiment_dir(DEFAULT_EXPERIMENT_ROOT)
    if not args.training_dirs:
        raise SystemExit("pass at least one --training-dir")
    training_dirs = [Path(item) for item in args.training_dirs]
    output_dir = Path(args.output_dir)
    ensure_output_dir(output_dir)

    seed_cases_list = [load_seed_cases(training_dir, args.scenario) for training_dir in training_dirs]

    reconstruction_random_values = []
    for subset in SUBSET_ORDER:
        subset_seed_values = []
        for cases in seed_cases_list:
            case = cases.get(subset)
            if not case:
                continue
            _, _, _, _, _, target = load_case_tensors(build_case_from_summary(case["summary"]))
            subset_seed_values.append(random_baseline_fixed_edge_count(target, trials=args.random_baseline_trials, seed=args.random_seed + subset)["f1"])
        if subset_seed_values:
            reconstruction_random_values.append(sum(subset_seed_values) / len(subset_seed_values))

    base_epochs = [int(row["epoch"]) for row in (seed_cases_list[0].get(450) or next(iter(seed_cases_list[0].values())))["history"]]
    random_mean = sum(reconstruction_random_values) / max(len(reconstruction_random_values), 1)
    random_series = [(epoch, random_mean, 0.0) for epoch in base_epochs]

    figures = {}
    dataset_series = build_dataset_series(seed_cases_list, random_points=random_series)
    figures[f"{args.scenario}_reconstruction_f1_vs_epochs"] = plot_errorbar_series(
        dataset_series,
        title="F1 Score vs. Epochs for Threshold=0.5",
        caption="Conflict graph reconstruction accuracy according to the number of epochs and fixed threshold of 0.5.",
        output_dir=output_dir,
        stem=f"{args.scenario}_reconstruction_f1_vs_epochs",
        styles=DATASET_CURVE_STYLES,
        legend_variant="dataset",
    )

    case450_summaries = [cases[450]["summary"] for cases in seed_cases_list if 450 in cases]
    reconstruction_threshold_series = build_threshold_series(
        case450_summaries,
        "reconstruction",
        trials=args.random_baseline_trials,
        random_seed=args.random_seed,
    )
    figures[f"{args.scenario}_reconstruction_f1_vs_threshold"] = plot_errorbar_series(
        reconstruction_threshold_series,
        title="F1 Score vs. Epochs for Dataset Size=450",
        caption="Conflict graph reconstruction accuracy according to the number of epochs and a fixed dataset size of 450.",
        output_dir=output_dir,
        stem=f"{args.scenario}_reconstruction_f1_vs_threshold",
        styles=THRESHOLD_CURVE_STYLES,
        legend_variant="threshold",
    )

    for conflict_type in available_conflict_types(case450_summaries):
        dataset_series = build_type_dataset_series(
            seed_cases_list,
            conflict_type,
            trials=args.random_baseline_trials,
            random_seed=args.random_seed + CONFLICT_TYPE_SEED_OFFSET.get(conflict_type, 0),
        )
        figures[f"{args.scenario}_{conflict_type}_f1_vs_epochs"] = plot_errorbar_series(
            dataset_series,
            title=f"F1 {conflict_type.title()} vs. Epochs for Threshold=0.5",
            caption=(
                f"{conflict_type.title()} conflict labeling accuracy according to the number of epochs "
                "and fixed threshold of 0.5."
            ),
            output_dir=output_dir,
            stem=f"{args.scenario}_{conflict_type}_f1_vs_epochs",
            styles=DATASET_CURVE_STYLES,
            legend_variant="dataset",
        )

        threshold_series = build_threshold_series(
            case450_summaries,
            conflict_type,
            trials=args.random_baseline_trials,
            random_seed=args.random_seed + CONFLICT_TYPE_SEED_OFFSET.get(conflict_type, 0),
        )
        figures[f"{args.scenario}_{conflict_type}_f1_vs_threshold"] = plot_errorbar_series(
            threshold_series,
            title=f"F1 {conflict_type.title()} vs. Epochs for Dataset Size=450",
            caption=(
                f"{conflict_type.title()} conflict labeling accuracy according to the number of epochs "
                "and a fixed dataset size of 450."
            ),
            output_dir=output_dir,
            stem=f"{args.scenario}_{conflict_type}_f1_vs_threshold",
            styles=THRESHOLD_CURVE_STYLES,
            legend_variant="threshold",
        )

    summary = {
        "experiment_dir": str(experiment_dir),
        "scenario": args.scenario,
        "training_dirs": [str(path) for path in training_dirs],
        "seeds_count": len(training_dirs),
        "figures": figures,
    }
    with (output_dir / "graphsage_paper_multiseed_summary.json").open("w") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    print(f"Figures written to: {output_dir}")
    for key, paths in figures.items():
        print(f"- {key}: {paths['png']}")
    print(f"- summary: {output_dir / 'graphsage_paper_multiseed_summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
