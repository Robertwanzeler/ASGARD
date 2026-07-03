#!/usr/bin/env python3
"""Export an article-aligned TA-SAM MARL dataset from the GreenRAN Data Lake.

The exporter is intentionally read-only. ARMD/GraphSAGE signals are copied as
context features when present, but this script does not modify ARMD artifacts or
runtime state.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from greenran_paths import RAPP_DB_PATH  # noqa: E402

SLICE_ORDER = ("eMBB", "mMTC", "URLLC")
EXTENDED_METRICS_PREFERRED_COLUMNS = (
    "sim_time_s",
    "throughput_kbps",
    "total_tx_bytes",
    "total_rx_bytes",
    "global_avg_latency_us",
    "latency_p95_us",
    "latency_p95_per_ue_us",
    "variance_per_ue_us2",
    "cvar_per_ue_us",
    "total_active_ues",
    "total_active_cameras",
    "total_critical_ues",
    "global_packet_loss_rate",
    "collector_mode",
    "throughput_source",
    "real_latency_sample_count",
    "proxy_latency_sample_count",
    "pdcp_stale",
    "rlc_stale",
    "mac_stale",
    "pdcp_trace_age_s",
    "rlc_trace_age_s",
    "mac_trace_age_s",
    "pdcp_latest_sim_time_s",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export final TA-SAM MARL transitions from GreenRAN collection DB"
    )
    parser.add_argument("--db", default=str(RAPP_DB_PATH), help="SQLite Data Lake path")
    parser.add_argument("--output-jsonl", required=True, help="Output transition JSONL")
    parser.add_argument("--summary-json", help="Optional summary JSON path")
    parser.add_argument("--limit", type=int, default=0, help="Maximum transitions to export")
    parser.add_argument("--since-ts", type=int, default=0, help="Only include samples at/after this timestamp")
    parser.add_argument("--until-ts", type=int, default=0, help="Only include samples at/before this timestamp")
    parser.add_argument(
        "--allow-proxy",
        action="store_true",
        help="Do not filter samples whose current metric snapshot contains proxy latency",
    )
    parser.add_argument(
        "--include-invalid",
        action="store_true",
        help="Write transitions even when collection_quality.valid_for_training is false",
    )
    parser.add_argument(
        "--max-step-gap-s",
        type=int,
        default=20,
        help="Maximum wall-clock timestamp gap allowed for s->s' pairing",
    )
    parser.add_argument(
        "--max-sim-reset-gap-s",
        type=float,
        default=1.0,
        help="If next sim_time drops by more than this value, mark transition as sim reset",
    )
    return parser


def table_exists(cursor: sqlite3.Cursor, name: str) -> bool:
    return cursor.execute(
        "select name from sqlite_master where type='table' and name=?", (name,)
    ).fetchone() is not None


def load_json(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except Exception:
        return default


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(out) or math.isinf(out):
        return default
    return out


def clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, safe_float(value, lower)))


def sigmoid_qos(value: float, center: float, scale: float) -> float:
    scale = max(scale, 1e-9)
    z = (safe_float(value) - center) / scale
    return 1.0 / (1.0 + math.exp(-z))


def fetch_one(cursor: sqlite3.Cursor, query: str, params: tuple[Any, ...]) -> sqlite3.Row | None:
    return cursor.execute(query, params).fetchone()


def fetch_slice_state(cursor: sqlite3.Cursor, timestamp: int) -> dict[str, dict[str, Any]]:
    rows = cursor.execute(
        """
        select slice_id, ue_count, demand, allocation, qos_pressure, completion_ratio,
               min_qos_met, budget_share, snapshot_json
        from marl_slice_state_history
        where timestamp=?
        order by slice_id
        """,
        (timestamp,),
    ).fetchall()
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        payload = load_json(row["snapshot_json"], {})
        payload.update(
            {
                "slice_id": row["slice_id"],
                "ue_count": row["ue_count"],
                "demand": safe_float(row["demand"]),
                "allocation": safe_float(row["allocation"]),
                "qos_pressure": safe_float(row["qos_pressure"]),
                "completion_ratio": safe_float(row["completion_ratio"]),
                "min_qos_met": safe_float(row["min_qos_met"]),
                "budget_share": safe_float(row["budget_share"]),
            }
        )
        out[str(row["slice_id"])] = payload
    return out


def fetch_du_states(cursor: sqlite3.Cursor, timestamp: int) -> list[dict[str, Any]]:
    rows = cursor.execute(
        """
        select du_id, role, primary_slice, ue_count, demand_share, allocation_share,
               slice_mix_json, state_vector_json, snapshot_json
        from marl_du_state_history
        where timestamp=?
        order by du_id
        """,
        (timestamp,),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        payload = load_json(row["snapshot_json"], {})
        payload.update(
            {
                "du_id": row["du_id"],
                "role": row["role"],
                "primary_slice": row["primary_slice"],
                "ue_count": row["ue_count"],
                "demand_share": safe_float(row["demand_share"]),
                "allocation_share": safe_float(row["allocation_share"]),
                "slice_mix": load_json(row["slice_mix_json"], {}),
                "state_vector": [safe_float(v) for v in load_json(row["state_vector_json"], [])],
            }
        )
        out.append(payload)
    return out


def fetch_global_state(cursor: sqlite3.Cursor, timestamp: int) -> dict[str, Any] | None:
    row = fetch_one(
        cursor,
        """
        select timestamp, datetime, topology_id, logical_du_count, total_demand,
               usable_budget, state_vector_json, snapshot_json
        from marl_global_state_history
        where timestamp=?
        """,
        (timestamp,),
    )
    if not row:
        return None
    return {
        "timestamp": row["timestamp"],
        "datetime": row["datetime"],
        "topology_id": row["topology_id"],
        "logical_du_count": row["logical_du_count"],
        "total_demand": safe_float(row["total_demand"]),
        "usable_budget": safe_float(row["usable_budget"], 1.0),
        "state_vector": [safe_float(v) for v in load_json(row["state_vector_json"], [])],
        "snapshot": load_json(row["snapshot_json"], {}),
    }


def extended_metrics_available_columns(cursor: sqlite3.Cursor) -> list[str]:
    rows = cursor.execute("PRAGMA table_info(extended_metrics)").fetchall()
    return [row[1] for row in rows]


def fetch_metrics(cursor: sqlite3.Cursor, timestamp: int) -> dict[str, Any]:
    available_columns = set(extended_metrics_available_columns(cursor))
    selected_columns = [
        column_name
        for column_name in EXTENDED_METRICS_PREFERRED_COLUMNS
        if column_name in available_columns
    ]
    if not selected_columns:
        return {}
    row = fetch_one(
        cursor,
        f"""
        select {", ".join(selected_columns)}
        from extended_metrics
        where timestamp=?
        """,
        (timestamp,),
    )
    if not row:
        return {}
    return {key: row[key] for key in row.keys()}


def fetch_decision(cursor: sqlite3.Cursor, timestamp: int) -> dict[str, Any]:
    row = fetch_one(cursor, "select * from decisions_history where timestamp=?", (timestamp,))
    return dict(row) if row else {}


def fetch_resource_action(cursor: sqlite3.Cursor, timestamp: int) -> dict[str, Any]:
    row = fetch_one(cursor, "select * from resource_allocation_history where timestamp=?", (timestamp,))
    if not row:
        return {}
    out = dict(row)
    out["snapshot"] = load_json(out.get("snapshot_json"), {})
    out.pop("snapshot_json", None)
    return out


def fetch_shadow(cursor: sqlite3.Cursor, timestamp: int) -> dict[str, Any]:
    row = fetch_one(
        cursor,
        "select * from marl_shadow_comparison_history where timestamp=? order by rowid desc limit 1",
        (timestamp,),
    )
    if not row:
        return {}
    out = dict(row)
    out["snapshot"] = load_json(out.get("snapshot_json"), {})
    out.pop("snapshot_json", None)
    return out


def scenario_stage_from_shadow(
    shadow: dict[str, Any],
    decision: dict[str, Any],
    conflict: dict[str, Any],
    metrics: dict[str, Any],
) -> str:
    snapshot = shadow.get("snapshot") if isinstance(shadow, dict) else {}
    if isinstance(snapshot, dict):
        marl_shadow = snapshot.get("marl_shadow") or {}
        if isinstance(marl_shadow, dict):
            stage = str(marl_shadow.get("scenario_stage") or "").strip()
            if stage:
                return stage
    return stage_label(decision, conflict, metrics)


def fetch_conflict_context(cursor: sqlite3.Cursor, timestamp: int, window_s: int = 300) -> dict[str, Any]:
    if not table_exists(cursor, "conflict_events"):
        return {"recent_count": 0, "latest": None}
    rows = cursor.execute(
        """
        select timestamp, datetime, source_agent, target_agent, conflict_type,
               affected_service, affected_kpi, confidence
        from conflict_events
        where timestamp between ? and ?
        order by timestamp desc
        limit 20
        """,
        (int(timestamp) - int(window_s), int(timestamp)),
    ).fetchall()
    latest = dict(rows[0]) if rows else None
    by_type: dict[str, int] = {}
    for row in rows:
        key = str(row["conflict_type"] or "unknown")
        by_type[key] = by_type.get(key, 0) + 1
    return {"recent_count": len(rows), "by_type": by_type, "latest": latest}


def collection_quality(metrics: dict[str, Any], current: dict[str, Any], nxt: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    proxy_count = safe_float(metrics.get("proxy_latency_sample_count"), 0.0)
    real_count = safe_float(metrics.get("real_latency_sample_count"), 0.0)
    collector_mode = str(metrics.get("collector_mode", current.get("collector_mode", "")) or "").lower()
    sim_now = safe_float(current.get("metrics", {}).get("sim_time_s"), 0.0)
    sim_next = safe_float(nxt.get("metrics", {}).get("sim_time_s"), sim_now)
    ts_gap = int(nxt["timestamp"]) - int(current["timestamp"])
    sim_reset = sim_next + args.max_sim_reset_gap_s < sim_now
    suspected_stale_proxy = (
        collector_mode == ""
        and proxy_count <= 0.0
        and real_count <= 0.0
        and safe_float(metrics.get("throughput_kbps"), 0.0) <= 0.0
        and safe_float(metrics.get("total_tx_bytes"), 0.0) <= 0.0
        and safe_float(metrics.get("total_rx_bytes"), 0.0) <= 0.0
        and sim_now <= 1.0
        and safe_float(metrics.get("cvar_per_ue_us"), 0.0) > 0.0
        and safe_float(metrics.get("variance_per_ue_us2"), 0.0) <= 0.0
    )
    if suspected_stale_proxy:
        collector_mode = "suspected_stale_proxy"
        proxy_count = max(proxy_count, 1.0)
    pdcp_real = collector_mode == "pdcp_real"
    return {
        "collector_mode": collector_mode,
        "real_latency_sample_count": real_count,
        "proxy_latency_sample_count": proxy_count,
        "pdcp_real": pdcp_real,
        "has_proxy": proxy_count > 0 or suspected_stale_proxy,
        "timestamp_gap_s": ts_gap,
        "sim_reset": sim_reset,
        "valid_for_training": (
            (args.allow_proxy or proxy_count <= 0)
            and pdcp_real
            and ts_gap <= args.max_step_gap_s
            and not sim_reset
            and not suspected_stale_proxy
        ),
    }


def compute_reward(record: dict[str, Any]) -> dict[str, Any]:
    slices = record["slice_state"]
    metrics = record["metrics"]
    conflict = record["conflict_context"]

    embb = slices.get("eMBB", {})
    mmtc = slices.get("mMTC", {})
    urllc = slices.get("URLLC", {})

    embb_qos = 0.55 * clamp(embb.get("completion_ratio")) + 0.45 * (1.0 - clamp(embb.get("qos_pressure")))
    mmtc_qos = 0.55 * clamp(mmtc.get("completion_ratio")) + 0.45 * (1.0 - clamp(mmtc.get("qos_pressure")))
    urllc_latency_ms = safe_float(metrics.get("cvar_per_ue_us")) / 1000.0
    urllc_latency_score = 1.0 - sigmoid_qos(urllc_latency_ms, center=80.0, scale=20.0)
    urllc_qos = 0.45 * clamp(urllc.get("completion_ratio")) + 0.35 * (1.0 - clamp(urllc.get("qos_pressure"))) + 0.20 * urllc_latency_score

    qos_score = (0.40 * embb_qos) + (0.25 * mmtc_qos) + (0.35 * urllc_qos)

    resource_action = record.get("action", {})
    usable = max(safe_float(resource_action.get("usable_budget"), 1.0), 1e-9)
    alloc_sum = safe_float(resource_action.get("r_ran")) + safe_float(resource_action.get("r_ai"))
    demand_sum = safe_float(resource_action.get("d_ran")) + safe_float(resource_action.get("d_ai"))
    over_alloc_penalty = max(0.0, (alloc_sum - usable) / usable)
    shortage_penalty = max(0.0, (demand_sum - alloc_sum) / max(demand_sum, 1e-9))
    min_qos_penalty = sum(1.0 - clamp((slices.get(sid, {}) or {}).get("min_qos_met")) for sid in SLICE_ORDER) / len(SLICE_ORDER)
    conflict_penalty = min(0.25, 0.025 * safe_float(conflict.get("recent_count"), 0.0))
    loss_penalty = min(0.20, safe_float(metrics.get("global_packet_loss_rate"), 0.0) * 20.0)

    reward = qos_score - (0.25 * over_alloc_penalty) - (0.20 * shortage_penalty) - (0.30 * min_qos_penalty) - conflict_penalty - loss_penalty
    return {
        "reward": round(float(reward), 6),
        "components": {
            "qos_score": round(qos_score, 6),
            "embb_qos": round(embb_qos, 6),
            "mmtc_qos": round(mmtc_qos, 6),
            "urllc_qos": round(urllc_qos, 6),
            "over_alloc_penalty": round(over_alloc_penalty, 6),
            "shortage_penalty": round(shortage_penalty, 6),
            "min_qos_penalty": round(min_qos_penalty, 6),
            "conflict_penalty": round(conflict_penalty, 6),
            "loss_penalty": round(loss_penalty, 6),
        },
    }


def stage_label(decision: dict[str, Any], conflict: dict[str, Any], metrics: dict[str, Any]) -> str:
    reason = str(decision.get("reason") or "").lower()
    if conflict.get("recent_count", 0) > 0:
        return "conflict_context"
    if "blocked" in str(decision.get("decision", "")).lower():
        return "blocked_pressure"
    if "conditional" in str(decision.get("decision", "")).lower():
        return "guarded_pressure"
    if safe_float(metrics.get("cvar_per_ue_us")) / 1000.0 >= 80.0:
        return "latency_pressure"
    return "baseline_healthy"


def build_snapshot(cursor: sqlite3.Cursor, timestamp: int) -> dict[str, Any] | None:
    global_state = fetch_global_state(cursor, timestamp)
    if not global_state:
        return None
    du_states = fetch_du_states(cursor, timestamp)
    slice_state = fetch_slice_state(cursor, timestamp)
    if not du_states or len(slice_state) < 3:
        return None
    metrics = fetch_metrics(cursor, timestamp)
    decision = fetch_decision(cursor, timestamp)
    action = fetch_resource_action(cursor, timestamp)
    shadow = fetch_shadow(cursor, timestamp)
    conflict = fetch_conflict_context(cursor, timestamp)
    armd_context = {
        "enabled": bool(decision.get("armd_enabled", 0)),
        "mode": decision.get("armd_mode"),
        "scenario": decision.get("armd_scenario"),
        "source": decision.get("armd_source"),
        "confidence": safe_float(decision.get("armd_confidence")),
        "override_applied": bool(decision.get("armd_override_applied", 0)),
    }
    return {
        "timestamp": timestamp,
        "datetime": global_state.get("datetime"),
        "topology_id": global_state.get("topology_id"),
        "global_state": global_state,
        "slice_state": slice_state,
        "du_states": du_states,
        "metrics": metrics,
        "decision": decision,
        "action": action,
        "shadow_comparison": shadow,
        "conflict_context": conflict,
        "armd_context": armd_context,
        "scenario_stage": scenario_stage_from_shadow(shadow, decision, conflict, metrics),
    }


def transition_record(current: dict[str, Any], nxt: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    reward = compute_reward(current)
    quality = collection_quality(current.get("metrics", {}), current, nxt, args)
    return {
        "schema": "greenran.tasam_article_transition.v1",
        "timestamp": current["timestamp"],
        "datetime": current.get("datetime"),
        "next_timestamp": nxt["timestamp"],
        "topology_id": current.get("topology_id"),
        "scenario_stage": current.get("scenario_stage"),
        "global_state": current["global_state"],
        "slice_state": current["slice_state"],
        "du_states": current["du_states"],
        "action": current["action"],
        "decision": current["decision"],
        "metrics": current["metrics"],
        "armd_context": current["armd_context"],
        "conflict_context": current["conflict_context"],
        "shadow_comparison": current["shadow_comparison"],
        "reward_hint": reward["reward"],
        "reward_components": reward["components"],
        "next_global_state": nxt["global_state"],
        "next_slice_state": nxt["slice_state"],
        "next_du_states": nxt["du_states"],
        "next_metrics": nxt["metrics"],
        "collection_quality": quality,
    }


def timestamps(cursor: sqlite3.Cursor, args: argparse.Namespace) -> list[int]:
    clauses = ["1=1"]
    params: list[Any] = []
    if args.since_ts:
        clauses.append("timestamp >= ?")
        params.append(args.since_ts)
    if args.until_ts:
        clauses.append("timestamp <= ?")
        params.append(args.until_ts)
    query = f"""
        select timestamp
        from marl_global_state_history
        where {' and '.join(clauses)}
        order by timestamp asc
    """
    return [int(row[0]) for row in cursor.execute(query, tuple(params)).fetchall()]


def main() -> int:
    args = build_parser().parse_args()
    db_path = Path(args.db)
    out_path = Path(args.output_jsonl)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    required = [
        "marl_global_state_history",
        "marl_slice_state_history",
        "marl_du_state_history",
        "resource_allocation_history",
        "decisions_history",
        "extended_metrics",
    ]
    missing = [name for name in required if not table_exists(cursor, name)]
    if missing:
        raise SystemExit(f"missing required tables: {', '.join(missing)}")

    ts_values = timestamps(cursor, args)
    snapshots: list[dict[str, Any]] = []
    for ts in ts_values:
        snap = build_snapshot(cursor, ts)
        if snap:
            snapshots.append(snap)

    written = 0
    skipped_invalid = 0
    written_invalid = 0
    stage_counts: dict[str, int] = {}
    decision_counts: dict[str, int] = {}
    with out_path.open("w", encoding="utf-8") as fh:
        for current, nxt in zip(snapshots, snapshots[1:]):
            record = transition_record(current, nxt, args)
            is_valid = bool(record["collection_quality"]["valid_for_training"])
            if not is_valid and not args.include_invalid:
                skipped_invalid += 1
                continue
            if not is_valid:
                written_invalid += 1
            stage = str(record.get("scenario_stage") or "unknown")
            decision = str((record.get("decision") or {}).get("decision") or "unknown")
            stage_counts[stage] = stage_counts.get(stage, 0) + 1
            decision_counts[decision] = decision_counts.get(decision, 0) + 1
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            written += 1
            if args.limit and written >= args.limit:
                break

    summary = {
        "schema": "greenran.tasam_article_dataset_summary.v1",
        "db": str(db_path),
        "output_jsonl": str(out_path),
        "candidate_snapshots": len(snapshots),
        "written_transitions": written,
        "skipped_invalid_transitions": skipped_invalid,
        "written_invalid_transitions": written_invalid,
        "stage_counts": stage_counts,
        "decision_counts": decision_counts,
        "armd_policy": "read_only_context_no_runtime_mutation",
        "include_invalid": bool(args.include_invalid),
    }
    if args.summary_json:
        summary_path = Path(args.summary_json)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
