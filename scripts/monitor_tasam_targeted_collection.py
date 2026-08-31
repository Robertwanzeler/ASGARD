#!/usr/bin/env python3
"""Collect a balanced set of valid real GreenRAN TA-SAM transitions.

The live Data Lake keeps every observation, but this monitor only counts and
exports transitions that are valid for training, PDCP-real, proxy-free, and
belong to the requested scenario stages.  It stops the collection only after
each target stage has enough valid transitions.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
EXPORTER = ROOT / "scripts" / "export_tasam_article_dataset.py"
STOP_SCRIPT = ROOT / "scripts" / "stop_tasam_article_ns3_collection.sh"

STAGE_GROUPS = {
    # Keep every article-adapted GreenRAN interaction independent. In
    # particular, do not merge ALLOWED recovery with vehicle BLOCKED.
    "allowed_bootstrap": ("allowed_bootstrap",),
    "allowed_stable": ("allowed_stable",),
    "camera_conditional": ("camera_conditional",),
    "camera_blocked": ("camera_blocked",),
    "vehicle_conditional": ("vehicle_conditional",),
    "vehicle_blocked": ("vehicle_blocked",),
    "app2_conditional": ("app2_conditional",),
    "app2_blocked": ("app2_blocked",),
    "allowed_recovery": ("allowed_recovery",),
}

DEFAULT_TARGETS = {stage: 500 for stage in STAGE_GROUPS}

EXPECTED_DECISIONS = {
    "allowed_bootstrap": "ALLOWED",
    "allowed_stable": "ALLOWED",
    "camera_conditional": "CONDITIONAL",
    "camera_blocked": "BLOCKED",
    "app2_conditional": "CONDITIONAL",
    "vehicle_conditional": "CONDITIONAL",
    "app2_blocked": "BLOCKED",
    "allowed_recovery": "ALLOWED",
    "vehicle_blocked": "BLOCKED",
}

STAGE_TO_GROUP = {
    stage: group for group, stages in STAGE_GROUPS.items() for stage in stages
}


def parse_target_specs(specs: list[str] | None) -> dict[str, int]:
    if not specs:
        return dict(DEFAULT_TARGETS)
    targets: dict[str, int] = {}
    for spec in specs:
        if "=" not in spec:
            raise ValueError(f"target inválido {spec!r}; use stage=quantidade")
        stage, raw_count = spec.split("=", 1)
        stage = stage.strip()
        if stage not in STAGE_GROUPS:
            raise ValueError(f"grupo não permitido para coleta direcionada: {stage!r}")
        try:
            count = int(raw_count)
        except ValueError as exc:
            raise ValueError(f"quantidade inválida em {spec!r}") from exc
        if count < 0:
            raise ValueError(f"quantidade não pode ser negativa em {spec!r}")
        targets[stage] = count
    missing = [stage for stage in STAGE_GROUPS if stage not in targets]
    if missing:
        raise ValueError(f"faltam stages obrigatórios: {', '.join(missing)}")
    return targets


def record_is_valid_target(record: dict[str, Any]) -> tuple[bool, str]:
    quality = record.get("collection_quality") or {}
    stage = str(record.get("scenario_stage") or "unknown")
    decision_payload = record.get("decision") or {}
    decision = str(decision_payload.get("decision") or "unknown").upper()
    if stage not in EXPECTED_DECISIONS:
        return False, "unexpected_stage"
    if not bool(decision_payload.get("collection_event_stage_authoritative")):
        return False, "missing_authoritative_stage"
    # A mismatch is a valuable RL sample: it is an action that the judge can
    # reward or penalize.  Only an unknown decision is structurally invalid.
    if decision not in {"ALLOWED", "CONDITIONAL", "BLOCKED"}:
        return False, "invalid_decision"
    if not bool(quality.get("valid_for_training")):
        return False, "invalid_for_training"
    if bool(quality.get("has_proxy")):
        return False, "proxy_present"
    try:
        if float(quality.get("proxy_latency_sample_count") or 0.0) > 0.0:
            return False, "proxy_present"
    except (TypeError, ValueError):
        return False, "invalid_proxy_count"
    collector_mode = str(quality.get("collector_mode") or "").strip().lower()
    if collector_mode != "pdcp_real" or not bool(quality.get("pdcp_real")):
        return False, "not_pdcp_real"
    if bool(quality.get("sim_reset")):
        return False, "sim_reset"
    if not record.get("metrics") or not record.get("next_metrics"):
        return False, "missing_metrics"
    if not bool(record.get("judge_feedback_observed")):
        return False, "missing_judge_feedback"
    if not bool(decision_payload.get("armd_proposal_present")) or not bool(decision_payload.get("armd_proposal_valid")):
        return False, "invalid_armd_proposal"
    if not bool(decision_payload.get("tasam_proposal_present")) or not bool(decision_payload.get("tasam_proposal_valid")):
        return False, "invalid_tasam_proposal"
    return True, "kept"


def load_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                records.append(record)
    return records


def select_balanced_records(
    records: list[dict[str, Any]], targets: dict[str, int]
) -> tuple[list[dict[str, Any]], Counter[str], Counter[str]]:
    selected: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    rejected: Counter[str] = Counter()
    for record in records:
        keep, reason = record_is_valid_target(record)
        if not keep:
            rejected[reason] += 1
            continue
        stage = str(record.get("scenario_stage"))
        group = STAGE_TO_GROUP[stage]
        if counts[group] >= targets[group]:
            continue
        selected.append(record)
        counts[group] += 1
    selected.sort(key=lambda item: int(item.get("timestamp") or 0))
    return selected, counts, rejected


def export_current_db(db: Path, raw_path: Path, summary_path: Path, since_ts: int) -> int:
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        str(EXPORTER),
        "--db",
        str(db),
        "--output-jsonl",
        str(raw_path),
        "--summary-json",
        str(summary_path),
        "--include-invalid",
    ]
    if since_ts > 0:
        command.extend(["--since-ts", str(since_ts)])
    result = subprocess.run(command, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, check=False)
    return int(result.returncode)


def write_final_export(
    output_dir: Path,
    selected: list[dict[str, Any]],
    counts: Counter[str],
    targets: dict[str, int],
    rejected: Counter[str],
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    trace_path = output_dir / "targeted_valid_trace.jsonl"
    summary_path = output_dir / "targeted_valid_summary.json"
    with trace_path.open("w", encoding="utf-8") as handle:
        for record in selected:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    decision_match_counts: Counter[str] = Counter()
    for record in selected:
        stage = str(record.get("scenario_stage") or "")
        decision = str((record.get("decision") or {}).get("decision") or "").upper()
        expected = EXPECTED_DECISIONS.get(stage)
        decision_match_counts["match" if expected == decision else "mismatch"] += 1
    summary = {
        "schema": "greenran.tasam_targeted_collection.v1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "targets": targets,
        "valid_counts": dict(sorted(counts.items())),
        "written_rows": len(selected),
        "rejected_counts": dict(sorted(rejected.items())),
        "stage_decision_match_counts": dict(sorted(decision_match_counts.items())),
        "real_only": True,
        "proxy_free": True,
        "training_ready_for_merge": all(counts[stage] >= target for stage, target in targets.items()),
        "trace_jsonl": str(trace_path.resolve()),
    }
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return trace_path, summary_path


def stop_collection(state_dir: Path) -> None:
    subprocess.run(
        ["/bin/bash", str(STOP_SCRIPT)],
        cwd=ROOT,
        env={"GREENRAN_STATE_DIR": str(state_dir)},
        check=False,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Monitorar coleta TA-SAM direcionada por stages válidos")
    parser.add_argument("--db", required=True, help="SQLite Data Lake da coleta")
    parser.add_argument("--state-dir", required=True, help="Estado GreenRAN usado para parar os serviços")
    parser.add_argument("--output-dir", default=None, help="Diretório do JSONL balanceado final")
    parser.add_argument(
        "--target-stage",
        action="append",
        default=None,
        help="Meta por stage no formato stage=quantidade; pode ser repetido",
    )
    parser.add_argument("--since-ts", type=int, default=0, help="Ignorar dados anteriores a este timestamp Unix")
    parser.add_argument("--poll-seconds", type=float, default=10.0)
    parser.add_argument("--no-stop", action="store_true", help="Não parar os serviços ao atingir todas as metas")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        targets = parse_target_specs(args.target_stage)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    db = Path(args.db).resolve()
    state_dir = Path(args.state_dir).resolve()
    output_dir = Path(args.output_dir).resolve() if args.output_dir else state_dir / "targeted_export"
    work_dir = output_dir / ".work"
    raw_path = work_dir / "latest_raw_export.jsonl"
    raw_summary = work_dir / "latest_raw_summary.json"

    total_target = sum(targets.values())
    print(f"Início: {datetime.now().isoformat(timespec='seconds')}", flush=True)
    print(f"Metas válidas: {targets} (total {total_target})", flush=True)
    print(
        "Critérios: valid_for_training + pdcp_real + sem proxy + métricas atual/próxima + "
        "feedback do juiz + propostas ARMD/TA-SAM válidas",
        flush=True,
    )

    while True:
        export_code = export_current_db(db, raw_path, raw_summary, int(args.since_ts)) if db.exists() else 1
        records = load_records(raw_path) if export_code == 0 else []
        selected, counts, rejected = select_balanced_records(records, targets)
        complete = all(counts[stage] >= target for stage, target in targets.items())
        progress = " ".join(f"{stage}={counts[stage]}/{targets[stage]}" for stage in targets)
        print(
            f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {progress} "
            f"total={len(selected)}/{total_target} válidas={sum(counts.values())} "
            f"rejeitadas={sum(rejected.values())}",
            flush=True,
        )
        if complete:
            trace_path, summary_path = write_final_export(output_dir, selected, counts, targets, rejected)
            print(f"Meta atingida. Trace final: {trace_path}", flush=True)
            print(f"Resumo: {summary_path}", flush=True)
            if not args.no_stop:
                print("Parando os serviços da coleta...", flush=True)
                stop_collection(state_dir)
            return 0
        time.sleep(max(1.0, float(args.poll_seconds)))


if __name__ == "__main__":
    raise SystemExit(main())
