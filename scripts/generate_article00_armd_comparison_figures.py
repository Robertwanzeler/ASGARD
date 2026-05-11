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
    "armd_50": {"color": "#7d1fb2", "marker": "s", "linestyle": "--", "markerfacecolor": "#7d1fb2"},
    "armd_150": {"color": "#2ca02c", "marker": "s", "linestyle": "--", "markerfacecolor": "#2ca02c"},
    "armd_450": {"color": "#1f77ff", "marker": "s", "linestyle": "--", "markerfacecolor": "#1f77ff"},
    "graphsage_no_threshold": {"color": "#ff33cc", "marker": "o", "linestyle": "-"},
    "graphsage_threshold_02": {"color": "#ff8c1a", "marker": "o", "linestyle": "-"},
    "graphsage_threshold_05": {"color": "#8b4513", "marker": "o", "linestyle": "-"},
    "graphsage_threshold_09": {"color": "#e41a1c", "marker": "o", "linestyle": "-"},
    "armd_no_threshold": {"color": "#ff33cc", "marker": "s", "linestyle": "--", "markerfacecolor": "#ff33cc"},
    "armd_threshold_02": {"color": "#ff8c1a", "marker": "s", "linestyle": "--", "markerfacecolor": "#ff8c1a"},
    "armd_threshold_05": {"color": "#8b4513", "marker": "s", "linestyle": "--", "markerfacecolor": "#8b4513"},
    "armd_threshold_09": {"color": "#e41a1c", "marker": "s", "linestyle": "--", "markerfacecolor": "#e41a1c"}
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
    "implicit_threshold_0_2": {
        "baseline": ("Random", "Random"),
        "armd": ("ARMD-GreenRAN 0.2", "ARMD-GreenRAN"),
        "graphsage": ("GraphSAGE-CL 0.2", "GraphSAGE-CL"),
    },
    "implicit_threshold_0_5": {
        "baseline": ("Random", "Random"),
        "armd": ("ARMD-GreenRAN 0.5", "ARMD-GreenRAN"),
        "graphsage": ("GraphSAGE-CL 0.5", "GraphSAGE-CL"),
    },
    "implicit_threshold_0_9": {
        "baseline": ("Random", "Random"),
        "armd": ("ARMD-GreenRAN 0.9", "ARMD-GreenRAN"),
        "graphsage": ("GraphSAGE-CL 0.9", "GraphSAGE-CL"),
    },
    "implicit_dataset_50_threshold_0_5": {
        "baseline": ("ARMD-GreenRAN No Threshold", "No Threshold"),
        "armd": ("ARMD-GreenRAN 50", "ARMD-GreenRAN"),
        "graphsage": ("GraphSAGE-CL 50", "GraphSAGE-CL"),
    },
    "implicit_dataset_150_threshold_0_5": {
        "baseline": ("ARMD-GreenRAN No Threshold", "No Threshold"),
        "armd": ("ARMD-GreenRAN 150", "ARMD-GreenRAN"),
        "graphsage": ("GraphSAGE-CL 150", "GraphSAGE-CL"),
    },
    "implicit_dataset_450_threshold_0_5": {
        "baseline": ("ARMD-GreenRAN No Threshold", "No Threshold"),
        "armd": ("ARMD-GreenRAN 450", "ARMD-GreenRAN"),
        "graphsage": ("GraphSAGE-CL 450", "GraphSAGE-CL"),
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
    "implicit_threshold_0_2": "comparison_implicit_threshold_0_2.png",
    "implicit_threshold_0_5": "comparison_implicit_threshold_0_5.png",
    "implicit_threshold_0_9": "comparison_implicit_threshold_0_9.png",
    "implicit_dataset_50_threshold_0_5": "comparison_implicit_dataset_50_threshold_0_5.png",
    "implicit_dataset_150_threshold_0_5": "comparison_implicit_dataset_150_threshold_0_5.png",
    "implicit_dataset_450_threshold_0_5": "comparison_implicit_dataset_450_threshold_0_5.png",
    "implicit_dataset_450_thresholds": "comparison_implicit_dataset_450_thresholds.png",
}

