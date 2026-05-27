#!/usr/bin/env python3
"""
Export real-scenario DRL confidence/isolation datasets for article figures.

Inputs:
  - DRL predictor trace JSONL generated with GREENRAN_DRL_TRACE=1
  - GreenRAN runtime Data Lake (/tmp/rapp_data_lake.db by default)

Outputs:
  - drl_isolation_timeseries.csv
  - drl_isolation_by_confidence_threshold.csv
  - drl_isolation_summary.json

No synthetic values are generated. Isolation degree is derived from observed SLA
status of active services in the real scenario:
  protected = 1.0
  guard     = 0.5
  violated  = 0.0
The per-step isolation degree is the mean score across active services.
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

from vehicle_policy_runtime import evaluate_vehicle_policy


DEFAULT_DB = Path("/tmp/rapp_data_lake.db")
DEFAULT_TRACE = Path("/tmp/drl_predictor_trace.jsonl")
DEFAULT_OUT = PROJECT_ROOT / "runs" / "eedrl_greenran_final" / "article_metrics_real"
CONFIDENCE_THRESHOLDS = [50, 60, 70, 80, 90, 95, 97, 99]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export DRL isolation/confidence dataset from the real GreenRAN scenario.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="path to the runtime SQLite database")
    parser.add_argument("--trace-file", type=Path, default=DEFAULT_TRACE, help="path to the DRL predictor trace JSONL")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT, help="directory for exported CSV/JSON files")
    parser.add_argument("--join-tolerance-s", type=int, default=2, help="max absolute timestamp delta for joining trace and DB rows")
    return parser.parse_args()


def safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def load_trace(trace_path: Path) -> list[dict]:
    items = []
    with trace_path.open(encoding="utf-8") as f:
        for line in f:
            text = line.strip()
            if not text:
                continue
            payload = json.loads(text)
            ts_iso = payload.get("timestamp")
            if not ts_iso:
                continue
            try:
                ts_epoch = int(datetime.fromisoformat(ts_iso).timestamp())
            except ValueError:
                continue
            payload["_epoch"] = ts_epoch
            items.append(payload)
    return items


def fetch_rows(conn: sqlite3.Connection, query: str) -> dict[int, dict]:
    conn.row_factory = sqlite3.Row
    cur = conn.execute(query)
    rows = {}
    for row in cur.fetchall():
        data = dict(row)
        ts = safe_int(data.get("timestamp"))
        if ts:
            rows[ts] = data
    return rows


def fetch_camera_rows(conn: sqlite3.Connection) -> dict[int, dict]:
    return fetch_rows(
        conn,
        """
        SELECT
            timestamp,
            COUNT(*) AS active_cameras,
            MIN(throughput_kbps) AS min_camera_throughput_kbps,
            MAX(latency_us) AS max_camera_latency_us
        FROM ue_metrics
        WHERE device_type = 'camera'
        GROUP BY timestamp
        ORDER BY timestamp
        """,
    )


def fetch_app2_rows(conn: sqlite3.Connection) -> dict[int, dict]:
    return fetch_rows(
        conn,
        """
        SELECT
            timestamp,
            total_sensors,
            connected_sensors,
            error_sensors,
            low_battery_sensors,
            packet_loss_percent,
            delivery_success_percent,
            avg_latency_ms,
            avg_battery_percent
        FROM app2_snapshots
        ORDER BY timestamp
        """,
    )


def fetch_app3_rows(conn: sqlite3.Connection) -> dict[int, dict]:
    return fetch_rows(
        conn,
        """
        SELECT
            timestamp,
            total_vehicles,
            ego_present,
            high_risk_vehicles,
            medium_risk_vehicles,
            degraded_autonomy_vehicles,
            max_latency_ms,
            max_packet_loss_percent,
            max_speed_mps
        FROM app3_snapshots
        ORDER BY timestamp
        """,
    )


def fetch_extended_rows(conn: sqlite3.Connection) -> dict[int, dict]:
    return fetch_rows(
        conn,
        """
        SELECT
            timestamp,
            datetime,
            sim_time_s,
            cvar_per_ue_us,
            latency_p95_per_ue_us,
            global_packet_loss_rate,
            throughput_kbps,
            total_active_ues,
            total_active_cameras,
            total_critical_ues
        FROM extended_metrics
        ORDER BY timestamp
        """,
    )


def nearest_row(rows: dict[int, dict], ts: int, tolerance_s: int) -> dict | None:
    if ts in rows:
        return rows[ts]
    for delta in range(1, tolerance_s + 1):
        if ts - delta in rows:
            return rows[ts - delta]
        if ts + delta in rows:
            return rows[ts + delta]
    return None


def camera_status(camera_row: dict | None) -> tuple[str, float, dict]:
    if not camera_row:
        return "inactive", 0.0, {"active_cameras": 0}
    active = safe_int(camera_row.get("active_cameras"))
    if active <= 0:
        return "inactive", 0.0, {"active_cameras": 0}
    min_tp = safe_float(camera_row.get("min_camera_throughput_kbps")) / 1000.0
    max_lat = safe_float(camera_row.get("max_camera_latency_us")) / 1000.0
    if min_tp < 25.0 or max_lat >= 80.0:
        status = "violated"
        score = 0.0
    elif min_tp < 30.0 or max_lat >= 60.0:
        status = "guard"
        score = 0.5
    else:
        status = "protected"
        score = 1.0
    return status, score, {
        "active_cameras": active,
        "min_camera_throughput_mbps": round(min_tp, 3),
        "max_camera_latency_ms": round(max_lat, 3),
    }


def app2_status(app2_row: dict | None) -> tuple[str, float, dict]:
    if not app2_row:
        return "inactive", 0.0, {"total_sensors": 0}
    total = safe_int(app2_row.get("total_sensors"))
    if total <= 0:
        return "inactive", 0.0, {"total_sensors": 0}

    connected = safe_int(app2_row.get("connected_sensors"))
    error_sensors = safe_int(app2_row.get("error_sensors"))
    low_battery = safe_int(app2_row.get("low_battery_sensors"))
    packet_loss = safe_float(app2_row.get("packet_loss_percent"))
    delivery = safe_float(app2_row.get("delivery_success_percent"), 100.0)
    latency_ms = safe_float(app2_row.get("avg_latency_ms"))
    battery = safe_float(app2_row.get("avg_battery_percent"), 100.0)
    connected_ratio = connected / total if total else 1.0
    error_ratio = error_sensors / total if total else 0.0

    critical = (
        connected_ratio < 0.85
        or packet_loss >= 10.0
        or delivery < 90.0
        or latency_ms >= 1000.0
        or battery < 15.0
        or error_ratio >= 0.20
    )
    guard = (
        connected_ratio < 0.90
        or packet_loss >= 5.0
        or delivery < 95.0
        or latency_ms >= 500.0
        or battery < 25.0
        or low_battery > 0
        or error_sensors >= 2
    )
    if critical:
        status = "violated"
        score = 0.0
    elif guard:
        status = "guard"
        score = 0.5
    else:
        status = "protected"
        score = 1.0
    return status, score, {
        "total_sensors": total,
        "connected_ratio": round(connected_ratio, 4),
        "packet_loss_percent": round(packet_loss, 3),
        "delivery_success_percent": round(delivery, 3),
        "avg_latency_ms": round(latency_ms, 3),
        "avg_battery_percent": round(battery, 3),
        "error_ratio": round(error_ratio, 4),
    }


def app3_status(app3_row: dict | None) -> tuple[str, float, dict]:
    if not app3_row:
        return "inactive", 0.0, {"total_vehicles": 0}
    total = safe_int(app3_row.get("total_vehicles"))
    if total <= 0:
        return "inactive", 0.0, {"total_vehicles": 0}

    metrics = {
        "available": True,
        "stale": False,
        "age_seconds": 0.0,
        "total_vehicles": total,
        "ego_present": bool(app3_row.get("ego_present")),
        "high_risk_vehicles": safe_int(app3_row.get("high_risk_vehicles")),
        "medium_risk_vehicles": safe_int(app3_row.get("medium_risk_vehicles")),
        "degraded_autonomy_vehicles": safe_int(app3_row.get("degraded_autonomy_vehicles")),
        "max_latency_ms": safe_float(app3_row.get("max_latency_ms")),
        "max_packet_loss_percent": safe_float(app3_row.get("max_packet_loss_percent")),
        "max_speed_mps": safe_float(app3_row.get("max_speed_mps")),
    }
    policy = evaluate_vehicle_policy(metrics)
    severity = str(policy.get("severity", "none"))
    if severity == "critical":
        status = "violated"
        score = 0.0
    elif severity == "warning":
        status = "guard"
        score = 0.5
    else:
        status = "protected"
        score = 1.0
    return status, score, {
        "total_vehicles": total,
        "high_risk_vehicles": metrics["high_risk_vehicles"],
        "medium_risk_vehicles": metrics["medium_risk_vehicles"],
        "degraded_autonomy_vehicles": metrics["degraded_autonomy_vehicles"],
        "max_latency_ms": round(metrics["max_latency_ms"], 3),
        "max_packet_loss_percent": round(metrics["max_packet_loss_percent"], 3),
    }


def summarize_thresholds(rows: list[dict]) -> list[dict]:
    return summarize_thresholds_for_field(rows, "drl_actor_decision_confidence_pct")


def summarize_thresholds_for_field(rows: list[dict], confidence_field: str) -> list[dict]:
    summary = []
    for threshold in CONFIDENCE_THRESHOLDS:
        filtered = [row for row in rows if safe_float(row[confidence_field]) >= threshold]
        if filtered:
            avg_isolation = sum(safe_float(row["isolation_degree"]) for row in filtered) / len(filtered)
        else:
            avg_isolation = 0.0
        summary.append(
            {
                "confidence_field": confidence_field,
                "confidence_threshold_pct": threshold,
                "samples": len(filtered),
                "avg_isolation_degree": round(avg_isolation, 6),
            }
        )
    return summary


def export_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def main() -> int:
    args = parse_args()
    if not args.db.exists():
        raise SystemExit(f"database not found: {args.db}")
    if not args.trace_file.exists():
        raise SystemExit(f"trace file not found: {args.trace_file}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    trace_items = load_trace(args.trace_file)
    if not trace_items:
        raise SystemExit(f"empty trace file: {args.trace_file}")

    conn = sqlite3.connect(str(args.db))
    cameras = fetch_camera_rows(conn)
    app2 = fetch_app2_rows(conn)
    app3 = fetch_app3_rows(conn)
    extended = fetch_extended_rows(conn)
    conn.close()

    timeseries = []
    for item in trace_items:
        ts = safe_int(item.get("_epoch"))
        output = item.get("output", {}) or {}
        decision_probabilities = output.get("decision_probabilities", []) or []
        while len(decision_probabilities) < 3:
            decision_probabilities.append(0.0)
        decision_probabilities = [safe_float(value) for value in decision_probabilities[:3]]
        actor_decision = str(output.get("a3c_decision", "") or "")
        final_decision = str(output.get("final_decision", "") or "")
        decision_index = {"ALLOWED": 0, "CONDITIONAL": 1, "BLOCKED": 2}
        actor_decision_confidence_pct = max(decision_probabilities) * 100.0
        final_decision_probability_pct = (
            decision_probabilities[decision_index[final_decision]] * 100.0
            if final_decision in decision_index
            else 0.0
        )
        actor_decision_probability_pct = (
            decision_probabilities[decision_index[actor_decision]] * 100.0
            if actor_decision in decision_index
            else 0.0
        )
        ext = nearest_row(extended, ts, args.join_tolerance_s) or {}
        camera_row = nearest_row(cameras, ts, args.join_tolerance_s)
        app2_row = nearest_row(app2, ts, args.join_tolerance_s)
        app3_row = nearest_row(app3, ts, args.join_tolerance_s)

        camera_state, camera_score, camera_meta = camera_status(camera_row)
        app2_state, app2_score, app2_meta = app2_status(app2_row)
        app3_state, app3_score, app3_meta = app3_status(app3_row)

        active_scores = []
        for status, score in ((camera_state, camera_score), (app2_state, app2_score), (app3_state, app3_score)):
            if status != "inactive":
                active_scores.append(score)
        isolation_degree = sum(active_scores) / len(active_scores) if active_scores else 0.0

        row = {
            "timestamp": ts,
            "datetime": ext.get("datetime") or item.get("timestamp"),
            "sim_time_s": safe_float(ext.get("sim_time_s")),
            "drl_actor_decision": actor_decision,
            "drl_final_decision": final_decision,
            "drl_policy_action": output.get("policy_action", ""),
            "drl_confidence": round(safe_float(output.get("confidence")), 6),
            "drl_confidence_pct": round(safe_float(output.get("confidence")) * 100.0, 3),
            "drl_actor_confidence": round(safe_float(output.get("actor_confidence")), 6),
            "drl_actor_action_confidence_pct": round(safe_float(output.get("confidence")) * 100.0, 3),
            "drl_actor_decision_confidence_pct": round(actor_decision_confidence_pct, 3),
            "drl_actor_decision_probability_pct": round(actor_decision_probability_pct, 3),
            "drl_final_decision_probability_pct": round(final_decision_probability_pct, 3),
            "drl_prob_allowed_pct": round(decision_probabilities[0] * 100.0, 3),
            "drl_prob_conditional_pct": round(decision_probabilities[1] * 100.0, 3),
            "drl_prob_blocked_pct": round(decision_probabilities[2] * 100.0, 3),
            "drl_risk_score": round(safe_float(output.get("risk_score")), 6),
            "drl_predicted_cvar_ms": round(safe_float(output.get("predicted_cvar_ms")), 6),
            "real_cvar_ms": round(safe_float(ext.get("cvar_per_ue_us")) / 1000.0, 6),
            "real_p95_ms": round(safe_float(ext.get("latency_p95_per_ue_us")) / 1000.0, 6),
            "camera_status": camera_state,
            "app2_status": app2_state,
            "app3_status": app3_state,
            "active_services": len(active_scores),
            "isolation_degree": round(isolation_degree, 6),
            "camera_active_cameras": camera_meta.get("active_cameras", 0),
            "camera_min_throughput_mbps": camera_meta.get("min_camera_throughput_mbps", 0.0),
            "camera_max_latency_ms": camera_meta.get("max_camera_latency_ms", 0.0),
            "app2_total_sensors": app2_meta.get("total_sensors", 0),
            "app2_connected_ratio": app2_meta.get("connected_ratio", 0.0),
            "app2_delivery_success_percent": app2_meta.get("delivery_success_percent", 0.0),
            "app2_packet_loss_percent": app2_meta.get("packet_loss_percent", 0.0),
            "app2_avg_latency_ms": app2_meta.get("avg_latency_ms", 0.0),
            "app3_total_vehicles": app3_meta.get("total_vehicles", 0),
            "app3_high_risk_vehicles": app3_meta.get("high_risk_vehicles", 0),
            "app3_degraded_autonomy_vehicles": app3_meta.get("degraded_autonomy_vehicles", 0),
            "app3_max_latency_ms": app3_meta.get("max_latency_ms", 0.0),
        }
        timeseries.append(row)

    threshold_rows = summarize_thresholds(timeseries)
    action_threshold_rows = summarize_thresholds_for_field(timeseries, "drl_actor_action_confidence_pct")
    final_probability_threshold_rows = summarize_thresholds_for_field(timeseries, "drl_final_decision_probability_pct")
    summary = {
        "trace_file": str(args.trace_file),
        "db": str(args.db),
        "samples": len(timeseries),
        "confidence_metric_primary": "drl_actor_decision_confidence_pct",
        "confidence_thresholds": threshold_rows,
        "action_confidence_thresholds": action_threshold_rows,
        "final_decision_probability_thresholds": final_probability_threshold_rows,
        "avg_isolation_degree": round(
            sum(safe_float(row["isolation_degree"]) for row in timeseries) / len(timeseries), 6
        ) if timeseries else 0.0,
        "avg_actor_action_confidence_pct": round(
            sum(safe_float(row["drl_actor_action_confidence_pct"]) for row in timeseries) / len(timeseries), 6
        ) if timeseries else 0.0,
        "avg_actor_decision_confidence_pct": round(
            sum(safe_float(row["drl_actor_decision_confidence_pct"]) for row in timeseries) / len(timeseries), 6
        ) if timeseries else 0.0,
        "avg_final_decision_probability_pct": round(
            sum(safe_float(row["drl_final_decision_probability_pct"]) for row in timeseries) / len(timeseries), 6
        ) if timeseries else 0.0,
        "status_counts": {
            "camera": dict(defaultdict(int)),
            "app2": dict(defaultdict(int)),
            "app3": dict(defaultdict(int)),
        },
    }

    for domain_key, status_key in (("camera", "camera_status"), ("app2", "app2_status"), ("app3", "app3_status")):
        counts: dict[str, int] = defaultdict(int)
        for row in timeseries:
            counts[str(row[status_key])] += 1
        summary["status_counts"][domain_key] = dict(counts)

    timeseries_path = args.output_dir / "drl_isolation_timeseries.csv"
    threshold_path = args.output_dir / "drl_isolation_by_confidence_threshold.csv"
    summary_path = args.output_dir / "drl_isolation_summary.json"

    export_csv(timeseries_path, timeseries, list(timeseries[0].keys()))
    export_csv(threshold_path, threshold_rows, list(threshold_rows[0].keys()))
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(json.dumps({
        "output_dir": str(args.output_dir),
        "timeseries_csv": str(timeseries_path),
        "threshold_csv": str(threshold_path),
        "summary_json": str(summary_path),
        "samples": len(timeseries),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
