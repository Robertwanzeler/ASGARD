#!/usr/bin/env python3
"""Human-readable status for TA-SAM offline growth collection."""

from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from run_tasam_article_offline_growth import (
    DEFAULT_OUTPUT,
    EXPECTED_TRACE_FILES,
    count_trace_lines,
    existing_round_dirs,
    load_round_payload,
)


ROOT = Path(__file__).resolve().parents[1]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Show TA-SAM offline growth status")
    parser.add_argument("--output-root", default=None, help="Offline growth root; auto-detect latest when omitted")
    parser.add_argument("--target-transitions", type=int, default=0, help="Override target transition count")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    return parser


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def resolve_output_root(raw: str | None) -> Path:
    if raw:
        return Path(raw)

    env_root = os.environ.get("GREENRAN_TASAM_OFFLINE_OUTPUT_ROOT", "").strip()
    if env_root:
        return Path(env_root)

    candidates: list[Path] = []
    runs_dir = ROOT / "runs"
    if runs_dir.exists():
        candidates.extend(
            sorted(
                (
                    path
                    for path in runs_dir.iterdir()
                    if path.is_dir()
                    and path.name.startswith("tasam_article_offline_collection")
                    and (
                        (path / "offline_collection_summary.json").exists()
                        or any(path.glob("round_*"))
                    )
                ),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
        )

    if candidates:
        return candidates[0]
    return DEFAULT_OUTPUT


def fmt_num(value: Any) -> str:
    try:
        return f"{int(value):,}".replace(",", ".")
    except Exception:
        return "0"


def fmt_float(value: Any, digits: int = 1) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except Exception:
        return "0.0"


def fmt_age(ts: float | None) -> str:
    if ts is None or ts <= 0:
        return "--"
    delta = max(0.0, time.time() - float(ts))
    if delta < 60:
        return f"{delta:.1f}s"
    if delta < 3600:
        return f"{delta / 60:.1f}m"
    return f"{delta / 3600:.1f}h"


def fmt_etime(seconds: Any) -> str:
    try:
        total = max(0, int(float(seconds)))
    except Exception:
        return "--"
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours >= 24:
        days, hours = divmod(hours, 24)
        return f"{days}d{hours:02d}h"
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def progress_bar(value: int, total: int, width: int = 26) -> str:
    value = max(0, int(value))
    total = max(1, int(total))
    pct = min(100.0, (value / total) * 100.0)
    fill = int((pct / 100.0) * width)
    return "[" + "#" * fill + "." * (width - fill) + f"] {pct:5.1f}%"


def sqlite_round_counts(db_path: Path) -> dict[str, int]:
    tables = {
        "extended_metrics": 0,
        "decisions_history": 0,
        "marl_global_state_history": 0,
        "marl_du_state_history": 0,
    }
    if not db_path.exists():
        return tables
    conn = sqlite3.connect(str(db_path))
    try:
        for table in tables:
            try:
                tables[table] = int(conn.execute(f"select count(*) from {table}").fetchone()[0])
            except sqlite3.DatabaseError:
                tables[table] = 0
    finally:
        conn.close()
    return tables


def summarize_trace_files(trace_dir: Path) -> dict[str, Any]:
    files: dict[str, dict[str, Any]] = {}
    all_present = True
    newest_mtime = 0.0
    for name in EXPECTED_TRACE_FILES:
        path = trace_dir / name
        exists = path.exists()
        info = {
            "exists": exists,
            "size_bytes": int(path.stat().st_size) if exists else 0,
            "mtime": float(path.stat().st_mtime) if exists else 0.0,
        }
        files[name] = info
        all_present = all_present and exists
        newest_mtime = max(newest_mtime, info["mtime"])

    pdcp_ok = all(files[name]["exists"] for name in ("DlPdcpStats.txt", "UlPdcpStats.txt"))
    rlc_ok = all(files[name]["exists"] for name in ("DlRlcStats.txt", "UlRlcStats.txt"))
    e2_ok = all(
        files[name]["exists"]
        for name in ("DlE2PdcpStats.txt", "DlE2RlcStats.txt", "UlE2PdcpStats.txt", "UlE2RlcStats.txt")
    )
    return {
        "all_present": all_present,
        "pdcp_ok": pdcp_ok,
        "rlc_ok": rlc_ok,
        "e2_ok": e2_ok,
        "newest_mtime": newest_mtime,
        "files": files,
    }


def round_artifact_status(round_dir: Path) -> dict[str, Any]:
    export_dir = round_dir / "export"
    state_dir = round_dir / "state"
    trace_jsonl = export_dir / "tasam_article_trace.jsonl"
    summary_json = export_dir / "tasam_article_export_summary.json"
    export_summary = load_json(summary_json)
    trace_lines = count_trace_lines(trace_jsonl)
    trace_status = summarize_trace_files(state_dir / "ns3_traces")
    db_counts = sqlite_round_counts(state_dir / "rapp_data_lake.db")
    complete = trace_jsonl.exists() and summary_json.exists()
    return {
        "round_id": round_dir.name,
        "round_dir": str(round_dir),
        "complete": complete,
        "trace_jsonl": str(trace_jsonl),
        "summary_json": str(summary_json),
        "written_transitions": int(export_summary.get("written_transitions", trace_lines) or 0),
        "candidate_snapshots": int(export_summary.get("candidate_snapshots", 0) or 0),
        "trace_lines": trace_lines,
        "trace_status": trace_status,
        "db_counts": db_counts,
        "export_mtime": float(summary_json.stat().st_mtime) if summary_json.exists() else 0.0,
    }


def read_ps_entries() -> list[dict[str, Any]]:
    try:
        result = subprocess.run(
            ["ps", "-eo", "pid,etimes,%cpu,%mem,args", "--no-headers"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except Exception:
        return []

    entries: list[dict[str, Any]] = []
    for raw in result.stdout.splitlines():
        parts = raw.strip().split(None, 4)
        if len(parts) < 5:
            continue
        pid, etimes, cpu, mem, args = parts
        entries.append(
            {
                "pid": pid,
                "etimes": etimes,
                "cpu": cpu,
                "mem": mem,
                "args": args,
            }
        )
    return entries


def pick_process(entries: list[dict[str, Any]], patterns: tuple[str, ...], preferred_paths: tuple[str, ...] = ()) -> dict[str, Any] | None:
    matches = [entry for entry in entries if all(pattern in entry["args"] for pattern in patterns)]
    if not matches:
        return None
    if preferred_paths:
        for entry in matches:
            if any(path and path in entry["args"] for path in preferred_paths):
                return entry
    return matches[0]


def process_summary(entry: dict[str, Any] | None) -> str:
    if not entry:
        return "--"
    return (
        f"PID {entry['pid']}  {fmt_etime(entry['etimes'])}  "
        f"CPU {entry['cpu']}%  MEM {entry['mem']}%"
    )


def determine_phase(active_round: dict[str, Any] | None, processes: dict[str, dict[str, Any] | None]) -> str:
    if processes.get("growth") or processes.get("collector_runner"):
        if processes.get("ns3"):
            return "coletando"
        return "finalizando"
    if active_round and not active_round.get("complete"):
        return "pendente_export"
    return "parada"


def compute_status_payload(output_root: Path, target_override: int = 0, ps_entries: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rounds = existing_round_dirs(output_root)
    round_statuses = [round_artifact_status(round_dir) for round_dir in rounds]
    complete_rounds = [item for item in round_statuses if item["complete"]]
    incomplete_rounds = [item for item in round_statuses if not item["complete"]]
    active_round = incomplete_rounds[-1] if incomplete_rounds else None
    latest_complete = complete_rounds[-1] if complete_rounds else None
    reference_round = active_round or latest_complete

    growth_summary = load_json(output_root / "offline_growth_summary.json")
    collection_summary = load_json(output_root / "offline_collection_summary.json")
    consolidated_trace = output_root / "tasam_article_trace.jsonl"

    local_consolidated_count = count_trace_lines(consolidated_trace)
    if local_consolidated_count <= 0:
        local_consolidated_count = int(collection_summary.get("written_transitions_total", 0) or 0)
    if local_consolidated_count <= 0:
        local_consolidated_count = sum(int(item.get("written_transitions", 0) or 0) for item in complete_rounds)

    target = int(target_override or growth_summary.get("target_transitions", 0) or 0)
    decision_targets = growth_summary.get("decision_targets") or {}
    reference_transitions = int(growth_summary.get("reference_transitions", 0) or 0)
    consolidated_count = int(growth_summary.get("current_transitions", 0) or 0)
    if consolidated_count <= 0:
        consolidated_count = local_consolidated_count + reference_transitions
    local_decision_counts = collection_summary.get("decision_counts_total") or growth_summary.get("local_decision_counts_current") or {}
    reference_decision_counts = growth_summary.get("reference_decision_counts") or {}
    decision_counts = growth_summary.get("effective_decision_counts_current") or growth_summary.get("decision_counts_current") or local_decision_counts
    decision_remaining = growth_summary.get("decision_targets_remaining") or {}
    remaining = max(0, target - consolidated_count) if target > 0 else 0
    average_per_round = (local_consolidated_count / len(complete_rounds)) if complete_rounds else 0.0
    est_rounds_remaining = int(math.ceil(remaining / average_per_round)) if target > 0 and average_per_round > 0 else 0

    ps_entries = ps_entries if ps_entries is not None else read_ps_entries()
    preferred_paths = tuple(
        str(Path(reference_round["round_dir"]) / "state")
        for reference_round in ([reference_round] if reference_round else [])
    )
    processes = {
        "growth": pick_process(ps_entries, ("run_tasam_article_offline_growth.py",), (str(output_root),)),
        "collector_runner": pick_process(ps_entries, ("run_tasam_article_offline_collection.py",), (str(output_root),)),
        "collector": pick_process(ps_entries, ("csv_to_metrics.py",), preferred_paths),
        "rapp": pick_process(ps_entries, ("rapp_orchestrator.py",), preferred_paths),
        "ns3": pick_process(ps_entries, ("Energy_saving_with_cell_utilization_scenario-default",), preferred_paths),
    }
    phase = determine_phase(active_round, processes)

    return {
        "schema": "greenran.tasam_article_offline_growth_status.v1",
        "timestamp": int(time.time()),
        "datetime": time.strftime("%Y-%m-%d %H:%M:%S"),
        "output_root": str(output_root),
        "phase": phase,
        "collecting": phase in {"coletando", "finalizando"},
        "active_round": active_round,
        "latest_complete_round": latest_complete,
        "reference_round": reference_round,
        "complete_round_count": len(complete_rounds),
        "incomplete_round_count": len(incomplete_rounds),
        "consolidated_trace_jsonl": str(consolidated_trace),
        "local_consolidated_transitions": local_consolidated_count,
        "reference_root": growth_summary.get("reference_root", ""),
        "reference_transitions": reference_transitions,
        "consolidated_transitions": consolidated_count,
        "target_transitions": target,
        "remaining_transitions": remaining,
        "decision_targets": decision_targets,
        "local_decision_counts": local_decision_counts,
        "reference_decision_counts": reference_decision_counts,
        "decision_counts": decision_counts,
        "decision_remaining": decision_remaining,
        "average_transitions_per_round": average_per_round,
        "estimated_rounds_remaining": est_rounds_remaining,
        "collection_phase": growth_summary.get("collection_phase", ""),
        "active_collection_event_profile": growth_summary.get("active_collection_event_profile", ""),
        "processes": {key: process_summary(value) for key, value in processes.items()},
        "process_entries": processes,
        "growth_summary": growth_summary,
        "collection_summary": collection_summary,
    }


def render_status(payload: dict[str, Any]) -> str:
    reference = payload.get("reference_round") or {}
    latest_complete = payload.get("latest_complete_round") or {}
    active_round = payload.get("active_round") or {}
    target = int(payload.get("target_transitions", 0) or 0)
    current = int(payload.get("consolidated_transitions", 0) or 0)
    local_current = int(payload.get("local_consolidated_transitions", 0) or 0)
    reference_total = int(payload.get("reference_transitions", 0) or 0)
    remaining = int(payload.get("remaining_transitions", 0) or 0)
    progress = progress_bar(current, target) if target > 0 else "--"
    average = float(payload.get("average_transitions_per_round", 0.0) or 0.0)
    decision_targets = payload.get("decision_targets") or {}
    local_decision_counts = payload.get("local_decision_counts") or {}
    reference_decision_counts = payload.get("reference_decision_counts") or {}
    decision_counts = payload.get("decision_counts") or {}
    decision_remaining = payload.get("decision_remaining") or {}
    trace_status = (reference.get("trace_status") or {}) if reference else {}
    db_counts = reference.get("db_counts") or {}

    lines = [
        "=" * 72,
        f"  TA-SAM COLETA OFFLINE        {payload.get('datetime', '--')}",
        "=" * 72,
        f"  Run: {payload.get('output_root', '--')}",
        "",
        "  Coleta",
        f"    status:          {'ATIVA' if payload.get('collecting') else 'PARADA'}",
        f"    fase:            {payload.get('phase', '--')}",
        f"    rodada ativa:    {active_round.get('round_id', '--') if active_round else '--'}",
        f"    ultima valida:   {latest_complete.get('round_id', '--') if latest_complete else '--'}",
        f"    rodadas validas: {fmt_num(payload.get('complete_round_count', 0))}",
        f"    fase controle:   {payload.get('collection_phase', '--') or '--'}",
        f"    perfil:          {payload.get('active_collection_event_profile', '--') or '--'}",
        "",
        "  Progresso",
        f"    meta:            {fmt_num(target) if target > 0 else '--'}",
        f"    consolidado:     {fmt_num(current)}",
        f"    run local:       {fmt_num(local_current)}",
        f"    referencia:      {fmt_num(reference_total)}",
        f"    falta:           {fmt_num(remaining) if target > 0 else '--'}",
        f"    media/rodada:    {fmt_float(average, 1)}",
        f"    rodadas est.:    {fmt_num(payload.get('estimated_rounds_remaining', 0)) if target > 0 and average > 0 else '--'}",
        f"    alvo:            {progress}",
        "",
        "  Decisoes",
        (
            f"    BLOCKED:         {fmt_num(decision_counts.get('BLOCKED', 0))}"
            + (f" / {fmt_num(decision_targets.get('BLOCKED', 0))}  falta={fmt_num(decision_remaining.get('BLOCKED', 0))}" if decision_targets else "")
        ),
        (
            f"    ALLOWED:         {fmt_num(decision_counts.get('ALLOWED', 0))}"
            + (f" / {fmt_num(decision_targets.get('ALLOWED', 0))}  falta={fmt_num(decision_remaining.get('ALLOWED', 0))}" if decision_targets else "")
        ),
        (
            f"    CONDITIONAL:     {fmt_num(decision_counts.get('CONDITIONAL', 0))}"
            + (f" / {fmt_num(decision_targets.get('CONDITIONAL', 0))}  falta={fmt_num(decision_remaining.get('CONDITIONAL', 0))}" if decision_targets else "")
        ),
        (
            "    origem:          "
            f"local(B={fmt_num(local_decision_counts.get('BLOCKED', 0))}, "
            f"A={fmt_num(local_decision_counts.get('ALLOWED', 0))}, "
            f"C={fmt_num(local_decision_counts.get('CONDITIONAL', 0))})  "
            f"ref(B={fmt_num(reference_decision_counts.get('BLOCKED', 0))}, "
            f"A={fmt_num(reference_decision_counts.get('ALLOWED', 0))}, "
            f"C={fmt_num(reference_decision_counts.get('CONDITIONAL', 0))})"
        ),
        "",
        "  Processos",
        f"    growth:          {payload['processes'].get('growth', '--')}",
        f"    runner:          {payload['processes'].get('collector_runner', '--')}",
        f"    ns-3:            {payload['processes'].get('ns3', '--')}",
        f"    coletor:         {payload['processes'].get('collector', '--')}",
        f"    rApp:            {payload['processes'].get('rapp', '--')}",
        "",
        "  Rodada de referencia",
        f"    round:           {reference.get('round_id', '--') if reference else '--'}",
        f"    export:          {'pronto' if reference.get('complete') else 'pendente' if reference else '--'}",
        f"    transitions:     {fmt_num(reference.get('written_transitions', 0)) if reference else '--'}",
        f"    snapshots:       {fmt_num(reference.get('candidate_snapshots', 0)) if reference else '--'}",
        (
            "    DB:              "
            f"ext={fmt_num(db_counts.get('extended_metrics', 0))}  "
            f"dec={fmt_num(db_counts.get('decisions_history', 0))}  "
            f"global={fmt_num(db_counts.get('marl_global_state_history', 0))}  "
            f"du={fmt_num(db_counts.get('marl_du_state_history', 0))}"
        ) if reference else "    DB:              --",
        (
            "    traces:          "
            f"PDCP={'ok' if trace_status.get('pdcp_ok') else 'missing'}  "
            f"RLC={'ok' if trace_status.get('rlc_ok') else 'missing'}  "
            f"E2={'ok' if trace_status.get('e2_ok') else 'missing'}"
        ) if reference else "    traces:          --",
        f"    export age:      {fmt_age(reference.get('export_mtime', 0.0)) if reference else '--'}",
        f"    trace age:       {fmt_age(trace_status.get('newest_mtime', 0.0)) if reference else '--'}",
        "=" * 72,
    ]
    return "\n".join(lines)


def main() -> int:
    args = build_parser().parse_args()
    output_root = resolve_output_root(args.output_root)
    if not output_root.exists():
        raise SystemExit(f"offline growth root not found: {output_root}")
    payload = compute_status_payload(output_root, target_override=int(args.target_transitions or 0))
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(render_status(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
