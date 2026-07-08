#!/usr/bin/env python3
"""Live status for TA-SAM offline collection: consolidated + active round counts."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

from status_tasam_offline_growth import (
    fmt_age,
    fmt_num,
    load_json,
    resolve_output_root,
    round_artifact_status,
)
from run_tasam_article_offline_growth import existing_round_dirs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Show live TA-SAM offline collection counters")
    parser.add_argument("--output-root", default=None, help="Offline growth root; auto-detect latest when omitted")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    return parser


def active_round_status(output_root: Path) -> dict[str, Any] | None:
    rounds = existing_round_dirs(output_root)
    if not rounds:
        return None
    statuses = [round_artifact_status(round_dir) for round_dir in rounds]
    incomplete = [item for item in statuses if not item["complete"]]
    return incomplete[-1] if incomplete else statuses[-1]


def read_live_round_payload(round_status: dict[str, Any] | None) -> dict[str, Any]:
    empty = {
        "round_id": None,
        "db_path": None,
        "decision_counts": {"BLOCKED": 0, "ALLOWED": 0, "CONDITIONAL": 0},
        "extended_metrics_count": 0,
        "latest_decisions": [],
        "latest_metric": None,
    }
    if not round_status:
        return empty

    db_path = Path(round_status["round_dir"]) / "state" / "rapp_data_lake.db"
    if not db_path.exists():
        empty["round_id"] = round_status["round_id"]
        empty["db_path"] = str(db_path)
        return empty

    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        counts = {"BLOCKED": 0, "ALLOWED": 0, "CONDITIONAL": 0}
        for decision, count in cur.execute(
            "select decision, count(*) from decisions_history group by decision"
        ).fetchall():
            label = str(decision or "").upper()
            counts[label] = int(count or 0)

        latest_decisions = cur.execute(
            "select datetime, decision, reason from decisions_history order by rowid desc limit 5"
        ).fetchall()
        latest_metric = cur.execute(
            """
            select datetime, sim_time_s, throughput_kbps, latency_p95_us, cvar_per_ue_us, total_active_ues
            from extended_metrics
            order by rowid desc limit 1
            """
        ).fetchone()
        extended_metrics_count = int(
            cur.execute("select count(*) from extended_metrics").fetchone()[0]
        )
    finally:
        conn.close()

    return {
        "round_id": round_status["round_id"],
        "db_path": str(db_path),
        "decision_counts": counts,
        "extended_metrics_count": extended_metrics_count,
        "latest_decisions": latest_decisions,
        "latest_metric": latest_metric,
    }


def compute_payload(output_root: Path) -> dict[str, Any]:
    summary = load_json(output_root / "offline_collection_summary.json")
    growth_summary = load_json(output_root / "offline_growth_summary.json")
    live_round = active_round_status(output_root)
    live_payload = read_live_round_payload(live_round)
    local_transitions = int(summary.get("written_transitions_total", 0) or 0)
    local_decisions = summary.get("decision_counts_total") or {}
    return {
        "schema": "greenran.tasam_article_offline_live_status.v1",
        "datetime": time.strftime("%Y-%m-%d %H:%M:%S"),
        "output_root": str(output_root),
        "reference_root": growth_summary.get("reference_root", ""),
        "reference_transitions": int(growth_summary.get("reference_transitions", 0) or 0),
        "reference_decision_counts": growth_summary.get("reference_decision_counts") or {},
        "local_transitions": local_transitions,
        "local_decision_counts": local_decisions,
        "consolidated_transitions": int(growth_summary.get("current_transitions", 0) or 0) or local_transitions,
        "consolidated_decision_counts": growth_summary.get("effective_decision_counts_current") or growth_summary.get("decision_counts_current") or local_decisions,
        "active_round": live_round,
        "live_round": live_payload,
    }


def render(payload: dict[str, Any]) -> str:
    consolidated = payload.get("consolidated_decision_counts") or {}
    active = payload.get("active_round") or {}
    live = payload.get("live_round") or {}
    latest_metric = live.get("latest_metric")

    lines = [
        "=" * 72,
        f"  TA-SAM AO VIVO              {payload.get('datetime', '--')}",
        "=" * 72,
        f"  Run: {payload.get('output_root', '--')}",
        "",
        "  Consolidado",
        f"    transicoes:      {fmt_num(payload.get('consolidated_transitions', 0))}",
        f"    local:           {fmt_num(payload.get('local_transitions', 0))}",
        f"    referencia:      {fmt_num(payload.get('reference_transitions', 0))}",
        f"    BLOCKED:         {fmt_num(consolidated.get('BLOCKED', 0))}",
        f"    ALLOWED:         {fmt_num(consolidated.get('ALLOWED', 0))}",
        f"    CONDITIONAL:     {fmt_num(consolidated.get('CONDITIONAL', 0))}",
        "",
        "  Rodada Ativa",
        f"    round:           {live.get('round_id') or '--'}",
        f"    export:          {'pronto' if active.get('complete') else 'pendente' if active else '--'}",
        f"    ext_metrics:     {fmt_num(live.get('extended_metrics_count', 0))}",
        f"    BLOCKED:         {fmt_num((live.get('decision_counts') or {}).get('BLOCKED', 0))}",
        f"    ALLOWED:         {fmt_num((live.get('decision_counts') or {}).get('ALLOWED', 0))}",
        f"    CONDITIONAL:     {fmt_num((live.get('decision_counts') or {}).get('CONDITIONAL', 0))}",
    ]
    if active:
        lines.extend(
            [
                f"    trace age:       {fmt_age((active.get('trace_status') or {}).get('newest_mtime', 0.0))}",
            ]
        )
    if latest_metric:
        lines.extend(
            [
                "",
                "  Ultima Metrica",
                f"    datetime:        {latest_metric[0]}",
                f"    sim_time_s:      {latest_metric[1]}",
                f"    throughput_kbps: {latest_metric[2]}",
                f"    latency_p95_us:  {latest_metric[3]}",
                f"    cvar_per_ue_us:  {latest_metric[4]}",
                f"    total_active_ues:{latest_metric[5]}",
            ]
        )
    latest_decisions = live.get("latest_decisions") or []
    if latest_decisions:
        lines.extend(["", "  Ultimas Decisoes"])
        for item in latest_decisions:
            lines.append(f"    {item[0]} | {item[1]} | {item[2]}")
    lines.append("=" * 72)
    return "\n".join(lines)


def main() -> int:
    args = build_parser().parse_args()
    output_root = resolve_output_root(args.output_root)
    payload = compute_payload(output_root)
    if args.json:
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0
    print(render(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
