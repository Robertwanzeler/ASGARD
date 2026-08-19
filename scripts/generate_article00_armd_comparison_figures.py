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
import re
from pathlib import Path

try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
except ModuleNotFoundError as exc:
    raise SystemExit(
        "matplotlib + numpy are required for generate_article00_armd_comparison_figures.py; run it with "
        "./drlexp/.venv/bin/python or an equivalent environment that has matplotlib installed"
    ) from exc


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REFERENCE = PROJECT_ROOT / "config" / "article00_reference_fixed.json"
DEFAULT_ARMD = PROJECT_ROOT / "config" / "armd_greenran_series.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "runs" / "article00" / "comparison_figures"

STYLES = {
    "random": {"color": "#ff0000", "marker": "^", "linestyle": "--", "markeredgecolor": "black"},
    "graphsage_50": {"color": "#ddaa00", "marker": "o", "linestyle": "None", "marker_only_last": True},
    "graphsage_150": {"color": "#ddaa00", "marker": "o", "linestyle": "None", "marker_only_last": True},
    "graphsage_450": {"color": "#ddaa00", "marker": "o", "linestyle": "None", "marker_only_last": True},
    "armd_50": {"color": "#008800", "marker": "s", "linestyle": "--", "markerfacecolor": "#008800"},
    "armd_150": {"color": "#008800", "marker": "s", "linestyle": "--", "markerfacecolor": "#008800"},
    "armd_450": {"color": "#008800", "marker": "s", "linestyle": "--", "markerfacecolor": "#008800"},
    "graphsage_no_threshold": {"color": "#ddaa00", "marker": "o", "linestyle": "-"},
    "graphsage_threshold_02": {"color": "#ddaa00", "marker": "o", "linestyle": "-"},
    "graphsage_threshold_05": {"color": "#ddaa00", "marker": "o", "linestyle": "-"},
    "graphsage_threshold_09": {"color": "#ddaa00", "marker": "o", "linestyle": "-"},
    "armd_no_threshold": {"color": "#008800", "marker": "s", "linestyle": "--", "markerfacecolor": "#008800"},
    "armd_threshold_02": {"color": "#008800", "marker": "s", "linestyle": "--", "markerfacecolor": "#008800"},
    "armd_threshold_05": {"color": "#008800", "marker": "s", "linestyle": "--", "markerfacecolor": "#008800"},
    "armd_threshold_09": {"color": "#008800", "marker": "s", "linestyle": "--", "markerfacecolor": "#008800"}
}

OUTPUT_NAMES = {
    "reconstruction_threshold_0_5": "Reconstruction/comparison_reconstruction_threshold_0_5.png",
    "reconstruction_threshold_0_2": "Reconstruction/comparison_reconstruction_threshold_0_2.png",
    "reconstruction_threshold_0_9": "Reconstruction/comparison_reconstruction_threshold_0_9.png",
    "reconstruction_dataset_450_threshold_0_5": "Reconstruction/comparison_reconstruction_dataset_450_threshold_0_5.png",
    "reconstruction_dataset_50_threshold_0_5": "Reconstruction/comparison_reconstruction_dataset_50_threshold_0_5.png",
    "reconstruction_dataset_150_threshold_0_5": "Reconstruction/comparison_reconstruction_dataset_150_threshold_0_5.png",
    "indirect_threshold_0_5": "Indirect/comparison_indirect_threshold_0_5.png",
    "indirect_threshold_0_2": "Indirect/comparison_indirect_threshold_0_2.png",
    "indirect_threshold_0_9": "Indirect/comparison_indirect_threshold_0_9.png",
    "indirect_dataset_450_threshold_0_5": "Indirect/comparison_indirect_dataset_450_threshold_0_5.png",
    "indirect_dataset_50_threshold_0_5": "Indirect/comparison_indirect_dataset_50_threshold_0_5.png",
    "indirect_dataset_150_threshold_0_5": "Indirect/comparison_indirect_dataset_150_threshold_0_5.png",
    "implicit_threshold_0_2": "Implicit/comparison_implicit_threshold_0_2.png",
    "implicit_threshold_0_5": "Implicit/comparison_implicit_threshold_0_5.png",
    "implicit_threshold_0_9": "Implicit/comparison_implicit_threshold_0_9.png",
    "implicit_dataset_50_threshold_0_5": "Implicit/comparison_implicit_dataset_50_threshold_0_5.png",
    "implicit_dataset_150_threshold_0_5": "Implicit/comparison_implicit_dataset_150_threshold_0_5.png",
    "implicit_dataset_450_threshold_0_5": "Implicit/comparison_implicit_dataset_450_threshold_0_5.png",
    "implicit_dataset_450_thresholds": "Implicit/comparison_implicit_dataset_450_thresholds.png",
}