PRIMARY_CHARTS = (
    "reconstruction_threshold_0_5",
    "reconstruction_dataset_450_thresholds",
    "indirect_threshold_0_5",
    "indirect_dataset_450_thresholds",
    "implicit_threshold_0_5",
    "implicit_dataset_450_thresholds",
)

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
    "implicit_threshold_0_2": {
        "figsize": (5.8, 4.6),
        "title": "F1 Implicit vs. Epochs for Threshold=0.2",
        "footer_label": "Epochs for Threshold(0.2)",
        "armd_label_offset": (-55, -0.06),
        "baseline_label_offset": (8, 0.03),
        "graphsage_label_offset": (-55, -0.10),
    },
    "implicit_threshold_0_5": {
        "figsize": (5.8, 4.6),
        "title": "F1 Implicit vs. Epochs for Threshold=0.5",
        "footer_label": "Epochs for Threshold(0.5)",
        "armd_label_offset": (-55, -0.06),
        "baseline_label_offset": (8, 0.03),
        "graphsage_label_offset": (-55, -0.10),
    },
    "implicit_threshold_0_9": {
        "figsize": (5.8, 4.6),
        "title": "F1 Implicit vs. Epochs for Threshold=0.9",
        "footer_label": "Epochs for Threshold(0.9)",
        "armd_label_offset": (-55, -0.06),
        "baseline_label_offset": (8, 0.03),
        "graphsage_label_offset": (-55, -0.10),
    },
    "implicit_dataset_50_threshold_0_5": {
        "figsize": (5.8, 4.6),
        "title": "F1 Implicit vs. Epochs for Dataset Size=50",
        "footer_label": "Epochs for Dataset(50)",
        "armd_label_offset": (-55, -0.06),
        "baseline_label_offset": (8, 0.03),
        "graphsage_label_offset": (-55, -0.10),
    },
    "implicit_dataset_150_threshold_0_5": {
        "figsize": (5.8, 4.6),
        "title": "F1 Implicit vs. Epochs for Dataset Size=150",
        "footer_label": "Epochs for Dataset(150)",
        "armd_label_offset": (-55, -0.06),
        "baseline_label_offset": (8, 0.03),
        "graphsage_label_offset": (-55, -0.10),
    },
    "implicit_dataset_450_threshold_0_5": {
        "figsize": (5.8, 4.6),
        "title": "F1 Implicit vs. Epochs for Dataset Size=450",
        "footer_label": "Epochs for Dataset(450)",
        "armd_label_offset": (-55, -0.06),
        "baseline_label_offset": (8, 0.03),
        "graphsage_label_offset": (-55, -0.10),
    },
    "implicit_dataset_450_thresholds": {
        "figsize": (5.8, 4.6),
        "title": "F1 Implicit vs. Epochs for Dataset Size=450",
        "footer_label": "Epochs for Dataset(450)",
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

IMPLICIT_X_VALUES = [50, 100, 200]

ARMD_SAMPLE_SERIES = {
    "50": {
        "no_threshold": {
            "parameter_kpi_f1": [0.17931, 0.17931, 0.17931],
            "parameter_kpi_f1_std": [0.0, 0.0, 0.0],
            "indirect_f1": [0.0, 0.0, 0.0],
            "indirect_f1_std": [0.0, 0.0, 0.0],
            "implicit_f1": [0.086957, 0.086957, 0.086957],
            "implicit_f1_std": [0.0, 0.0, 0.0],
        },
        "0.50": {
            "parameter_kpi_f1": [0.363636, 0.433333, 0.52],
            "parameter_kpi_f1_std": [0.0, 0.0, 0.0],
            "indirect_f1": [0.0, 0.0, 0.0],
            "indirect_f1_std": [0.0, 0.0, 0.0],
            "implicit_f1": [0.173913, 0.244898, 0.322581],
            "implicit_f1_std": [0.0, 0.0, 0.0],
        },
    },
    "150": {
        "no_threshold": {
            "parameter_kpi_f1": [0.17931, 0.17931, 0.17931],
            "parameter_kpi_f1_std": [0.0, 0.0, 0.0],
            "indirect_f1": [0.0, 0.0, 0.0],
            "indirect_f1_std": [0.0, 0.0, 0.0],
            "implicit_f1": [0.086957, 0.086957, 0.086957],
            "implicit_f1_std": [0.0, 0.0, 0.0],
        },
        "0.50": {
            "parameter_kpi_f1": [0.363636, 0.433333, 0.52],
            "parameter_kpi_f1_std": [0.0, 0.0, 0.0],
            "indirect_f1": [0.0, 0.0, 0.0],
            "indirect_f1_std": [0.0, 0.0, 0.0],
            "implicit_f1": [0.166667, 0.230769, 0.363636],
            "implicit_f1_std": [0.0, 0.0, 0.0],
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


def merge_sample_series(base: dict, override: dict) -> dict:
    merged = json.loads(json.dumps(base))
    for sample_size, sample_payload in (override or {}).items():
        merged.setdefault(sample_size, {})
        for threshold_key, threshold_payload in sample_payload.items():
            merged[sample_size].setdefault(threshold_key, {})
            merged[sample_size][threshold_key].update(threshold_payload)
    return merged


def merge_chart(reference_chart: dict, armd_chart: dict) -> dict:
    merged = dict(reference_chart)
    merged_series = dict(reference_chart.get("series", {}))
    merged_series.update(armd_chart.get("series", {}))
    merged["series"] = merged_series
    return merged


def zero_err(values: list[float]) -> list[float]:
    return [0.0] * len(values)


def build_manual_chart(y_label: str, series: dict[str, dict]) -> dict:
    return {
        "title": "",
        "subtitle": "",
        "x_label": "",
        "y_label": y_label,
        "x_values": list(IMPLICIT_X_VALUES),
        "series": series,
    }


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
    x_values = list(chart["x_values"])
    plt.rcParams.update(
        {
            "font.family": "serif",
            "font.size": 7,
            "axes.titlesize": 8,
            "axes.labelsize": 7,
            "legend.fontsize": 5.2,
            "xtick.labelsize": 6,
            "ytick.labelsize": 6,
        }
    )
    fig, ax = plt.subplots(figsize=layout["figsize"])
    for series_name, payload in chart["series"].items():
        group = payload.get("group", "random")
        style = STYLES.get(group, STYLES["random"])
        series_x = list(payload.get("x_values", x_values))
        series_y = list(payload["y"][: len(series_x)])
        ax.plot(
            series_x,
            series_y,
            label=series_name,
            color=style["color"],
            marker=style["marker"],
            linestyle=style["linestyle"],
            linewidth=1.15,
            markersize=4.0,
            markerfacecolor=style.get("markerfacecolor", style["color"]),
            markeredgewidth=0.7,
            markeredgecolor=style["color"] if group == "random" else "black",
            alpha=0.98,
            zorder=3,
        )

    ax.set_title(layout["title"])
    ax.set_xlabel("Epochs")
    ax.set_ylabel(chart["y_label"])
    ax.set_ylim(-0.02, 1.05)
    x_ticks = x_values
    ax.set_xticks(x_ticks)
    if x_ticks:
        x_padding = 20 if x_ticks[-1] <= 200 else 35
        ax.set_xlim(0, x_ticks[-1] + x_padding)
    ax.tick_params(axis="both", width=0.8, length=4)
    ax.grid(True, linestyle="--", linewidth=0.45, alpha=0.28)
    ax.legend(
        loc="upper center",
        bbox_to_anchor=(0.5, -0.17),
        frameon=True,
        framealpha=0.95,
        fancybox=False,
        borderpad=0.18,
        labelspacing=0.16,
        columnspacing=0.7,
        handlelength=1.45,
        handletextpad=0.4,
        ncol=2,
    )
    for spine in ax.spines.values():
        spine.set_linewidth(0.8)
    fig.subplots_adjust(bottom=0.28, left=0.12, right=0.985, top=0.88)
    output_path = output_dir / OUTPUT_NAMES[chart_name]
    fig.savefig(output_path, dpi=260, bbox_inches="tight")
    plt.close(fig)
    return str(output_path)


def build_sample_chart(reference_chart: dict, sample_series: dict, sample_size: str, metric_key: str, std_key: str) -> dict:
    payload = sample_series[sample_size]
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
                "group": f"armd_{sample_size}",
                "y": payload["0.50"][metric_key],
                "yerr": payload["0.50"][std_key],
            },
            f"GraphSAGE-CL {sample_size}": reference_chart["series"][f"GraphSAGE-CL {sample_size}"],
        },
    }


def build_threshold_dataset_chart(
    reference_chart: dict,
    armd_chart: dict,
    sample_series: dict,
    metric_key: str,
    std_key: str,
) -> dict:
    return {
        "title": reference_chart["title"],
        "subtitle": reference_chart.get("subtitle", ""),
        "x_label": reference_chart.get("x_label", ""),
        "y_label": reference_chart["y_label"],
        "x_values": list(reference_chart["x_values"]),
        "series": {
            "Random": reference_chart["series"]["Random"],
            "GraphSAGE-CL 50": reference_chart["series"]["GraphSAGE-CL 50"],
            "ARMD-GreenRAN 50": {
                "group": "armd_50",
                "x_values": [50, 100, 200],
                "y": sample_series["50"]["0.50"][metric_key],
                "yerr": sample_series["50"]["0.50"][std_key],
            },
            "GraphSAGE-CL 150": reference_chart["series"]["GraphSAGE-CL 150"],
            "ARMD-GreenRAN 150": {
                "group": "armd_150",
                "x_values": [50, 100, 200],
                "y": sample_series["150"]["0.50"][metric_key],
                "yerr": sample_series["150"]["0.50"][std_key],
            },
            "GraphSAGE-CL 450": reference_chart["series"]["GraphSAGE-CL 450"],
            "ARMD-GreenRAN 450": {
                "group": "armd_450",
                "y": armd_chart["series"]["ARMD-GreenRAN chosen (450 samples)"]["y"],
                "yerr": armd_chart["series"]["ARMD-GreenRAN chosen (450 samples)"]["yerr"],
            },
        },
    }


def build_threshold_single_chart(reference_chart: dict, threshold_key: str, random_series: dict) -> dict:
    threshold_map = {
        "0.2": ("GraphSAGE-CL 0.2", "ARMD-GreenRAN 0.2"),
        "0.5": ("GraphSAGE-CL 0.5", "ARMD-GreenRAN 0.5"),
        "0.9": ("GraphSAGE-CL 0.9", "ARMD-GreenRAN 0.9"),
    }
    graphsage_key, armd_key = threshold_map[threshold_key]
    return {
        "title": reference_chart["title"],
        "subtitle": reference_chart.get("subtitle", ""),
        "x_label": reference_chart.get("x_label", ""),
        "y_label": reference_chart["y_label"],
        "x_values": list(reference_chart["x_values"]),
        "series": {
            "Random": random_series,
            graphsage_key: reference_chart["series"][graphsage_key],
            armd_key: reference_chart["series"][armd_key],
        },
    }


def build_implicit_threshold_chart(threshold_key: str, reference_payload: dict, armd_payload: dict) -> dict:
    group_map = {
        "0.2": "graphsage_threshold_02",
        "0.5": "graphsage_threshold_05",
        "0.9": "graphsage_threshold_09",
    }
    armd_map = {
        "0.2": ("ARMD-GreenRAN 0.2", "0.20", "armd_threshold_02"),
        "0.5": ("ARMD-GreenRAN 0.5", "0.50", "armd_threshold_05"),
        "0.9": ("ARMD-GreenRAN 0.9", "0.90", "armd_threshold_09"),
    }
    armd_label, armd_series_key, armd_group = armd_map[threshold_key]
    return build_manual_chart(
        "F1 Score",
        {
            "Random": {
                "group": "random",
                "y": reference_payload["Random"],
                "yerr": zero_err(reference_payload["Random"]),
            },
            f"GraphSAGE-CL {threshold_key}": {
                "group": group_map[threshold_key],
                "y": reference_payload[f"GraphSAGE-CL {threshold_key}"],
                "yerr": zero_err(reference_payload[f"GraphSAGE-CL {threshold_key}"]),
            },
            armd_label: {
                "group": armd_group,
                "y": armd_payload[armd_series_key],
                "yerr": zero_err(armd_payload[armd_series_key]),
            },
        },
    )


def build_implicit_threshold_dataset_chart(reference_payload: dict, armd_payload: dict, sample_series: dict) -> dict:
    return build_manual_chart(
        "F1 Score",
        {
            "Random": {
                "group": "random",
                "y": reference_payload["Random"],
                "yerr": zero_err(reference_payload["Random"]),
            },
            "GraphSAGE-CL 50": {
                "group": "graphsage_50",
                "y": reference_payload["GraphSAGE-CL 50"],
                "yerr": zero_err(reference_payload["GraphSAGE-CL 50"]),
            },
            "ARMD-GreenRAN 50": {
                "group": "armd_50",
                "y": sample_series["50"]["0.50"]["implicit_f1"],
                "yerr": sample_series["50"]["0.50"]["implicit_f1_std"],
            },
            "GraphSAGE-CL 150": {
                "group": "graphsage_150",
                "y": reference_payload["GraphSAGE-CL 150"],
                "yerr": zero_err(reference_payload["GraphSAGE-CL 150"]),
            },
            "ARMD-GreenRAN 150": {
                "group": "armd_150",
                "y": sample_series["150"]["0.50"]["implicit_f1"],
                "yerr": sample_series["150"]["0.50"]["implicit_f1_std"],
            },
            "GraphSAGE-CL 450": {
                "group": "graphsage_450",
                "y": reference_payload["GraphSAGE-CL 450"],
                "yerr": zero_err(reference_payload["GraphSAGE-CL 450"]),
            },
            "ARMD-GreenRAN 450": {
                "group": "armd_450",
                "y": armd_payload["0.50"],
                "yerr": zero_err(armd_payload["0.50"]),
            },
        },
    )


def build_implicit_dataset_450_chart(reference_payload: dict, armd_payload: dict) -> dict:
    return build_manual_chart(
        "F1 Score",
        {
            "ARMD-GreenRAN No Threshold": {
                "group": "armd_no_threshold",
                "y": armd_payload["no_threshold"],
                "yerr": zero_err(armd_payload["no_threshold"]),
            },
            "GraphSAGE-CL 450": {
                "group": "graphsage_450",
                "y": reference_payload["GraphSAGE-CL 450"],
                "yerr": zero_err(reference_payload["GraphSAGE-CL 450"]),
            },
            "ARMD-GreenRAN 450": {
                "group": "armd_450",
                "y": armd_payload["0.50"],
                "yerr": zero_err(armd_payload["0.50"]),
            },
        },
    )


def build_implicit_dataset_450_thresholds_chart(reference_payload: dict, armd_payload: dict) -> dict:
    return build_manual_chart(
        "F1 Score",
        {
            "GraphSAGE-CL No Threshold": {
                "group": "graphsage_no_threshold",
                "y": [0.89, 0.89, 0.89],
                "yerr": zero_err([0.89, 0.89, 0.89]),
            },
            "ARMD-GreenRAN No Threshold": {
                "group": "armd_no_threshold",
                "y": armd_payload["no_threshold"],
                "yerr": zero_err(armd_payload["no_threshold"]),
            },
            "GraphSAGE-CL 0.2": {
                "group": "graphsage_threshold_02",
                "y": reference_payload["GraphSAGE-CL 0.2"],
                "yerr": zero_err(reference_payload["GraphSAGE-CL 0.2"]),
            },
            "ARMD-GreenRAN 0.2": {
                "group": "armd_threshold_02",
                "y": armd_payload["0.20"],
                "yerr": zero_err(armd_payload["0.20"]),
            },
            "GraphSAGE-CL 0.5": {
                "group": "graphsage_threshold_05",
                "y": reference_payload["GraphSAGE-CL 0.5"],
                "yerr": zero_err(reference_payload["GraphSAGE-CL 0.5"]),
            },
            "ARMD-GreenRAN 0.5": {
                "group": "armd_threshold_05",
                "y": armd_payload["0.50"],
                "yerr": zero_err(armd_payload["0.50"]),
            },
            "GraphSAGE-CL 0.9": {
                "group": "graphsage_threshold_09",
                "y": reference_payload["GraphSAGE-CL 0.9"],
                "yerr": zero_err(reference_payload["GraphSAGE-CL 0.9"]),
            },
            "ARMD-GreenRAN 0.9": {
                "group": "armd_threshold_09",
                "y": armd_payload["0.90"],
                "yerr": zero_err(armd_payload["0.90"]),
            },
        },
    )


def build_implicit_sample_chart(sample_series: dict, sample_size: str, reference_payload: dict) -> dict:
    payload = sample_series[sample_size]
    return build_manual_chart(
        "F1 Score",
        {
            "ARMD-GreenRAN No Threshold": {
                "group": "armd_no_threshold",
                "y": payload["no_threshold"]["implicit_f1"],
                "yerr": payload["no_threshold"]["implicit_f1_std"],
            },
            f"GraphSAGE-CL {sample_size}": {
                "group": f"graphsage_{sample_size}",
                "y": reference_payload[f"GraphSAGE-CL {sample_size}"],
                "yerr": zero_err(reference_payload[f"GraphSAGE-CL {sample_size}"]),
            },
            f"ARMD-GreenRAN {sample_size}": {
                "group": f"armd_{sample_size}",
                "y": payload["0.50"]["implicit_f1"],
                "yerr": payload["0.50"]["implicit_f1_std"],
            },
        },
    )


def main() -> int:
    args = parse_args()
    reference = load_json(Path(args.reference_json))
    armd = load_json(Path(args.armd_json))
    implicit_reference = reference.get("implicit_reference_series", {})
    implicit_armd_450 = armd.get("implicit_450_series", {})
    sample_series = merge_sample_series(ARMD_SAMPLE_SERIES, armd.get("sample_series", {}))
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    for stale_png in output_dir.glob("comparison_*.png"):
        stale_png.unlink()

    merged_charts = {}
    for chart_name, reference_chart in reference.get("charts", {}).items():
        armd_chart = armd.get("charts", {}).get(chart_name, {})
        merged_charts[chart_name] = merge_chart(reference_chart, armd_chart)

    for derived_name, source_name in DERIVED_CHART_SOURCES.items():
        merged_charts[derived_name] = dict(merged_charts[source_name])

    merged_charts["reconstruction_threshold_0_5"] = build_threshold_dataset_chart(
        reference["charts"]["reconstruction_threshold_0_5"],
        armd["charts"]["reconstruction_threshold_0_5"],
        sample_series,
        "parameter_kpi_f1",
        "parameter_kpi_f1_std",
    )
    merged_charts["indirect_threshold_0_5"] = build_threshold_dataset_chart(
        reference["charts"]["indirect_threshold_0_5"],
        armd["charts"]["indirect_threshold_0_5"],
        sample_series,
        "indirect_f1",
        "indirect_f1_std",
    )
    merged_charts["reconstruction_threshold_0_2"] = build_threshold_single_chart(
        merged_charts["reconstruction_dataset_450_thresholds"],
        "0.2",
        reference["charts"]["reconstruction_threshold_0_5"]["series"]["Random"],
    )
    merged_charts["reconstruction_threshold_0_9"] = build_threshold_single_chart(
        merged_charts["reconstruction_dataset_450_thresholds"],
        "0.9",
        reference["charts"]["reconstruction_threshold_0_5"]["series"]["Random"],
    )
    merged_charts["indirect_threshold_0_2"] = build_threshold_single_chart(
        merged_charts["indirect_dataset_450_thresholds"],
        "0.2",
        reference["charts"]["indirect_threshold_0_5"]["series"]["Random"],
    )
    merged_charts["indirect_threshold_0_9"] = build_threshold_single_chart(
        merged_charts["indirect_dataset_450_thresholds"],
        "0.9",
        reference["charts"]["indirect_threshold_0_5"]["series"]["Random"],
    )

    merged_charts["reconstruction_dataset_50_threshold_0_5"] = build_sample_chart(
        merged_charts["reconstruction_threshold_0_5"], sample_series, "50", "parameter_kpi_f1", "parameter_kpi_f1_std"
    )
    merged_charts["indirect_dataset_50_threshold_0_5"] = build_sample_chart(
        merged_charts["indirect_threshold_0_5"], sample_series, "50", "indirect_f1", "indirect_f1_std"
    )
    merged_charts["reconstruction_dataset_150_threshold_0_5"] = build_sample_chart(
        merged_charts["reconstruction_threshold_0_5"], sample_series, "150", "parameter_kpi_f1", "parameter_kpi_f1_std"
    )
    merged_charts["indirect_dataset_150_threshold_0_5"] = build_sample_chart(
        merged_charts["indirect_threshold_0_5"], sample_series, "150", "indirect_f1", "indirect_f1_std"
    )
    merged_charts["implicit_threshold_0_5"] = build_implicit_threshold_dataset_chart(
        implicit_reference, implicit_armd_450, sample_series
    )
    merged_charts["implicit_threshold_0_2"] = build_implicit_threshold_chart(
        "0.2", implicit_reference, implicit_armd_450
    )
    merged_charts["implicit_threshold_0_9"] = build_implicit_threshold_chart(
        "0.9", implicit_reference, implicit_armd_450
    )
    merged_charts["implicit_dataset_50_threshold_0_5"] = build_implicit_sample_chart(sample_series, "50", implicit_reference)
    merged_charts["implicit_dataset_150_threshold_0_5"] = build_implicit_sample_chart(sample_series, "150", implicit_reference)
    merged_charts["implicit_dataset_450_threshold_0_5"] = build_implicit_dataset_450_chart(
        implicit_reference, implicit_armd_450
    )
    merged_charts["implicit_dataset_450_thresholds"] = build_implicit_dataset_450_thresholds_chart(
        implicit_reference, implicit_armd_450
    )

    generated = {}
    for chart_name in PRIMARY_CHARTS:
        merged = merged_charts[chart_name]
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
