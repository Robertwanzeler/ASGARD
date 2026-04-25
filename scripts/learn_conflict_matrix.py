#!/usr/bin/env python3
"""
Learn an operational conflict matrix from the exported GreenRAN conflict dataset.

This is the bridge between explicit rApp rules and a future graph-learning model:
it derives weighted edges from observed conflict rows and compares them against the
operational graph exported by the current system.
"""

import argparse
import csv
import json
import math
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path


DEFAULT_DATASET_PATH = Path("/tmp/greenran_conflict_dataset.csv")
DEFAULT_GRAPH_PATH = Path("/tmp/greenran_conflict_graph.json")
DEFAULT_ADJ_PATH = Path("/tmp/greenran_conflict_adjacency.json")
DEFAULT_REPORT_PATH = Path("/tmp/greenran_conflict_report.json")
ARBITER = "rApp-ResourceOptimizer"


def parse_args():
    parser = argparse.ArgumentParser(
        description="Learn a weighted conflict matrix from GreenRAN conflict rows."
    )
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET_PATH), help="CSV conflict dataset path")
    parser.add_argument("--graph", default=str(DEFAULT_GRAPH_PATH), help="baseline operational graph JSON path")
    parser.add_argument("--adjacency", default=str(DEFAULT_ADJ_PATH), help="learned adjacency JSON output path")
    parser.add_argument("--report", default=str(DEFAULT_REPORT_PATH), help="comparison report JSON output path")
    parser.add_argument("--min-count", type=int, default=3, help="minimum observations for a strong edge")
    parser.add_argument("--threshold", type=float, default=0.55, help="minimum strength for a strong edge")
    return parser.parse_args()


def load_dataset(path):
    dataset_path = Path(path)
    if not dataset_path.exists():
        raise FileNotFoundError(
            f"dataset not found: {dataset_path}. Run scripts/export_conflict_dataset.py first."
        )

    with dataset_path.open() as f:
        return [row for row in csv.DictReader(f)]


def load_graph(path):
    graph_path = Path(path)
    if not graph_path.exists():
        raise FileNotFoundError(
            f"graph not found: {graph_path}. Run scripts/export_conflict_dataset.py first."
        )

    with graph_path.open() as f:
        return json.load(f)


