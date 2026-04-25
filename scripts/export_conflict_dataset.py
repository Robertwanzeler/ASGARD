#!/usr/bin/env python3
"""
Export GreenRAN conflict events as a tabular dataset and operational graph.

The dataset is the intermediate step between explicit rApp rules and a learned
conflict graph: each row captures an observed conflict event, the mitigation
applied by the rApp, and the nearest network context stored in the Data Lake.
"""

import argparse
import csv
import json
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from greenran_paths import RAPP_DB_PATH  # noqa: E402


DEFAULT_DATASET_PATH = Path("/tmp/greenran_conflict_dataset.csv")
DEFAULT_GRAPH_PATH = Path("/tmp/greenran_conflict_graph.json")


CONFLICT_QUERY = """
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
    ce.graph_path,
    dh.decision AS rapp_decision,
    dh.reason AS rapp_reason,
    dh.confidence AS rapp_confidence,
    dh.energy_state,
    dh.slicer_state,
    dh.ml_decision,
    dh.ml_confidence,
    dh.ml_predicted_cvar_ms,
    (
        SELECT ec.command
        FROM energy_commands ec
        WHERE ec.timestamp <= ce.timestamp
        ORDER BY ec.timestamp DESC
        LIMIT 1
    ) AS latest_energy_command,
    (
        SELECT ec.power_percent
        FROM energy_commands ec
        WHERE ec.timestamp <= ce.timestamp
        ORDER BY ec.timestamp DESC
        LIMIT 1
    ) AS latest_power_percent,
    (
        SELECT em.sim_time_s
        FROM extended_metrics em
        WHERE em.timestamp <= ce.timestamp
        ORDER BY em.timestamp DESC
        LIMIT 1
    ) AS sim_time_s,
    (
        SELECT em.cvar_per_ue_us
        FROM extended_metrics em
        WHERE em.timestamp <= ce.timestamp
        ORDER BY em.timestamp DESC
        LIMIT 1
    ) AS cvar_per_ue_us,
    (
        SELECT em.latency_p95_per_ue_us
        FROM extended_metrics em
        WHERE em.timestamp <= ce.timestamp
        ORDER BY em.timestamp DESC
        LIMIT 1
    ) AS latency_p95_per_ue_us,
    (
        SELECT em.throughput_kbps
        FROM extended_metrics em
        WHERE em.timestamp <= ce.timestamp
        ORDER BY em.timestamp DESC
        LIMIT 1
    ) AS throughput_kbps,
    (
        SELECT em.global_packet_loss_rate
        FROM extended_metrics em
        WHERE em.timestamp <= ce.timestamp
        ORDER BY em.timestamp DESC
        LIMIT 1
    ) AS global_packet_loss_rate,
    (
        SELECT em.total_active_cameras
        FROM extended_metrics em
        WHERE em.timestamp <= ce.timestamp
        ORDER BY em.timestamp DESC
        LIMIT 1
    ) AS total_active_cameras,
    (
        SELECT em.total_active_ues
        FROM extended_metrics em
        WHERE em.timestamp <= ce.timestamp
        ORDER BY em.timestamp DESC
        LIMIT 1
    ) AS total_active_ues
FROM conflict_events ce
LEFT JOIN decisions_history dh ON dh.timestamp = ce.timestamp
WHERE ce.timestamp >= ?
"""


def parse_args():
    parser = argparse.ArgumentParser(
        description="Export GreenRAN conflict dataset and graph from the rApp Data Lake."
    )
    parser.add_argument("--db", default=str(RAPP_DB_PATH), help="SQLite Data Lake path")
    parser.add_argument(
        "--dataset-input",
        default="",
        help="optional prebuilt CSV dataset path; when set, skips the database query and rebuilds only the graph",
    )
    parser.add_argument("--hours", type=float, default=24.0, help="lookback window in hours")
    parser.add_argument("--since-ts", type=int, default=0, help="inclusive lower timestamp bound (epoch seconds)")
    parser.add_argument("--until-ts", type=int, default=0, help="inclusive upper timestamp bound (epoch seconds)")
    parser.add_argument(
        "--until-exclusive-ts",
        type=int,
        default=0,
        help="exclusive upper timestamp bound (epoch seconds)",
    )
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET_PATH), help="CSV output path")
    parser.add_argument("--graph", default=str(DEFAULT_GRAPH_PATH), help="graph JSON output path")
    parser.add_argument("--limit", type=int, default=0, help="optional max rows from the newest window")
    return parser.parse_args()


