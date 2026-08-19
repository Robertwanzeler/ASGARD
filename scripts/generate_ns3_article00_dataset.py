#!/usr/bin/env python3
"""
Convert a collected GreenRAN/ns-3 conflict dataset into the temporal dataset
layout consumed by the isolated article00 GraphSAGE trainer.

This stays offline: it does not start ns-3, the RIC, or the rApp. It only
reads an already collected conflict export and rewrites it as:

- timeseries.csv
- article00_metadata.json
- article00_graph_reference.json
- article00_temporal_graph.json
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "runs" / "ns3_article00" / "datasets"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert a GreenRAN/ns-3 conflict export into article00 temporal dataset format."
    )
    parser.add_argument(
        "--experiment-dir",
        help="experiment root created by run_conflict_experiments.py; used together with --scenario",
    )
    parser.add_argument(
        "--scenario",
        help="scenario slug under the experiment dir, for example conflito_implicito",
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=450,
        help="subset size to load from the scenario export; use 0 to load the full export",
    )
    parser.add_argument(
        "--dataset-csv",
        help="optional explicit conflict_dataset_*.csv path; overrides --experiment-dir/--scenario/--samples",
    )
    parser.add_argument(
        "--output-dir",
        help="output directory for article00-style files; defaults to runs/ns3_article00/datasets/<scenario>/samples_<n>",
    )
    return parser.parse_args()


def load_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in {"", None}:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def decision_score(label: str) -> float:
    normalized = (label or "").strip().upper()
    if normalized in {"BLOCKED", "CRITICAL"}:
        return 1.0
    if normalized in {"GUARDED", "WARN", "WARNING"}:
        return 0.66
    if normalized in {"MITIGATED", "APPLIED"}:
        return 0.5
    if normalized in {"ALLOWED", "OK", "HEALTHY", "NORMAL"}:
        return 0.2
    return 0.0


def severity_score(row: dict[str, str]) -> float:
    observed = safe_float(row.get("observed_value"))
    threshold = safe_float(row.get("threshold_value"))
    delta = safe_float(row.get("observed_delta_from_threshold"))

    if threshold > 0:
        return clamp(abs(delta) / max(abs(threshold), 1e-6))
    if observed != 0.0:
        return clamp(abs(delta) / max(abs(observed), 1e-6))
    return 0.0


def parameter_signal(row: dict[str, str]) -> float:
    power = clamp(safe_float(row.get("latest_power_percent")) / 100.0)
    confidence = clamp(
        max(
            safe_float(row.get("conflict_confidence")),
            safe_float(row.get("rapp_confidence")),
            safe_float(row.get("ml_confidence")),
        )
    )
    decision = max(
        decision_score(row.get("conflict_decision", "")),
        decision_score(row.get("rapp_decision", "")),
        decision_score(row.get("mitigation_action", "")),
    )
    severity = severity_score(row)
    return round((0.35 * power) + (0.25 * confidence) + (0.20 * decision) + (0.20 * severity), 6)


def resolve_dataset_path(args: argparse.Namespace) -> tuple[Path, str, int]:
    if args.samples not in {0, 50, 150, 450}:
        raise SystemExit("--samples must be one of: 0, 50, 150, 450")

    if args.dataset_csv:
        dataset_path = Path(args.dataset_csv)
        if not dataset_path.exists():
            raise SystemExit(f"dataset csv not found: {dataset_path}")
        if args.scenario:
            scenario = args.scenario
        elif dataset_path.parent.name.startswith("subset_"):
            scenario = dataset_path.parent.parent.parent.name
        elif dataset_path.parent.name == "scenario_exports":
            scenario = dataset_path.parent.parent.name
        else:
            scenario = dataset_path.parent.name
        samples = args.samples
        return dataset_path, scenario, samples

    if not args.experiment_dir or not args.scenario:
        raise SystemExit("use --dataset-csv or provide both --experiment-dir and --scenario")

    experiment_dir = Path(args.experiment_dir)
    if args.samples > 0:
        dataset_path = (
            experiment_dir
            / args.scenario
            / "scenario_exports"
            / f"subset_{args.samples}"
            / f"conflict_dataset_{args.samples}.csv"
        )
    else:
        dataset_path = experiment_dir / args.scenario / "scenario_exports" / "conflict_dataset_full.csv"

    if not dataset_path.exists():
        raise SystemExit(f"conflict dataset not found: {dataset_path}")
    return dataset_path, args.scenario, args.samples


def prefixed_feature_names(raw_names: list[str], prefix: str) -> tuple[list[str], dict[str, str], dict[str, str]]:
    ordered_ids: list[str] = []
    id_to_raw: dict[str, str] = {}
    raw_to_id: dict[str, str] = {}
    for index, raw_name in enumerate(sorted(raw_names), start=1):
        feature_id = f"{prefix}{index}__{raw_name}"
        ordered_ids.append(feature_id)
        id_to_raw[feature_id] = raw_name
        raw_to_id[raw_name] = feature_id
    return ordered_ids, id_to_raw, raw_to_id


def build_feature_rows(
    rows: list[dict[str, str]],
    parameter_ids: list[str],
    kpi_ids: list[str],
    raw_parameter_to_id: dict[str, str],
    raw_kpi_to_id: dict[str, str],
) -> list[dict[str, Any]]:
    state = {feature_id: 0.0 for feature_id in [*parameter_ids, *kpi_ids]}
    timeseries_rows: list[dict[str, Any]] = []

    for index, row in enumerate(rows):
        raw_parameter = row.get("parameter", "")
        raw_kpi = row.get("affected_kpi", "")
        if raw_parameter in raw_parameter_to_id:
            state[raw_parameter_to_id[raw_parameter]] = parameter_signal(row)
        if raw_kpi in raw_kpi_to_id:
            state[raw_kpi_to_id[raw_kpi]] = round(safe_float(row.get("observed_value")), 6)

        payload = {"time_index": index}
        for feature_id in [*parameter_ids, *kpi_ids]:
            payload[feature_id] = round(float(state[feature_id]), 6)
        timeseries_rows.append(payload)

    if not timeseries_rows:
        raise SystemExit("no rows found in the ns-3 conflict dataset")
    return timeseries_rows


def build_reference_graph(
    rows: list[dict[str, str]],
    raw_parameter_to_id: dict[str, str],
    raw_kpi_to_id: dict[str, str],
) -> dict[str, Any]:
    xapps = sorted(
        {
            row.get("source_agent", "").strip()
            for row in rows
            if row.get("source_agent", "").strip()
        }
        | {
            row.get("target_agent", "").strip()
            for row in rows
            if row.get("target_agent", "").strip()
        }
    )
    parameter_ids = sorted(raw_parameter_to_id.values())
    kpi_ids = sorted(raw_kpi_to_id.values())

    nodes = [{"id": app_id, "type": "xapp"} for app_id in xapps]
    nodes += [{"id": parameter_id, "type": "parameter"} for parameter_id in parameter_ids]
    nodes += [{"id": kpi_id, "type": "kpi"} for kpi_id in kpi_ids]

    controls = set()
    monitors = set()
    influences = set()
    for row in rows:
        source = row.get("source_agent", "").strip()
        target = row.get("target_agent", "").strip()
        raw_parameter = row.get("parameter", "").strip()
        raw_kpi = row.get("affected_kpi", "").strip()

        parameter_id = raw_parameter_to_id.get(raw_parameter)
        kpi_id = raw_kpi_to_id.get(raw_kpi)
        if source and parameter_id:
            controls.add((source, parameter_id))
        if target and kpi_id:
            monitors.add((target, kpi_id))
        if parameter_id and kpi_id:
            influences.add((parameter_id, kpi_id))

    edges = []
    for source, target in sorted(controls):
        edges.append({"source": source, "target": target, "relation": "controls", "known": True})
    for source, target in sorted(monitors):
        edges.append({"source": source, "target": target, "relation": "monitors", "known": True})
    for source, target in sorted(influences):
        edges.append({"source": source, "target": target, "relation": "influences", "known": False})

    return {
        "schema": "greenran.ns3_article00_reference_graph.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "nodes": nodes,
        "edges": edges,
        "notes": [
            "Graph converted from GreenRAN/ns-3 conflict exports into the article00 temporal format.",
            "controls edges come from source_agent -> parameter.",
            "monitors edges come from target_agent -> affected_kpi.",
            "influences edges come from parameter -> affected_kpi event pairs.",
        ],
    }


def build_temporal_graph(samples: int) -> dict[str, Any]:
    nodes = [{"id": f"v_{index}", "time_index": index} for index in range(samples)]
    edges = [{"source": f"v_{index}", "target": f"v_{index + 1}"} for index in range(max(samples - 1, 0))]
    return {
        "schema": "greenran.ns3_article00_temporal_graph.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "nodes": nodes,
        "edges": edges,
        "notes": [
            "Temporal chain graph derived from sequential conflict events.",
            "Each node represents one exported event/time step.",
        ],
    }


def write_timeseries(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    args = parse_args()
    dataset_path, scenario, samples = resolve_dataset_path(args)
    rows = load_rows(dataset_path)

    raw_parameters = sorted({row.get("parameter", "").strip() for row in rows if row.get("parameter", "").strip()})
    raw_kpis = sorted({row.get("affected_kpi", "").strip() for row in rows if row.get("affected_kpi", "").strip()})
    if not raw_parameters or not raw_kpis:
        raise SystemExit("the selected conflict dataset does not contain parameter/KPI labels")

    parameter_ids, parameter_id_to_raw, raw_parameter_to_id = prefixed_feature_names(raw_parameters, "P")
    kpi_ids, kpi_id_to_raw, raw_kpi_to_id = prefixed_feature_names(raw_kpis, "K")
    feature_order = [*parameter_ids, *kpi_ids]

    sample_label = "full" if samples <= 0 else str(samples)
    output_dir = (
        Path(args.output_dir)
        if args.output_dir
        else (DEFAULT_OUTPUT_ROOT / scenario / f"samples_{sample_label}")
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    timeseries_rows = build_feature_rows(rows, parameter_ids, kpi_ids, raw_parameter_to_id, raw_kpi_to_id)
    reference_graph = build_reference_graph(rows, raw_parameter_to_id, raw_kpi_to_id)
    temporal_graph = build_temporal_graph(len(timeseries_rows))

    timeseries_path = output_dir / "timeseries.csv"
    metadata_path = output_dir / "article00_metadata.json"
    graph_path = output_dir / "article00_graph_reference.json"
    temporal_graph_path = output_dir / "article00_temporal_graph.json"

    write_timeseries(timeseries_path, timeseries_rows)
    with metadata_path.open("w", encoding="utf-8") as handle:
        json.dump(
            {
                "schema": "greenran.ns3_article00_dataset_metadata.v1",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "source_dataset_csv": str(dataset_path),
                "scenario": scenario,
                "samples_requested": samples,
                "samples_actual": len(timeseries_rows),
                "feature_order": feature_order,
                "feature_groups": {
                    "parameters": parameter_ids,
                    "kpis": kpi_ids,
                },
                "raw_feature_names": {
                    "parameters": parameter_id_to_raw,
                    "kpis": kpi_id_to_raw,
                },
                "timeseries_path": str(timeseries_path),
                "graph_reference_path": str(graph_path),
                "temporal_graph_path": str(temporal_graph_path),
                "notes": [
                    "Converted from a collected GreenRAN/ns-3 conflict dataset.",
                    "Feature ids are prefixed with P*/K* so the isolated article00 trainer can reuse its current logic.",
                    "Parameter values are event-driven proxy control signals derived from confidence, mitigation, power, and severity.",
                    "KPI values are forward-filled observed KPI measurements from the conflict rows.",
                ],
            },
            handle,
            indent=2,
        )
    with graph_path.open("w", encoding="utf-8") as handle:
        json.dump(reference_graph, handle, indent=2)
    with temporal_graph_path.open("w", encoding="utf-8") as handle:
        json.dump(temporal_graph, handle, indent=2)

    print(f"ns3 article00-style dataset generated at: {output_dir}")
    print(f"- source dataset: {dataset_path}")
    print(f"- timeseries: {timeseries_path}")
    print(f"- metadata: {metadata_path}")
    print(f"- graph reference: {graph_path}")
    print(f"- temporal graph: {temporal_graph_path}")


if __name__ == "__main__":
    main()
