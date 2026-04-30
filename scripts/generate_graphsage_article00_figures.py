#!/usr/bin/env python3
"""
Generate experimental figures for the isolated article00 track.

The figures stay outside GreenRAN and summarize the temporal article00 runs
found under `runs/article00/training`.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

try:
    import matplotlib.pyplot as plt
except ModuleNotFoundError as exc:
    raise SystemExit(
        "matplotlib is required for generate_graphsage_article00_figures.py; run it with ./drlexp/.venv/bin/python "
        "or an equivalent environment that has matplotlib installed"
    ) from exc


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TRAINING_ROOT = PROJECT_ROOT / "runs" / "article00" / "training"
DEFAULT_FIGURES_ROOT = PROJECT_ROOT / "runs" / "article00" / "figures"
MAX_EPOCH_DISPLAY = 400

EXPECTED_FIGURES = [
    "reconstruction_f1_vs_epochs.png",
    "reconstruction_f1_vs_threshold.png",
    "implicit_f1_vs_epochs.png",
    "implicit_f1_vs_threshold.png",
    "indirect_f1_vs_epochs.png",
    "indirect_f1_vs_threshold.png",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate article00 experimental figures.")
    parser.add_argument("--training-root", default=str(DEFAULT_TRAINING_ROOT))
    parser.add_argument("--output-dir", default=str(DEFAULT_FIGURES_ROOT))
    return parser.parse_args()


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


def aggregate_metric(
    summaries: list[dict],
    metric_path: tuple[str, ...],
) -> dict[int, list[float]]:
    by_epoch: dict[int, list[float]] = {}
    for summary in summaries:
        for item in summary.get("history", []):
            value = item
            for key in metric_path:
                value = value[key]
            epoch = int(item["epoch"])
            by_epoch.setdefault(epoch, []).append(float(value))
    return by_epoch


def plot_epoch_series(
    series: dict[str, dict[int, list[float]]],
    title: str,
    ylabel: str,
    output_path: Path,
) -> None:
    plt.figure(figsize=(8, 5))
    for label, values_by_epoch in sorted(series.items()):
        epochs = [epoch for epoch in sorted(values_by_epoch) if epoch <= MAX_EPOCH_DISPLAY]
        means = []
        stds = []
        for epoch in epochs:
            mean, std = mean_std(values_by_epoch[epoch])
            means.append(mean)
            stds.append(std)
        plt.plot(epochs, means, marker="o", linewidth=2, label=label)
        if any(std > 0 for std in stds):
            lower = [max(0.0, mean - std) for mean, std in zip(means, stds)]
            upper = [min(1.0, mean + std) for mean, std in zip(means, stds)]
            plt.fill_between(epochs, lower, upper, alpha=0.15)
    plt.title(title)
    plt.xlabel("Epochs")
    plt.ylabel(ylabel)
    plt.ylim(0.0, 1.05)
    plt.xlim(0, MAX_EPOCH_DISPLAY)
    plt.grid(alpha=0.25)
    plt.legend()
    plt.tight_layout()
    plt.savefig(output_path, dpi=160)
    plt.close()


def main() -> None:
    args = parse_args()
    training_root = Path(args.training_root)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    summaries = sorted(training_root.glob("*/training_summary.json"))
    if not summaries:
        raise SystemExit(f"no article00 training summaries found under {training_root}")

    loaded = [load_json(path) for path in summaries]
    by_sample_size: dict[int, list[dict]] = {}
    for summary in loaded:
        by_sample_size.setdefault(int(summary.get("samples", 0)), []).append(summary)

    sample_sizes = sorted(by_sample_size)
    reference_sample_size = sample_sizes[-1]
    reference_loaded = by_sample_size[reference_sample_size][0]
    thresholds = reference_loaded.get("thresholds_requested", [])
    selection_threshold = float(reference_loaded.get("selection_threshold", 0.5))
    selection_label = f"{selection_threshold:.2f}"

    reconstruction_series = {
        f"Dataset size: {sample_size} samples": aggregate_metric(
            sample_summaries,
            ("threshold_metrics", selection_label, "parameter_kpi_metrics", "f1"),
        )
        for sample_size, sample_summaries in sorted(by_sample_size.items())
    }
    implicit_series = {
        f"Dataset size: {sample_size} samples": aggregate_metric(
            sample_summaries,
            ("threshold_metrics", selection_label, "conflict_metrics", "implicit", "f1"),
        )
        for sample_size, sample_summaries in sorted(by_sample_size.items())
    }
    indirect_series = {
        f"Dataset size: {sample_size} samples": aggregate_metric(
            sample_summaries,
            ("threshold_metrics", selection_label, "conflict_metrics", "indirect", "f1"),
        )
        for sample_size, sample_summaries in sorted(by_sample_size.items())
    }

    reconstruction_threshold_series = {
        ("No Threshold" if threshold == "no_threshold" else f"Threshold {threshold}"): aggregate_metric(
            by_sample_size[reference_sample_size],
            ("threshold_metrics", threshold, "parameter_kpi_metrics", "f1"),
        )
        for threshold in ["no_threshold", *[f"{float(value):.2f}" for value in thresholds]]
    }
    implicit_threshold_series = {
        ("No Threshold" if threshold == "no_threshold" else f"Threshold {threshold}"): aggregate_metric(
            by_sample_size[reference_sample_size],
            ("threshold_metrics", threshold, "conflict_metrics", "implicit", "f1"),
        )
        for threshold in ["no_threshold", *[f"{float(value):.2f}" for value in thresholds]]
    }
    indirect_threshold_series = {
        ("No Threshold" if threshold == "no_threshold" else f"Threshold {threshold}"): aggregate_metric(
            by_sample_size[reference_sample_size],
            ("threshold_metrics", threshold, "conflict_metrics", "indirect", "f1"),
        )
        for threshold in ["no_threshold", *[f"{float(value):.2f}" for value in thresholds]]
    }

    plot_epoch_series(
        reconstruction_series,
        title=f"F1 Reconstruction vs. Epochs for Threshold={selection_threshold:.2f}",
        ylabel="F1 Score",
        output_path=output_dir / "reconstruction_f1_vs_epochs.png",
    )
    plot_epoch_series(
        reconstruction_threshold_series,
        title=f"F1 Reconstruction vs. Epochs for Dataset Size={reference_sample_size}",
        ylabel="F1 Score",
        output_path=output_dir / "reconstruction_f1_vs_threshold.png",
    )
    plot_epoch_series(
        implicit_series,
        title=f"F1 Implicit vs. Epochs for Threshold={selection_threshold:.2f}",
        ylabel="F1 Score",
        output_path=output_dir / "implicit_f1_vs_epochs.png",
    )
    plot_epoch_series(
        implicit_threshold_series,
        title=f"F1 Implicit vs. Epochs for Dataset Size={reference_sample_size}",
        ylabel="F1 Score",
        output_path=output_dir / "implicit_f1_vs_threshold.png",
    )
    plot_epoch_series(
        indirect_series,
        title=f"F1 Indirect vs. Epochs for Threshold={selection_threshold:.2f}",
        ylabel="F1 Score",
        output_path=output_dir / "indirect_f1_vs_epochs.png",
    )
    plot_epoch_series(
        indirect_threshold_series,
        title=f"F1 Indirect vs. Epochs for Dataset Size={reference_sample_size}",
        ylabel="F1 Score",
        output_path=output_dir / "indirect_f1_vs_threshold.png",
    )

    payload = {
        "schema": "greenran.article00_figure_manifest.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "training_root": str(training_root),
        "training_summaries": [str(path) for path in summaries],
        "expected_figures": EXPECTED_FIGURES,
        "status": "experimental_temporal_graphsage",
    }

    manifest_path = output_dir / "article00_figure_manifest.json"
    with manifest_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)

    print(f"Article00 figures and manifest written to: {output_dir}")


if __name__ == "__main__":
    main()
