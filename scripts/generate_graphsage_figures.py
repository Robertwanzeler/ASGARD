#!/usr/bin/env python3
"""
Generate GraphSAGE training figures for a completed conflitos experiment.

Outputs are written to:
  <experiment_dir>/graphsage_figures/
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXPERIMENT_ROOT = PROJECT_ROOT / "runs" / "experimentos_conflitos"

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
FIGSIZE = (10.5, 6.2)


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
        "--output-dir",
        help="directory where PNG/SVG figures will be written; defaults to <experiment>/graphsage_figures",
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
            value = confirmed / max(baseline_edges, 1)
            metrics[scenario][requested] = value
    return metrics


def line_plot_by_subset(
    scenario: str,
    case_map: dict[int, dict],
    metric: str,
    *,
    title: str,
    ylabel: str,
    output_dir: Path,
    stem: str,
    ylim: tuple[float, float] | None = None,
) -> dict:
    fig, ax = plt.subplots(figsize=FIGSIZE)
    for subset in SUBSET_ORDER:
        case = case_map.get(subset)
        if not case:
            continue
        history = case["history"]
        xs = [row["epoch"] for row in history]
        ys = [row[metric] for row in history]
        ax.plot(xs, ys, marker="o", linewidth=1.8, markersize=4, label=f"{subset} samples")
    ax.set_title(title)
    ax.set_xlabel("Epochs")
    ax.set_ylabel(ylabel)
    ax.grid(True, linestyle="--", alpha=0.35)
    if ylim is not None:
        ax.set_ylim(*ylim)
    ax.legend(loc="best", fontsize=8)
    return save_figure(fig, output_dir, stem)


def grouped_bar_best_f1(
    cases: dict[str, dict[int, dict]],
    *,
    title: str,
    ylabel: str,
    output_dir: Path,
    stem: str,
    ylim: tuple[float, float] | None = None,
) -> dict:
    fig, ax = plt.subplots(figsize=(12, 6.4))
    scenario_slugs = [slug for slug in SCENARIO_ORDER if slug in cases]
    width = 0.8 / len(SUBSET_ORDER)
    positions = list(range(len(scenario_slugs)))

    for idx, subset in enumerate(SUBSET_ORDER):
        offset = (idx - (len(SUBSET_ORDER) - 1) / 2.0) * width
        heights = []
        for slug in scenario_slugs:
            case = cases[slug].get(subset)
            best = (case or {}).get("summary", {}).get("best_metrics", {})
            heights.append(float(best.get("f1", 0.0)))
        ax.bar([pos + offset for pos in positions], heights, width=width, label=str(subset))

    ax.set_title(title)
    ax.set_ylabel(ylabel)
    ax.set_xticks(positions)
    ax.set_xticklabels([scenario_label(slug) for slug in scenario_slugs], rotation=20, ha="right")
    ax.grid(True, axis="y", linestyle="--", alpha=0.35)
    if ylim is not None:
        ax.set_ylim(*ylim)
    ax.legend(loc="best", fontsize=8, title="Samples")
    return save_figure(fig, output_dir, stem)


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


def write_summary(output_dir: Path, payload: dict) -> None:
    path = output_dir / "graphsage_figures_summary.json"
    with path.open("w") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)


def main() -> int:
    args = parse_args()
    experiment_dir = Path(args.experiment_dir) if args.experiment_dir else latest_experiment_dir(DEFAULT_EXPERIMENT_ROOT)
    training_dir = Path(args.training_dir) if args.training_dir else experiment_dir / "graphsage_training"
    output_dir = Path(args.output_dir) if args.output_dir else experiment_dir / "graphsage_figures"
    ensure_output_dir(output_dir)

    cases = load_training_cases(training_dir)
    heuristic = load_heuristic_subset_metrics(experiment_dir)
    figures = {}

    for scenario in [slug for slug in SCENARIO_ORDER if slug in cases]:
        case_map = cases[scenario]
        slug = scenario
        figures[f"{slug}_f1_vs_epochs"] = line_plot_by_subset(
            scenario,
            case_map,
            "f1",
            title=f"{scenario_label(scenario)}: F1 vs Epochs",
            ylabel="F1 score",
            output_dir=output_dir,
            stem=f"{slug}_f1_vs_epochs",
            ylim=(0.0, 1.05),
        )
        figures[f"{slug}_precision_vs_epochs"] = line_plot_by_subset(
            scenario,
            case_map,
            "precision",
            title=f"{scenario_label(scenario)}: Precision vs Epochs",
            ylabel="Precision",
            output_dir=output_dir,
            stem=f"{slug}_precision_vs_epochs",
            ylim=(0.0, 1.05),
        )
        figures[f"{slug}_recall_vs_epochs"] = line_plot_by_subset(
            scenario,
            case_map,
            "recall",
            title=f"{scenario_label(scenario)}: Recall vs Epochs",
            ylabel="Recall",
            output_dir=output_dir,
            stem=f"{slug}_recall_vs_epochs",
            ylim=(0.0, 1.05),
        )
        figures[f"{slug}_loss_vs_epochs"] = line_plot_by_subset(
            scenario,
            case_map,
            "loss",
            title=f"{scenario_label(scenario)}: Loss vs Epochs",
            ylabel="Loss",
            output_dir=output_dir,
            stem=f"{slug}_loss_vs_epochs",
        )
        figures[f"{slug}_graphsage_vs_heuristic"] = grouped_bar_compare_heuristic(
            cases,
            heuristic,
            scenario=scenario,
            output_dir=output_dir,
            stem=f"{slug}_graphsage_vs_heuristic",
        )

    figures["graphsage_best_f1_by_subset"] = grouped_bar_best_f1(
        cases,
        title="GraphSAGE Best F1 by Dataset Size",
        ylabel="Best F1",
        output_dir=output_dir,
        stem="graphsage_best_f1_by_subset",
        ylim=(0.0, 1.05),
    )

    write_summary(
        output_dir,
        {
            "experiment_dir": str(experiment_dir),
            "training_dir": str(training_dir),
            "note": (
                "These figures are generated from GraphSAGE training histories and "
                "can be compared against the current heuristic reconstruction support."
            ),
            "figures": figures,
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
