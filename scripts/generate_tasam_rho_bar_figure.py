#!/usr/bin/env python3
"""Generate a TA-SAM rho bar chart matching the paper's grouped-bar layout."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_ROOT = ROOT / "runs" / "tasam_greenran_real_article_rho"
DEFAULT_OUTPUT = ROOT / "runs" / "tasam_greenran_comparison" / "tasam_rho_bar_figure.png"

DISPLAY_MODES = ("both_sam", "actor_sam", "critic_sam")
LABELS = {
    "both_sam": "both SAM",
    "actor_sam": "actor SAM",
    "critic_sam": "critic SAM",
}
COLORS = {
    "both_sam": "#1f77b4",
    "actor_sam": "#d9a11f",
    "critic_sam": "#8e5ea2",
}
BASE_SCENARIO_ORDER = ("equal", "non_equal", "dynamic")
SCENARIO_RHO_VALUES = {
    "equal": 0.01,
    "non_equal": 0.02,
    "dynamic": 0.05,
}
PAPER_RHO_TICKS = [0.0, 0.01, 0.02, 0.03, 0.04, 0.05, 0.06]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a grouped rho bar figure from TA-SAM article-style runs."
    )
    parser.add_argument(
        "--input-root",
        default=str(DEFAULT_INPUT_ROOT),
        help="Root containing equal/non_equal/dynamic scenario folders.",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="Output PNG path.",
    )
    parser.add_argument(
        "--metric",
        choices=("eval_return", "cumulative_return"),
        default="eval_return",
        help="Metric to extract from final_metrics.",
    )
    parser.add_argument(
        "--ylabel",
        default="Maximum reward",
        help="Y-axis label for the generated figure.",
    )
    parser.add_argument(
        "--paper-caption-mode",
        action="store_true",
        help="Apply paper-like layout defaults without adding a chart title.",
    )
    return parser


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _extract_metric(summary_path: Path, metric: str) -> float:
    payload = _load_json(summary_path)
    final_metrics = payload.get("final_metrics")
    if not isinstance(final_metrics, dict):
        raise KeyError(f"Missing final_metrics in {summary_path}")
    if metric not in final_metrics:
        raise KeyError(f"Missing final_metrics.{metric} in {summary_path}")
    value = final_metrics[metric]
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid numeric value for final_metrics.{metric} in {summary_path}: {value!r}") from exc


def _extract_requested_rho(summary_path: Path) -> float | None:
    payload = _load_json(summary_path)
    actor_rho = payload.get("actor_sam_rho")
    actor_rho_final = payload.get("actor_sam_rho_final")
    critic_rho = payload.get("critic_sam_rho")
    critic_rho_final = payload.get("critic_sam_rho_final")
    values = [actor_rho, actor_rho_final, critic_rho, critic_rho_final]
    try:
        parsed = [float(value) for value in values]
    except (TypeError, ValueError):
        return None
    if max(parsed) - min(parsed) > 1e-9:
        return None
    return float(parsed[0])


def _parse_custom_rho_scenarios(input_root: Path) -> list[tuple[str, float]]:
    custom: list[tuple[str, float]] = []
    for child in sorted(input_root.iterdir()):
        if not child.is_dir():
            continue
        name = child.name
        if not name.startswith("rho_0_"):
            continue
        suffix = name.removeprefix("rho_")
        try:
            rho_value = float(suffix.replace("_", "."))
        except ValueError:
            continue
        if any(not (child / mode / "tasam_marl_summary.json").exists() for mode in DISPLAY_MODES):
            continue
        custom.append((name, rho_value))
    return custom


def resolve_scenarios(input_root: Path) -> list[tuple[str, float]]:
    scenarios = [(name, SCENARIO_RHO_VALUES[name]) for name in BASE_SCENARIO_ORDER]
    scenarios.extend(_parse_custom_rho_scenarios(input_root))
    return sorted(scenarios, key=lambda item: item[1])


def build_plot_payload(input_root: Path, metric: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for scenario, rho_value in resolve_scenarios(input_root):
        scenario_root = input_root / scenario
        for mode in DISPLAY_MODES:
            summary_path = scenario_root / mode / "tasam_marl_summary.json"
            if not summary_path.exists():
                raise FileNotFoundError(
                    f"Expected summary for scenario={scenario} mode={mode} at {summary_path}"
                )
            rows.append(
                {
                    "scenario": scenario,
                    "rho_value": _extract_requested_rho(summary_path) or rho_value,
                    "mode": mode,
                    "label": LABELS[mode],
                    "value": _extract_metric(summary_path, metric),
                    "summary_path": str(summary_path),
                }
            )
    return rows


def _group_values(rows: list[dict[str, Any]]) -> dict[str, list[float]]:
    grouped = {mode: [] for mode in DISPLAY_MODES}
    ordered_rhos = sorted({round(float(row["rho_value"]), 4) for row in rows})
    for rho_value in ordered_rhos:
        rho_rows = [row for row in rows if round(float(row["rho_value"]), 4) == rho_value]
        row_by_mode = {row["mode"]: row for row in rho_rows}
        for mode in DISPLAY_MODES:
            payload = row_by_mode.get(mode)
            grouped[mode].append(float(payload["value"]) if payload else float("nan"))
    return grouped


def render_figure(
    rows: list[dict[str, Any]],
    output_path: Path,
    *,
    ylabel: str,
    paper_caption_mode: bool = False,
) -> Path:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ModuleNotFoundError as exc:  # pragma: no cover - environment dependent
        raise SystemExit(
            "matplotlib is required for generate_tasam_rho_bar_figure.py; "
            "run it with ./drlexp/.venv/bin/python or an equivalent environment."
        ) from exc

    output_path.parent.mkdir(parents=True, exist_ok=True)

    grouped = _group_values(rows)
    x_positions = sorted({round(float(row["rho_value"]), 4) for row in rows})
    bar_width = 0.00155 if paper_caption_mode else 0.00185
    offsets = {
        "both_sam": -bar_width,
        "actor_sam": 0.0,
        "critic_sam": bar_width,
    }

    fig, ax = plt.subplots(figsize=(4.8, 4.0) if paper_caption_mode else (5.6, 4.4))
    for mode in DISPLAY_MODES:
        xs = [x + offsets[mode] for x in x_positions]
        label_drawn = False
        for x_value, y_value in zip(xs, grouped[mode]):
            if y_value != y_value:
                continue
            ax.bar(
                x_value,
                y_value,
                width=bar_width,
                color=COLORS[mode],
                edgecolor="black",
                linewidth=0.65,
                label=LABELS[mode] if not label_drawn else None,
                zorder=3,
            )
            label_drawn = True

    tick_values = PAPER_RHO_TICKS
    tick_labels = ["0", "0.01", "0.02", "0.03", "0.04", "0.05", "0.06"]
    ax.set_xlim(-0.001, 0.061)
    ax.set_xticks(tick_values)
    ax.set_xticklabels(tick_labels)
    ax.set_xlabel(r"$\rho$ values")
    ax.set_ylabel(ylabel)
    ax.grid(True, alpha=0.18, linewidth=0.6, zorder=0)
    ax.legend(loc="upper right", fontsize=8, frameon=True)
    ax.tick_params(axis="both", labelsize=9)
    ax.set_axisbelow(True)

    fig.tight_layout()
    fig.savefig(output_path, dpi=220, bbox_inches="tight")
    plt.close(fig)
    return output_path


def main() -> int:
    args = build_parser().parse_args()
    input_root = Path(args.input_root)
    output_path = Path(args.output)
    rows = build_plot_payload(input_root, args.metric)
    render_figure(
        rows,
        output_path,
        ylabel=args.ylabel,
        paper_caption_mode=args.paper_caption_mode,
    )
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
