#!/usr/bin/env python3
"""
Automate the GreenRAN AWAC refresh cycle without changing the runtime.

Pipeline:
1. Export the latest resource-allocation trace from the live Data Lake.
2. Check whether the collection has grown enough versus the production baseline.
3. Train a new offline AWAC candidate only when growth is material.
4. Compare the candidate against the current production checkpoint.
5. Emit a machine-readable decision summary, but never promote automatically.
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from greenran_paths import RAPP_DB_PATH


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    output_root = repo_root / "runs" / "sac_bootstrap"
    parser = argparse.ArgumentParser(description="Run an automated AWAC refresh cycle for GreenRAN")
    parser.add_argument("--db", default=str(RAPP_DB_PATH), help="SQLite database path")
    parser.add_argument(
        "--workload-csv",
        default=str(output_root / "workload_trace_real.csv"),
        help="CSV path exported from the live collection",
    )
    parser.add_argument(
        "--baseline-summary",
        default=str(output_root / "offline_awac_20260524_refresh" / "sac_offline_summary.json"),
        help="Production baseline summary JSON used for comparison",
    )
    parser.add_argument(
        "--output-root",
        default=str(output_root),
        help="Root directory where automated AWAC runs are stored",
    )
    parser.add_argument(
        "--min-new-rows",
        type=int,
        default=5000,
        help="Minimum growth versus the production baseline before retraining",
    )
    parser.add_argument(
        "--ran-tolerance",
        type=float,
        default=0.0025,
        help="Maximum acceptable drop in avg_ran_completion versus the production baseline",
    )
    parser.add_argument(
        "--ai-tolerance",
        type=float,
        default=0.005,
        help="Maximum acceptable drop in avg_ai_completion versus the production baseline",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Run training even if the collection growth threshold is not met",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Export and evaluate the collection, but never start a new training run",
    )
    return parser.parse_args()


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def summarize_csv(csv_path: Path) -> dict:
    rows = list(csv.DictReader(csv_path.open("r", encoding="utf-8")))
    if not rows:
        return {
            "rows": 0,
            "d_ran_unique": 0,
            "d_ai_unique": 0,
            "ran_completion_unique": 0,
            "ai_completion_unique": 0,
            "utilization_unique": 0,
            "latest_row": {},
        }

    def values(key: str) -> list[float]:
        return [float(row[key]) for row in rows]

    def unique_count(key: str) -> int:
        return len({round(value, 6) for value in values(key)})

    def stats(key: str) -> list[float]:
        vals = values(key)
        return [min(vals), sum(vals) / len(vals), max(vals)]

    return {
        "rows": len(rows),
        "d_ran_unique": unique_count("d_ran"),
        "d_ai_unique": unique_count("d_ai"),
        "ran_completion_unique": unique_count("ran_completion_ratio"),
        "ai_completion_unique": unique_count("ai_completion_ratio"),
        "utilization_unique": unique_count("utilization_ratio"),
        "d_ran_stats": stats("d_ran"),
        "d_ai_stats": stats("d_ai"),
        "ran_completion_stats": stats("ran_completion_ratio"),
        "ai_completion_stats": stats("ai_completion_ratio"),
        "utilization_stats": stats("utilization_ratio"),
        "latest_row": rows[-1],
    }


def compare_candidate(baseline: dict, candidate: dict, ran_tolerance: float, ai_tolerance: float) -> dict:
    baseline_val = float(baseline.get("val_action_mae", 1e9) or 1e9)
    candidate_val = float(candidate.get("val_action_mae", 1e9) or 1e9)

    baseline_rollout = baseline.get("rollout", {}) or {}
    candidate_rollout = candidate.get("rollout", {}) or {}
    baseline_ran = float(baseline_rollout.get("avg_ran_completion", 0.0) or 0.0)
    candidate_ran = float(candidate_rollout.get("avg_ran_completion", 0.0) or 0.0)
    baseline_ai = float(baseline_rollout.get("avg_ai_completion", 0.0) or 0.0)
    candidate_ai = float(candidate_rollout.get("avg_ai_completion", 0.0) or 0.0)

    promote = (
        candidate_val < baseline_val
        and candidate_ran >= (baseline_ran - ran_tolerance)
        and candidate_ai >= (baseline_ai - ai_tolerance)
    )
    return {
        "promote": promote,
        "baseline_val_action_mae": baseline_val,
        "candidate_val_action_mae": candidate_val,
        "baseline_avg_ran_completion": baseline_ran,
        "candidate_avg_ran_completion": candidate_ran,
        "baseline_avg_ai_completion": baseline_ai,
        "candidate_avg_ai_completion": candidate_ai,
        "ran_tolerance": ran_tolerance,
        "ai_tolerance": ai_tolerance,
    }


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    workload_csv = Path(args.workload_csv)
    baseline_summary_path = Path(args.baseline_summary)
    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    if not baseline_summary_path.exists():
        raise SystemExit(f"baseline summary not found: {baseline_summary_path}")

    baseline_summary = load_json(baseline_summary_path)
    baseline_rows = int(baseline_summary.get("trace_points", 0) or 0)

    export_cmd = [
        sys.executable,
        str(repo_root / "scripts" / "export_sac_workload_trace.py"),
        "--db",
        args.db,
        "--output-csv",
        str(workload_csv),
    ]
    subprocess.run(export_cmd, cwd=repo_root, check=True)

    csv_summary = summarize_csv(workload_csv)
    row_growth = int(csv_summary["rows"]) - baseline_rows
    should_train = args.force or row_growth >= args.min_new_rows

    cycle_summary = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "baseline_summary": str(baseline_summary_path),
        "baseline_trace_points": baseline_rows,
        "csv_summary": csv_summary,
        "row_growth_vs_baseline": row_growth,
        "min_new_rows": args.min_new_rows,
        "should_train": should_train,
        "force": bool(args.force),
        "dry_run": bool(args.dry_run),
        "decision": "keep_production",
        "reason": "",
    }

    if args.dry_run:
        cycle_summary["reason"] = (
            "Dry run: collection exported and evaluated; no training was started."
        )
        latest_summary_path = output_root / "awac_refresh_cycle_latest.json"
        latest_summary_path.write_text(json.dumps(cycle_summary, indent=2), encoding="utf-8")
        print(json.dumps(cycle_summary, indent=2))
        return 0

    if not should_train:
        cycle_summary["reason"] = (
            f"Collection grew by {row_growth} rows; minimum required is {args.min_new_rows}."
        )
        latest_summary_path = output_root / "awac_refresh_cycle_latest.json"
        latest_summary_path.write_text(json.dumps(cycle_summary, indent=2), encoding="utf-8")
        print(json.dumps(cycle_summary, indent=2))
        return 0

    run_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = output_root / f"offline_awac_{run_stamp}_auto"
    train_cmd = [
        str(repo_root / "drlexp" / ".venv" / "bin" / "python"),
        str(repo_root / "drlexp" / "training" / "train_sac.py"),
        "--workload-csv",
        str(workload_csv),
        "--output-dir",
        str(output_dir),
    ]
    subprocess.run(train_cmd, cwd=repo_root, check=True)

    candidate_summary_path = output_dir / "sac_offline_summary.json"
    candidate_summary = load_json(candidate_summary_path)
    comparison = compare_candidate(
        baseline_summary,
        candidate_summary,
        ran_tolerance=args.ran_tolerance,
        ai_tolerance=args.ai_tolerance,
    )

    cycle_summary.update({
        "candidate_summary": str(candidate_summary_path),
        "candidate_trace_points": int(candidate_summary.get("trace_points", 0) or 0),
        "comparison": comparison,
        "decision": "promote_candidate" if comparison["promote"] else "keep_production",
        "reason": (
            "Candidate beat the production baseline under the current validation and rollout guardrails."
            if comparison["promote"]
            else "Candidate did not beat the production baseline under the current validation and rollout guardrails."
        ),
    })

    cycle_summary_path = output_dir / "awac_refresh_cycle_summary.json"
    latest_summary_path = output_root / "awac_refresh_cycle_latest.json"
    cycle_summary_path.write_text(json.dumps(cycle_summary, indent=2), encoding="utf-8")
    latest_summary_path.write_text(json.dumps(cycle_summary, indent=2), encoding="utf-8")
    print(json.dumps(cycle_summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
