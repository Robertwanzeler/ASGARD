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
from rapp_judge import RAppJudge, expected_verdict_for_stage  # noqa: E402

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
# The real metrics publisher is independent from the rApp and currently emits
# snapshots every 5-6 seconds, with occasional 11-12 second gaps. A nearest
# match can therefore be at most 6 seconds away without accepting a stale
# snapshot from a wider window.
METRICS_TIMESTAMP_TOLERANCE_S = 6
# The Judge callback can persist its outcome on the following real
# observation, which is 5-6 seconds after the rApp decision in this runtime.
# Keep the join bounded and audit the matched timestamp below.
JUDGE_TIMESTAMP_TOLERANCE_S = 6


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
    parser.add_argument(
        "--allocation-total-head-enabled",
        action="store_true",
        help=(
            "Emit the economic three-output allocation-target contract. "
            "Only the online-economic controller sets this; historical "
            "exports retain the legacy two-output contract."
        ),
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


def fetch_metrics(cursor: sqlite3.Cursor, timestamp: int) -> tuple[dict[str, Any], dict[str, Any]]:
    available_columns = set(extended_metrics_available_columns(cursor))
    selected_columns = [
        column_name
        for column_name in EXTENDED_METRICS_PREFERRED_COLUMNS
        if column_name in available_columns
    ]
    if not selected_columns:
        return {}, {}
    selected_sql = ", ".join(selected_columns)
    row = fetch_one(
        cursor,
        f"""
        select timestamp as _source_timestamp, {selected_sql}
        from extended_metrics
        where timestamp=?
        """,
        (timestamp,),
    )
    if not row:
        # The collector and rApp publish on independent cadences. Recover a
        # nearby metric snapshot instead of dropping an otherwise valid
        # transition, while bounding the match tightly enough to avoid
        # crossing scenario stages.
        row = fetch_one(
            cursor,
            f"""
            select timestamp as _source_timestamp, {selected_sql}
            from extended_metrics
            where timestamp between ? and ?
            order by abs(timestamp - ?) asc
            limit 1
            """,
            (
                int(timestamp) - METRICS_TIMESTAMP_TOLERANCE_S,
                int(timestamp) + METRICS_TIMESTAMP_TOLERANCE_S,
                int(timestamp),
            ),
        )
    if not row:
        return {}, {}
    source_timestamp = int(row["_source_timestamp"])
    alignment = {
        "requested_timestamp": int(timestamp),
        "source_timestamp": source_timestamp,
        "skew_s": abs(source_timestamp - int(timestamp)),
        "within_tolerance": abs(source_timestamp - int(timestamp)) <= METRICS_TIMESTAMP_TOLERANCE_S,
    }
    return (
        {key: row[key] for key in selected_columns},
        alignment,
    )


def fetch_decision(cursor: sqlite3.Cursor, timestamp: int) -> dict[str, Any]:
    row = fetch_one(cursor, "select * from decisions_history where timestamp=?", (timestamp,))
    decision = dict(row) if row else {}
    if decision:
        decision["economic_action"] = load_json(
            decision.get("economic_action_json"), {}
        )
        decision["pdcp_loss_coverage"] = load_json(
            decision.get("pdcp_coverage_json"), {}
        )
    return decision


def fetch_judge_outcome(
    cursor: sqlite3.Cursor,
    timestamp: int,
    decision_id: int | None = None,
) -> dict[str, Any]:
    """Load delayed judge feedback associated with one decision.

    The rApp persists the decision first and scores it on the next real
    observation.  Keeping this join in the exporter lets the DRL trace use
    the actual ARMD/TA-SAM credit without changing the runtime database.
    """
    if not table_exists(cursor, "judge_outcome_history"):
        return {}
    columns = {
        str(row[1])
        for row in cursor.execute("PRAGMA table_info(judge_outcome_history)").fetchall()
    }
    row = None
    if decision_id is not None and "decision_id" in columns:
        row = fetch_one(
            cursor,
            "select * from judge_outcome_history where decision_id=? order by id desc limit 1",
            (int(decision_id),),
        )
    match_method = "decision_id" if row else "timestamp"
    if not row:
        order_clause = " order by id desc" if "id" in columns else ""
        row = fetch_one(
            cursor,
            f"select * from judge_outcome_history where decision_timestamp=?{order_clause} limit 1",
            (timestamp,),
        )
    if not row:
        # Decision and Judge timestamps are produced by independent runtime
        # callbacks. A bounded nearest match recovers the delayed outcome
        # without accepting a feedback record farther than one rApp cadence.
        row = fetch_one(
            cursor,
            """
            select * from judge_outcome_history
            where decision_timestamp between ? and ?
            order by abs(decision_timestamp - ?) asc
            limit 1
            """,
            (
                int(timestamp) - JUDGE_TIMESTAMP_TOLERANCE_S,
                int(timestamp) + JUDGE_TIMESTAMP_TOLERANCE_S,
                int(timestamp),
            ),
        )
    if not row:
        return {}
    outcome = dict(row)
    outcome["judge_alignment"] = {
        "requested_timestamp": int(timestamp),
        "source_timestamp": int(row["decision_timestamp"]),
        "skew_s": abs(int(row["decision_timestamp"]) - int(timestamp)),
        "within_tolerance": abs(int(row["decision_timestamp"]) - int(timestamp)) <= JUDGE_TIMESTAMP_TOLERANCE_S,
        "match_method": match_method,
        "requested_decision_id": int(decision_id) if decision_id is not None else None,
        "source_decision_id": (
            int(row["decision_id"])
            if "decision_id" in row.keys() and row["decision_id"] is not None
            else None
        ),
    }
    payload = load_json(outcome.get("feedback_json"), {})
    outcome["feedback"] = payload.get("feedback", {}) if isinstance(payload, dict) else {}
    outcome["observation"] = payload.get("observation", {}) if isinstance(payload, dict) else {}
    feedback = outcome["feedback"]
    observation = outcome["observation"]
    # Repair feedback persisted by runtimes predating the nested TA-SAM state
    # normalization in RAppJudge.  The old record contains both complete
    # proposals and the observed outcome, so this is a deterministic,
    # read-only migration performed at export time; the SQLite database is
    # intentionally left untouched.
    if (
        isinstance(feedback, dict)
        and isinstance(observation, dict)
        and feedback.get("outcome_observed")
        and isinstance(feedback.get("armd_proposal"), dict)
        and isinstance(feedback.get("tasam_proposal"), dict)
    ):
        judge_result = {
            "armd_proposal": feedback.get("armd_proposal"),
            "tasam_proposal": feedback.get("tasam_proposal"),
            "selected_proposal": feedback.get("selected_proposal") or {},
        }
        repaired = RAppJudge({"enabled": True, "production": True}).evaluate_outcome(
            judge_result,
            observation,
        )
        for key in (
            "correct_verdict",
            "severity_penalty",
            "outcome_reward",
            "armd_credit",
            "tasam_credit",
            "armd_state_credit",
            "tasam_state_credit",
            "tasam_resource_credit",
            "tasam_category_credit",
            "tasam_category_penalty",
            "tasam_category_error",
            "tasam_training_category_credit",
            "tasam_training_category_penalty",
            "tasam_training_reward",
            "tasam_predicted_verdict",
            "tasam_observed_verdict",
            "proposal_errors",
            "proposal_penalties",
            "credit_assignment",
            "resource_reward",
            "outcome_observed",
            "feedback_status",
        ):
            if key in repaired:
                feedback[key] = repaired[key]
        feedback["feedback_repaired_at_export"] = True
    return outcome


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
    authoritative_stage = str(decision.get("collection_event_stage_name") or "").strip()
    if authoritative_stage and bool(decision.get("collection_event_stage_authoritative", 0)):
        return authoritative_stage
    controlled_stage = controlled_stage_label(decision)
    if controlled_stage:
        return controlled_stage
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
    max_metric_skew_s = int(getattr(args, "max_metric_skew_s", METRICS_TIMESTAMP_TOLERANCE_S))
    current_alignment = current.get("metrics_alignment") or {}
    next_alignment = nxt.get("metrics_alignment") or {}
    # Keep hand-built/unit snapshots backward compatible. Live exports always
    # carry explicit alignment metadata from fetch_metrics().
    if metrics and not current_alignment:
        current_alignment = {
            "source_timestamp": current.get("timestamp"),
            "skew_s": 0.0,
        }
    if nxt.get("metrics") and not next_alignment:
        next_alignment = {
            "source_timestamp": nxt.get("timestamp"),
            "skew_s": 0.0,
        }
    current_skew_s = safe_float(current_alignment.get("skew_s"), 0.0)
    next_skew_s = safe_float(next_alignment.get("skew_s"), 0.0)
    metric_alignment_valid = bool(
        metrics
        and nxt.get("metrics")
        and current_alignment.get("source_timestamp") is not None
        and next_alignment.get("source_timestamp") is not None
        and current_skew_s <= max_metric_skew_s
        and next_skew_s <= max_metric_skew_s
    )
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
        "metric_alignment_valid": metric_alignment_valid,
        "current_metric_timestamp": current_alignment.get("source_timestamp"),
        "current_metric_skew_s": current_skew_s,
        "next_metric_timestamp": next_alignment.get("source_timestamp"),
        "next_metric_skew_s": next_skew_s,
        "sim_reset": sim_reset,
        "valid_for_training": (
            (args.allow_proxy or proxy_count <= 0)
            and (pdcp_real or args.allow_proxy)
            and ts_gap <= args.max_step_gap_s
            and metric_alignment_valid
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
    utilization_ratio = alloc_sum / usable
    resource_use_penalty = clamp((utilization_ratio - 0.80) / 0.20)
    over_alloc_penalty = max(0.0, (alloc_sum - usable) / usable)
    shortage_penalty = max(0.0, (demand_sum - alloc_sum) / max(demand_sum, 1e-9))
    min_qos_penalty = sum(1.0 - clamp((slices.get(sid, {}) or {}).get("min_qos_met")) for sid in SLICE_ORDER) / len(SLICE_ORDER)
    conflict_penalty = min(0.25, 0.025 * safe_float(conflict.get("recent_count"), 0.0))
    loss_penalty = min(0.20, safe_float(metrics.get("global_packet_loss_rate"), 0.0) * 20.0)

    ran_completion = clamp(embb.get("completion_ratio"))
    ai_demand = safe_float(mmtc.get("demand")) + safe_float(urllc.get("demand"))
    ai_completion = (
        (safe_float(mmtc.get("completion_ratio")) * safe_float(mmtc.get("demand"))
         + safe_float(urllc.get("completion_ratio")) * safe_float(urllc.get("demand"))) / ai_demand
        if ai_demand > 1e-9 else 1.0
    )
    ran_target, ai_target = 0.95, 0.75
    completion_shortfall_penalty = min(
        1.0,
        (0.60 * max(0.0, ran_target - ran_completion) / ran_target)
        + (0.40 * max(0.0, ai_target - ai_completion) / ai_target),
    )
    underallocation_penalty = min(1.0, max(shortage_penalty, completion_shortfall_penalty))
    excess_allocation_penalty = min(1.0, max(over_alloc_penalty, resource_use_penalty))
    allocation_state = str(resource_action.get("allocation_state", "") or "").upper()
    power_raw = resource_action.get("power_percent", record.get("tasam_power_percent", 100.0))
    try:
        power_percent = max(25.0, min(100.0, float(power_raw)))
    except (TypeError, ValueError):
        power_percent = {"POWER_DOWN_ECO": 25.0, "REDUCE_POWER": 60.0, "CONDITIONAL_REDUCE": 60.0, "FULL_POWER": 100.0}.get(str(power_raw).upper(), 100.0)
    service_safe = (
        allocation_state != "BLOCKED"
        and ran_completion >= ran_target
        and ai_completion >= ai_target
        and urllc_latency_ms <= 120.0
        and safe_float(metrics.get("global_packet_loss_rate")) <= 0.01
    )
    power_cost_penalty = ((power_percent - 25.0) / 75.0) if service_safe else 0.0

    normalized_loss = clamp(loss_penalty / 0.20)
    continuous_error = (
        (0.25 * clamp(1.0 - qos_score))
        + (0.10 * clamp(min(1.0, safe_float(metrics.get("cvar_per_ue_us")) / 1000.0 / 120.0 - 1.0)))
        + (0.05 * normalized_loss)
        + (0.35 * completion_shortfall_penalty)
        + (0.15 * underallocation_penalty)
        + (0.05 * excess_allocation_penalty)
        + (0.05 * power_cost_penalty)
    )
    reward = max(-1.0, min(1.0, float(1.0 - (2.0 * clamp(continuous_error)))))
    return {
        "reward": round(float(reward), 6),
        "components": {
            "qos_score": round(qos_score, 6),
            "embb_qos": round(embb_qos, 6),
            "mmtc_qos": round(mmtc_qos, 6),
            "urllc_qos": round(urllc_qos, 6),
            "over_alloc_penalty": round(over_alloc_penalty, 6),
            "resource_use_penalty": round(resource_use_penalty, 6),
            "excess_allocation_penalty": round(excess_allocation_penalty, 6),
            "shortage_penalty": round(shortage_penalty, 6),
            "min_qos_penalty": round(min_qos_penalty, 6),
            "conflict_penalty": round(conflict_penalty, 6),
            "loss_penalty": round(loss_penalty, 6),
            "ran_completion": round(ran_completion, 6),
            "ai_completion": round(ai_completion, 6),
            "ran_completion_target": ran_target,
            "ai_completion_target": ai_target,
            "completion_shortfall_penalty": round(completion_shortfall_penalty, 6),
            "underallocation_penalty": round(underallocation_penalty, 6),
            "power_percent": round(power_percent, 6),
            "power_cost_penalty": round(power_cost_penalty, 6),
            "power_penalty_gated_by_service": int(service_safe),
        },
    }


def controlled_stage_label(decision: dict[str, Any]) -> str:
    """Recover the balanced collection stage from a controlled decision."""
    if str(decision.get("improvement_source") or "").strip().lower() != "scenario_control_override":
        return ""
    decision_name = str(decision.get("decision") or "").strip().upper()
    reason = " ".join(
        str(decision.get(key) or "").strip().lower()
        for key in ("reason", "priority_violation", "armd_scenario")
    )
    if "vehicle" in reason:
        domain = "vehicle"
    elif "app2" in reason or "mtc" in reason or "mmtc" in reason:
        domain = "app2"
    else:
        domain = "camera"
    return {
        "ALLOWED": "allowed_stable",
        "CONDITIONAL": f"{domain}_conditional",
        "BLOCKED": f"{domain}_blocked",
    }.get(decision_name, "")


def stage_label(decision: dict[str, Any], conflict: dict[str, Any], metrics: dict[str, Any]) -> str:
    controlled_stage = controlled_stage_label(decision)
    if controlled_stage:
        return controlled_stage
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
    metrics, metrics_alignment = fetch_metrics(cursor, timestamp)
    decision = fetch_decision(cursor, timestamp)
    judge_outcome = fetch_judge_outcome(
        cursor,
        timestamp,
        decision_id=decision.get("id"),
    )
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
        "metrics_alignment": metrics_alignment,
        "decision": decision,
        "judge_outcome": judge_outcome,
        "action": action,
        "shadow_comparison": shadow,
        "conflict_context": conflict,
        "armd_context": armd_context,
        "scenario_stage": scenario_stage_from_shadow(shadow, decision, conflict, metrics),
    }


def build_temporal_context(current: dict[str, Any], nxt: dict[str, Any]) -> list[float]:
    """Build internal temporal features while preserving the external 13-D state."""
    current_metrics = current.get("metrics") or {}
    next_metrics = nxt.get("metrics") or {}
    current_slices = current.get("slice_state") or {}
    next_slices = nxt.get("slice_state") or {}
    def completion(group: tuple[str, ...]) -> float:
        demand = sum(safe_float((current_slices.get(sid) or {}).get("demand")) for sid in group)
        return (
            sum(safe_float((current_slices.get(sid) or {}).get("completion_ratio")) * safe_float((current_slices.get(sid) or {}).get("demand")) for sid in group) / demand
            if demand > 1e-9 else 1.0
        )
    def next_completion(group: tuple[str, ...]) -> float:
        demand = sum(safe_float((next_slices.get(sid) or {}).get("demand")) for sid in group)
        return (
            sum(safe_float((next_slices.get(sid) or {}).get("completion_ratio")) * safe_float((next_slices.get(sid) or {}).get("demand")) for sid in group) / demand
            if demand > 1e-9 else 1.0
        )
    decision = current.get("decision") or {}
    category = {"ALLOWED": 0.0, "CONDITIONAL": 0.5, "BLOCKED": 1.0}.get(str(decision.get("energy_saver", "")).upper(), 0.5)
    action = current.get("action") or {}
    alloc_total = safe_float(action.get("r_ran")) + safe_float(action.get("r_ai"))
    budget = max(safe_float(action.get("usable_budget"), 1.0), 1e-9)
    power = safe_float(action.get("power_percent"), 100.0) / 100.0
    stage_boundary = int(str(current.get("scenario_stage", "")) != str(nxt.get("scenario_stage", "")))
    return [
        category,
        (safe_float(next_metrics.get("cvar_per_ue_us")) - safe_float(current_metrics.get("cvar_per_ue_us"))) / 100000.0,
        (safe_float(next_metrics.get("global_packet_loss_rate")) - safe_float(current_metrics.get("global_packet_loss_rate"))),
        (safe_float(next_metrics.get("cvar_per_ue_us")) - safe_float(current_metrics.get("cvar_per_ue_us"))) / 100000.0,
        next_completion(("eMBB",)) - completion(("eMBB",)),
        next_completion(("mMTC", "URLLC")) - completion(("mMTC", "URLLC")),
        alloc_total / budget,
        0.0,
        min(1.0, abs(safe_float(next_metrics.get("cvar_per_ue_us")) - safe_float(current_metrics.get("cvar_per_ue_us"))) / 100000.0 + 0.5 * stage_boundary),
        power,
    ]


def transition_record(current: dict[str, Any], nxt: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    reward = compute_reward(current)
    judge_outcome = current.get("judge_outcome") or {}
    judge_feedback = judge_outcome.get("feedback") or {}
    judge_observed = bool(
        judge_feedback.get("outcome_observed")
        or judge_outcome.get("observed")
    )
    if judge_observed:
        # Integral online training uses the continuous error from the next
        # real observation and the stronger categorical signal.  Older rows
        # have no strong field and deliberately fall back to canonical credit.
        continuous_reward = judge_feedback.get("tasam_continuous_reward")
        category_credit = judge_feedback.get("tasam_category_credit")
        strong_credit = judge_feedback.get("tasam_training_category_credit")
        explicit_category_credit = category_credit is not None
        explicit_strong_credit = strong_credit is not None
        if category_credit is None and (
            "tasam_category_penalty" in judge_feedback
            or "tasam_category_error" in judge_feedback
        ):
            category_credit = judge_feedback.get("tasam_state_credit")
        if strong_credit is None:
            strong_credit = category_credit
        if (
            continuous_reward is not None
            and strong_credit is not None
            and judge_feedback.get("tasam_reward_source") == "observed_real_metrics"
        ):
            reward_hint = min(clamp(continuous_reward, -1.0, 1.0), safe_float(strong_credit))
            reward_source = (
                "observed_real_metrics_with_strong_categorical_penalty"
                if explicit_strong_credit
                else "observed_real_metrics_with_categorical_penalty"
            )
        elif continuous_reward is not None and judge_feedback.get("tasam_reward_source") == "observed_real_metrics":
            reward_hint = clamp(continuous_reward, -1.0, 1.0)
            reward_source = "observed_real_metrics"
        else:
            reward_hint = strong_credit if strong_credit is not None else category_credit
            reward_hint = safe_float(reward_hint if reward_hint is not None else judge_feedback.get("tasam_credit", 0.0))
            reward_source = (
                "rapp_judge_strong_category_credit"
                if explicit_strong_credit
                else "rapp_judge_category_credit"
                if explicit_category_credit
                else "rapp_judge_tasam_credit"
            )
        reward_components = dict(reward["components"])
        reward_components.update({
            "judge_outcome_reward": safe_float(judge_feedback.get("outcome_reward")),
            "judge_state_credit": safe_float(judge_feedback.get("tasam_state_credit")),
            "judge_resource_credit": safe_float(judge_feedback.get("tasam_resource_credit")),
            "judge_tasam_credit": safe_float(judge_feedback.get("tasam_credit")),
            "tasam_observed_error": safe_float(judge_feedback.get("tasam_observed_error")),
            "tasam_continuous_reward": safe_float(judge_feedback.get("tasam_continuous_reward")),
            "tasam_reward_source": judge_feedback.get("tasam_reward_source", ""),
            "tasam_action_applied": bool(judge_feedback.get("tasam_action_applied", False)),
            "tasam_category_credit": safe_float(judge_feedback.get("tasam_category_credit", category_credit)),
            "tasam_category_penalty": safe_float(judge_feedback.get("tasam_category_penalty")),
            "tasam_category_error": bool(judge_feedback.get("tasam_category_error", False)),
            "tasam_training_category_credit": safe_float(judge_feedback.get("tasam_training_category_credit", strong_credit)),
            "tasam_training_category_penalty": safe_float(judge_feedback.get("tasam_training_category_penalty")),
            "tasam_training_reward": safe_float(judge_feedback.get("tasam_training_reward", reward_hint)),
            "tasam_predicted_verdict": judge_feedback.get("tasam_predicted_verdict", ""),
            "tasam_observed_verdict": judge_feedback.get("tasam_observed_verdict", judge_feedback.get("correct_verdict", "")),
        })
    else:
        reward_hint = reward["reward"]
        reward_source = "article_network_reward_unobserved_judge"
        reward_components = reward["components"]
    quality = collection_quality(current.get("metrics", {}), current, nxt, args)
    current_decision = current.get("decision") or {}
    decision_economic_action = current_decision.get("economic_action") or {}
    feedback_economic_action = judge_feedback.get("economic_action") or {}
    economic_action = (
        feedback_economic_action
        if isinstance(feedback_economic_action, dict) and feedback_economic_action
        else decision_economic_action
    )
    economic_contract = str(
        judge_feedback.get("economic_action_contract")
        or current_decision.get("economic_action_contract")
        or economic_action.get("contract")
        or ""
    )
    economic_eligible = bool(
        judge_feedback.get(
            "economic_transition_eligible",
            economic_action.get("economic_transition_eligible", False),
        )
    )
    if economic_contract == "applied_action_v2":
        # The category trace remains useful after a rejected intervention,
        # but the economic critic/actor must not learn a fictional action.
        reward_hint = safe_float(
            judge_feedback.get("tasam_online_reward", 0.0) if economic_eligible else 0.0
        )
        reward_source = (
            "realized_applied_economic_reward_v2"
            if economic_eligible
            else "economic_action_ineligible_v2"
        )
        reward_components = dict(reward_components)
        reward_components.update({
            "realized_energy_saving_fraction": safe_float(
                judge_feedback.get("realized_energy_saving_fraction")
            ),
            "realized_allocation_saving_fraction": safe_float(
                judge_feedback.get("realized_allocation_saving_fraction")
            ),
            "economic_transition_eligible": int(economic_eligible),
            "economic_invalid_reason": str(
                judge_feedback.get("economic_invalid_reason")
                or economic_action.get("outcome_invalid_reason")
                or ""
            ),
        })
    decision_stage_name = str(
        judge_feedback.get("decision_stage_name")
        or current.get("scenario_stage")
        or current_decision.get("collection_event_stage_name")
        or ""
    )
    observed_stage_name = str(judge_feedback.get("observed_stage_name") or "")
    stage_boundary_feedback = bool(
        judge_feedback.get(
            "stage_boundary_feedback",
            bool(
                decision_stage_name
                and observed_stage_name
                and decision_stage_name != observed_stage_name
            ),
        )
    )
    nominal_expected_verdict = str(
        judge_feedback.get("nominal_expected_verdict")
        or expected_verdict_for_stage(decision_stage_name)
        or "UNKNOWN"
    )
    predicted_verdict = str(judge_feedback.get("tasam_predicted_verdict") or "UNKNOWN")
    observed_verdict = str(
        judge_feedback.get("tasam_observed_verdict")
        or judge_feedback.get("correct_verdict")
        or "UNKNOWN"
    )
    if current_decision.get("training_run_invalid"):
        quality["valid_for_training"] = False
        quality["invalid_reason"] = current_decision.get("invalid_reason", "invalid_tasam_proposal")
    action = dict(current.get("action") or {})
    decision = current.get("decision") or {}
    power_percent = action.get("power_percent", decision.get("energy_power_level"))
    if power_percent is None:
        power_percent = {"POWER_DOWN_ECO": 25.0, "REDUCE_POWER": 60.0, "CONDITIONAL_REDUCE": 60.0, "FULL_POWER": 100.0}.get(
            str(decision.get("tasam_energy_action", "") or decision.get("action", "")).upper(), 100.0
        )
    action["power_percent"] = power_percent
    resource_allocation = dict(decision.get("resource_allocation") or action.get("resource_allocation") or {})
    marl_shadow = resource_allocation.get("marl_shadow") or {}
    allocation_head = marl_shadow.get("allocation_head") or {}
    allocation_projection = marl_shadow.get("allocation_projection") or {}
    temporal_context = build_temporal_context(current, nxt)
    return {
        "schema": "greenran.tasam_article_transition.v1",
        "timestamp": current["timestamp"],
        "datetime": current.get("datetime"),
        "next_timestamp": nxt["timestamp"],
        "topology_id": current.get("topology_id"),
        "scenario_stage": current.get("scenario_stage"),
        "next_scenario_stage": nxt.get("scenario_stage"),
        "decision_stage_name": decision_stage_name,
        "observed_stage_name": observed_stage_name,
        "stage_boundary_feedback": stage_boundary_feedback,
        "nominal_expected_verdict": nominal_expected_verdict,
        "nominal_category_match": bool(
            judge_observed
            and nominal_expected_verdict != "UNKNOWN"
            and predicted_verdict == nominal_expected_verdict
        ),
        "real_category_match": bool(
            judge_observed
            and predicted_verdict != "UNKNOWN"
            and predicted_verdict == observed_verdict
        ),
        "global_state": current["global_state"],
        "slice_state": current["slice_state"],
        "du_states": current["du_states"],
        "action": action,
        "decision": decision,
        "decision_id": decision.get("decision_id", decision.get("id")),
        "snapshot_sequence_id": decision.get("snapshot_sequence_id") or current.get("metrics_alignment", {}).get("snapshot_sequence_id", current.get("timestamp")),
        "temporal_context": temporal_context,
        "tasam_energy_action": decision.get("tasam_energy_action", ""),
        "tasam_power_percent": power_percent,
        "tasam_allocation_predicted_ran": safe_float(allocation_head.get("predicted_ran_share")),
        "tasam_allocation_predicted_ai": safe_float(allocation_head.get("predicted_ai_share")),
        "tasam_allocation_predicted_total": safe_float(allocation_head.get("predicted_total_budget_fraction")),
        "tasam_allocation_target_ran": safe_float(allocation_projection.get("ran_min_share")),
        "tasam_allocation_target_ai": safe_float(allocation_projection.get("ai_min_share")),
        "tasam_allocation_target_total": safe_float(allocation_projection.get("total_budget_fraction")),
        "tasam_allocation_target_source": allocation_projection.get("reason", ""),
        "tasam_allocation_target_feasible": bool(allocation_projection.get("feasible", True)),
        # The online controller declares its three-output contract at launch.
        # A SQLite decision can omit optional nested allocation diagnostics
        # while the active checkpoint still contains the economic head.
        "allocation_total_head_enabled": bool(
            getattr(args, "allocation_total_head_enabled", False)
            or allocation_head.get("total_budget_head_enabled")
            or str((marl_shadow.get("checkpoint_run_dir") or "")).endswith("economic_v1")
        ),
        "training_run_invalid": bool(current_decision.get("training_run_invalid", False)),
        "judge_feedback": judge_feedback,
        "judge_observation": judge_outcome.get("observation", {}),
        "judge_feedback_observed": judge_observed,
        "judge_alignment": judge_outcome.get("judge_alignment") or {},
        "selected_assistant": (current.get("decision") or {}).get("selected_assistant", ""),
        "credit_assignment": judge_feedback.get("credit_assignment", ""),
        "armd_credit": safe_float(judge_feedback.get("armd_credit")),
        "tasam_credit": safe_float(judge_feedback.get("tasam_credit")),
        "judge_reward": safe_float(judge_feedback.get("outcome_reward")),
        "tasam_observed_error": safe_float(judge_feedback.get("tasam_observed_error")),
        "tasam_continuous_reward": safe_float(judge_feedback.get("tasam_continuous_reward")),
        "tasam_reward_source": judge_feedback.get("tasam_reward_source", ""),
        "tasam_error_components": judge_feedback.get("tasam_error_components") or {},
        "tasam_action_applied": bool(judge_feedback.get("tasam_action_applied", False)),
        "tasam_category_credit": safe_float(judge_feedback.get("tasam_category_credit")),
        "tasam_category_penalty": safe_float(judge_feedback.get("tasam_category_penalty")),
        "tasam_category_error": bool(judge_feedback.get("tasam_category_error", False)),
        "tasam_training_category_credit": safe_float(judge_feedback.get("tasam_training_category_credit")),
        "tasam_training_category_penalty": safe_float(judge_feedback.get("tasam_training_category_penalty")),
        "tasam_training_reward": safe_float(judge_feedback.get("tasam_training_reward", reward_hint)),
        "tasam_online_reward": safe_float(judge_feedback.get("tasam_online_reward", reward_hint)),
        "tasam_energy_reward": safe_float(judge_feedback.get("tasam_energy_reward")),
        "tasam_allocation_reward": safe_float(judge_feedback.get("tasam_allocation_reward")),
        "tasam_sla_penalty": safe_float(judge_feedback.get("tasam_sla_penalty")),
        "applied_power_percent": safe_float(judge_feedback.get("applied_power_percent", decision.get("tasam_power_applied_percent"))),
        "applied_ran_allocation": safe_float(judge_feedback.get("applied_ran_allocation", resource_allocation.get("r_ran"))),
        "applied_ai_allocation": safe_float(judge_feedback.get("applied_ai_allocation", resource_allocation.get("r_ai"))),
        "applied_total_allocation": safe_float(judge_feedback.get("applied_total_allocation", (resource_allocation.get("r_ran", 0.0) or 0.0) + (resource_allocation.get("r_ai", 0.0) or 0.0))),
        "economic_action_contract": economic_contract,
        "economic_action": economic_action,
        "economic_transition_eligible": economic_eligible,
        "economic_application_status": str(
            judge_feedback.get("economic_application_status")
            or current_decision.get("economic_application_status")
            or economic_action.get("application_status")
            or ""
        ),
        "economic_rejection_reason": str(
            current_decision.get("economic_rejection_reason")
            or economic_action.get("rejection_reason")
            or judge_feedback.get("economic_invalid_reason")
            or ""
        ),
        "live_power_percent": safe_float(judge_feedback.get("live_power_percent", (economic_action.get("live_candidate") or {}).get("power_percent"))),
        "live_total_allocation": safe_float(judge_feedback.get("live_total_allocation", (economic_action.get("live_candidate") or {}).get("total_allocation"))),
        "realized_energy_saving_fraction": safe_float(judge_feedback.get("realized_energy_saving_fraction")),
        "realized_allocation_saving_fraction": safe_float(judge_feedback.get("realized_allocation_saving_fraction")),
        "tasam_predicted_verdict": predicted_verdict if judge_observed else "",
        "tasam_observed_verdict": observed_verdict if judge_observed else "",
        "metrics": current["metrics"],
        "metrics_alignment": {
            "current": current.get("metrics_alignment") or {},
            "next": nxt.get("metrics_alignment") or {},
        },
        "armd_context": current["armd_context"],
        "conflict_context": current["conflict_context"],
        "shadow_comparison": current["shadow_comparison"],
        "reward_hint": reward_hint,
        "reward_source": reward_source,
        "reward_components": reward_components,
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
    reward_source_counts: dict[str, int] = {}
    credit_assignment_counts: dict[str, int] = {}
    real_category_matrix: dict[str, int] = {}
    nominal_category_matrix: dict[str, int] = {}
    judge_feedback_observed = 0
    stage_boundary_feedback = 0
    real_category_errors = 0
    nominal_category_matches = 0
    nominal_category_evaluated = 0
    tasam_credits: list[float] = []
    economic_contract_counts: dict[str, int] = {}
    economic_eligible_transitions = 0
    economic_ineligible_reasons: dict[str, int] = {}
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
            source = str(record.get("reward_source") or "unknown")
            reward_source_counts[source] = reward_source_counts.get(source, 0) + 1
            assignment = str(record.get("credit_assignment") or "unobserved")
            credit_assignment_counts[assignment] = credit_assignment_counts.get(assignment, 0) + 1
            contract = str(record.get("economic_action_contract") or "legacy")
            economic_contract_counts[contract] = economic_contract_counts.get(contract, 0) + 1
            if record.get("economic_transition_eligible"):
                economic_eligible_transitions += 1
            elif contract == "applied_action_v2":
                reason = str(record.get("economic_rejection_reason") or "ineligible")
                economic_ineligible_reasons[reason] = economic_ineligible_reasons.get(reason, 0) + 1
            if record.get("judge_feedback_observed"):
                judge_feedback_observed += 1
                tasam_credits.append(safe_float(record.get("tasam_credit")))
                predicted = str(record.get("tasam_predicted_verdict") or "UNKNOWN")
                observed = str(record.get("tasam_observed_verdict") or "UNKNOWN")
                nominal = str(record.get("nominal_expected_verdict") or "UNKNOWN")
                real_key = f"{predicted}->{observed}"
                real_category_matrix[real_key] = real_category_matrix.get(real_key, 0) + 1
                if predicted != observed:
                    real_category_errors += 1
                if record.get("stage_boundary_feedback"):
                    stage_boundary_feedback += 1
                if nominal != "UNKNOWN":
                    nominal_category_evaluated += 1
                    if predicted == nominal:
                        nominal_category_matches += 1
                    nominal_key = f"{predicted}->{nominal}"
                    nominal_category_matrix[nominal_key] = nominal_category_matrix.get(nominal_key, 0) + 1
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
        "judge_feedback_observed": judge_feedback_observed,
        "judge_feedback_coverage": judge_feedback_observed / max(written, 1),
        "real_category_matrix": real_category_matrix,
        "real_category_errors": real_category_errors,
        "real_category_accuracy": (
            (judge_feedback_observed - real_category_errors) / max(judge_feedback_observed, 1)
        ),
        "nominal_category_matrix": nominal_category_matrix,
        "nominal_category_matches": nominal_category_matches,
        "nominal_category_evaluated": nominal_category_evaluated,
        "nominal_category_accuracy": (
            nominal_category_matches / max(nominal_category_evaluated, 1)
        ),
        "stage_boundary_feedback": stage_boundary_feedback,
        "reward_source_counts": reward_source_counts,
        "credit_assignment_counts": credit_assignment_counts,
        "tasam_credit_mean": sum(tasam_credits) / max(len(tasam_credits), 1),
        "economic_action_contract_counts": economic_contract_counts,
        "economic_eligible_transitions": economic_eligible_transitions,
        "economic_ineligible_reasons": economic_ineligible_reasons,
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
