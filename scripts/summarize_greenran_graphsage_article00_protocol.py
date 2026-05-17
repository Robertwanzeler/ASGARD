#!/usr/bin/env python3
"""
Summarize the official GreenRAN GraphSAGE protocol aligned with ARTICLE00.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "runs" / "graphsage_article00_protocol"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Summarize progress and metrics for the GreenRAN GraphSAGE ARTICLE00-aligned protocol."
    )
    parser.add_argument(
        "--output-root",
        default=str(DEFAULT_OUTPUT_ROOT),
        help="protocol output root; defaults to runs/graphsage_article00_protocol",
    )
    parser.add_argument(
        "--focus-subset",
        type=int,
        default=450,
        help="subset used in the headline summary",
    )
    return parser.parse_args()


def load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def parse_summary_path(path: Path, output_root: Path) -> dict:
    rel = path.relative_to(output_root)
    parts = rel.parts
    if len(parts) < 6:
        raise SystemExit(f"unexpected summary path layout: {path}")
    threshold = parts[0].replace("threshold_", "").replace("_", ".")
    seed = parts[1].replace("seed_", "")
    scenario = parts[2]
    subset = int(parts[4].replace("subset_", ""))
    return {
        "threshold": float(threshold),
        "seed": int(seed),
        "scenario": scenario,
        "subset_size": subset,
    }


def history_index(summary: dict) -> dict[int, dict]:
    return {int(item["epoch"]): item for item in summary.get("history", [])}


def target_row(summary: dict, target_epoch: int) -> dict:
    return history_index(summary).get(target_epoch, {})


def format_float(value: float | None) -> str:
    if value is None:
        return ""
    return f"{float(value):.6f}"


def build_rows(output_root: Path, target_epoch: int) -> list[dict]:
    rows = []
    for path in sorted(output_root.glob("threshold_*/*/*/*/subset_*/training_summary.json")):
        summary = load_json(path)
        parsed = parse_summary_path(path, output_root)
        at_target = target_row(summary, target_epoch)
        best_metrics = summary.get("best_metrics", {}) or {}
        rows.append(
            {
                **parsed,
                "best_epoch": summary.get("best_epoch"),
                "best_f1": best_metrics.get("f1"),
                "best_precision": best_metrics.get("precision"),
                "best_recall": best_metrics.get("recall"),
                "f1_at_target_epoch": at_target.get("f1"),
                "precision_at_target_epoch": at_target.get("precision"),
                "recall_at_target_epoch": at_target.get("recall"),
                "split_mode": summary.get("split_mode", ""),
                "contributing_rounds": ",".join(str(v) for v in summary.get("contributing_rounds", [])),
                "target_met": float(at_target.get("f1", 0.0) or 0.0) >= 1.0,
                "path": str(path),
            }
        )
    return rows


def aggregate_focus(rows: list[dict], focus_subset: int) -> list[dict]:
    grouped: dict[tuple[float, str], list[dict]] = defaultdict(list)
    for row in rows:
        if int(row["subset_size"]) != int(focus_subset):
            continue
        grouped[(float(row["threshold"]), row["scenario"])].append(row)

    aggregates = []
    for (threshold, scenario), items in sorted(grouped.items()):
        f1_values = [float(item["f1_at_target_epoch"] or 0.0) for item in items]
        best_values = [float(item["best_f1"] or 0.0) for item in items]
        target_hits = sum(1 for item in items if item["target_met"])
        aggregates.append(
            {
                "threshold": threshold,
                "scenario": scenario,
                "completed_seeds": len(items),
                "target_hits": target_hits,
                "mean_f1_at_target_epoch": round(sum(f1_values) / len(f1_values), 6),
                "mean_best_f1": round(sum(best_values) / len(best_values), 6),
            }
        )
    return aggregates


def build_progress(manifest: dict, rows: list[dict]) -> dict:
    requested_subsets = tuple(int(v) for v in manifest.get("protocol", {}).get("subset_sizes", []))
    completed_run_keys = set()
    per_run_subsets: dict[tuple[float, int, str], set[int]] = defaultdict(set)
    for row in rows:
        key = (float(row["threshold"]), int(row["seed"]), row["scenario"])
        per_run_subsets[key].add(int(row["subset_size"]))
    for key, subsets in per_run_subsets.items():
        if all(subset in subsets for subset in requested_subsets):
            completed_run_keys.add(key)

    return {
        "planned_runs": len(manifest.get("runs", [])),
        "completed_training_summaries": len(rows),
        "completed_run_groups": len(completed_run_keys),
        "requested_subsets": list(requested_subsets),
    }


def write_csv(path: Path, rows: list[dict]) -> None:
    fieldnames = [
        "threshold",
        "seed",
        "scenario",
        "subset_size",
        "best_epoch",
        "best_f1",
        "best_precision",
        "best_recall",
        "f1_at_target_epoch",
        "precision_at_target_epoch",
        "recall_at_target_epoch",
        "split_mode",
        "contributing_rounds",
        "target_met",
        "path",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_markdown(
    path: Path,
    manifest: dict,
    progress: dict,
    focus_aggregates: list[dict],
    target_epoch: int,
    focus_subset: int,
) -> None:
    lines = [
        "# GreenRAN GraphSAGE Protocol Summary",
        "",
        f"- output_root: `{manifest.get('output_root', '')}`",
        f"- target_epoch: `{target_epoch}`",
        f"- focus_subset: `{focus_subset}`",
        f"- planned_runs: `{progress['planned_runs']}`",
        f"- completed_run_groups: `{progress['completed_run_groups']}`",
        f"- completed_training_summaries: `{progress['completed_training_summaries']}`",
        "",
        "## Focus Summary",
        "",
        "| Threshold | Scenario | Completed seeds | Hits at target | Mean F1 at target | Mean best F1 |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for item in focus_aggregates:
        lines.append(
            "| {threshold:.1f} | {scenario} | {completed_seeds} | {target_hits} | {mean_target:.6f} | {mean_best:.6f} |".format(
                threshold=float(item["threshold"]),
                scenario=item["scenario"],
                completed_seeds=int(item["completed_seeds"]),
                target_hits=int(item["target_hits"]),
                mean_target=float(item["mean_f1_at_target_epoch"]),
                mean_best=float(item["mean_best_f1"]),
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    output_root = Path(args.output_root).resolve()
    manifest_path = output_root / "protocol_manifest.json"
    if not manifest_path.exists():
        raise SystemExit(f"protocol manifest not found: {manifest_path}")

    manifest = load_json(manifest_path)
    target_epoch = int(manifest.get("official_target", {}).get("target_epoch", 200))
    rows = build_rows(output_root, target_epoch)
    progress = build_progress(manifest, rows)
    focus_aggregates = aggregate_focus(rows, args.focus_subset)

    summary_json = output_root / "protocol_summary.json"
    summary_csv = output_root / "protocol_summary.csv"
    summary_md = output_root / "protocol_summary.md"

    payload = {
        "manifest_path": str(manifest_path),
        "target_epoch": target_epoch,
        "focus_subset": args.focus_subset,
        "progress": progress,
        "focus_aggregates": focus_aggregates,
        "rows": rows,
    }
    with summary_json.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
    write_csv(summary_csv, rows)
    write_markdown(summary_md, manifest, progress, focus_aggregates, target_epoch, args.focus_subset)

    print(json.dumps(
        {
            "summary_json": str(summary_json),
            "summary_csv": str(summary_csv),
            "summary_md": str(summary_md),
            "progress": progress,
            "focus_rows": len(focus_aggregates),
        },
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
