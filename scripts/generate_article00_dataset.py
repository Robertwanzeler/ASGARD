#!/usr/bin/env python3
"""
Generate a synthetic temporal dataset inspired by artigo00.

This script creates a dedicated `article00` dataset under `runs/article00`
without touching the current GreenRAN conflict pipeline.

The generator intentionally remains isolated from the runtime, but it now
tracks the conflict model from artigo00 more closely:

- 4 xApps;
- 7 control parameters;
- 4 KPIs following the Gaussian equations cited in the paper;
- known A-P and A-K subscriptions;
- P-K relationships used as the reconstruction target.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "runs" / "article00" / "datasets"
DEFAULT_SAMPLES = 600
DEFAULT_SEED = 42

# Ranges copied from Table I in artigo00.
PARAMETER_RANGES = {
    "P1": (0.0, 300.0),
    "P2": (0.0, 300.0),
    "P3": (0.0, 3.0),
    "P4": (0.0, 3.0),
    "P5": (0.0, 3.0),
    "P6": (0.0, 3.0),
    "P7": (0.0, 3.0),
}

KNOWN_APP_PARAMETER_EDGES = {
    "A1": ["P1", "P2"],
    "A2": ["P2", "P3"],
    "A3": ["P4", "P5"],
    "A4": ["P6", "P7"],
}

KNOWN_APP_KPI_EDGES = {
    "A1": ["K1"],
    "A2": ["K1", "K2"],
    "A3": ["K2", "K3"],
    "A4": ["K3", "K4"],
}

FEATURE_ORDER = ("P1", "P2", "P3", "P4", "P5", "P6", "P7", "K1", "K2", "K3", "K4")
PARAMETER_KPI_EDGES = (
    ("P1", "K1"),
    ("P2", "K1"),
    ("P1", "K2"),
    ("P3", "K2"),
    ("P4", "K3"),
    ("P5", "K3"),
    ("P6", "K4"),
    ("P7", "K4"),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a synthetic temporal dataset for the article00 track.")
    parser.add_argument("--samples", type=int, default=DEFAULT_SAMPLES, help="number of time steps to generate")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="random seed")
    parser.add_argument(
        "--output-dir",
        help="directory for generated files; defaults to runs/article00/datasets/seed_<seed>",
    )
    parser.add_argument(
        "--smoothing",
        type=float,
        default=0.82,
        help="temporal smoothing factor in [0,1); higher values create more inertia",
    )
    return parser.parse_args()


def gaussian_response(value: float, center: float, sigma: float, scale: float = 1.0) -> float:
    sigma = max(sigma, 0.1)
    exponent = -((value - center) ** 2) / (2.0 * (sigma ** 2))
    return scale * math.exp(exponent)


def sample_parameters(previous: dict[str, float] | None, smoothing: float) -> dict[str, float]:
    current: dict[str, float] = {}
    for parameter, (low, high) in PARAMETER_RANGES.items():
        raw = random.uniform(low, high)
        if previous is None:
            current[parameter] = raw
            continue
        alpha = min(max(smoothing, 0.0), 0.99)
        current[parameter] = (alpha * previous[parameter]) + ((1.0 - alpha) * raw)
    return current


def compute_kpis(parameters: dict[str, float]) -> dict[str, float]:
    # KPI equations taken from artigo00 Section V-A.
    k1 = gaussian_response(parameters["P1"], center=-50.0, sigma=parameters["P2"], scale=0.5)
    k2 = gaussian_response(parameters["P1"], center=50.0, sigma=parameters["P3"], scale=1.0)
    k3 = gaussian_response(parameters["P4"] + k1, center=0.0, sigma=parameters["P5"], scale=1.0)
    k4 = gaussian_response(parameters["P7"] + k2, center=0.0, sigma=parameters["P6"], scale=1.0)
    return {
        "K1": round(k1, 6),
        "K2": round(k2, 6),
        "K3": round(k3, 6),
        "K4": round(k4, 6),
    }


def build_reference_graph() -> dict:
    nodes = []
    for app_id in sorted(KNOWN_APP_PARAMETER_EDGES):
        nodes.append({"id": app_id, "type": "xapp"})
    for parameter in PARAMETER_RANGES:
        nodes.append({"id": parameter, "type": "parameter"})
    for kpi in ("K1", "K2", "K3", "K4"):
        nodes.append({"id": kpi, "type": "kpi"})

    edges = []
    for app_id, parameters in KNOWN_APP_PARAMETER_EDGES.items():
        for parameter in parameters:
            edges.append({"source": app_id, "target": parameter, "relation": "controls", "known": True})
    for app_id, kpis in KNOWN_APP_KPI_EDGES.items():
        for kpi in kpis:
            edges.append({"source": app_id, "target": kpi, "relation": "monitors", "known": True})

    for source, target in PARAMETER_KPI_EDGES:
        edges.append({"source": source, "target": target, "relation": "influences", "known": False})

    return {
        "schema": "greenran.article00_reference_graph.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "nodes": nodes,
        "edges": edges,
        "notes": [
            "Known A-P and A-K edges are encoded explicitly.",
            "P-K edges follow the Gaussian conflict model described in artigo00.",
            "This graph is the synthetic ground-truth reference, not the reconstructed output.",
        ],
    }


def build_temporal_graph(samples: int) -> dict:
    nodes = [{"id": f"v_{index}", "time_index": index} for index in range(samples)]
    edges = []
    for index in range(samples - 1):
        edges.append({"source": f"v_{index}", "target": f"v_{index + 1}"})
    return {
        "schema": "greenran.article00_temporal_graph.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "nodes": nodes,
        "edges": edges,
        "notes": [
            "Temporal chain graph used by the isolated article00 GraphSAGE trainer.",
            "Each node represents one time step and connects to the next time step.",
        ],
    }


def write_timeseries(path: Path, rows: list[dict]) -> None:
    if not rows:
        raise SystemExit("no rows generated for article00 dataset")
    fieldnames = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    args = parse_args()
    random.seed(args.seed)

    output_dir = Path(args.output_dir) if args.output_dir else (DEFAULT_OUTPUT_ROOT / f"seed_{args.seed}")
    output_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    previous_parameters: dict[str, float] | None = None
    for step in range(args.samples):
        parameters = sample_parameters(previous_parameters, args.smoothing)
        kpis = compute_kpis(parameters)
        row = {"time_index": step}
        for key in sorted(parameters):
            row[key] = round(parameters[key], 6)
        row.update(kpis)
        rows.append(row)
        previous_parameters = parameters

    timeseries_path = output_dir / "timeseries.csv"
    metadata_path = output_dir / "article00_metadata.json"
    graph_path = output_dir / "article00_graph_reference.json"
    temporal_graph_path = output_dir / "article00_temporal_graph.json"

    write_timeseries(timeseries_path, rows)
    with metadata_path.open("w", encoding="utf-8") as handle:
        json.dump(
            {
                "schema": "greenran.article00_dataset_metadata.v1",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "seed": args.seed,
                "samples": args.samples,
                "smoothing": args.smoothing,
                "feature_order": list(FEATURE_ORDER),
                "parameter_ranges": PARAMETER_RANGES,
                "known_app_parameter_edges": KNOWN_APP_PARAMETER_EDGES,
                "known_app_kpi_edges": KNOWN_APP_KPI_EDGES,
                "parameter_kpi_edges": list(PARAMETER_KPI_EDGES),
                "timeseries_path": str(timeseries_path),
                "graph_reference_path": str(graph_path),
                "temporal_graph_path": str(temporal_graph_path),
                "notes": [
                    "Synthetic article00 experimental dataset.",
                    "Temporal graph is exported separately for the isolated article00 trainer.",
                ],
            },
            handle,
            indent=2,
        )
    with graph_path.open("w", encoding="utf-8") as handle:
        json.dump(build_reference_graph(), handle, indent=2)
    with temporal_graph_path.open("w", encoding="utf-8") as handle:
        json.dump(build_temporal_graph(args.samples), handle, indent=2)

    print(f"Article00 dataset generated at: {output_dir}")
    print(f"- timeseries: {timeseries_path}")
    print(f"- metadata: {metadata_path}")
    print(f"- graph reference: {graph_path}")
    print(f"- temporal graph: {temporal_graph_path}")


if __name__ == "__main__":
    main()
