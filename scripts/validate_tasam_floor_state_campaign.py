#!/usr/bin/env python3
"""Validate a paired floor-policy campaign for a target runtime state."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


VALID_STATES = {"CONDITIONAL", "BLOCKED"}


def _count_decisions(db_path: Path) -> tuple[int, dict[str, int], dict[str, int]]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        columns = {row[1] for row in conn.execute("PRAGMA table_info(decisions_history)")}
        tracked = [
            "armd_enabled",
            "armd_proposal_present",
            "armd_proposal_valid",
            "tasam_proposal_present",
            "tasam_proposal_valid",
            "advisor_proposal_pair_complete",
            "proposal_applied_exactly",
            "ta_sam_actuation_applied",
            "assistant_only_failure",
        ]
        selected = ["decision", *(column for column in tracked if column in columns)]
        rows = conn.execute(
            f"SELECT {', '.join(selected)} FROM decisions_history"
        ).fetchall()
    counts: dict[str, int] = {}
    integrity = {
        "armd_enabled": 0,
        "armd_proposal_present": 0,
        "armd_proposal_valid": 0,
        "tasam_proposal_present": 0,
        "tasam_proposal_valid": 0,
        "proposal_pair_complete": 0,
        "proposal_applied_exactly": 0,
        "tasam_actuation": 0,
        "assistant_only_failure": 0,
    }
    for row in rows:
        state = str(row["decision"] or "UNKNOWN").upper()
        counts[state] = counts.get(state, 0) + 1
        for column, target in (
            ("armd_enabled", "armd_enabled"),
            ("armd_proposal_present", "armd_proposal_present"),
            ("armd_proposal_valid", "armd_proposal_valid"),
            ("tasam_proposal_present", "tasam_proposal_present"),
            ("tasam_proposal_valid", "tasam_proposal_valid"),
            ("advisor_proposal_pair_complete", "proposal_pair_complete"),
            ("proposal_applied_exactly", "proposal_applied_exactly"),
            ("ta_sam_actuation_applied", "tasam_actuation"),
            ("assistant_only_failure", "assistant_only_failure"),
        ):
            if column in row.keys():
                integrity[target] += int(row[column] or 0)
    return len(rows), counts, integrity


def _judge_feedback_summary(db_path: Path) -> dict[str, object]:
    """Summarize delayed quality credit without requiring TA-SAM to win."""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='judge_outcome_history'"
        ).fetchone()
        if not exists:
            return {
                "rows": 0,
                "observed": 0,
                "winner_counts": {},
                "credit_assignment_counts": {},
                "armd_credit_mean": 0.0,
                "tasam_credit_mean": 0.0,
            }
        rows = conn.execute(
            "SELECT selected_assistant, observed, outcome_reward, armd_credit, "
            "tasam_credit, credit_assignment FROM judge_outcome_history"
        ).fetchall()
    winner_counts: dict[str, int] = {}
    assignment_counts: dict[str, int] = {}
    armd_credits: list[float] = []
    tasam_credits: list[float] = []
    observed = 0
    for row in rows:
        winner = str(row["selected_assistant"] or "none")
        winner_counts[winner] = winner_counts.get(winner, 0) + 1
        assignment = str(row["credit_assignment"] or "unknown")
        assignment_counts[assignment] = assignment_counts.get(assignment, 0) + 1
        if int(row["observed"] or 0):
            observed += 1
            armd_credits.append(float(row["armd_credit"] or 0.0))
            tasam_credits.append(float(row["tasam_credit"] or 0.0))
    return {
        "rows": len(rows),
        "observed": observed,
        "winner_counts": winner_counts,
        "credit_assignment_counts": assignment_counts,
        "armd_credit_mean": sum(armd_credits) / max(len(armd_credits), 1),
        "tasam_credit_mean": sum(tasam_credits) / max(len(tasam_credits), 1),
    }


def _resource_audit(db_path: Path, target_state: str) -> dict[str, int]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT allocation_state, reinforcement_ran, reinforcement_ai, "
            "per_ue_floor_violation_count FROM resource_allocation_history"
        ).fetchall()

    floor_violations = 0
    normal_reinforcement = 0
    critical_reinforcement = 0
    target_rows = 0
    for row in rows:
        state = str(row["allocation_state"] or "UNKNOWN").upper()
        reinforcement = float(row["reinforcement_ran"] or 0.0) + float(row["reinforcement_ai"] or 0.0)
        if state == target_state:
            target_rows += 1
        if int(row["per_ue_floor_violation_count"] or 0) > 0:
            floor_violations += 1
        if state in {"ALLOWED", "CONDITIONAL"} and reinforcement > 1e-12:
            normal_reinforcement += 1
        if state in {"BLOCKED", "CRITICAL"} and reinforcement > 1e-12:
            critical_reinforcement += 1
    return {
        "allocation_rows": len(rows),
        "target_allocation_rows": target_rows,
        "floor_violation_rows": floor_violations,
        "normal_reinforcement_rows": normal_reinforcement,
        "critical_reinforcement_rows": critical_reinforcement,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--target-state", required=True, choices=sorted(VALID_STATES))
    parser.add_argument("--minimum-target-decisions", type=int, default=100)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    repetitions = []
    total_target = 0
    total_decisions = 0
    totals = {
        "armd_proposals_present": 0,
        "armd_proposals_valid": 0,
        "tasam_proposals_present": 0,
        "tasam_proposals_valid": 0,
        "proposal_pairs_complete": 0,
        "proposals_applied_exactly": 0,
        "tasam_actuation": 0,
        "assistant_only_failures": 0,
        "floor_violation_rows": 0,
        "normal_reinforcement_rows": 0,
        "critical_reinforcement_rows": 0,
    }

    for rep_dir in sorted(args.root.glob("rep_*")):
        if not rep_dir.is_dir():
            continue
        baseline_db = rep_dir / "baseline" / "rapp_data_lake.db"
        assistant_db = rep_dir / "assistant" / "rapp_data_lake.db"
        if not baseline_db.exists() or not assistant_db.exists():
            continue
        baseline_n, baseline_states, _ = _count_decisions(baseline_db)
        assistant_n, assistant_states, integrity = _count_decisions(assistant_db)
        resource = _resource_audit(assistant_db, args.target_state)
        feedback = _judge_feedback_summary(assistant_db)
        baseline_target = baseline_states.get(args.target_state, 0)
        assistant_target = assistant_states.get(args.target_state, 0)
        target_count = min(baseline_target, assistant_target)
        repetitions.append({
            "repetition": int(rep_dir.name.split("_")[-1]),
            "baseline_decisions": baseline_n,
            "assistant_decisions": assistant_n,
            "baseline_states": baseline_states,
            "assistant_states": assistant_states,
            "baseline_target_decisions": baseline_target,
            "assistant_target_decisions": assistant_target,
            "paired_target_decisions": target_count,
            "assistant_integrity": integrity,
            "judge_feedback": feedback,
            "resource_audit": resource,
        })
        total_decisions += assistant_n
        total_target += target_count
        totals["armd_proposals_present"] += integrity["armd_proposal_present"]
        totals["armd_proposals_valid"] += integrity["armd_proposal_valid"]
        totals["tasam_proposals_present"] += integrity["tasam_proposal_present"]
        totals["tasam_proposals_valid"] += integrity["tasam_proposal_valid"]
        totals["proposal_pairs_complete"] += integrity["proposal_pair_complete"]
        totals["proposals_applied_exactly"] += integrity["proposal_applied_exactly"]
        totals["tasam_actuation"] += integrity["tasam_actuation"]
        totals["assistant_only_failures"] += integrity["assistant_only_failure"]
        totals["floor_violation_rows"] += resource["floor_violation_rows"]
        totals["normal_reinforcement_rows"] += resource["normal_reinforcement_rows"]
        totals["critical_reinforcement_rows"] += resource["critical_reinforcement_rows"]

    baseline_target_total = sum(item["baseline_target_decisions"] for item in repetitions)
    assistant_target_total = sum(item["assistant_target_decisions"] for item in repetitions)
    target_observed = baseline_target_total >= args.minimum_target_decisions
    assistants_valid = (
        total_decisions > 0
        and totals["armd_proposals_present"] == total_decisions
        and totals["armd_proposals_valid"] == total_decisions
        and totals["tasam_proposals_present"] == total_decisions
        and totals["tasam_proposals_valid"] == total_decisions
        and totals["proposal_pairs_complete"] == total_decisions
        and totals["proposals_applied_exactly"] == total_decisions
        and totals["assistant_only_failures"] == 0
    )
    floor_policy_valid = totals["floor_violation_rows"] == 0 and totals["normal_reinforcement_rows"] == 0
    critical_path_observed = (
        args.target_state != "BLOCKED"
        or assistant_target_total > 0
        and totals["critical_reinforcement_rows"] > 0
    )
    payload = {
        "schema": "greenran.tasam_floor_state_campaign.v1",
        "target_state": args.target_state,
        "repetition_count": len(repetitions),
        "total_assistant_decisions": total_decisions,
        "baseline_target_decisions": baseline_target_total,
        "assistant_target_decisions": assistant_target_total,
        "paired_target_decisions": total_target,
        "minimum_target_decisions": args.minimum_target_decisions,
        "target_state_observed": target_observed,
        "assistants_valid": assistants_valid,
        "floor_policy_valid": floor_policy_valid,
        "critical_path_observed": critical_path_observed,
        "tasam_actuation_observed": totals["tasam_actuation"] > 0,
        "judge_credit_observed": sum(
            int(item["judge_feedback"].get("observed", 0)) for item in repetitions
        ) > 0,
        "approved": bool(target_observed and assistants_valid and floor_policy_valid and critical_path_observed),
        "totals": totals,
        "repetitions": repetitions,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["approved"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