def open_db(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def load_rows(conn, hours, limit, since_ts=0, until_ts=0, until_exclusive_ts=0):
    lower_bound = int(since_ts or (time.time() - hours * 3600))
    query = CONFLICT_QUERY
    params = [lower_bound]

    if until_exclusive_ts:
        query += "\nAND ce.timestamp < ?"
        params.append(int(until_exclusive_ts))
    elif until_ts:
        query += "\nAND ce.timestamp <= ?"
        params.append(int(until_ts))

    query += "\nORDER BY ce.timestamp ASC"

    rows = [dict(row) for row in conn.execute(query, params).fetchall()]
    if limit and len(rows) > limit:
        rows = rows[-limit:]
    return enrich_rows(rows)


def load_dataset_rows(path):
    dataset_path = Path(path)
    if not dataset_path.exists():
        raise FileNotFoundError(f"dataset not found: {dataset_path}")

    with dataset_path.open() as f:
        return [dict(row) for row in csv.DictReader(f)]


def enrich_rows(rows):
    previous_by_kpi = {}
    for row in rows:
        observed = _safe_float(row.get("observed_value"))
        threshold = _safe_float(row.get("threshold_value"))
        kpi = row.get("affected_kpi") or "unknown"
        previous = previous_by_kpi.get(kpi)

        row["observed_delta_from_threshold"] = (
            observed - threshold if observed is not None and threshold is not None else None
        )
        row["previous_observed_value"] = previous
        row["observed_delta_from_previous"] = (
            observed - previous if observed is not None and previous is not None else None
        )
        row["cvar_ms"] = _us_to_ms(row.get("cvar_per_ue_us"))
        row["p95_ms"] = _us_to_ms(row.get("latency_p95_per_ue_us"))
        row["throughput_mbps"] = _kbps_to_mbps(row.get("throughput_kbps"))
        row["global_packet_loss_percent"] = _safe_float(row.get("global_packet_loss_rate"), 0.0) * 100.0

        if observed is not None:
            previous_by_kpi[kpi] = observed

    return rows


def write_dataset(rows, dataset_path):
    output = Path(dataset_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    fields = [
        "timestamp",
        "datetime",
        "source_agent",
        "target_agent",
        "conflict_type",
        "parameter",
        "affected_service",
        "affected_kpi",
        "observed_value",
        "threshold_value",
        "observed_delta_from_threshold",
        "previous_observed_value",
        "observed_delta_from_previous",
        "conflict_decision",
        "mitigation_action",
        "latest_energy_command",
        "latest_power_percent",
        "rapp_decision",
        "rapp_confidence",
        "energy_state",
        "slicer_state",
        "ml_decision",
        "ml_confidence",
        "ml_predicted_cvar_ms",
        "sim_time_s",
        "cvar_ms",
        "p95_ms",
        "throughput_mbps",
        "global_packet_loss_percent",
        "total_active_cameras",
        "total_active_ues",
        "graph_path",
        "conflict_reason",
        "rapp_reason",
    ]

    with output.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    return output


def build_graph(rows):
    nodes = {}
    edges = {}
    arbiter = "rApp-ResourceOptimizer"
    stats = {
        "total_events": len(rows),
        "by_conflict_type": Counter(),
        "by_service": Counter(),
        "by_kpi": Counter(),
        "by_mitigation": Counter(),
    }

    for row in rows:
        source = row.get("source_agent") or "unknown_agent"
        target = row.get("target_agent") or "rApp-ResourceOptimizer"
        parameter = row.get("parameter") or "unknown_parameter"
        service = row.get("affected_service") or "unknown_service"
        kpi = row.get("affected_kpi") or "unknown_kpi"
        mitigation = row.get("mitigation_action") or "NONE"
        conflict_type = row.get("conflict_type") or "unknown"

        _add_node(nodes, source, "agent")
        _add_node(nodes, target, "agent")
        _add_node(nodes, arbiter, "arbiter")
        _add_node(nodes, parameter, "parameter")
        _add_node(nodes, service, "service")
        _add_node(nodes, kpi, "kpi")
        _add_node(nodes, mitigation, "mitigation")

        _add_edge(edges, source, parameter, "controls", row)
        _add_edge(edges, parameter, kpi, "affects", row)
        _add_edge(edges, kpi, service, "belongs_to", row)
        _add_edge(edges, kpi, arbiter, "triggers_arbitration", row)
        _add_edge(edges, arbiter, mitigation, "mitigates", row)
        _add_edge(edges, mitigation, service, "protects", row)

        stats["by_conflict_type"][conflict_type] += 1
        stats["by_service"][service] += 1
        stats["by_kpi"][kpi] += 1
        stats["by_mitigation"][mitigation] += 1

    return {
        "schema": "greenran.conflict_graph.v1",
        "generated_at": int(time.time()),
        "window_events": len(rows),
        "stats": _jsonable_stats(stats),
        "nodes": sorted(nodes.values(), key=lambda item: (item["type"], item["id"])),
        "edges": sorted(edges.values(), key=lambda item: (item["source"], item["target"], item["relation"])),
    }


def write_graph(graph, graph_path):
    output = Path(graph_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w") as f:
        json.dump(graph, f, indent=2, ensure_ascii=False)
    return output


def _add_node(nodes, node_id, node_type):
    if node_id not in nodes:
        nodes[node_id] = {
            "id": node_id,
            "label": node_id,
            "type": node_type,
            "event_count": 0,
        }
    nodes[node_id]["event_count"] += 1


def _add_edge(edges, source, target, relation, row):
    key = (source, target, relation)
    if key not in edges:
        edges[key] = {
            "source": source,
            "target": target,
            "relation": relation,
            "weight": 0,
            "conflict_types": {},
            "affected_kpis": {},
            "latest_timestamp": None,
            "latest_reason": "",
        }

    edge = edges[key]
    edge["weight"] += 1
    _counter_increment(edge["conflict_types"], row.get("conflict_type") or "unknown")
    _counter_increment(edge["affected_kpis"], row.get("affected_kpi") or "unknown")
    edge["latest_timestamp"] = row.get("timestamp")
    edge["latest_reason"] = row.get("conflict_reason") or ""


def _counter_increment(counter, key):
    counter[key] = int(counter.get(key, 0)) + 1


def _jsonable_stats(stats):
    return {
        "total_events": stats["total_events"],
        "by_conflict_type": dict(stats["by_conflict_type"]),
        "by_service": dict(stats["by_service"]),
        "by_kpi": dict(stats["by_kpi"]),
        "by_mitigation": dict(stats["by_mitigation"]),
    }


def _safe_float(value, default=None):
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _us_to_ms(value):
    safe = _safe_float(value)
    return safe / 1000.0 if safe is not None else None


def _kbps_to_mbps(value):
    safe = _safe_float(value)
    return safe / 1000.0 if safe is not None else None


def main():
    args = parse_args()
    if args.until_ts and args.until_exclusive_ts:
        raise SystemExit("--until-ts and --until-exclusive-ts are mutually exclusive")

    if args.dataset_input:
        rows = load_dataset_rows(args.dataset_input)
        dataset_path = Path(args.dataset_input)
    else:
        conn = open_db(args.db)
        if args.since_ts and args.until_ts and args.until_ts < args.since_ts:
            raise SystemExit("--until-ts must be greater than or equal to --since-ts")
        if args.since_ts and args.until_exclusive_ts and args.until_exclusive_ts < args.since_ts:
            raise SystemExit("--until-exclusive-ts must be greater than or equal to --since-ts")

        rows = load_rows(
            conn,
            args.hours,
            args.limit,
            since_ts=args.since_ts,
            until_ts=args.until_ts,
            until_exclusive_ts=args.until_exclusive_ts,
        )
        dataset_path = write_dataset(rows, args.dataset)

    graph = build_graph(rows)
    graph_path = write_graph(graph, args.graph)

    print(json.dumps({
        "rows": len(rows),
        "dataset_input": args.dataset_input or None,
        "since_ts": args.since_ts or None,
        "until_ts": args.until_ts or None,
        "until_exclusive_ts": args.until_exclusive_ts or None,
        "dataset": str(dataset_path),
        "graph": str(graph_path),
        "nodes": len(graph["nodes"]),
        "edges": len(graph["edges"]),
        "conflict_types": graph["stats"]["by_conflict_type"],
    }, indent=2))


if __name__ == "__main__":
    main()
