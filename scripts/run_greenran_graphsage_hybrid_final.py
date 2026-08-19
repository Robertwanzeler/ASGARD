#!/usr/bin/env python3
"""
Build a hybrid final GreenRAN GraphSAGE package by selecting the best observed
scenario result across the official protocol and calibration runs.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROTOCOL_ROOT = PROJECT_ROOT / "runs" / "graphsage_article00_protocol"
DEFAULT_CALIBRATION_ROOT = PROJECT_ROOT / "runs" / "graphsage_article00_calibration"
DEFAULT_CALIBRATION_VEHICLE_RECOVERY_ROOT = PROJECT_ROOT / "runs" / "graphsage_article00_calibration_vehicle_recovery"
DEFAULT_FAMILY_ROOT = PROJECT_ROOT / "runs" / "graphsage_article00_calibration_family"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "runs" / "graphsage_article00_hybrid_final"
SOURCE_PRIORITY = {
    "calibration_vehicle_recovery": 7,
    "protocol": 3,
    "calibration_v1": 2,
    "family_v1": 1,
}


@dataclass(frozen=True)
class AggregateRow:
    source: str
    scenario: str
    threshold: float
    completed_seeds: int
    target_hits: int
    mean_f1_at_target_epoch: float
    mean_best_f1: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a hybrid final package from the best observed GraphSAGE runs.")
    parser.add_argument("--protocol-root", default=str(DEFAULT_PROTOCOL_ROOT))
    parser.add_argument("--calibration-root", default=str(DEFAULT_CALIBRATION_ROOT))
    parser.add_argument("--calibration-vehicle-recovery-root", default=str(DEFAULT_CALIBRATION_VEHICLE_RECOVERY_ROOT))
    parser.add_argument("--family-root", default=str(DEFAULT_FAMILY_ROOT))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--threshold", type=float, default=0.5, help="selection threshold to freeze")
    parser.add_argument("--subset-size", type=int, default=450, help="subset size to freeze")
    parser.add_argument("--plan-only", action="store_true", help="only write the selection summary; do not copy artifacts")
    return parser.parse_args()


def load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def protocol_aggregate_rows(path: Path, source: str = "protocol") -> list[AggregateRow]:
    payload = load_json(path)
    rows = []
    for item in payload.get("focus_aggregates", []):
        rows.append(
            AggregateRow(
                source=source,
                scenario=item["scenario"],
                threshold=float(item["threshold"]),
                completed_seeds=int(item["completed_seeds"]),
                target_hits=int(item["target_hits"]),
                mean_f1_at_target_epoch=float(item["mean_f1_at_target_epoch"]),
                mean_best_f1=float(item["mean_best_f1"]),
            )
        )
    return rows


def calibration_aggregate_rows(path: Path, source: str) -> list[AggregateRow]:
    payload = load_json(path)
    rows = []
    for item in payload.get("aggregates", []):
        rows.append(
            AggregateRow(
                source=source,
                scenario=item["scenario"],
                threshold=float(item["threshold"]),
                completed_seeds=int(item["completed_seeds"]),
                target_hits=int(item["target_hits"]),
                mean_f1_at_target_epoch=float(item["mean_f1_at_target_epoch"]),
                mean_best_f1=float(item["mean_best_f1"]),
            )
        )
    return rows


def select_best_source(rows: list[AggregateRow], threshold: float) -> dict[str, AggregateRow]:
    by_scenario: dict[str, list[AggregateRow]] = {}
    for row in rows:
        if float(row.threshold) != float(threshold):
            continue
        by_scenario.setdefault(row.scenario, []).append(row)

    selected: dict[str, AggregateRow] = {}
    for scenario, options in sorted(by_scenario.items()):
        selected[scenario] = max(
            options,
            key=lambda row: (
                float(row.mean_f1_at_target_epoch),
                int(row.target_hits),
                float(row.mean_best_f1),
                int(SOURCE_PRIORITY.get(row.source, 0)),
            ),
        )
    return selected


def gather_row_records(summary_path: Path, source: str, threshold: float, subset_size: int) -> list[dict]:
    payload = load_json(summary_path)
    rows_key = "rows"
    records = []
    for row in payload.get(rows_key, []):
        if float(row.get("threshold", 0.0)) != float(threshold):
            continue
        if int(row.get("subset_size", 0)) != int(subset_size):
            continue
        records.append({"source": source, **row})
    return records


def copy_subset_dir(source_summary_path: Path, target_dir: Path) -> None:
    subset_dir = source_summary_path.parent
    if target_dir.exists():
        shutil.rmtree(target_dir)
    shutil.copytree(subset_dir, target_dir)


def write_summary(output_root: Path, payload: dict) -> None:
    summary_json = output_root / "hybrid_final_summary.json"
    summary_csv = output_root / "hybrid_final_summary.csv"
    summary_md = output_root / "hybrid_final_summary.md"

    with summary_json.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)

    with summary_csv.open("w", newline="", encoding="utf-8") as handle:
        fieldnames = [
            "scenario",
            "selected_source",
            "threshold",
            "subset_size",
            "completed_seeds",
            "target_hits",
            "mean_f1_at_target_epoch",
            "mean_best_f1",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(payload["selected"])

    lines = [
        "# GreenRAN GraphSAGE Hybrid Final",
        "",
        f"- threshold congelado: `{payload['threshold']}`",
        f"- subset congelado: `{payload['subset_size']}`",
        f"- plan_only: `{payload['plan_only']}`",
        "",
        "| Scenario | Selected source | Completed seeds | Hits at target | Mean F1 at target | Mean best F1 |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for row in payload["selected"]:
        lines.append(
            "| {scenario} | {selected_source} | {completed_seeds} | {target_hits} | {mean_target:.6f} | {mean_best:.6f} |".format(
                scenario=row["scenario"],
                selected_source=row["selected_source"],
                completed_seeds=int(row["completed_seeds"]),
                target_hits=int(row["target_hits"]),
                mean_target=float(row["mean_f1_at_target_epoch"]),
                mean_best=float(row["mean_best_f1"]),
            )
        )
    summary_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    protocol_root = Path(args.protocol_root).resolve()
    calibration_root = Path(args.calibration_root).resolve()
    calibration_vehicle_recovery_root = Path(args.calibration_vehicle_recovery_root).resolve()
    family_root = Path(args.family_root).resolve()
    output_root = Path(args.output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    aggregate_rows = []
    aggregate_rows.extend(protocol_aggregate_rows(protocol_root / "protocol_summary.json"))
    aggregate_rows.extend(calibration_aggregate_rows(calibration_root / "calibration_summary.json", "calibration_v1"))
    if (calibration_vehicle_recovery_root / "calibration_summary.json").exists():
        aggregate_rows.extend(
            calibration_aggregate_rows(
                calibration_vehicle_recovery_root / "calibration_summary.json",
                "calibration_vehicle_recovery",
            )
        )
    aggregate_rows.extend(calibration_aggregate_rows(family_root / "calibration_summary.json", "family_v1"))
    selected = select_best_source(aggregate_rows, args.threshold)

    protocol_records = gather_row_records(protocol_root / "protocol_summary.json", "protocol", args.threshold, args.subset_size)
    calibration_records = gather_row_records(calibration_root / "calibration_summary.json", "calibration_v1", args.threshold, args.subset_size)
    calibration_vehicle_recovery_records = []
    if (calibration_vehicle_recovery_root / "calibration_summary.json").exists():
        calibration_vehicle_recovery_records = gather_row_records(
            calibration_vehicle_recovery_root / "calibration_summary.json",
            "calibration_vehicle_recovery",
            args.threshold,
            args.subset_size,
        )
    family_records = gather_row_records(family_root / "calibration_summary.json", "family_v1", args.threshold, args.subset_size)
    all_records = (
        protocol_records
        + calibration_records
        + calibration_vehicle_recovery_records
        + family_records
    )

    selected_rows = []
    materialized = []
    for scenario, agg in sorted(selected.items()):
        selected_rows.append(
            {
                "scenario": scenario,
                "selected_source": agg.source,
                "threshold": agg.threshold,
                "subset_size": args.subset_size,
                "completed_seeds": agg.completed_seeds,
                "target_hits": agg.target_hits,
                "mean_f1_at_target_epoch": agg.mean_f1_at_target_epoch,
                "mean_best_f1": agg.mean_best_f1,
            }
        )
        scenario_records = sorted(
            [
                row for row in all_records
                if row["scenario"] == scenario and row["source"] == agg.source
            ],
            key=lambda row: int(row["seed"]),
        )
        for row in scenario_records:
            src_summary = Path(row["path"])
            target_dir = output_root / "selected_artifacts" / scenario / f"seed_{row['seed']}"
            materialized.append(
                {
                    "scenario": scenario,
                    "seed": int(row["seed"]),
                    "source": agg.source,
                    "source_summary_path": str(src_summary),
                    "target_dir": str(target_dir),
                }
            )
            if not args.plan_only:
                copy_subset_dir(src_summary, target_dir)

    payload = {
        "generated_at": str(Path().cwd()),
        "threshold": args.threshold,
        "subset_size": args.subset_size,
        "plan_only": args.plan_only,
        "selected": selected_rows,
        "materialized": materialized,
    }
    write_summary(output_root, payload)

    print(
        json.dumps(
            {
                "summary_json": str(output_root / "hybrid_final_summary.json"),
                "summary_csv": str(output_root / "hybrid_final_summary.csv"),
                "summary_md": str(output_root / "hybrid_final_summary.md"),
                "selected_scenarios": len(selected_rows),
                "materialized_dirs": len(materialized),
                "plan_only": args.plan_only,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