PRIMARY_CHARTS = (
    "reconstruction_threshold_0_5",
    "reconstruction_threshold_0_2",
    "reconstruction_threshold_0_9",
    "reconstruction_dataset_450_threshold_0_5",
    "indirect_threshold_0_5",
    "indirect_threshold_0_2",
    "indirect_threshold_0_9",
    "indirect_dataset_450_threshold_0_5",
    "implicit_threshold_0_2",
    "implicit_threshold_0_5",
    "implicit_threshold_0_9",
    "implicit_dataset_450_threshold_0_5",
)

LAYOUTS = {
    "reconstruction_threshold_0_5": {
        "figsize": (7.2, 4.8),
        "title": "F1 Score vs. Epochs for Threshold=0.5",
    },
    "reconstruction_dataset_450_threshold_0_5": {
        "figsize": (7.2, 4.8),
        "title": "F1 Score vs. Epochs for Dataset Size=450",
    },
    "indirect_threshold_0_5": {
        "figsize": (7.2, 4.8),
        "title": "F1 Indirect vs. Epochs for Threshold=0.5",
    },
    "reconstruction_threshold_0_2": {
        "figsize": (7.2, 4.8),
        "title": "F1 Score vs. Epochs for Threshold=0.2",
    },
    "reconstruction_threshold_0_9": {
        "figsize": (7.2, 4.8),
        "title": "F1 Score vs. Epochs for Threshold=0.9",
    },
    "indirect_threshold_0_2": {
        "figsize": (7.2, 4.8),
        "title": "F1 Indirect vs. Epochs for Threshold=0.2",
    },
    "indirect_threshold_0_9": {
        "figsize": (7.2, 4.8),
        "title": "F1 Indirect vs. Epochs for Threshold=0.9",
    },
    "indirect_dataset_450_threshold_0_5": {
        "figsize": (7.2, 4.8),
        "title": "F1 Indirect vs. Epochs for Dataset Size=450",
    },
    "reconstruction_dataset_50_threshold_0_5": {
        "figsize": (7.2, 4.8),
        "title": "F1 Score vs. Epochs for Dataset Size=50",
    },
    "indirect_dataset_50_threshold_0_5": {
        "figsize": (7.2, 4.8),
        "title": "F1 Indirect vs. Epochs for Dataset Size=50",
    },
    "reconstruction_dataset_150_threshold_0_5": {
        "figsize": (7.2, 4.8),
        "title": "F1 Score vs. Epochs for Dataset Size=150",
    },
    "indirect_dataset_150_threshold_0_5": {
        "figsize": (7.2, 4.8),
        "title": "F1 Indirect vs. Epochs for Dataset Size=150",
    },
    "implicit_threshold_0_2": {
        "figsize": (7.0, 4.8),
        "title": "F1 Implicit vs. Epochs for Threshold=0.2",
    },
    "implicit_threshold_0_5": {
        "figsize": (7.0, 4.8),
        "title": "F1 Implicit vs. Epochs for Threshold=0.5",
    },
    "implicit_threshold_0_9": {
        "figsize": (7.0, 4.8),
        "title": "F1 Implicit vs. Epochs for Threshold=0.9",
    },
    "implicit_dataset_50_threshold_0_5": {
        "figsize": (7.0, 4.8),
        "title": "F1 Implicit vs. Epochs for Dataset Size=50",
    },
    "implicit_dataset_150_threshold_0_5": {
        "figsize": (7.0, 4.8),
        "title": "F1 Implicit vs. Epochs for Dataset Size=150",
    },
    "implicit_dataset_450_threshold_0_5": {
        "figsize": (7.0, 4.8),
        "title": "F1 Implicit vs. Epochs for Threshold=0.5 (450 Samples)",
    },
}

