#!/usr/bin/env python3
"""Freeze and summarize a completed TA-SAM online rollout window.

The source database remains untouched and the runtime may continue writing to it.
Only the requested decision window is copied to compact, immutable evidence files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any


def rows_as_dicts(conn: sqlite3.Connection, query: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    return [dict(row) for row in conn.execute(query, params).fetchall()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> str:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def numeric_mean(rows: list[dict[str, Any]], key: str) -> float | None:
    values = []
    for row in rows:
        try:
            value = float(row.get(key))
        except (TypeError, ValueError):
            continue
        values.append(value)
    return mean(values) if values else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--window-size", type=int, default=100)
    parser.add_argument("--start-id", type=int)
    args = parser.parse_args()

    run_dir = args.run_dir.resolve()
    db_path = run_dir / "rapp_data_lake.db"
    if not db_path.is_file():
        raise SystemExit(f"Banco não encontrado: {db_path}")
    state = {}
    state_path = run_dir / "online_state.json"
    if state_path.is_file():
        state = json.loads(state_path.read_text(encoding="utf-8"))

    with sqlite3.connect(str(db_path)) as conn:
        decision_count = int(conn.execute("select count(*) from decisions_history").fetchone()[0] or 0)
        start_id = args.start_id
        if start_id is None:
            stage_started = int(state.get("stage_started_decisions", 0) or 0)
            start_id = stage_started + 1 if state.get("stage") == "full" else max(1, decision_count - args.window_size + 1)
        end_id = start_id + args.window_size - 1
        if decision_count < end_id:
            raise SystemExit(f"Janela incompleta: requerida até {end_id}, banco tem {decision_count}")

        decisions = rows_as_dicts(
            conn,
            "select * from decisions_history where id between ? and ? order by id",
            (start_id, end_id),
        )
        metrics = rows_as_dicts(
            conn,
            "select * from extended_metrics where id between ? and ? order by id",
            (start_id, end_id),
        )
        resources = rows_as_dicts(
            conn,
            "select * from resource_allocation_history where id between ? and ? order by id",
            (start_id, end_id),
        )

    if len(decisions) != args.window_size:
        raise SystemExit(f"Janela de decisões incompleta: {len(decisions)}/{args.window_size}")

    output_dir = (args.output_dir or run_dir / "frozen_evidence" / f"full_{start_id}_{end_id}").resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    hashes = {
        "decisions.jsonl": write_jsonl(output_dir / "decisions.jsonl", decisions),
        "metrics.jsonl": write_jsonl(output_dir / "metrics.jsonl", metrics),
        "resources.jsonl": write_jsonl(output_dir / "resources.jsonl", resources),
    }

    floor_violations = sum(
        1 for row in decisions
        if str(row.get("floor_feasible", "1")) in {"0", "0.0", "False"}
        or int(row.get("per_ue_floor_violation_count", 0) or 0) > 0
    )
    proxy_rows = sum(float(row.get("proxy_latency_sample_count", 0) or 0) > 0 for row in metrics)
    non_pdcp_rows = sum(str(row.get("collector_mode", "")) != "pdcp_real" for row in metrics)
    summary = {
        "schema": "greenran.tasam_online_frozen_window.v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_run": str(run_dir),
        "source_database": str(db_path),
        "window": {"start_decision_id": start_id, "end_decision_id": end_id, "decisions": len(decisions)},
        "rollout_at_freeze": {
            "stage": state.get("stage"),
            "fraction": state.get("rollout_fraction"),
            "active_checkpoint": state.get("active_checkpoint"),
        },
        "assistant_control": {
            "armd_proposals": sum(bool(row.get("armd_proposal_present")) for row in decisions),
            "tasam_proposals": sum(bool(row.get("tasam_proposal_present")) for row in decisions),
            "judge_arbitrations": sum(bool(row.get("advisor_arbitration_present")) for row in decisions),
            "exact_applied": sum(bool(row.get("proposal_applied_exactly")) for row in decisions),
            "ta_sam_actuation": sum(bool(row.get("ta_sam_actuation_applied")) for row in decisions),
            "selected_assistant": {
                name: sum(str(row.get("selected_assistant", "")) == name for row in decisions)
                for name in ("armd", "ta_sam", "joint", "none")
            },
        },
        "quality_gate": {
            "decision_rows": len(decisions) == args.window_size,
            "metric_rows": len(metrics) == args.window_size,
            "resource_rows": len(resources) == args.window_size,
            "collector_mode_pdcp_real": non_pdcp_rows == 0 and bool(metrics),
            "proxy_rows_zero": proxy_rows == 0,
            "floor_violations_zero": floor_violations == 0,
            "rollback": bool(state.get("rollback_reason")),
        },
        "metrics": {
            "mean_p95_us": numeric_mean(metrics, "latency_p95_per_ue_us"),
            "mean_cvar_us": numeric_mean(metrics, "cvar_per_ue_us"),
            "mean_throughput_kbps": numeric_mean(metrics, "throughput_kbps"),
            "mean_packet_loss": numeric_mean(metrics, "global_packet_loss_rate"),
            "real_pdcp_rows": len(metrics) - non_pdcp_rows,
            "proxy_rows": proxy_rows,
        },
        "resource": {
            "mean_ran_allocation": numeric_mean(resources, "r_ran"),
            "mean_ai_allocation": numeric_mean(resources, "r_ai"),
            "mean_utilization_ratio": numeric_mean(resources, "utilization_ratio"),
        },
        "artifacts": {"sha256": hashes},
        "comparison": {
            "status": "requires_matched_rapp_only_run",
            "baseline": "rApp-only, same topology/event profile/duration and real-PDCP alignment",
            "note": "A baseline de outra rodada não é usado como ganho definitivo; a comparação pareada deve usar a mesma janela experimental.",
        },
    }
    summary_path = output_dir / "frozen_window_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (output_dir / "FROZEN").write_text("janela imutável por convenção; fonte ainda preservada no run-dir\n", encoding="utf-8")
    print(json.dumps({"output_dir": str(output_dir), "summary": str(summary_path), "window": summary["window"], "quality_gate": summary["quality_gate"], "comparison": summary["comparison"]}, indent=2, ensure_ascii=False))
    return 0 if all(summary["quality_gate"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
