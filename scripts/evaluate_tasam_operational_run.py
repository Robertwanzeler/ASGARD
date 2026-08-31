#!/usr/bin/env python3
"""Summarize one real-PDCP ARMD + TA-SAM operational run."""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from evaluate_tasam_network_campaign import read_network_run


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.is_file():
        return rows
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _stage_counts(run_dir: Path) -> dict[str, int]:
    rows = _read_jsonl(run_dir / "article00_scenario_control_history.jsonl")
    counts: Counter[str] = Counter()
    for row in rows:
        name = str(
            row.get("collection_event_stage_name")
            or row.get("stage_name")
            or row.get("stage")
            or "unknown"
        )
        counts[name] += 1
    return dict(sorted(counts.items()))


def _runtime_flags(run_dir: Path) -> dict[str, Any]:
    flags: dict[str, Any] = {
        "real_only": True,
        "proxy_allowed": False,
        "external_policy": "disabled_by_assistant_only_control",
        "fallback": "invalid_run_stop",
    }
    control = run_dir / "control_trial_state.json"
    if control.is_file():
        try:
            payload = json.loads(control.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                flags["control_trial_state"] = {
                    key: payload[key]
                    for key in (
                        "mode",
                        "target_decisions",
                        "decisions",
                        "applied",
                        "rollbacks",
                        "invalid_run",
                        "last_reason",
                    )
                    if key in payload
                }
        except json.JSONDecodeError:
            flags["control_trial_state_error"] = "invalid_json"
    wall = run_dir / "wall_clock_status.json"
    if wall.is_file():
        try:
            payload = json.loads(wall.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                flags["wall_clock"] = payload
        except json.JSONDecodeError:
            flags["wall_clock_status_error"] = "invalid_json"
    return flags


def _decision_integrity(run_dir: Path) -> dict[str, Any]:
    db_path = run_dir / "rapp_data_lake.db"
    if not db_path.is_file():
        return {"database_present": False}
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT * FROM decisions_history ORDER BY id").fetchall()
    finally:
        conn.close()
    if not rows:
        return {"database_present": True, "decisions": 0}
    def count_int(column: str) -> int:
        return sum(int(row[column] or 0) for row in rows) if column in rows[0].keys() else 0
    algorithms = Counter(str(row["effective_policy_algorithm"] or "") for row in rows if "effective_policy_algorithm" in row.keys())
    sources = Counter(str(row["effective_policy_source"] or "") for row in rows if "effective_policy_source" in row.keys())
    modes = Counter(str(row["control_trial_mode"] or "") for row in rows if "control_trial_mode" in row.keys())
    reasons = Counter(str(row["control_trial_reason"] or "") for row in rows if "control_trial_reason" in row.keys())
    exact_json_matches = 0
    for row in rows:
        selected = str(row["selected_proposal_json"] or "") if "selected_proposal_json" in row.keys() else ""
        source = str(row["selected_assistant"] or "") if "selected_assistant" in row.keys() else ""
        source_json = ""
        if source == "armd" and "armd_proposal_json" in row.keys():
            source_json = str(row["armd_proposal_json"] or "")
        elif source == "ta_sam" and "tasam_proposal_json" in row.keys():
            source_json = str(row["tasam_proposal_json"] or "")
        if selected and source_json and selected == source_json:
            exact_json_matches += 1
    return {
        "database_present": True,
        "decisions": len(rows),
        "armd_enabled": count_int("armd_enabled"),
        "armd_proposal_decisions": count_int("armd_proposal_present"),
        "armd_valid_proposal_decisions": count_int("armd_proposal_valid"),
        "armd_override_applied": count_int("armd_override_applied"),
        "armd_actuation_applied": count_int("armd_actuation_applied"),
        "tasam_proposal_decisions": count_int("tasam_proposal_present"),
        "tasam_valid_proposal_decisions": count_int("tasam_proposal_valid"),
        "tasam_actuation_applied": count_int("ta_sam_actuation_applied"),
        "arbitrated_decisions": count_int("advisor_arbitration_present"),
        "complete_assistant_pairs": count_int("advisor_proposal_pair_complete"),
        "selected_assistant_decisions": sum(
            1 for row in rows
            if "selected_assistant" in row.keys() and str(row["selected_assistant"] or "") in {"armd", "ta_sam", "joint"}
        ),
        "exactly_applied_decisions": count_int("proposal_applied_exactly"),
        "exact_proposal_json_matches": exact_json_matches,
        "external_last_resort_decisions": count_int("external_last_resort_used"),
        "rollbacks": count_int("control_trial_rollback"),
        "effective_algorithms": dict(sorted(algorithms.items())),
        "effective_sources": dict(sorted(sources.items())),
        "control_trial_modes": dict(sorted(modes.items())),
        "control_trial_reasons": dict(sorted(reasons.items())),
    }


def build_report(run_dir: Path) -> dict[str, Any]:
    network = read_network_run(run_dir)
    integrity = _decision_integrity(run_dir)
    criteria = {
        "real_pdcp_only": bool(network["valid_real_only"]),
        "no_proxy_rows": int(network["proxy_metric_rows"]) == 0,
        "strict_assistant_only": bool(network["strict_assistant_control"]),
        "armd_proposal_on_all_decisions": integrity.get("armd_proposal_decisions", 0) == integrity.get("decisions", 0) > 0,
        "tasam_proposal_on_all_decisions": integrity.get("tasam_proposal_decisions", 0) == integrity.get("decisions", 0) > 0,
        "rapp_arbitration_on_all_decisions": integrity.get("arbitrated_decisions", 0) == integrity.get("decisions", 0) > 0,
        "complete_assistant_pair_on_all_decisions": integrity.get("complete_assistant_pairs", 0) == integrity.get("decisions", 0) > 0,
        "single_winner_on_all_decisions": integrity.get("selected_assistant_decisions", 0) == integrity.get("decisions", 0) > 0,
        "winner_applied_exactly_on_all_decisions": integrity.get("exactly_applied_decisions", 0) == integrity.get("decisions", 0) > 0,
        "winner_json_matches_selected_on_all_decisions": integrity.get("exact_proposal_json_matches", 0) == integrity.get("decisions", 0) > 0,
        "no_rollbacks": integrity.get("rollbacks", 0) == 0,
        "no_live_allocator_or_heuristic": not any(
            key in {"fallback_after_tasam_error", "heuristic_baseline"}
            for key in integrity.get("effective_sources", {})
        ),
    }
    return {
        "schema": "greenran.tasam_operational_run.v1",
        "run_dir": str(run_dir.resolve()),
        "runtime": _runtime_flags(run_dir),
        "stage_counts": _stage_counts(run_dir),
        "decision_integrity": integrity,
        "network_metrics": network,
        "acceptance": {"criteria": criteria, "valid": all(criteria.values())},
        "interpretation": {
            "comparison_target": "rApp with ARMD + TA-SAM assistants active",
            "baseline_not_collected_in_this_run": True,
            "energy": "calibrated_ru_mmwave_power_model; direct hardware power meter unavailable",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = build_report(args.run_dir.resolve())
    args.output.resolve().parent.mkdir(parents=True, exist_ok=True)
    args.output.resolve().write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output.resolve()), "valid": report["acceptance"]["valid"], "criteria": report["acceptance"]["criteria"]}, ensure_ascii=False, indent=2))
    return 0 if report["acceptance"]["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