def build_learned_edges(rows, min_count, threshold):
    relation_stats = defaultdict(
        lambda: defaultdict(lambda: {"count": 0.0, "severity_sum": 0.0})
    )
    relation_meta = defaultdict(
        lambda: defaultdict(
            lambda: {
                "conflict_types": Counter(),
                "contexts": Counter(),
                "latest_reason": "",
            }
        )
    )
    relation_source_totals = defaultdict(Counter)
    relation_target_totals = defaultdict(Counter)

    for row in rows:
        source_agent = _clean(row.get("source_agent"), "unknown_agent")
        parameter = _clean(row.get("parameter"), "unknown_parameter")
        kpi = _clean(row.get("affected_kpi"), "unknown_kpi")
        service = _clean(row.get("affected_service"), "unknown_service")
        mitigation = _clean(row.get("mitigation_action"), "NONE")
        conflict_type = _clean(row.get("conflict_type"), "unknown")
        reason = _clean(row.get("conflict_reason"), "")
        severity = _compute_severity(row)

        _register_edge(
            relation_stats,
            relation_meta,
            relation_source_totals,
            relation_target_totals,
            "controls",
            source_agent,
            parameter,
            severity,
            conflict_type,
            kpi,
            reason,
        )
        _register_edge(
            relation_stats,
            relation_meta,
            relation_source_totals,
            relation_target_totals,
            "affects",
            parameter,
            kpi,
            severity,
            conflict_type,
            service,
            reason,
        )
        _register_edge(
            relation_stats,
            relation_meta,
            relation_source_totals,
            relation_target_totals,
            "belongs_to",
            kpi,
            service,
            0.0,
            conflict_type,
            mitigation,
            reason,
        )
        _register_edge(
            relation_stats,
            relation_meta,
            relation_source_totals,
            relation_target_totals,
            "triggers_arbitration",
            kpi,
            ARBITER,
            severity,
            conflict_type,
            mitigation,
            reason,
        )
        _register_edge(
            relation_stats,
            relation_meta,
            relation_source_totals,
            relation_target_totals,
            "mitigates",
            ARBITER,
            mitigation,
            severity,
            conflict_type,
            service,
            reason,
        )
        _register_edge(
            relation_stats,
            relation_meta,
            relation_source_totals,
            relation_target_totals,
            "protects",
            mitigation,
            service,
            severity,
            conflict_type,
            kpi,
            reason,
        )

    learned_edges = []
    max_count_by_relation = {
        relation: max((int(stats["count"]) for stats in edges.values()), default=1)
        for relation, edges in relation_stats.items()
    }

    for relation, edges in relation_stats.items():
        max_count = max_count_by_relation.get(relation, 1)
        for edge_key, stats in edges.items():
            source, target = edge_key
            count = int(stats["count"])
            avg_severity = stats["severity_sum"] / count if count else 0.0
            normalized_count = count / max_count if max_count else 0.0
            source_total = relation_source_totals[relation][source] or 1
            target_total = relation_target_totals[relation][target] or 1
            source_confidence = count / source_total
            target_confidence = count / target_total
            confidence = max(source_confidence, target_confidence)

            if relation in {"affects", "triggers_arbitration", "mitigates", "protects"}:
                strength = 0.45 * normalized_count + 0.35 * confidence + 0.20 * avg_severity
            else:
                strength = 0.60 * normalized_count + 0.40 * confidence

            edge = {
                "source": source,
                "target": target,
                "relation": relation,
                "count": count,
                "normalized_count": round(normalized_count, 4),
                "confidence": round(confidence, 4),
                "avg_severity": round(avg_severity, 4),
                "strength": round(min(strength, 1.0), 4),
                "classification": (
                    "strong"
                    if count >= min_count and strength >= threshold
                    else "weak"
                ),
                "conflict_types": dict(relation_meta[relation][edge_key]["conflict_types"]),
                "contexts": dict(relation_meta[relation][edge_key]["contexts"]),
                "latest_reason": relation_meta[relation][edge_key]["latest_reason"],
            }
            learned_edges.append(edge)

    learned_edges.sort(key=lambda item: (item["relation"], -item["strength"], -item["count"], item["source"], item["target"]))
    return learned_edges


def compare_to_baseline(learned_edges, baseline_graph):
    baseline_edges = baseline_graph.get("edges", [])
    baseline_map = {
        (edge.get("source"), edge.get("target"), edge.get("relation")): edge
        for edge in baseline_edges
    }
    learned_map = {
        (edge["source"], edge["target"], edge["relation"]): edge
        for edge in learned_edges
    }

    confirmed = []
    weak = []
    spurious = []
    emergent = []

    for edge_key, baseline in baseline_map.items():
        learned = learned_map.get(edge_key)
        if learned is None:
            spurious.append({
                "source": edge_key[0],
                "target": edge_key[1],
                "relation": edge_key[2],
                "reason": "baseline_without_data_support",
                "baseline_weight": baseline.get("weight", 0),
            })
        elif learned["classification"] == "strong":
            confirmed.append(_merge_baseline_learned(baseline, learned))
        else:
            weak.append(_merge_baseline_learned(baseline, learned))

    for edge_key, learned in learned_map.items():
        if learned["classification"] == "strong" and edge_key not in baseline_map:
            emergent.append(learned)

    return {
        "confirmed_by_data": confirmed,
        "weak_or_low_support": weak,
        "spurious_in_baseline": spurious,
        "emergent_from_data": emergent,
        "summary": {
            "baseline_edges": len(baseline_edges),
            "learned_edges": len(learned_edges),
            "confirmed_by_data": len(confirmed),
            "weak_or_low_support": len(weak),
            "spurious_in_baseline": len(spurious),
            "emergent_from_data": len(emergent),
        },
    }


