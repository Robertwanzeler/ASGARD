#!/usr/bin/env python3
"""Evaluate the isolated 20-UE rApp versus ARMD+TA-SAM shadow smoke pair."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any


REQUIRED_DUS = {"du_camera_edge", "du_sensor_mixed", "du_vehicle_edge"}


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _grouped(conn: sqlite3.Connection, table: str, column: str) -> dict[str, int]:
    try:
        return {("" if key is None else str(key)): int(count) for key, count in conn.execute(
            f"SELECT {column}, COUNT(*) FROM {table} GROUP BY {column}"
        )}
    except sqlite3.Error:
        return {}


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


def _count(conn: sqlite3.Connection, table: str) -> int:
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    except sqlite3.Error:
        return 0


def _arm_report(run_dir: Path, *, shadow: bool) -> dict[str, Any]:
    manifest = _json(run_dir / "arm_manifest.json")
    db_path = run_dir / "rapp_data_lake.db"
    report: dict[str, Any] = {
        "run_dir": str(run_dir.resolve()),
        "manifest_status": manifest.get("status", ""),
        "database_present": db_path.is_file(),
    }
    if not db_path.is_file():
        report["criteria"] = {"database_present": False}
        report["valid"] = False
        return report

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        decision_columns = _columns(conn, "decisions_history")
        ue_columns = _columns(conn, "ue_metrics")
        extended_columns = _columns(conn, "extended_metrics")
        decisions = _count(conn, "decisions_history")
        report.update({
            "decisions": decisions,
            "ue_rows": _count(conn, "ue_metrics"),
            "judge_outcomes": _count(conn, "judge_outcome_history"),
            "shadow_comparisons": _count(conn, "marl_shadow_comparison_history"),
            "du_state_rows": _count(conn, "marl_du_state_history"),
            "du_ids": sorted(_grouped(conn, "marl_du_state_history", "du_id")),
            "stages": _grouped(conn, "decisions_history", "collection_event_stage_name"),
        })
        real_pdcp = (
            ue_columns
            and set(_grouped(conn, "ue_metrics", "pdcp_provenance")) == {"pdcp_real"}
            and "latency_is_proxy" in ue_columns
            and _grouped(conn, "ue_metrics", "latency_is_proxy") == {"0": report["ue_rows"]}
        )
        collector_real = (
            "collector_mode" in extended_columns
            and _grouped(conn, "extended_metrics", "collector_mode") == {"pdcp_real": _count(conn, "extended_metrics")}
        )
        no_proxy = (
            "proxy_latency_sample_count" in extended_columns
            and _grouped(conn, "extended_metrics", "proxy_latency_sample_count")
            == {"0": _count(conn, "extended_metrics")}
        )
        report["pdcp"] = {
            "real_only": bool(real_pdcp),
            "collector_real": bool(collector_real),
            "no_proxy_samples": bool(no_proxy),
        }
        criteria: dict[str, bool] = {
            "manifest_finished": manifest.get("status") == "finished",
            "database_present": True,
            "decisions_present": decisions >= 20,
            "three_dus": set(report["du_ids"]) == REQUIRED_DUS and report["du_state_rows"] >= decisions * 3,
            "real_pdcp_only": bool(real_pdcp),
            "collector_real": bool(collector_real),
            "no_proxy_samples": bool(no_proxy),
            "no_invalid_decisions": (
                "training_run_invalid" not in decision_columns
                or not any(int(row[0] or 0) for row in conn.execute(
                    "SELECT training_run_invalid FROM decisions_history"
                ))
            ),
        }
        if shadow:
            grouped_source = _grouped(conn, "decisions_history", "tasam_source")
            grouped_influence = _grouped(conn, "decisions_history", "tasam_would_influence")
            grouped_applied = _grouped(conn, "decisions_history", "tasam_actuation_applied")
            criteria.update({
                "checkpoint_source_on_all_decisions": grouped_source == {"checkpoint": decisions},
                "shadow_proposals_on_all_decisions": _grouped(conn, "decisions_history", "tasam_proposal_present") == {"1": decisions},
                "armd_on_all_decisions": _grouped(conn, "decisions_history", "armd_enabled") == {"1": decisions},
                "shadow_did_not_influence": grouped_influence == {"0": decisions},
                "shadow_did_not_actuate": grouped_applied == {"0": decisions},
                "judge_feedback_complete": report["judge_outcomes"] == decisions,
                "checkpoint_frozen": bool(manifest.get("checkpoint_frozen_verified")),
            })
        else:
            criteria.update({
                "armd_disabled": _grouped(conn, "decisions_history", "armd_enabled") == {"0": decisions},
                "tasam_disabled": _grouped(conn, "decisions_history", "tasam_enabled") == {"0": decisions},
            })
        report["criteria"] = criteria
        report["valid"] = all(criteria.values())
    finally:
        conn.close()
    infra = _json(run_dir / "infrastructure_metrics.json")
    report["infrastructure"] = {
        "enforced": bool(infra.get("enforced", False)),
        "complete": bool(infra.get("complete", False)),
        "collection_mode": infra.get("collection_mode", "unknown"),
    }
    report["artifact_bytes"] = int((infra.get("totals") or {}).get("artifact_bytes", 0) or 0)
    return report


def build_report(baseline: Path, shadow: Path) -> dict[str, Any]:
    baseline_manifest = _json(baseline / "arm_manifest.json")
    shadow_manifest = _json(shadow / "arm_manifest.json")
    pair = {
        "same_seed": baseline_manifest.get("seed") == shadow_manifest.get("seed"),
        "same_profile": baseline_manifest.get("profile") == shadow_manifest.get("profile"),
        "same_wall_time": baseline_manifest.get("wall_time_s") == shadow_manifest.get("wall_time_s"),
    }
    baseline_report = _arm_report(baseline, shadow=False)
    shadow_report = _arm_report(shadow, shadow=True)
    return {
        "schema": "greenran.tasam.smoke_pair.v1",
        "pair": pair,
        "baseline": baseline_report,
        "combined_shadow": shadow_report,
        "smoke_valid": all(pair.values()) and baseline_report.get("valid", False) and shadow_report.get("valid", False),
        "infrastructure_measurement_valid": (
            baseline_report.get("infrastructure", {}).get("enforced", False)
            and baseline_report.get("infrastructure", {}).get("complete", False)
            and shadow_report.get("infrastructure", {}).get("enforced", False)
            and shadow_report.get("infrastructure", {}).get("complete", False)
        ),
        "control_trial_allowed": False,
        "control_trial_reason": "shadow smoke only; no manual control gate approval was supplied",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--shadow", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = build_report(args.baseline.resolve(), args.shadow.resolve())
    args.output.resolve().parent.mkdir(parents=True, exist_ok=True)
    args.output.resolve().write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(args.output.resolve()),
        "smoke_valid": report["smoke_valid"],
        "infrastructure_measurement_valid": report["infrastructure_measurement_valid"],
    }, ensure_ascii=False))
    return 0 if report["smoke_valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
