#!/usr/bin/env python3
"""
Summarize targeted GreenRAN GraphSAGE calibration runs.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "runs" / "graphsage_article00_calibration"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize targeted GreenRAN GraphSAGE calibration runs.")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    return parser.parse_args()


def load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def history_index(summary: dict) -> dict[int, dict]:
    return {int(item["epoch"]): item for item in summary.get("history", [])}


def main() -> int:
    args = parse_args()
    output_root = Path(args.output_root).resolve()
    manifest_path = output_root / "calibration_manifest.json"
    if not manifest_path.exists():
        raise SystemExit(f"calibration manifest not found: {manifest_path}")
    manifest = load_json(manifest_path)
    target_epoch = max(int(v) for v in manifest.get("epochs", [200]))
    rows = []
    for path in sorted(output_root.glob("**/training_summary.json")):
        rel = path.relative_to(output_root)
        if len(rel.parts) < 6:
            continue
        profile = rel.parts[0]
        threshold = float(rel.parts[1].replace("threshold_", "").replace("_", "."))
        seed = int(rel.parts[2].replace("seed_", ""))
        scenario = rel.parts[3]
        subset_size = int(rel.parts[5].replace("subset_", ""))
        summary = load_json(path)
        at_target = history_index(summary).get(target_epoch, {})
        rows.append(
            {
                "profile": profile,
                "threshold": threshold,
                "seed": seed,
                "scenario": scenario,
                "subset_size": subset_size,
                "best_epoch": summary.get("best_epoch"),
                "best_f1": summary.get("best_metrics", {}).get("f1"),
                "f1_at_target_epoch": at_target.get("f1"),
                "precision_at_target_epoch": at_target.get("precision"),
                "recall_at_target_epoch": at_target.get("recall"),
                "target_met": float(at_target.get("f1", 0.0) or 0.0) >= 1.0,
                "path": str(path),
            }
        )

    by_group: dict[tuple[str, float, str], list[dict]] = defaultdict(list)
    for row in rows:
        if int(row["subset_size"]) != 450:
            continue
        by_group[(row["profile"], float(row["threshold"]), row["scenario"])].append(row)

    aggregates = []
    for (profile, threshold, scenario), items in sorted(by_group.items()):
        mean_200 = sum(float(item["f1_at_target_epoch"] or 0.0) for item in items) / len(items)
        mean_best = sum(float(item["best_f1"] or 0.0) for item in items) / len(items)
        hits = sum(1 for item in items if item["target_met"])
        aggregates.append(
            {
                "profile": profile,
                "threshold": threshold,
                "scenario": scenario,
                "completed_seeds": len(items),
                "target_hits": hits,
                "mean_f1_at_target_epoch": round(mean_200, 6),
                "mean_best_f1": round(mean_best, 6),
            }
        )

    summary_json = output_root / "calibration_summary.json"
    summary_csv = output_root / "calibration_summary.csv"
    summary_md = output_root / "calibration_summary.md"
    payload = {
        "manifest_path": str(manifest_path),
        "target_epoch": target_epoch,
        "rows": rows,
        "aggregates": aggregates,
    }
    with summary_json.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
    with summary_csv.open("w", newline="", encoding="utf-8") as handle:
        fieldnames = [
            "profile",
            "threshold",
            "seed",
            "scenario",
            "subset_size",
            "best_epoch",
            "best_f1",
            "f1_at_target_epoch",
            "precision_at_target_epoch",
            "recall_at_target_epoch",
            "target_met",
            "path",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    lines = [
        "# GreenRAN GraphSAGE Calibration Summary",
        "",
        f"- target_epoch: `{target_epoch}`",
        "",
        "| Profile | Threshold | Scenario | Completed seeds | Hits at target | Mean F1 at target | Mean best F1 |",
        "|---|---|---|---:|---:|---:|---:|",
    ]
    for item in aggregates:
        lines.append(
            "| {profile} | {threshold:.1f} | {scenario} | {completed_seeds} | {target_hits} | {mean_target:.6f} | {mean_best:.6f} |".format(
                profile=item["profile"],
                threshold=float(item["threshold"]),
                scenario=item["scenario"],
                completed_seeds=int(item["completed_seeds"]),
                target_hits=int(item["target_hits"]),
                mean_target=float(item["mean_f1_at_target_epoch"]),
                mean_best=float(item["mean_best_f1"]),
            )
        )
    summary_md.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(json.dumps(
        {
            "summary_json": str(summary_json),
            "summary_csv": str(summary_csv),
            "summary_md": str(summary_md),
            "aggregate_rows": len(aggregates),
            "training_summaries": len(rows),
        },
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