def build_outputs(rows, learned_edges, comparison, args):
    by_relation = Counter(edge["relation"] for edge in learned_edges)
    by_classification = Counter(edge["classification"] for edge in learned_edges)

    adjacency = {
        "schema": "greenran.conflict_learning.v1",
        "generated_at": int(time.time()),
        "config": {
            "dataset": args.dataset,
            "graph": args.graph,
            "min_count": args.min_count,
            "threshold": args.threshold,
            "rows": len(rows),
        },
        "stats": {
            "by_relation": dict(by_relation),
            "by_classification": dict(by_classification),
            "comparison": comparison["summary"],
        },
        "edges": learned_edges,
    }

    report = {
        "schema": "greenran.conflict_learning_report.v1",
        "generated_at": adjacency["generated_at"],
        "config": adjacency["config"],
        "summary": comparison["summary"],
        "top_strong_edges": [edge for edge in learned_edges if edge["classification"] == "strong"][:20],
        "confirmed_by_data": comparison["confirmed_by_data"][:30],
        "weak_or_low_support": comparison["weak_or_low_support"][:30],
        "spurious_in_baseline": comparison["spurious_in_baseline"][:30],
        "emergent_from_data": comparison["emergent_from_data"][:30],
    }

    return adjacency, report


def write_json(path, payload):
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    return output


def _register_edge(
    relation_stats,
    relation_meta,
    relation_source_totals,
    relation_target_totals,
    relation,
    source,
    target,
    severity,
    conflict_type,
    context,
    reason,
):
    key = (source, target)
    relation_stats[relation][key]["count"] += 1
    relation_stats[relation][key]["severity_sum"] += severity
    relation_source_totals[relation][source] += 1
    relation_target_totals[relation][target] += 1
    relation_meta[relation][key]["conflict_types"][conflict_type] += 1
    relation_meta[relation][key]["contexts"][context] += 1
    relation_meta[relation][key]["latest_reason"] = reason


def _compute_severity(row):
    observed = _safe_float(row.get("observed_value"))
    threshold = _safe_float(row.get("threshold_value"))
    delta = _safe_float(row.get("observed_delta_from_threshold"))

    if observed is None or threshold is None:
        return 0.0

    if delta is None:
        delta = observed - threshold

    scale = max(abs(threshold), 1.0)
    normalized = min(abs(delta) / scale, 1.0)
    return round(normalized, 4)


def _merge_baseline_learned(baseline, learned):
    return {
        "source": learned["source"],
        "target": learned["target"],
        "relation": learned["relation"],
        "baseline_weight": baseline.get("weight", 0),
        "learned_count": learned["count"],
        "learned_strength": learned["strength"],
        "learned_confidence": learned["confidence"],
        "classification": learned["classification"],
        "latest_reason": learned["latest_reason"],
    }


def _clean(value, default):
    text = (value or "").strip()
    return text if text else default


def _safe_float(value):
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def main():
    args = parse_args()
    rows = load_dataset(args.dataset)
    baseline_graph = load_graph(args.graph)
    learned_edges = build_learned_edges(rows, args.min_count, args.threshold)
    comparison = compare_to_baseline(learned_edges, baseline_graph)
    adjacency, report = build_outputs(rows, learned_edges, comparison, args)

    adjacency_path = write_json(args.adjacency, adjacency)
    report_path = write_json(args.report, report)

    print(json.dumps({
        "rows": len(rows),
        "adjacency": str(adjacency_path),
        "report": str(report_path),
        "strong_edges": adjacency["stats"]["by_classification"].get("strong", 0),
        "weak_edges": adjacency["stats"]["by_classification"].get("weak", 0),
        "comparison": comparison["summary"],
    }, indent=2))


if __name__ == "__main__":
    main()
