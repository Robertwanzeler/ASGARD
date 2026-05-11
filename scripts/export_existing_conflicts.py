#!/usr/bin/env python3
"""
Export conflict data from existing database (offline mode).
Uses data already stored in /tmp/rapp_data_lake.db

Usage:
    python3 export_existing_conflicts.py --output-dir runs/experimentos_conflitos/existing
"""

import argparse
import csv
import json
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path


DB_PATH = "/tmp/rapp_data_lake.db"


def export_existing_conflicts(
    since_ts: int | None = None,
    until_ts: int | None = None,
    focus: str = "all",
    dataset_path: Path | None = None,
    graph_path: Path | None = None,
):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    query = """
        SELECT
            ce.timestamp,
            ce.datetime,
            ce.source_agent,
            ce.target_agent,
            ce.conflict_type,
            ce.parameter,
            ce.affected_service,
            ce.affected_kpi,
            ce.observed_value,
            ce.threshold_value,
            ce.decision AS conflict_decision,
            ce.mitigation_action,
            ce.reason AS conflict_reason,
            ce.confidence AS conflict_confidence,
            em.global_worst_latency_us,
            em.latency_p95_per_ue_us,
            em.cvar_per_ue_us,
            em.throughput_kbps,
            em.global_packet_loss_rate
        FROM conflict_events ce
        LEFT JOIN extended_metrics em ON em.timestamp = ce.timestamp
        WHERE 1=1
    """
    params = []

    if since_ts:
        query += " AND ce.timestamp >= ?"
        params.append(since_ts)
    if until_ts:
        query += " AND ce.timestamp < ?"
        params.append(until_ts)
    if focus != "all":
        query += " AND ce.conflict_type = ?"
        params.append(focus)

    rows = conn.execute(query, params).fetchall()
    nodes = set()
    edges = []

    for row in rows:
        if row["affected_service"]:
            nodes.add(row["affected_service"])
        if row["source_agent"]:
            nodes.add(row["source_agent"])
        if row["target_agent"]:
            nodes.add(row["target_agent"])
        if row["conflict_type"] and row["affected_kpi"]:
            edges.append((row["conflict_type"], row["affected_kpi"]))

    adjacency = {}
    for src, tgt in edges:
        if src not in adjacency:
            adjacency[src] = []
        if tgt not in adjacency[src]:
            adjacency[src].append(tgt)

    graph_data = {
        "nodes": list(nodes),
        "adjacency": adjacency,
        "conflict_count": len(rows),
        "timestamp_range": {
            "start": rows[0]["timestamp"] if rows else None,
            "end": rows[-1]["timestamp"] if rows else None,
        },
    }

    dataset_rows = []
    for row in rows:
        if focus == "all" or row["conflict_type"] == focus:
            dataset_rows.append({
                "timestamp": row["timestamp"],
                "datetime": row["datetime"],
                "conflict_type": row["conflict_type"],
                "affected_service": row["affected_service"],
                "observed_value": row["observed_value"],
                "threshold_value": row["threshold_value"],
                "decision": row["conflict_decision"],
                "latency_worst_us": row["global_worst_latency_us"],
                "latency_p95_us": row["latency_p95_per_ue_us"],
                "cvar_us": row["cvar_per_ue_us"],
                "throughput_kbps": row["throughput_kbps"],
                "packet_loss": row["global_packet_loss_rate"],
            })

    if dataset_path:
        dataset_path.parent.mkdir(parents=True, exist_ok=True)
        with open(dataset_path, "w", newline="") as f:
            if dataset_rows:
                writer = csv.DictWriter(f, fieldnames=dataset_rows[0].keys())
                writer.writeheader()
                writer.writerows(dataset_rows)

    if graph_path:
        graph_path.parent.mkdir(parents=True, exist_ok=True)
        with open(graph_path, "w") as f:
            json.dump(graph_data, f, indent=2)

    conn.close()

    return {
        "rows": len(dataset_rows),
        "nodes": len(nodes),
        "edges": len(edges),
        "conflict_types": dict(Counter(r["conflict_type"] for r in rows)),
        "services": dict(Counter(r["affected_service"] for r in rows if r["affected_service"])),
    }


def main():
    parser = argparse.ArgumentParser(description="Export existing conflicts from database")
    parser.add_argument("--since-ts", type=int, help="Start timestamp")
    parser.add_argument("--until-ts", type=int, help="End timestamp")
    parser.add_argument("--focus", default="all", choices=["all", "implicit", "indirect"])
    parser.add_argument("--output-dir", type=Path, default=Path("runs/existing_conflicts"))
    parser.add_argument("--dataset", type=Path, default=Path("conflict_dataset.csv"))
    parser.add_argument("--graph", type=Path, default=Path("conflict_graph.json"))

    args = parser.parse_args()

    output_dir = args.output_dir
    dataset_path = output_dir / args.dataset
    graph_path = output_dir / args.graph

    result = export_existing_conflicts(
        since_ts=args.since_ts,
        until_ts=args.until_ts,
        focus=args.focus,
        dataset_path=dataset_path,
        graph_path=graph_path,
    )

    print(f"Exported: {result['rows']} conflicts, {result['nodes']} nodes, {result['edges']} edges")
    print(f"Dataset: {dataset_path}")
    print(f"Graph: {graph_path}")
    print(f"Conflict types: {result['conflict_types']}")
    print(f"Services: {result['services']}")


if __name__ == "__main__":
    main()