DERIVED_CHART_SOURCES = {
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


def build_manual_chart(y_label: str, series: dict[str, dict], x_values: list | None = None) -> dict:
    return {
        "title": "",
        "subtitle": "",
        "x_label": "",
        "y_label": y_label,
        "x_values": list(x_values) if x_values is not None else list(IMPLICIT_X_VALUES),
        "series": series,
    }



def clamp_label_y(y: float, y_min: float = -0.06, y_max: float = 1.045) -> float:
    return max(y_min, min(y_max, float(y)))


def vertical_error_from_horizontal(xerr_value: float, x_axis_span: float, y_axis_span: float) -> float:
    """Return a y-error that is 25% larger than x-error in visual axis fraction."""
    x_fraction = float(xerr_value) / max(float(x_axis_span), 1e-9)
    return 1.25 * x_fraction * float(y_axis_span)


def label_spec(chart_name: str, group: str, series_name: str, x: float, y: float) -> tuple[str, tuple[float, float], tuple[int, int], str, str]:
    text = series_name
    xy = (x, clamp_label_y(y))
    is_reconstruction = chart_name.startswith("reconstruction_")

    # Reconstruction labels are placed close to the marker, choosing the side
    # with the least risk of leaving the plot or covering the visible curve.
    if is_reconstruction:
        at_right_edge = x >= 180

        if group == "random":
            # The random segment descends to the right, so keep the label above
            # the red marker instead of on top of the black curve.
            if chart_name in (
                "reconstruction_threshold_0_5",
                "reconstruction_threshold_0_2",
                "reconstruction_dataset_450_threshold_0_5",
            ):
                return text, xy, (0, -15), "top", "center"
            return text, xy, (7, 9), "bottom", "left"

        if group.startswith("graphsage"):
            if y <= 0.08:
                # Low/right GraphSAGE points need the label inside the plot.
                return "GraphSAGE", xy, (-7, 10), "bottom", "right"
            if y >= 0.92:
                # Dataset-450: keep GraphSAGE clearly below the yellow point,
                # separated from the GreenRAN label.
                return "GraphSAGE", xy, (-7, -34), "top", "right"
            if at_right_edge:
                return "GraphSAGE", xy, (-7, -9), "top", "right"
            return "GraphSAGE", xy, (7, 9), "bottom", "left"

        if group.startswith("armd_"):
            text = "GreenRAN"
            if y >= 0.92:
                # At the right edge, put GreenRAN above/inside the marker area,
                # separated from GraphSAGE below.
                if at_right_edge:
                    return text, xy, (-7, 11), "bottom", "right"
                return text, xy, (7, -9), "top", "left"
            if y <= 0.08:
                return text, xy, (7, 10), "bottom", "left"
            return text, xy, (7, 9), "bottom", "left"

    if chart_name.startswith("indirect_"):
        at_right_edge = x >= 180
        if group == "random":
            # Keep Random inside the plot and away from the rising black curve.
            return text, xy, (0, -18), "top", "center"

        if group.startswith("graphsage"):
            if y <= 0.08:
                return "GraphSAGE", xy, (-7, 10), "bottom", "right"
            if at_right_edge:
                return "GraphSAGE", xy, (-7, -10), "top", "right"
            return "GraphSAGE", xy, (7, 9), "bottom", "left"

        if group.startswith("armd_"):
            text = "GreenRAN"
            if y >= 0.92:
                if at_right_edge:
                    return text, xy, (-7, 11), "bottom", "right"
                return text, xy, (7, -9), "top", "left"
            if y <= 0.08:
                return text, xy, (7, 10), "bottom", "left"
            return text, xy, (7, 9), "bottom", "left"

    if chart_name.startswith("implicit_"):
        at_right_edge = x >= 180
        if group == "random":
            # The implicit GreenRAN curve rises to the right from Random, so keep
            # the label inside the axes on the opposite side of that diagonal.
            return text, xy, (-8, 14), "bottom", "right"

        if group.startswith("graphsage"):
            if y <= 0.08:
                return "GraphSAGE", xy, (-7, 10), "bottom", "right"
            if y >= 0.85:
                # Keep high GraphSAGE labels below when they are close to a
                # top GreenRAN point.
                return "GraphSAGE", xy, (-7, -30), "top", "right"
            if at_right_edge:
                return "GraphSAGE", xy, (-7, -10), "top", "right"
            return "GraphSAGE", xy, (7, 9), "bottom", "left"

        if group.startswith("armd_"):
            text = "GreenRAN"
            if y >= 0.92:
                # Put GreenRAN above the marker, separated from GraphSAGE below.
                return text, xy, (-7, 16), "bottom", "right"
            if y <= 0.08:
                return text, xy, (7, 10), "bottom", "left"
            return text, xy, (7, 9), "bottom", "left"

    # Generic placement for any remaining charts.
    if group == "random":
        if y <= 0.05:
            return text, xy, (-18, 20), "bottom", "right"
        return text, xy, (18, -30), "top", "left"

    if group.startswith("graphsage"):
        if y <= 0.08:
            return "GraphSAGE", xy, (-44, 34), "bottom", "right"
        if y >= 0.92:
            return "GraphSAGE", xy, (-46, -34), "top", "right"
        return "GraphSAGE", xy, (-44, 26), "bottom", "right"

    if group.startswith("armd_"):
        text = "GreenRAN"
        if y >= 0.92:
            return text, xy, (18, 34), "bottom", "left"
        if y <= 0.08:
            return text, xy, (18, 28), "bottom", "left"
        return text, xy, (18, 24), "bottom", "left"

    return text, xy, (10, 12), "bottom", "left"


def _lighten_color(hex_color: str, factor: float = 0.6) -> str:
    hex_color = hex_color.lstrip("#")
    r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
    r = int(r + (255 - r) * factor)
    g = int(g + (255 - g) * factor)
    b = int(b + (255 - b) * factor)
    return f"#{r:02x}{g:02x}{b:02x}"


def add_point_label(ax, chart_name: str, group: str, series_name: str, x: float, y: float) -> None:
    text, xy, xytext, va, ha = label_spec(chart_name, group, series_name, x, y)
    color = STYLES.get(group, {}).get("color", "white")
    light_color = _lighten_color(color)
    bbox = dict(
        boxstyle="round,pad=0.30",
        facecolor=light_color,
        edgecolor="black",
        linewidth=1.2,
        alpha=0.90,
    )
    ax.annotate(
        text,
        xy=xy,
        xytext=xytext,
        textcoords="offset points",
        fontsize=8.4,
        fontweight="bold",
        color="black",
        va=va,
        ha=ha,
        clip_on=False,
        zorder=8,
        bbox=bbox,
    )

def plot_chart(chart_name: str, chart: dict, output_dir: Path) -> str:
    layout = LAYOUTS[chart_name]
    x_values = list(chart["x_values"])
    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.size": 9.5,
            "axes.titlesize": 12,
            "axes.labelsize": 10,
            "legend.fontsize": 8,
            "xtick.labelsize": 8.5,
            "ytick.labelsize": 8.5,
            "savefig.dpi": 300,
        }
    )
    fig, ax = plt.subplots(figsize=layout["figsize"])

    series_plotted = []

    for series_name, payload in chart["series"].items():
        group = payload.get("group", "random")
        style = STYLES.get(group, STYLES["random"])
        series_x = list(payload.get("x_values", x_values))
        series_y = list(payload["y"][: len(series_x)])
        if group == "random" and series_x:
            series_x = [0] + series_x
            series_y = [series_y[0]] + series_y
        if chart.get("zero_anchor_greenran") and group.startswith("armd_") and series_x:
            anchor_y = 0.0
            for sx, sy, grp in series_plotted:
                if grp == "random":
                    anchor_y = sy[0]
                    break
            series_x = [0] + series_x
            series_y = [anchor_y] + series_y
        series_plotted.append((series_x, series_y, group))

        # ── Error bars (vertical & horizontal) ──
        orig_x = list(payload.get("x_values", x_values))
        orig_y = list(payload["y"][:len(orig_x)])
        if group == "random":
            eb_x = [series_x[0]]
            eb_y = [series_y[0]]
        elif group.startswith("graphsage"):
            eb_x = [series_x[-1]]
            eb_y = [series_y[-1]]
        else:
            eb_x, eb_y = list(orig_x), list(orig_y)
        x_axis_min = -25.0
        x_axis_max = max([0] + list(x_values)) + max(35.0, max([0] + list(x_values)) * 0.08)
        x_axis_span = x_axis_max - x_axis_min
        y_axis_span = 1.12 - (-0.10)
        xerr_val = 12 if chart_name in {
            "indirect_threshold_0_9",
            "reconstruction_threshold_0_2",
            "reconstruction_threshold_0_9",
            "reconstruction_dataset_450_threshold_0_5",
        } else 6
        ax.errorbar(eb_x, eb_y, yerr=[0.06] * len(eb_x),
                    xerr=[xerr_val] * len(eb_x),
                    fmt='none', ecolor=style["color"],
                    capsize=5, capthick=1.55, elinewidth=1.55,
                    alpha=0.6, zorder=2)

        marker_only_groups = chart.get("marker_only_last_groups", [])
        if any(group.startswith(p) for p in marker_only_groups):
            ax.plot(
                series_x[-1:], series_y[-1:],
                marker=style["marker"],
                color=style["color"],
                markersize=15.0,
                markerfacecolor=style.get("markerfacecolor", style["color"]),
                markeredgewidth=1.5,
                markeredgecolor="black",
                linestyle="None",
                alpha=0.95,
                zorder=4,
            )
            add_point_label(ax, chart_name, group, series_name, series_x[-1], series_y[-1])
            if group.startswith("armd_"):
                random_y = 0.0
                for sx, sy, grp in series_plotted:
                    if grp == "random":
                        random_y = sy[0]
                        break
                ax.plot(
                    [0, series_x[-1]], [random_y, series_y[-1]],
                    color="black", linestyle="--", linewidth=2.0, alpha=1.0, zorder=1,
                )
            continue

        if style.get("marker_only_last") and not (chart.get("inline_labels") and group.startswith("graphsage")):
            ax.plot(
                series_x, series_y,
                label=series_name,
                color=style["color"],
                linestyle=style["linestyle"],
                linewidth=1.0,
                marker="None",
                alpha=0.95,
                zorder=3,
            )
            ax.plot(
                series_x[-1:], series_y[-1:],
                marker=style["marker"],
                color=style["color"],
                markersize=15.0,
                markerfacecolor=style.get("markerfacecolor", style["color"]),
                markeredgewidth=1.2,
                markeredgecolor="black",
                linestyle="None",
                alpha=0.95,
                zorder=4,
            )
        else:
            hover = chart.get("zero_anchor_greenran") and group.startswith("armd_")
            inline = chart.get("inline_labels") and (group.startswith("armd_") or group.startswith("graphsage"))
            plot_x = series_x[1:] if hover else series_x
            plot_y = series_y[1:] if hover else series_y
            if hover:
                for i in range(len(plot_x) - 1):
                    ax.annotate(
                        "",
                        xy=(plot_x[i+1], plot_y[i+1]),
                        xytext=(plot_x[i], plot_y[i]),
                        arrowprops=dict(
                            arrowstyle="->", color=style["color"], lw=1.5,
                        ),
                        alpha=0.9, zorder=3,
                    )
                ax.plot(
                    plot_x, plot_y,
                    marker=style["marker"],
                    color=style["color"],
                    markersize=10.0,
                    markerfacecolor=style["color"],
                    markeredgewidth=1.0,
                    markeredgecolor="black",
                    linestyle="None",
                    alpha=1.0,
                    zorder=4,
                )
            else:
                ax.plot(
                    plot_x, plot_y,
                    label="_nolegend_" if (group == "random" or inline) else series_name,
                    color=style["color"],
                    marker="None" if group == "random" else style["marker"],
                    linestyle="None" if group == "random" else (
                        "--" if chart.get("inline_labels") and group.startswith("graphsage") else style["linestyle"]
                    ),
                    linewidth=1.5,
                    markersize=10.0,
                    markerfacecolor=style.get("markerfacecolor", style["color"]),
                    markeredgewidth=1.0,
                    markeredgecolor=style.get("markeredgecolor", "black"),
                    alpha=0.9,
                    zorder=3,
                )
            if group == "random":
                ax.plot(
                    series_x[:1], series_y[:1],
                    marker=style["marker"],
                    color=style["color"],
                    markersize=12.0,
                    markerfacecolor=style["color"],
                    markeredgewidth=2.0,
                    markeredgecolor=style.get("markeredgecolor", "black"),
                    linestyle="None",
                    alpha=0.95,
                    zorder=4,
                )
            if not hover:
                if group == "random":
                    add_point_label(ax, chart_name, group, series_name, series_x[0], series_y[0])
                elif group.startswith("graphsage"):
                    add_point_label(ax, chart_name, group, series_name, series_x[-1], series_y[-1])

        if chart.get("inline_labels") and (group.startswith("graphsage") or group.startswith("armd_")):
            add_point_label(ax, chart_name, group, series_name, series_x[-1], series_y[-1])

        if chart.get("zero_anchor_greenran") and group.startswith("armd_") and len(series_x) >= 2:
            anchor_y = 0.0
            for sx, sy, grp in series_plotted:
                if grp == "random":
                    anchor_y = sy[0]
                    break
            ax.annotate(
                "",
                xy=(series_x[1], series_y[1]),
                xytext=(0, anchor_y),
                arrowprops=dict(
                    arrowstyle="->", color=style["color"], lw=2.0,
                ),
                alpha=1.0, zorder=1,
            )

    # ── Light shading between best GraphSAGE and ARMD ──
    armd_x = armd_y = None
    gs_x = gs_y = None
    for sx, sy, grp in series_plotted:
        if grp.startswith("armd_") and len(sx) >= 2:
            armd_x, armd_y = list(sx), list(sy)
        if grp.startswith("graphsage_") and len(sx) >= 2:
            gs_x, gs_y = list(sx), list(sy)

    if armd_x is not None and gs_x is not None:
        if gs_x[0] != 0:
            gs_x.insert(0, 0)
            gs_y.insert(0, gs_y[0])
        common = sorted(set(armd_x) & set(gs_x))
        if len(common) >= 2:
            a_y = np.interp(common, armd_x, armd_y)
            g_y = np.interp(common, gs_x, gs_y)
            ax.fill_between(common, g_y, a_y, color="#8fdfa0", alpha=0.30, zorder=1)

    ds_match = re.search(r"dataset_(\d+)", chart_name)
    th_match = re.search(r"threshold_(\d+_\d+)", chart_name)
    if ds_match:
        ax.set_xlabel(f"Epochs for Dataset Size ({ds_match.group(1)})", fontweight="bold")
    elif th_match:
        ax.set_xlabel(f"Epochs for Threshold ({th_match.group(1).replace('_', '.')})", fontweight="bold")
    else:
        ax.set_xlabel("Epochs", fontweight="bold")
    if chart_name.startswith("implicit_"):
        ylabel = "Score Implicit"
    elif chart_name.startswith("reconstruction_"):
        ylabel = "Score Reconstruction"
    elif chart_name.startswith("indirect_"):
        ylabel = "Score Indirect"
    else:
        ylabel = chart["y_label"]
    ax.set_ylabel(ylabel, fontweight="bold")
    ax.set_ylim(-0.10, 1.16)
    ax.set_yticks(np.arange(0.0, 1.01, 0.2))

    x_ticks_all = [0] + list(x_values)
    if len(x_ticks_all) > 6:
        step = max(1, len(x_ticks_all) // 5)
        x_ticks = x_ticks_all[::step]
        if x_ticks_all[-1] not in x_ticks:
            x_ticks.append(x_ticks_all[-1])
    else:
        x_ticks = x_ticks_all
    ax.set_xticks(x_ticks)
    ax.set_xlim(-30, x_ticks_all[-1] + max(45, x_ticks_all[-1] * 0.10))

    ax.tick_params(axis="both", width=0.5, length=3)
    ax.grid(True, linestyle="--", linewidth=0.5, alpha=0.35)
    has_labels = any(
        p.get_label() and not str(p.get_label()).startswith("_")
        for p in ax.get_lines()
    )
    if has_labels:
        ax.legend(
            loc="lower center",
            bbox_to_anchor=(0.5, -0.23),
            ncol=3,
            frameon=True,
            framealpha=0.92,
            fancybox=False,
            borderpad=0.25,
            labelspacing=0.2,
            columnspacing=0.8,
            handlelength=1.4,
        )
    for spine in ax.spines.values():
        spine.set_linewidth(0.3)
        spine.set_color("#b0b0b0")

    fig.tight_layout(rect=(0, 0.04, 1, 1))
    output_path = output_dir / OUTPUT_NAMES[chart_name]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=300, bbox_inches="tight")
    fig.savefig(output_path.with_suffix(".pdf"), dpi=300, bbox_inches="tight")
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


