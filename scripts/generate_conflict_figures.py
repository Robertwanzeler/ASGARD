#!/usr/bin/env python3
"""
Generate comparison figures from a completed GreenRAN conflitos experiment.

This script does not claim GraphSAGE accuracy curves. Instead, it produces
figures inspired by the same comparison structure using the metrics we
actually have today:

- reconstruction support ratio: confirmed_by_data / baseline_edges
- low-support ratio: weak_or_low_support / baseline_edges
- indirect conflict share in collected rows
- implicit conflict share in collected rows

Outputs are written to:
  <experiment_dir>/conflict_figures/
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
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

FIGSIZE = (10.5, 6.2)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate comparison figures from a completed GreenRAN conflitos experiment."
    )
    parser.add_argument(
        "--experiment-dir",
        help="completed experiment directory; defaults to the latest one under runs/experimentos_conflitos",
    )
    parser.add_argument(
        "--output-dir",
        help="directory where PNG/SVG figures will be written; defaults to <experiment>/conflict_figures",
    )
    parser.add_argument(
        "--top-kpis",
        type=int,
        default=6,
        help="number of most frequent KPIs to include in the heatmap summary",
    )
    return parser.parse_args()


def latest_experiment_dir(root: Path) -> Path:
    candidates = sorted(root.glob("*/experiment_report.json"))
    if not candidates:
        raise FileNotFoundError(
            f"no completed experiment_report.json found under {root}"
        )
    return candidates[-1].parent


def load_json(path: Path) -> dict:
    with path.open() as f:
        return json.load(f)


def read_csv_rows(path: Path) -> list[dict]:
    with path.open() as f:
        return list(csv.DictReader(f))


def clean_type(value: str) -> str:
    text = (value or "").strip().lower()
    if text in {"direct", "indirect", "implicit"}:
        return text
    return "unknown"


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


def line_plot(
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
    for scenario in SCENARIO_ORDER:
        points = series_map.get(scenario)
        if not points:
            continue
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        ax.plot(xs, ys, marker="o", linewidth=1.8, markersize=4, label=scenario_label(scenario))

    ax.set_title(title)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.grid(True, linestyle="--", alpha=0.35)
    if ylim is not None:
        ax.set_ylim(*ylim)
    ax.legend(loc="best", fontsize=8)
    return save_figure(fig, output_dir, stem)


def grouped_bar_plot(
    values_by_scenario: dict[str, dict[str, float]],
    categories: list[str],
    *,
    title: str,
    ylabel: str,
    output_dir: Path,
    stem: str,
    ylim: tuple[float, float] | None = None,
) -> dict:
    fig, ax = plt.subplots(figsize=(12, 6.4))
    scenario_slugs = [slug for slug in SCENARIO_ORDER if slug in values_by_scenario]
    if not scenario_slugs:
        return save_figure(fig, output_dir, stem)

    width = 0.8 / max(len(categories), 1)
    positions = list(range(len(scenario_slugs)))

    for idx, category in enumerate(categories):
        offset = (idx - (len(categories) - 1) / 2.0) * width
        heights = [
            values_by_scenario[slug].get(category, 0.0)
            for slug in scenario_slugs
        ]
        ax.bar(
            [pos + offset for pos in positions],
            heights,
            width=width,
            label=category,
        )

    ax.set_title(title)
    ax.set_ylabel(ylabel)
    ax.set_xticks(positions)
    ax.set_xticklabels([scenario_label(slug) for slug in scenario_slugs], rotation=20, ha="right")
    ax.grid(True, axis="y", linestyle="--", alpha=0.35)
    if ylim is not None:
        ax.set_ylim(*ylim)
    ax.legend(loc="best", fontsize=8)
    return save_figure(fig, output_dir, stem)


def stacked_conflict_plot(
    counts_by_scenario: dict[str, Counter],
    *,
    title: str,
    output_dir: Path,
    stem: str,
) -> dict:
    fig, ax = plt.subplots(figsize=(12, 6.4))
    scenario_slugs = [slug for slug in SCENARIO_ORDER if slug in counts_by_scenario]
    if not scenario_slugs:
        return save_figure(fig, output_dir, stem)

    bottoms = [0] * len(scenario_slugs)
    categories = ["direct", "indirect", "implicit", "unknown"]
    colors = {
        "direct": "#4c78a8",
        "indirect": "#f58518",
        "implicit": "#54a24b",
        "unknown": "#bab0ac",
    }

    for category in categories:
        heights = [counts_by_scenario[slug].get(category, 0) for slug in scenario_slugs]
        ax.bar(
            range(len(scenario_slugs)),
            heights,
            bottom=bottoms,
            color=colors[category],
            label=category,
        )
        bottoms = [bottom + height for bottom, height in zip(bottoms, heights)]

    ax.set_title(title)
    ax.set_ylabel("Conflict rows")
    ax.set_xticks(range(len(scenario_slugs)))
    ax.set_xticklabels([scenario_label(slug) for slug in scenario_slugs], rotation=20, ha="right")
    ax.grid(True, axis="y", linestyle="--", alpha=0.35)
    ax.legend(loc="best", fontsize=8)
    return save_figure(fig, output_dir, stem)


def heatmap_plot(
    matrix: list[list[int]],
    row_labels: list[str],
    col_labels: list[str],
    *,
    title: str,
    output_dir: Path,
    stem: str,
) -> dict:
    fig, ax = plt.subplots(figsize=(11.2, 6.4))
    image = ax.imshow(matrix, cmap="YlOrRd", aspect="auto")
    ax.set_title(title)
    ax.set_xticks(range(len(col_labels)))
    ax.set_xticklabels(col_labels, rotation=25, ha="right")
    ax.set_yticks(range(len(row_labels)))
    ax.set_yticklabels(row_labels)

    for i, row in enumerate(matrix):
        for j, value in enumerate(row):
            ax.text(j, i, str(value), ha="center", va="center", fontsize=8, color="black")

    fig.colorbar(image, ax=ax, shrink=0.9, label="Conflict rows")
    return save_figure(fig, output_dir, stem)


def build_round_metrics(round_rows: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in round_rows:
        scenario = row["scenario"]
        report_summary = load_json(Path(row["report_path"])).get("summary", {})
        dataset_rows = read_csv_rows(Path(row["dataset_path"]))
        type_counts = Counter(clean_type(item.get("conflict_type")) for item in dataset_rows)

        baseline_edges = int(report_summary.get("baseline_edges", 0) or 0)
        confirmed = int(report_summary.get("confirmed_by_data", 0) or 0)
        weak = int(report_summary.get("weak_or_low_support", 0) or 0)
        total_rows = int(row.get("rows", 0) or 0)

        grouped[scenario].append(
            {
                "round": int(row["round"]),
                "rows": total_rows,
                "confirmed_ratio": confirmed / max(baseline_edges, 1),
                "weak_ratio": weak / max(baseline_edges, 1),
                "indirect_share": type_counts["indirect"] / max(total_rows, 1),
                "implicit_share": type_counts["implicit"] / max(total_rows, 1),
            }
        )

    for scenario in grouped:
        grouped[scenario].sort(key=lambda item: item["round"])
        cumulative_rows = 0
        for item in grouped[scenario]:
            cumulative_rows += item["rows"]
            item["cumulative_rows"] = cumulative_rows
    return grouped


def build_subset_metrics(scenario_reports: list[dict]) -> tuple[dict[str, dict[str, float]], dict[str, dict[str, float]], dict[str, Counter], dict[str, Counter]]:
    reconstruction: dict[str, dict[str, float]] = defaultdict(dict)
    weak_support: dict[str, dict[str, float]] = defaultdict(dict)
    type_shares: dict[str, Counter] = defaultdict(Counter)
    kpi_counts: dict[str, Counter] = defaultdict(Counter)

    for scenario_report in scenario_reports:
        scenario = scenario_report["scenario"]
        full_dataset_rows = read_csv_rows(Path(scenario_report["full_export"]["dataset_path"]))

        for item in full_dataset_rows:
            type_shares[scenario][clean_type(item.get("conflict_type"))] += 1
            kpi_counts[scenario][item.get("affected_kpi", "unknown_kpi")] += 1

        for subset in scenario_report.get("subsets", []):
            requested = str(int(subset.get("requested_rows", 0) or 0))
            summary = subset.get("summary", {}) or {}
            baseline_edges = int(summary.get("baseline_edges", 0) or 0)
            reconstruction[scenario][requested] = int(summary.get("confirmed_by_data", 0) or 0) / max(baseline_edges, 1)
            weak_support[scenario][requested] = int(summary.get("weak_or_low_support", 0) or 0) / max(baseline_edges, 1)

    return reconstruction, weak_support, type_shares, kpi_counts


def write_summary(output_dir: Path, payload: dict) -> None:
    path = output_dir / "conflict_figures_summary.json"
    with path.open("w") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)


def main() -> int:
    args = parse_args()
    experiment_dir = Path(args.experiment_dir) if args.experiment_dir else latest_experiment_dir(DEFAULT_EXPERIMENT_ROOT)
    output_dir = Path(args.output_dir) if args.output_dir else experiment_dir / "conflict_figures"
    ensure_output_dir(output_dir)

    experiment_report = load_json(experiment_dir / "experiment_report.json")
    round_rows = read_csv_rows(experiment_dir / "experiment_round_summary.csv")

    round_metrics = build_round_metrics(round_rows)
    reconstruction_by_subset, weak_by_subset, type_counts, kpi_counts = build_subset_metrics(
        experiment_report.get("scenario_reports", [])
    )

    figures = {}

    figures["fig05_reconstruction_vs_round"] = line_plot(
        {
            scenario: [(item["round"], item["confirmed_ratio"]) for item in items]
            for scenario, items in round_metrics.items()
        },
        title="Fig. 5 (GreenRAN): Reconstruction Support by Round",
        xlabel="Round",
        ylabel="Confirmed edge ratio",
        output_dir=output_dir,
        stem="fig05_reconstruction_vs_round",
        ylim=(0.0, 1.05),
    )

    figures["fig06_reconstruction_vs_dataset_size"] = grouped_bar_plot(
        reconstruction_by_subset,
        categories=["50", "150", "450"],
        title="Fig. 6 (GreenRAN): Reconstruction Support by Dataset Size",
        ylabel="Confirmed edge ratio",
        output_dir=output_dir,
        stem="fig06_reconstruction_vs_dataset_size",
        ylim=(0.0, 1.05),
    )

    figures["fig07_indirect_share_vs_round"] = line_plot(
        {
            scenario: [(item["round"], item["indirect_share"]) for item in items]
            for scenario, items in round_metrics.items()
        },
        title="Fig. 7 (GreenRAN): Indirect Conflict Share by Round",
        xlabel="Round",
        ylabel="Indirect conflict share",
        output_dir=output_dir,
        stem="fig07_indirect_share_vs_round",
        ylim=(0.0, 1.05),
    )

    figures["fig08_low_support_vs_dataset_size"] = grouped_bar_plot(
        weak_by_subset,
        categories=["50", "150", "450"],
        title="Fig. 8 (GreenRAN): Low-Support Edge Ratio by Dataset Size",
        ylabel="Weak edge ratio",
        output_dir=output_dir,
        stem="fig08_low_support_vs_dataset_size",
        ylim=(0.0, 1.05),
    )

    figures["fig09_implicit_share_vs_round"] = line_plot(
        {
            scenario: [(item["round"], item["implicit_share"]) for item in items]
            for scenario, items in round_metrics.items()
        },
        title="Fig. 9 (GreenRAN): Implicit Conflict Share by Round",
        xlabel="Round",
        ylabel="Implicit conflict share",
        output_dir=output_dir,
        stem="fig09_implicit_share_vs_round",
        ylim=(0.0, 1.05),
    )

    figures["fig10_conflict_type_distribution"] = stacked_conflict_plot(
        type_counts,
        title="Fig. 10 (GreenRAN): Conflict Type Distribution by Scenario",
        output_dir=output_dir,
        stem="fig10_conflict_type_distribution",
    )

    top_kpis = Counter()
    for counter in kpi_counts.values():
        top_kpis.update(counter)
    top_kpi_labels = [label for label, _ in top_kpis.most_common(max(args.top_kpis, 1))]
    kpi_matrix = []
    row_labels = []
    for scenario in SCENARIO_ORDER:
        if scenario not in kpi_counts:
            continue
        row_labels.append(scenario_label(scenario))
        kpi_matrix.append([kpi_counts[scenario].get(label, 0) for label in top_kpi_labels])

    if kpi_matrix and top_kpi_labels:
        figures["kpi_heatmap"] = heatmap_plot(
            kpi_matrix,
            row_labels,
            top_kpi_labels,
            title="Top KPI Conflicts by Scenario",
            output_dir=output_dir,
            stem="kpi_conflict_heatmap",
        )

    write_summary(
        output_dir,
        {
            "experiment_dir": str(experiment_dir),
            "note": (
                "These figures are comparison analogues for the current heuristic learner. "
                "They are not GraphSAGE epoch-accuracy plots."
            ),
            "figures": figures,
        },
    )

    print(f"Figures written to: {output_dir}")
    for key, paths in figures.items():
        print(f"- {key}: {paths['png']}")
    print(f"- summary: {output_dir / 'conflict_figures_summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
