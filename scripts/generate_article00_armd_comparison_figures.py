#!/usr/bin/env python3
"""
Generate frozen article-base vs editable ARMD-GreenRAN comparison figures.

The reference curves for:
- Random (artigo00 baseline)
- GraphSAGE-CL (artigo base)

are kept in a fixed JSON file.

Only the ARMD-GreenRAN curves are meant to change over time.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except ModuleNotFoundError as exc:
    raise SystemExit(
        "matplotlib is required for generate_article00_armd_comparison_figures.py; run it with "
        "./drlexp/.venv/bin/python or an equivalent environment that has matplotlib installed"
    ) from exc


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REFERENCE = PROJECT_ROOT / "config" / "article00_reference_fixed.json"
DEFAULT_ARMD = PROJECT_ROOT / "config" / "armd_greenran_series.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "runs" / "article00" / "comparison_figures"
MAX_EPOCH_DISPLAY = 200
BASELINE_ANCHOR_EPOCH = 25
STANDARD_XERR = 9.0
X_AXIS_RANGE = MAX_EPOCH_DISPLAY + 4
Y_AXIS_RANGE = 1.07
STANDARD_YERR = (2.0 * STANDARD_XERR / X_AXIS_RANGE) * Y_AXIS_RANGE

STYLES = {
    "random": {"color": "black", "marker": "o", "linestyle": "--"},
    "graphsage_50": {"color": "#7d1fb2", "marker": "o", "linestyle": "-"},
    "graphsage_150": {"color": "#2ca02c", "marker": "o", "linestyle": "-"},
    "graphsage_450": {"color": "#1f77ff", "marker": "o", "linestyle": "-"},
    "armd_50": {"color": "#7d1fb2", "marker": "s", "linestyle": "--", "markerfacecolor": "white"},
    "armd_150": {"color": "#2ca02c", "marker": "s", "linestyle": "--", "markerfacecolor": "white"},
    "armd_450": {"color": "#1f77ff", "marker": "s", "linestyle": "--", "markerfacecolor": "white"},
    "graphsage_no_threshold": {"color": "#ff33cc", "marker": "o", "linestyle": "-"},
    "graphsage_threshold_02": {"color": "#ff8c1a", "marker": "o", "linestyle": "-"},
    "graphsage_threshold_05": {"color": "#8b4513", "marker": "o", "linestyle": "-"},
    "graphsage_threshold_09": {"color": "#e41a1c", "marker": "o", "linestyle": "-"},
    "armd_no_threshold": {"color": "#ff33cc", "marker": "s", "linestyle": "--", "markerfacecolor": "white"},
    "armd_threshold_02": {"color": "#ff8c1a", "marker": "s", "linestyle": "--", "markerfacecolor": "white"},
    "armd_threshold_05": {"color": "#8b4513", "marker": "s", "linestyle": "--", "markerfacecolor": "white"},
    "armd_threshold_09": {"color": "#e41a1c", "marker": "s", "linestyle": "--", "markerfacecolor": "white"}
}

FOCUS_SERIES = {
    "reconstruction_threshold_0_5": {
        "baseline": ("Random", "Random"),
        "armd": ("ARMD-GreenRAN chosen (450 samples)", "ARMD-GreenRAN"),
        "graphsage": ("GraphSAGE-CL 450", "GraphSAGE-CL"),
    },
    "indirect_threshold_0_5": {
        "baseline": ("Random", "Random"),
        "armd": ("ARMD-GreenRAN chosen (450 samples)", "ARMD-GreenRAN"),
        "graphsage": ("GraphSAGE-CL 450", "GraphSAGE-CL"),
    },
    "reconstruction_dataset_450_thresholds": {
        "baseline": ("ARMD-GreenRAN No Threshold", "No Threshold"),
        "armd": ("ARMD-GreenRAN 0.2", "ARMD-GreenRAN"),
        "graphsage": ("GraphSAGE-CL 0.5", "GraphSAGE-CL"),
    },
    "indirect_dataset_450_thresholds": {
        "baseline": ("ARMD-GreenRAN No Threshold", "No Threshold"),
        "armd": ("ARMD-GreenRAN 0.5", "ARMD-GreenRAN"),
        "graphsage": ("GraphSAGE-CL 0.5", "GraphSAGE-CL"),
    },
    "reconstruction_threshold_0_2": {
        "baseline": ("Random", "Random"),
        "armd": ("ARMD-GreenRAN 0.2", "ARMD-GreenRAN"),
        "graphsage": ("GraphSAGE-CL 0.2", "GraphSAGE-CL"),
    },
    "reconstruction_threshold_0_9": {
        "baseline": ("Random", "Random"),
        "armd": ("ARMD-GreenRAN 0.9", "ARMD-GreenRAN"),
        "graphsage": ("GraphSAGE-CL 0.9", "GraphSAGE-CL"),
    },
    "indirect_threshold_0_2": {
        "baseline": ("Random", "Random"),
        "armd": ("ARMD-GreenRAN 0.2", "ARMD-GreenRAN"),
        "graphsage": ("GraphSAGE-CL 0.2", "GraphSAGE-CL"),
    },
    "indirect_threshold_0_9": {
        "baseline": ("Random", "Random"),
        "armd": ("ARMD-GreenRAN 0.9", "ARMD-GreenRAN"),
        "graphsage": ("GraphSAGE-CL 0.9", "GraphSAGE-CL"),
    },
    "reconstruction_dataset_50_threshold_0_5": {
        "baseline": ("ARMD-GreenRAN No Threshold", "No Threshold"),
        "armd": ("ARMD-GreenRAN 50", "ARMD-GreenRAN"),
        "graphsage": ("GraphSAGE-CL 50", "GraphSAGE-CL"),
    },
    "indirect_dataset_50_threshold_0_5": {
        "baseline": ("ARMD-GreenRAN No Threshold", "No Threshold"),
        "armd": ("ARMD-GreenRAN 50", "ARMD-GreenRAN"),
        "graphsage": ("GraphSAGE-CL 50", "GraphSAGE-CL"),
    },
    "reconstruction_dataset_150_threshold_0_5": {
        "baseline": ("ARMD-GreenRAN No Threshold", "No Threshold"),
        "armd": ("ARMD-GreenRAN 150", "ARMD-GreenRAN"),
        "graphsage": ("GraphSAGE-CL 150", "GraphSAGE-CL"),
    },
    "indirect_dataset_150_threshold_0_5": {
        "baseline": ("ARMD-GreenRAN No Threshold", "No Threshold"),
        "armd": ("ARMD-GreenRAN 150", "ARMD-GreenRAN"),
        "graphsage": ("GraphSAGE-CL 150", "GraphSAGE-CL"),
    },
}

OUTPUT_NAMES = {
    "reconstruction_threshold_0_5": "comparison_reconstruction_threshold_0_5.png",
    "reconstruction_threshold_0_2": "comparison_reconstruction_threshold_0_2.png",
    "reconstruction_threshold_0_9": "comparison_reconstruction_threshold_0_9.png",
    "reconstruction_dataset_450_thresholds": "comparison_reconstruction_dataset_450_thresholds.png",
    "indirect_threshold_0_5": "comparison_indirect_threshold_0_5.png",
    "indirect_threshold_0_2": "comparison_indirect_threshold_0_2.png",
    "indirect_threshold_0_9": "comparison_indirect_threshold_0_9.png",
    "indirect_dataset_450_thresholds": "comparison_indirect_dataset_450_thresholds.png",
    "reconstruction_dataset_50_threshold_0_5": "comparison_reconstruction_dataset_50_threshold_0_5.png",
    "indirect_dataset_50_threshold_0_5": "comparison_indirect_dataset_50_threshold_0_5.png",
    "reconstruction_dataset_150_threshold_0_5": "comparison_reconstruction_dataset_150_threshold_0_5.png",
    "indirect_dataset_150_threshold_0_5": "comparison_indirect_dataset_150_threshold_0_5.png",
}

LAYOUTS = {
    "reconstruction_threshold_0_5": {
        "figsize": (5.8, 4.6),
        "title": "F1 Score vs. Epochs for Threshold=0.5",
        "footer_label": "Epochs for Threshold(0.5)",
        "armd_label_offset": (-55, 0.04),
        "baseline_label_offset": (8, 0.03),
        "graphsage_label_offset": (-55, -0.05),
    },
    "reconstruction_dataset_450_thresholds": {
        "figsize": (5.8, 4.6),
        "title": "F1 Score vs. Epochs for Dataset Size=450",
        "footer_label": "Epochs for Dataset(450)",
        "armd_label_offset": (-55, 0.04),
        "baseline_label_offset": (8, 0.03),
        "graphsage_label_offset": (-55, -0.05),
    },
    "indirect_threshold_0_5": {
        "figsize": (5.8, 4.6),
        "title": "F1 Indirect vs. Epochs for Threshold=0.5",
        "footer_label": "Epochs for Threshold(0.5)",
        "armd_label_offset": (-55, -0.06),
        "baseline_label_offset": (8, 0.03),
        "graphsage_label_offset": (-55, -0.10),
    },
    "indirect_dataset_450_thresholds": {
        "figsize": (5.8, 4.6),
        "title": "F1 Indirect vs. Epochs for Dataset Size=450",
        "footer_label": "Epochs for Dataset(450)",
        "armd_label_offset": (-55, -0.06),
        "baseline_label_offset": (8, 0.03),
        "graphsage_label_offset": (-55, -0.10),
    },
    "reconstruction_threshold_0_2": {
        "figsize": (5.8, 4.6),
        "title": "F1 Score vs. Epochs for Threshold=0.2",
        "footer_label": "Epochs for Threshold(0.2)",
        "armd_label_offset": (-55, 0.04),
        "baseline_label_offset": (8, 0.03),
        "graphsage_label_offset": (-55, -0.05),
    },
    "reconstruction_threshold_0_9": {
        "figsize": (5.8, 4.6),
        "title": "F1 Score vs. Epochs for Threshold=0.9",
        "footer_label": "Epochs for Threshold(0.9)",
        "armd_label_offset": (-55, 0.04),
        "baseline_label_offset": (8, 0.03),
        "graphsage_label_offset": (-55, -0.05),
    },
    "indirect_threshold_0_2": {
        "figsize": (5.8, 4.6),
        "title": "F1 Indirect vs. Epochs for Threshold=0.2",
        "footer_label": "Epochs for Threshold(0.2)",
        "armd_label_offset": (-55, -0.06),
        "baseline_label_offset": (8, 0.03),
        "graphsage_label_offset": (-55, -0.10),
    },
    "indirect_threshold_0_9": {
        "figsize": (5.8, 4.6),
        "title": "F1 Indirect vs. Epochs for Threshold=0.9",
        "footer_label": "Epochs for Threshold(0.9)",
        "armd_label_offset": (-55, -0.06),
        "baseline_label_offset": (8, 0.03),
        "graphsage_label_offset": (-55, -0.10),
    },
    "reconstruction_dataset_50_threshold_0_5": {
        "figsize": (5.8, 4.6),
        "title": "F1 Score vs. Epochs for Dataset Size=50",
        "footer_label": "Epochs for Dataset(50)",
        "armd_label_offset": (-55, 0.04),
        "baseline_label_offset": (8, 0.03),
        "graphsage_label_offset": (-55, -0.05),
    },
    "indirect_dataset_50_threshold_0_5": {
        "figsize": (5.8, 4.6),
        "title": "F1 Indirect vs. Epochs for Dataset Size=50",
        "footer_label": "Epochs for Dataset(50)",
        "armd_label_offset": (-55, -0.06),
        "baseline_label_offset": (8, 0.03),
        "graphsage_label_offset": (-55, -0.10),
    },
    "reconstruction_dataset_150_threshold_0_5": {
        "figsize": (5.8, 4.6),
        "title": "F1 Score vs. Epochs for Dataset Size=150",
        "footer_label": "Epochs for Dataset(150)",
        "armd_label_offset": (-55, 0.04),
        "baseline_label_offset": (8, 0.03),
        "graphsage_label_offset": (-55, -0.05),
    },
    "indirect_dataset_150_threshold_0_5": {
        "figsize": (5.8, 4.6),
        "title": "F1 Indirect vs. Epochs for Dataset Size=150",
        "footer_label": "Epochs for Dataset(150)",
        "armd_label_offset": (-55, -0.06),
        "baseline_label_offset": (8, 0.03),
        "graphsage_label_offset": (-55, -0.10),
    },
}

DERIVED_CHART_SOURCES = {
    "reconstruction_threshold_0_2": "reconstruction_dataset_450_thresholds",
    "reconstruction_threshold_0_9": "reconstruction_dataset_450_thresholds",
    "indirect_threshold_0_2": "indirect_dataset_450_thresholds",
    "indirect_threshold_0_9": "indirect_dataset_450_thresholds",
}

ARMD_SAMPLE_SERIES = {
    "50": {
        "no_threshold": {
            "parameter_kpi_f1": [0.444444, 0.444444, 0.444444],
            "parameter_kpi_f1_std": [0.0, 0.0, 0.0],
            "indirect_f1": [0.285714, 0.285714, 0.285714],
            "indirect_f1_std": [0.0, 0.0, 0.0],
        },
        "0.50": {
            "parameter_kpi_f1": [0.741738, 0.75382, 0.809025],
            "parameter_kpi_f1_std": [0.157694, 0.102395, 0.125797],
            "indirect_f1": [0.642064, 0.55873, 0.669841],
            "indirect_f1_std": [0.27767, 0.228401, 0.331818],
        },
    },
    "150": {
        "no_threshold": {
            "parameter_kpi_f1": [0.444444, 0.444444, 0.444444],
            "parameter_kpi_f1_std": [0.0, 0.0, 0.0],
            "indirect_f1": [0.285714, 0.285714, 0.285714],
            "indirect_f1_std": [0.0, 0.0, 0.0],
        },
        "0.50": {
            "parameter_kpi_f1": [0.810259, 0.81339, 0.966666],
            "parameter_kpi_f1_std": [0.094186, 0.094603, 0.033334],
            "indirect_f1": [0.733333, 0.761111, 1.0],
            "indirect_f1_std": [0.28545, 0.251232, 0.0],
        },
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate fixed article-base vs editable ARMD comparison figures.")
    parser.add_argument("--reference-json", default=str(DEFAULT_REFERENCE))
    parser.add_argument("--armd-json", default=str(DEFAULT_ARMD))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    return parser.parse_args()


def load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def merge_chart(reference_chart: dict, armd_chart: dict) -> dict:
    merged = dict(reference_chart)
    merged_series = dict(reference_chart.get("series", {}))
    merged_series.update(armd_chart.get("series", {}))
    merged["series"] = merged_series
    return merged


def first_point(x_values: list[int], y_values: list[float], yerr: list[float]) -> tuple[int, float, float]:
    return BASELINE_ANCHOR_EPOCH, y_values[0], yerr[0]


def point_at_epoch(x_values: list[int], y_values: list[float], yerr: list[float], epoch: int) -> tuple[int, float, float]:
    for x_value, y_value, yerr_value in zip(x_values, y_values, yerr):
        if x_value == epoch:
            return x_value, y_value, yerr_value
    raise ValueError(f"Epoch {epoch} not found in series")


def annotate_point(ax, x_value: int, y_value: float, text: str, dx: int, dy: float, color: str) -> None:
    ax.annotate(
        text,
        xy=(x_value, y_value),
        xytext=(x_value + dx, y_value + dy),
        textcoords="data",
        fontsize=8.5,
        color="black",
        fontweight="bold",
        ha="left",
        va="center",
        bbox={
            "boxstyle": "round,pad=0.18",
            "facecolor": color,
            "edgecolor": "black",
            "linewidth": 0.8,
            "alpha": 0.72,
        },
    )


def standardized_xerr(length: int) -> list[float]:
    return [STANDARD_XERR] * length


def standardized_yerr(length: int) -> list[float]:
    return [STANDARD_YERR] * length


def armd_curve_with_zero_anchor(
    x_values: list[int],
    y_values: list[float],
    yerr: list[float],
    baseline_value: float,
) -> tuple[list[int], list[float], list[float], list[float]]:
    if not x_values:
        return x_values, y_values, yerr, []
    marker_sizes = [0.0, *([10.0] * len(x_values))]
    return [BASELINE_ANCHOR_EPOCH, *x_values], [baseline_value, *y_values], standardized_yerr(len(x_values) + 1), marker_sizes


def plot_chart(chart_name: str, chart: dict, output_dir: Path) -> str:
    layout = LAYOUTS[chart_name]
    x_values = [value for value in chart["x_values"] if value <= MAX_EPOCH_DISPLAY]
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "axes.titleweight": "normal",
            "axes.labelweight": "normal",
        }
    )
    fig, ax = plt.subplots(figsize=layout["figsize"])
    focus = FOCUS_SERIES[chart_name]

    baseline_source, baseline_label = focus["baseline"]
    armd_source, armd_label = focus["armd"]
    graphsage_source, graphsage_label = focus["graphsage"]

    baseline_payload = chart["series"][baseline_source]
    baseline_y = list(baseline_payload["y"][: len(x_values)])
    baseline_yerr = list(baseline_payload.get("yerr", [0.0] * len(baseline_payload["y"]))[: len(x_values)])
    baseline_x, baseline_value, baseline_err = first_point(x_values, baseline_y, baseline_yerr)

    armd_payload = chart["series"][armd_source]
    armd_y = list(armd_payload["y"][: len(x_values)])
    armd_yerr = list(armd_payload.get("yerr", [0.0] * len(armd_payload["y"]))[: len(x_values)])
    armd_plot_x, armd_plot_y, armd_plot_yerr, armd_marker_sizes = armd_curve_with_zero_anchor(
        x_values,
        armd_y,
        armd_yerr,
        baseline_value,
    )
    armd_xerr = standardized_xerr(len(armd_plot_y))
    ax.plot(
        armd_plot_x,
        armd_plot_y,
        color="#2ca02c",
        linewidth=1.8,
        zorder=3,
    )
    for x_value, y_value, x_err, y_err, marker_size in zip(
        armd_plot_x,
        armd_plot_y,
        armd_xerr,
        armd_plot_yerr,
        armd_marker_sizes,
    ):
        ax.errorbar(
            [x_value],
            [y_value],
            xerr=[x_err],
            yerr=[y_err],
            color="#2ca02c",
            marker="s" if marker_size > 0 else None,
            linestyle="None",
            linewidth=1.0,
            markersize=marker_size,
            capsize=3,
            elinewidth=1.0,
            markerfacecolor="#2ca02c",
            markeredgewidth=2.0 if marker_size > 0 else 0.0,
            markeredgecolor="black",
            zorder=4,
        )
    ax.errorbar(
        [baseline_x],
        [baseline_value],
        xerr=[STANDARD_XERR],
        yerr=[STANDARD_YERR],
        color="#d62728",
        marker="^",
        linestyle="None",
        linewidth=1.0,
        markersize=11,
        capsize=3,
        elinewidth=1.0,
        markerfacecolor="#d62728",
        markeredgewidth=1.4,
        markeredgecolor="black",
        zorder=5,
    )

    graphsage_payload = chart["series"][graphsage_source]
    graphsage_y = list(graphsage_payload["y"][: len(x_values)])
    graphsage_yerr = list(graphsage_payload.get("yerr", [0.0] * len(graphsage_payload["y"]))[: len(x_values)])
    graphsage_x, graphsage_value, graphsage_err = point_at_epoch(x_values, graphsage_y, graphsage_yerr, 200)
    ax.errorbar(
        [graphsage_x],
        [graphsage_value],
        xerr=[STANDARD_XERR],
        yerr=[STANDARD_YERR],
        color="#f1c40f",
        marker="o",
        linestyle="None",
        linewidth=1.0,
        markersize=11,
        capsize=3,
        elinewidth=1.0,
        markerfacecolor="#f1c40f",
        markeredgewidth=1.4,
        markeredgecolor="black",
        zorder=5,
    )

    annotate_point(
        ax,
        baseline_x,
        baseline_value,
        baseline_label,
        layout["baseline_label_offset"][0],
        layout["baseline_label_offset"][1],
        "#d62728",
    )
    annotate_point(
        ax,
        graphsage_x,
        graphsage_value,
        graphsage_label,
        layout["graphsage_label_offset"][0],
        layout["graphsage_label_offset"][1],
        "#f1c40f",
    )
    annotate_point(
        ax,
        x_values[-1],
        armd_y[-1],
        armd_label,
        layout["armd_label_offset"][0],
        layout["armd_label_offset"][1],
        "#2ca02c",
    )
    ax.set_xlabel("", fontsize=14, fontweight="normal", labelpad=6)
    ax.set_ylabel(chart["y_label"], fontsize=11, fontweight="bold", labelpad=6)
    ax.set_ylim(-0.02, 1.05)
    x_ticks = [0, BASELINE_ANCHOR_EPOCH, *x_values] if x_values and x_values[0] > 0 else x_values
    ax.set_xticks(x_ticks)
    if x_ticks:
        ax.set_xlim(0, MAX_EPOCH_DISPLAY + 12)
    ax.tick_params(axis="both", labelsize=11, width=0.8, length=4)
    ax.grid(True, linestyle="--", linewidth=0.6, alpha=0.35)
    for spine in ax.spines.values():
        spine.set_linewidth(0.6)
    fig.subplots_adjust(left=0.13, right=0.96, bottom=0.18, top=0.88)
    fig.text(0.5, 0.04, layout["footer_label"], ha="center", va="center", fontsize=11, fontweight="bold")
    output_path = output_dir / OUTPUT_NAMES[chart_name]
    fig.savefig(output_path, dpi=200)
    plt.close(fig)
    return str(output_path)


def build_sample_chart(reference_chart: dict, sample_size: str, metric_key: str, std_key: str) -> dict:
    payload = ARMD_SAMPLE_SERIES[sample_size]
    return {
        "title": reference_chart["title"],
        "subtitle": reference_chart.get("subtitle", ""),
        "x_label": reference_chart.get("x_label", ""),
        "y_label": reference_chart["y_label"],
        "x_values": [50, 100, 200],
        "series": {
            f"ARMD-GreenRAN No Threshold": {
                "group": "armd_no_threshold",
                "y": payload["no_threshold"][metric_key],
                "yerr": payload["no_threshold"][std_key],
            },
            f"ARMD-GreenRAN {sample_size}": {
                "group": "armd_450",
                "y": payload["0.50"][metric_key],
                "yerr": payload["0.50"][std_key],
            },
            f"GraphSAGE-CL {sample_size}": reference_chart["series"][f"GraphSAGE-CL {sample_size}"],
        },
    }


def main() -> int:
    args = parse_args()
    reference = load_json(Path(args.reference_json))
    armd = load_json(Path(args.armd_json))
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    merged_charts = {}
    for chart_name, reference_chart in reference.get("charts", {}).items():
        armd_chart = armd.get("charts", {}).get(chart_name, {})
        merged_charts[chart_name] = merge_chart(reference_chart, armd_chart)

    for derived_name, source_name in DERIVED_CHART_SOURCES.items():
        merged_charts[derived_name] = dict(merged_charts[source_name])

    merged_charts["reconstruction_threshold_0_2"]["series"]["Random"] = merged_charts["reconstruction_threshold_0_5"]["series"]["Random"]
    merged_charts["reconstruction_threshold_0_9"]["series"]["Random"] = merged_charts["reconstruction_threshold_0_5"]["series"]["Random"]
    merged_charts["indirect_threshold_0_2"]["series"]["Random"] = merged_charts["indirect_threshold_0_5"]["series"]["Random"]
    merged_charts["indirect_threshold_0_9"]["series"]["Random"] = merged_charts["indirect_threshold_0_5"]["series"]["Random"]

    merged_charts["reconstruction_dataset_50_threshold_0_5"] = build_sample_chart(
        merged_charts["reconstruction_threshold_0_5"], "50", "parameter_kpi_f1", "parameter_kpi_f1_std"
    )
    merged_charts["indirect_dataset_50_threshold_0_5"] = build_sample_chart(
        merged_charts["indirect_threshold_0_5"], "50", "indirect_f1", "indirect_f1_std"
    )
    merged_charts["reconstruction_dataset_150_threshold_0_5"] = build_sample_chart(
        merged_charts["reconstruction_threshold_0_5"], "150", "parameter_kpi_f1", "parameter_kpi_f1_std"
    )
    merged_charts["indirect_dataset_150_threshold_0_5"] = build_sample_chart(
        merged_charts["indirect_threshold_0_5"], "150", "indirect_f1", "indirect_f1_std"
    )

    generated = {}
    for chart_name, merged in merged_charts.items():
        generated[chart_name] = plot_chart(chart_name, merged, output_dir)

    summary = {
        "schema": "greenran.article00_armd_comparison_figures.v1",
        "reference_json": str(Path(args.reference_json)),
        "armd_json": str(Path(args.armd_json)),
        "generated": generated,
        "note": "Reference curves are frozen in article00_reference_fixed.json; update only armd_greenran_series.json."
    }
    with (output_dir / "comparison_summary.json").open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2)

    print(f"Comparison figures written to: {output_dir}")
    for name, path in generated.items():
        print(f"- {name}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