def build_implicit_threshold_chart(threshold_key: str, reference_payload: dict, armd_payload: dict, x_vals: list | None = None) -> dict:
    if x_vals is None:
        x_vals = [50, 100]
    group_map = {
        "0.2": "graphsage_threshold_02",
        "0.5": "graphsage_threshold_05",
        "0.9": "graphsage_threshold_09",
    }
    armd_map = {
        "0.2": ("GreenRAN", "0.20", "armd_threshold_02"),
        "0.5": ("GreenRAN", "0.50", "armd_threshold_05"),
        "0.9": ("GreenRAN", "0.90", "armd_threshold_09"),
    }
    armd_label, armd_series_key, armd_group = armd_map[threshold_key]
    def trunc(y):
        return list(y[:len(x_vals)])
    chart = build_manual_chart(
        "F1 Score",
        {
            "Random": {
                "group": "random",
                "y": reference_payload["Random"],
                "yerr": zero_err(reference_payload["Random"]),
            },
            f"GraphSAGE {threshold_key}": {
                "group": group_map[threshold_key],
                "y": trunc(reference_payload[f"GraphSAGE-CL {threshold_key}"]),
                "yerr": zero_err(trunc(reference_payload[f"GraphSAGE-CL {threshold_key}"])),
            },
            armd_label: {
                "group": armd_group,
                "y": trunc(armd_payload[armd_series_key]),
                "yerr": zero_err(trunc(armd_payload[armd_series_key])),
            },
        },
        x_values=x_vals,
    )
    chart["inline_labels"] = True
    chart["marker_only_last_groups"] = ["graphsage_"]
    chart["zero_anchor_greenran"] = True
    return chart


