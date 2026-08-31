#!/usr/bin/env python3
"""Fail-fast readiness check for a real-PDCP TA-SAM stress run."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from pathlib import Path


def _count(conn: sqlite3.Connection, query: str) -> int:
    return int(conn.execute(query).fetchone()[0] or 0)


def validate(state_dir: Path, mode: str, target: int, min_real_pdcp_rows: int | None = None) -> dict:
    db_path = state_dir / "rapp_data_lake.db"
    if not db_path.is_file():
        raise RuntimeError(f"Data Lake ausente: {db_path}")
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        decisions = _count(conn, "SELECT COUNT(*) FROM decisions_history")
        metrics = _count(conn, "SELECT COUNT(*) FROM extended_metrics")
        real_metrics = _count(
            conn,
            """SELECT COUNT(*) FROM extended_metrics
               WHERE collector_mode='pdcp_real'
                 AND COALESCE(proxy_latency_sample_count, 0)=0
                 AND COALESCE(pdcp_stale, 0)=0""",
        )
        invalid_metrics = metrics - real_metrics
        minimum_real_rows = target if min_real_pdcp_rows is None else max(1, int(min_real_pdcp_rows))
        minimum_real_rows = min(minimum_real_rows, target)
        result = {
            "state_dir": str(state_dir.resolve()),
            "mode": mode,
            "target_decisions": target,
            "target_real_pdcp_rows": target,
            "minimum_real_pdcp_rows": minimum_real_rows,
            "decisions": decisions,
            "extended_metric_rows": metrics,
            "real_pdcp_rows": real_metrics,
            "invalid_or_proxy_rows": invalid_metrics,
            "e2_required": os.environ.get("GREENRAN_ALLOW_PDCP_WITHOUT_E2", "0").lower() not in {"1", "true", "yes", "on"},
            "ready": False,
            "failures": [],
        }
        if decisions != target:
            result["failures"].append(f"decision_count={decisions}, expected={target}")
        if metrics < minimum_real_rows:
            suffix = f"expected={target}" if minimum_real_rows == target else f"minimum={minimum_real_rows}"
            result["failures"].append(f"extended_metric_rows={metrics}, {suffix}")
        if real_metrics < minimum_real_rows:
            suffix = f"expected={target}" if minimum_real_rows == target else f"minimum={minimum_real_rows}"
            result["failures"].append(f"real_pdcp_rows={real_metrics}, {suffix}")
        if invalid_metrics != 0:
            result["failures"].append(f"invalid_or_proxy_metric_rows={invalid_metrics}")

        columns = {row[1] for row in conn.execute("PRAGMA table_info(decisions_history)").fetchall()}
        required = {
            "armd_enabled",
            "ta_sam_actuation_applied",
            "control_trial_mode",
            "effective_policy_algorithm",
            "effective_policy_source",
        }
        if mode == "joint":
            if not required.issubset(columns):
                result["failures"].append("assistant_control_columns_missing")
            elif {
                "armd_proposal_present",
                "armd_proposal_valid",
                "tasam_proposal_present",
                "tasam_proposal_valid",
                "rapp_judge_mode",
                "selected_assistant",
                "proposal_applied_exactly",
            }.issubset(columns):
                # Current architecture: both assistants must provide a valid
                # complete proposal; the rApp chooses exactly one winner and
                # that package is applied verbatim.  TA-SAM does not need to
                # win every decision, and its legacy ``tasam_valid`` gate is
                # not the proposal validity contract.
                rows = conn.execute(
                    """SELECT armd_enabled, armd_proposal_present,
                              armd_proposal_valid, tasam_proposal_present,
                              tasam_proposal_valid, control_trial_mode,
                              rapp_judge_mode, selected_assistant,
                              proposal_applied_exactly, effective_policy_source
                       FROM decisions_history"""
                ).fetchall()
                bad = sum(
                    not (
                        int(row["armd_enabled"] or 0) == 1
                        and int(row["armd_proposal_present"] or 0) == 1
                        and int(row["armd_proposal_valid"] or 0) == 1
                        and int(row["tasam_proposal_present"] or 0) == 1
                        and int(row["tasam_proposal_valid"] or 0) == 1
                        and str(row["control_trial_mode"] or "") in {"assistant_judge", "assistant_only_control"}
                        and str(row["rapp_judge_mode"] or "") == "rapp_judge_v1"
                        and str(row["selected_assistant"] or "") in {"armd", "ta_sam", "joint"}
                        and int(row["proposal_applied_exactly"] or 0) == 1
                        and str(row["effective_policy_source"] or "") not in {
                            "fallback_after_tasam_error",
                            "heuristic_baseline",
                            "live_allocator",
                        }
                    )
                    for row in rows
                )
                result["assistant_invalid_decisions"] = bad
                if bad:
                    result["failures"].append(f"assistant_invalid_decisions={bad}")
            else:
                # Legacy databases created before proposal-level arbitration.
                rows = conn.execute(
                    """SELECT armd_enabled, ta_sam_actuation_applied,
                              control_trial_mode, effective_policy_algorithm,
                              effective_policy_source
                       FROM decisions_history"""
                ).fetchall()
                bad = sum(
                    not (
                        int(row["armd_enabled"] or 0) == 1
                        and int(row["ta_sam_actuation_applied"] or 0) == 1
                        and str(row["control_trial_mode"] or "") == "assistant_only_control"
                        and str(row["effective_policy_algorithm"] or "") == "TA-SAM-MARL"
                        and str(row["effective_policy_source"] or "") not in {"fallback_after_tasam_error", "heuristic_baseline"}
                    )
                    for row in rows
                )
                result["assistant_invalid_decisions"] = bad
                if bad:
                    result["failures"].append(f"assistant_invalid_decisions={bad}")
        result["ready"] = not result["failures"]
        return result
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--mode", choices=("baseline", "joint"), required=True)
    parser.add_argument("--target", type=int, default=65)
    parser.add_argument("--min-real-pdcp-rows", type=int, default=None)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = validate(args.state_dir.resolve(), args.mode, args.target, args.min_real_pdcp_rows)
    except (OSError, sqlite3.Error, RuntimeError) as exc:
        result = {"ready": False, "failures": [str(exc)], "state_dir": str(args.state_dir.resolve())}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("ready") else 1


if __name__ == "__main__":
    raise SystemExit(main())
