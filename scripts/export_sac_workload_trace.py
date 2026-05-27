#!/usr/bin/env python3
"""
Export a real CAORA-style workload trace from the GreenRAN Data Lake.

The trace format (d_ran, d_ai, r_ran, r_ai, usable_budget) follows the
shared-resource state space defined in [1].

[1] Lotfi, F., Rajoli, H. & Afghah, F. "Task-Specific Sharpness-Aware O-RAN
    Resource Management using Multi-Agent Reinforcement Learning".
    IEEE TMLCN, 2025. arXiv:2511.15002.
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rapp_data_lake import DataLake
from greenran_paths import RAPP_DB_PATH


DEFAULT_DB_PATH = str(RAPP_DB_PATH)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export SAC workload trace from resource_allocation_history")
    parser.add_argument("--db", default=DEFAULT_DB_PATH, help="SQLite database path")
    parser.add_argument("--output-csv", required=True, help="Output CSV path")
    parser.add_argument("--limit", type=int, default=0, help="Optional row limit from newest samples")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    # Initialize DataLake once to ensure the latest schema exists even if the
    # database was created before the SAC migration landed.
    dl = DataLake(args.db)
    conn = dl.conn
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    resource_columns = {
        row["name"] if isinstance(row, sqlite3.Row) else row[1]
        for row in cursor.execute("PRAGMA table_info(resource_allocation_history)").fetchall()
    }
    has_usable_budget = "usable_budget" in resource_columns

    usable_budget_expr = "usable_budget" if has_usable_budget else "resource_budget AS usable_budget"

    query = f"""
        SELECT timestamp, datetime, controller_id, target_policy_id,
               decision_domain, action_semantics, d_ran, d_ai, r_ran, r_ai,
               {usable_budget_expr}, delta_r_ran, delta_r_ai, ran_completion_ratio,
               ai_completion_ratio, utilization_ratio
        FROM resource_allocation_history
        ORDER BY timestamp
    """
    rows = cursor.execute(query).fetchall()
    if args.limit and args.limit > 0:
        rows = rows[-args.limit:]

    if not rows:
        print("exported_rows=0")
        print("reason=no_resource_allocation_samples_found")
        print(
            "hint=run the updated rapp_orchestrator first so it can populate "
            "resource_allocation_history"
        )
        return 0

    out_path = Path(args.output_csv)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "timestamp",
            "datetime",
            "controller_id",
            "target_policy_id",
            "decision_domain",
            "action_semantics",
            "d_ran",
            "d_ai",
            "r_ran",
            "r_ai",
            "usable_budget",
            "delta_r_ran",
            "delta_r_ai",
            "ran_completion_ratio",
            "ai_completion_ratio",
            "utilization_ratio",
        ])
        for row in rows:
            writer.writerow([
                row["timestamp"],
                row["datetime"],
                row["controller_id"],
                row["target_policy_id"],
                row["decision_domain"],
                row["action_semantics"],
                row["d_ran"],
                row["d_ai"],
                row["r_ran"],
                row["r_ai"],
                row["usable_budget"],
                row["delta_r_ran"],
                row["delta_r_ai"],
                row["ran_completion_ratio"],
                row["ai_completion_ratio"],
                row["utilization_ratio"],
            ])

    print(f"exported_rows={len(rows)}")
    print(f"output_csv={out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