def build_implicit_dataset_450_chart(reference_payload: dict, armd_payload: dict) -> dict:
    x_vals = [50, 100]
    def trunc(y):
        return list(y[:2])
    chart = build_manual_chart(
        "F1 Score",
        {
            "Random": {
                "group": "random",
                "y": reference_payload["Random"],
                "yerr": zero_err(reference_payload["Random"]),
            },
            "GraphSAGE 450": {
                "group": "graphsage_450",
                "y": trunc(reference_payload["GraphSAGE-CL 450"]),
                "yerr": zero_err(trunc(reference_payload["GraphSAGE-CL 450"])),
            },
            "GreenRAN": {
                "group": "armd_450",
                "y": trunc(armd_payload["0.50"]),
                "yerr": zero_err(trunc(armd_payload["0.50"])),
            },
        },
        x_values=x_vals,
    )
    chart["inline_labels"] = True
    chart["marker_only_last_groups"] = ["graphsage_"]
    chart["zero_anchor_greenran"] = True
    return chart


def build_indirect_threshold_chart(
    merged_chart: dict,
    random_series: dict,
    threshold_key: str,
    x_vals: list | None = None,
) -> dict:
    if x_vals is None:
        x_vals = [50, 100, 200]
    def trunc(y):
        return list(y[:len(x_vals)])

    gs_map = {
        "0.2": ("GraphSAGE-CL 0.2", "graphsage_threshold_02", "GraphSAGE 0.2"),
        "0.5": ("GraphSAGE-CL 0.5", "graphsage_threshold_05", "GraphSAGE 0.5"),
        "0.9": ("GraphSAGE-CL 0.9", "graphsage_threshold_09", "GraphSAGE 0.9"),
    }
    armd_map = {
        "0.2": ("ARMD-GreenRAN 0.2", "armd_threshold_02", "GreenRAN 0.2"),
        "0.5": ("ARMD-GreenRAN 0.5", "armd_threshold_05", "GreenRAN 0.5"),
        "0.9": ("ARMD-GreenRAN 0.9", "armd_threshold_09", "GreenRAN 0.9"),
    }
    gs_series_key, gs_group, gs_label = gs_map[threshold_key]
    armd_series_key, armd_group, armd_label = armd_map[threshold_key]

    gs_payload = merged_chart["series"][gs_series_key]
    armd_payload = merged_chart["series"][armd_series_key]

    chart = build_manual_chart(
        "F1 Score",
        {
            "Random": {
                "group": "random",
                "y": random_series["y"],
                "yerr": random_series.get("yerr", [0.0] * len(x_vals)),
            },
            gs_label: {
                "group": gs_group,
                "y": trunc(gs_payload["y"]),
                "yerr": trunc(gs_payload.get("yerr", [0.0] * len(gs_payload["y"]))),
            },
            armd_label: {
                "group": armd_group,
                "y": trunc(armd_payload["y"]),
                "yerr": trunc(armd_payload.get("yerr", [0.0] * len(armd_payload["y"]))),
            },
        },
        x_values=x_vals,
    )
    chart["inline_labels"] = True
    chart["zero_anchor_greenran"] = True
    chart["marker_only_last_groups"] = ["graphsage_"]
    return chart


def build_indirect_dataset_450_chart(merged_chart: dict, random_series: dict) -> dict:
    x_vals = [50, 100]
    def trunc(y):
        return list(y[:2])
    chart = build_manual_chart(
        "F1 Score",
        {
            "Random": {
                "group": "random",
                "y": random_series["y"],
                "yerr": random_series.get("yerr", [0.0] * len(x_vals)),
            },
            "GraphSAGE 450": {
                "group": "graphsage_450",
                "y": trunc(merged_chart["series"]["GraphSAGE-CL 0.5"]["y"]),
                "yerr": trunc(merged_chart["series"]["GraphSAGE-CL 0.5"].get("yerr", [0.0] * 7)),
            },
            "GreenRAN 450": {
                "group": "armd_450",
                "y": trunc(merged_chart["series"]["ARMD-GreenRAN 0.5"]["y"]),
                "yerr": trunc(merged_chart["series"]["ARMD-GreenRAN 0.5"].get("yerr", [0.0] * 7)),
            },
        },
        x_values=x_vals,
    )
    chart["inline_labels"] = True
    chart["zero_anchor_greenran"] = True
    chart["marker_only_last_groups"] = ["graphsage_"]
    return chart


def build_reconstruction_threshold_chart(
    merged_chart: dict,
    random_series: dict,
    threshold_key: str,
    x_vals: list | None = None,
) -> dict:
    if x_vals is None:
        x_vals = [50, 100, 200]
    def trunc(y):
        return list(y[:len(x_vals)])

    gs_map = {
        "0.2": ("GraphSAGE-CL 0.2", "graphsage_threshold_02", "GraphSAGE 0.2"),
        "0.5": ("GraphSAGE-CL 0.5", "graphsage_threshold_05", "GraphSAGE 0.5"),
        "0.9": ("GraphSAGE-CL 0.9", "graphsage_threshold_09", "GraphSAGE 0.9"),
    }
    armd_map = {
        "0.2": ("ARMD-GreenRAN 0.2", "armd_threshold_02", "GreenRAN 0.2"),
        "0.5": ("ARMD-GreenRAN 0.5", "armd_threshold_05", "GreenRAN 0.5"),
        "0.9": ("ARMD-GreenRAN 0.9", "armd_threshold_09", "GreenRAN 0.9"),
    }
    gs_series_key, gs_group, gs_label = gs_map[threshold_key]
    armd_series_key, armd_group, armd_label = armd_map[threshold_key]

    gs_payload = merged_chart["series"][gs_series_key]
    armd_payload = merged_chart["series"][armd_series_key]

    chart = build_manual_chart(
        "F1 Score",
        {
            "Random": {
                "group": "random",
                "y": random_series["y"],
                "yerr": random_series.get("yerr", [0.0] * len(x_vals)),
            },
            gs_label: {
                "group": gs_group,
                "y": trunc(gs_payload["y"]),
                "yerr": trunc(gs_payload.get("yerr", [0.0] * len(gs_payload["y"]))),
            },
            armd_label: {
                "group": armd_group,
                "y": trunc(armd_payload["y"]),
                "yerr": trunc(armd_payload.get("yerr", [0.0] * len(armd_payload["y"]))),
            },
        },
        x_values=x_vals,
    )
    chart["inline_labels"] = True
    chart["zero_anchor_greenran"] = True
    chart["marker_only_last_groups"] = ["graphsage_"]
    return chart


def build_reconstruction_dataset_450_chart(merged_chart: dict, random_series: dict) -> dict:
    x_vals = [50, 100]
    def trunc(y):
        return list(y[:len(x_vals)])
    chart = build_manual_chart(
        "F1 Score",
        {
            "Random": {
                "group": "random",
                "y": random_series["y"],
                "yerr": random_series.get("yerr", [0.0] * len(x_vals)),
            },
            "GraphSAGE 450": {
                "group": "graphsage_threshold_05",
                "y": trunc(merged_chart["series"]["GraphSAGE-CL 0.5"]["y"]),
                "yerr": trunc(merged_chart["series"]["GraphSAGE-CL 0.5"].get("yerr", [0.0] * 7)),
            },
            "GreenRAN 450": {
                "group": "armd_threshold_05",
                "y": trunc(merged_chart["series"]["ARMD-GreenRAN 0.5"]["y"]),
                "yerr": trunc(merged_chart["series"]["ARMD-GreenRAN 0.5"].get("yerr", [0.0] * 7)),
            },
        },
        x_values=x_vals,
    )
    chart["inline_labels"] = True
    chart["zero_anchor_greenran"] = True
    chart["marker_only_last_groups"] = ["graphsage_"]
    return chart


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
    for subdir in ("Reconstruction", "Indirect", "Implicit"):
        for stale_png in (output_dir / subdir).glob("comparison_*.png"):
            stale_png.unlink()

    merged_charts = {}
    for chart_name, reference_chart in reference.get("charts", {}).items():
        armd_chart = armd.get("charts", {}).get(chart_name, {})
        merged_charts[chart_name] = merge_chart(reference_chart, armd_chart)

    for derived_name, source_name in DERIVED_CHART_SOURCES.items():
        merged_charts[derived_name] = dict(merged_charts[source_name])

    merged_charts["reconstruction_threshold_0_5"] = build_reconstruction_threshold_chart(
        merged_charts["reconstruction_dataset_450_thresholds"],
        reference["charts"]["reconstruction_threshold_0_5"]["series"]["Random"],
        "0.5",
        x_vals=[50, 100],
    )
    merged_charts["indirect_threshold_0_5"] = build_indirect_threshold_chart(
        merged_charts["indirect_dataset_450_thresholds"],
        reference["charts"]["indirect_threshold_0_5"]["series"]["Random"],
        "0.5",
    )
    merged_charts["reconstruction_threshold_0_2"] = build_reconstruction_threshold_chart(
        merged_charts["reconstruction_dataset_450_thresholds"],
        reference["charts"]["reconstruction_threshold_0_5"]["series"]["Random"],
        "0.2",
        x_vals=[50, 100, 200],
    )
    merged_charts["reconstruction_threshold_0_9"] = build_reconstruction_threshold_chart(
        merged_charts["reconstruction_dataset_450_thresholds"],
        reference["charts"]["reconstruction_threshold_0_5"]["series"]["Random"],
        "0.9",
        x_vals=[50, 100, 200, 400],
    )
    merged_charts["reconstruction_dataset_450_threshold_0_5"] = build_reconstruction_dataset_450_chart(
        merged_charts["reconstruction_dataset_450_thresholds"],
        reference["charts"]["reconstruction_threshold_0_5"]["series"]["Random"],
    )
    merged_charts["indirect_threshold_0_2"] = build_indirect_threshold_chart(
        merged_charts["indirect_dataset_450_thresholds"],
        reference["charts"]["indirect_threshold_0_5"]["series"]["Random"],
        "0.2",
        x_vals=[50, 100, 200],
    )
    merged_charts["indirect_threshold_0_9"] = build_indirect_threshold_chart(
        merged_charts["indirect_dataset_450_thresholds"],
        reference["charts"]["indirect_threshold_0_5"]["series"]["Random"],
        "0.9",
        x_vals=[50, 100, 200, 400],
    )
    merged_charts["indirect_dataset_450_threshold_0_5"] = build_indirect_dataset_450_chart(
        merged_charts["indirect_dataset_450_thresholds"],
        reference["charts"]["indirect_threshold_0_5"]["series"]["Random"],
    )

    merged_charts["reconstruction_dataset_50_threshold_0_5"] = build_sample_chart(
        reference["charts"]["reconstruction_threshold_0_5"], sample_series, "50", "parameter_kpi_f1", "parameter_kpi_f1_std"
    )
    merged_charts["indirect_dataset_50_threshold_0_5"] = build_sample_chart(
        reference["charts"]["indirect_threshold_0_5"], sample_series, "50", "indirect_f1", "indirect_f1_std"
    )
    merged_charts["reconstruction_dataset_150_threshold_0_5"] = build_sample_chart(
        reference["charts"]["reconstruction_threshold_0_5"], sample_series, "150", "parameter_kpi_f1", "parameter_kpi_f1_std"
    )
    merged_charts["indirect_dataset_150_threshold_0_5"] = build_sample_chart(
        reference["charts"]["indirect_threshold_0_5"], sample_series, "150", "indirect_f1", "indirect_f1_std"
    )
    merged_charts["implicit_threshold_0_2"] = build_implicit_threshold_chart(
        "0.2", implicit_reference, implicit_armd_450, x_vals=[50, 100, 200]
    )
    merged_charts["implicit_threshold_0_5"] = build_implicit_threshold_chart(
        "0.5", implicit_reference, implicit_armd_450, x_vals=[50, 100]
    )
    merged_charts["implicit_threshold_0_9"] = build_implicit_threshold_chart(
        "0.9", implicit_reference, implicit_armd_450, x_vals=[50, 100, 200]
    )
    merged_charts["implicit_dataset_50_threshold_0_5"] = build_implicit_sample_chart(sample_series, "50", implicit_reference)
    merged_charts["implicit_dataset_150_threshold_0_5"] = build_implicit_sample_chart(sample_series, "150", implicit_reference)
    merged_charts["implicit_dataset_450_threshold_0_5"] = build_implicit_dataset_450_chart(
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
