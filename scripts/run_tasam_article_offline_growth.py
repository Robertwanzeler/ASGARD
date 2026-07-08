#!/usr/bin/env python3
"""Grow the TA-SAM offline dataset in validated collection blocks."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from run_tasam_article_offline_collection import (
    BASE_CONFIG,
    DEFAULT_NS3_BIN,
    collect_round,
    export_round,
    load_base_config,
    merge_round_traces,
    resolve_ns3_bin,
    sqlite_counts,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "runs" / "tasam_article_offline_collection_growth"
EXPECTED_TRACE_FILES = (
    "DlPdcpStats.txt",
    "DlRlcStats.txt",
    "UlPdcpStats.txt",
    "UlRlcStats.txt",
    "DlE2PdcpStats.txt",
    "DlE2RlcStats.txt",
    "UlE2PdcpStats.txt",
    "UlE2RlcStats.txt",
)
DECISION_LABELS = ("BLOCKED", "ALLOWED", "CONDITIONAL")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Grow the TA-SAM offline collection in validated blocks")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT), help="Collection root to create or resume")
    parser.add_argument("--target-transitions", type=int, default=10_000, help="Stop once the merged trace reaches this many transitions")
    parser.add_argument("--target-blocked", type=int, default=0, help="Controlled collection target for BLOCKED decisions")
    parser.add_argument("--target-allowed", type=int, default=0, help="Controlled collection target for ALLOWED decisions")
    parser.add_argument("--target-conditional", type=int, default=0, help="Controlled collection target for CONDITIONAL decisions")
    parser.add_argument("--rounds-per-block", type=int, default=5, help="Short rounds to execute before each consolidation pass")
    parser.add_argument("--max-blocks", type=int, default=0, help="Maximum blocks to execute; 0 means until target is reached")
    parser.add_argument("--min-round-transitions", type=int, default=200, help="Minimum transitions expected from each accepted round")
    parser.add_argument("--sim-time", type=int, default=30, help="Simulation time per round")
    parser.add_argument("--settle-seconds", type=float, default=3.0, help="Wait after ns-3 exits before DB validation")
    parser.add_argument("--post-ns3-grace-seconds", type=float, default=5.0, help="Keep collector/rApp alive briefly after ns-3 exits so final traces can be ingested")
    parser.add_argument("--collector-poll-interval", type=float, default=0.25, help="Collector polling interval")
    parser.add_argument("--orchestrator-interval", type=float, default=0.2, help="rApp interval during offline collection")
    parser.add_argument("--ue-count", type=int, default=50, help="Total UE count per round")
    parser.add_argument("--camera-ue-count", type=int, default=19, help="Camera UE count per round")
    parser.add_argument("--vehicle-ue-count", type=int, default=12, help="Vehicle UE count per round")
    parser.add_argument("--mmwave-enb-nodes", type=int, default=3, help="mmWave DU/gNB node count")
    parser.add_argument("--logical-du-count", type=int, default=3, help="Logical DU count")
    parser.add_argument("--ue-speed-min", type=float, default=0.0, help="Minimum UE speed")
    parser.add_argument("--ue-speed-max", type=float, default=3.0, help="Maximum UE speed")
    parser.add_argument("--ran-pressure-profile", default="drl_article_v1", help="Scenario traffic pressure profile")
    parser.add_argument("--blocked-control-profile", default="drl_balanced_blocked_v1", help="Alternator profile used until BLOCKED target is met")
    parser.add_argument("--borderline-control-profile", default="drl_balanced_borderline_v1", help="Alternator profile used to gather ALLOWED and CONDITIONAL samples")
    parser.add_argument("--allowed-control-profile", default="drl_allowed_only_v1", help="Alternator profile used to backfill ALLOWED decisions after other targets are satisfied")
    parser.add_argument("--collection-event-time-source", choices=("sim", "wall"), default="wall", help="Drive collection-event stages from sim or wall clock")
    parser.add_argument("--armd-mode", default="off", help="ARMD runtime mode during offline growth collection")
    parser.add_argument("--reference-root", default="", help="Optional existing collection root used as the baseline when computing remaining targets")
    parser.add_argument("--pdcp-stale-seconds", type=float, default=3600.0, help="PDCP stale threshold propagated to offline rounds")
    parser.add_argument("--export-limit", type=int, default=0, help="Optional transition cap per round")
    parser.add_argument("--allow-proxy", action="store_true", help="Keep proxy-latency transitions in exported traces")
    parser.add_argument("--ns3-bin", default=str(DEFAULT_NS3_BIN), help="ns-3 scenario binary")
    parser.add_argument("--base-config", default=str(BASE_CONFIG), help="Base scenario JSON config")
    parser.add_argument("--dry-run", action="store_true", help="Prepare commands without executing them")
    return parser


def round_dir(output_root: Path, round_index: int) -> Path:
    return output_root / f"round_{round_index:04d}"


def existing_round_dirs(output_root: Path) -> list[Path]:
    return sorted(
        (
            path
            for path in output_root.glob("round_*")
            if path.is_dir() and path.name[6:].isdigit()
        ),
        key=lambda path: int(path.name[6:]),
    )


def count_trace_lines(trace_path: Path) -> int:
    if not trace_path.exists():
        return 0
    with trace_path.open("r", encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


def validate_round_artifacts(round_dir_path: Path, min_round_transitions: int) -> list[str]:
    problems: list[str] = []
    trace_dir = round_dir_path / "state" / "ns3_traces"
    export_dir = round_dir_path / "export"
    trace_jsonl = export_dir / "tasam_article_trace.jsonl"
    summary_json = export_dir / "tasam_article_export_summary.json"

    for name in EXPECTED_TRACE_FILES:
        if not (trace_dir / name).exists():
            problems.append(f"missing trace file: {name}")

    if not trace_jsonl.exists():
        problems.append("missing exported trace jsonl")
    if not summary_json.exists():
        problems.append("missing export summary json")

    written_transitions = count_trace_lines(trace_jsonl)
    if written_transitions < int(min_round_transitions):
        problems.append(
            f"written_transitions below minimum: {written_transitions} < {int(min_round_transitions)}"
        )

    if summary_json.exists():
        try:
            payload = json.loads(summary_json.read_text(encoding="utf-8"))
            summary_written = int(payload.get("written_transitions", 0) or 0)
            if summary_written != written_transitions:
                problems.append(
                    f"summary mismatch: summary={summary_written} trace_lines={written_transitions}"
                )
        except (OSError, json.JSONDecodeError) as exc:
            problems.append(f"invalid export summary json: {exc}")

    return problems


def round_has_repairable_raw_artifacts(round_dir_path: Path) -> bool:
    trace_dir = round_dir_path / "state" / "ns3_traces"
    db_path = round_dir_path / "state" / "rapp_data_lake.db"
    if not db_path.exists():
        return False
    return all((trace_dir / name).exists() for name in EXPECTED_TRACE_FILES)


def try_repair_round_artifacts(
    round_dir_path: Path,
    min_round_transitions: int,
    *,
    allow_proxy: bool = False,
    export_limit: int = 0,
) -> tuple[bool, list[str]]:
    if not round_has_repairable_raw_artifacts(round_dir_path):
        return False, ["raw artifacts not repairable"]

    export_dir = round_dir_path / "export"
    export_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "db_path": round_dir_path / "state" / "rapp_data_lake.db",
        "trace_jsonl": export_dir / "tasam_article_trace.jsonl",
        "summary_json": export_dir / "tasam_article_export_summary.json",
    }
    repair_args = SimpleNamespace(export_limit=int(export_limit), allow_proxy=bool(allow_proxy))
    try:
        export_round(paths, repair_args, False)
    except Exception as exc:
        return False, [f"repair export failed: {exc}"]

    problems = validate_round_artifacts(round_dir_path, min_round_transitions)
    return len(problems) == 0, problems


def load_round_payload(round_dir_path: Path) -> dict[str, Any]:
    export_dir = round_dir_path / "export"
    trace_jsonl = export_dir / "tasam_article_trace.jsonl"
    summary_json = export_dir / "tasam_article_export_summary.json"
    summary: dict[str, Any] = {}
    if summary_json.exists():
        summary = json.loads(summary_json.read_text(encoding="utf-8"))
    return {
        "round_id": round_dir_path.name,
        "round_dir": str(round_dir_path),
        "state_dir": str(round_dir_path / "state"),
        "trace_jsonl": str(trace_jsonl),
        "summary_json": str(summary_json),
        "ns3_exit_code": 0,
        "db_counts": sqlite_counts(round_dir_path / "state" / "rapp_data_lake.db"),
        "written_transitions": int(summary.get("written_transitions", count_trace_lines(trace_jsonl)) or 0),
        "candidate_snapshots": int(summary.get("candidate_snapshots", 0) or 0),
        "decision_counts": summary.get("decision_counts") or {},
        "stage_counts": summary.get("stage_counts") or {},
        "collection_event_profile": str(summary.get("collection_event_profile") or ""),
    }


def load_existing_round_payloads(output_root: Path, min_round_transitions: int) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for round_dir_path in existing_round_dirs(output_root):
        problems = validate_round_artifacts(round_dir_path, min_round_transitions)
        if problems:
            raise SystemExit(
                f"Existing round {round_dir_path.name} is incomplete or invalid:\n- " + "\n- ".join(problems)
            )
        payloads.append(load_round_payload(round_dir_path))
    return payloads


def archive_incomplete_round(output_root: Path, round_dir_path: Path, problems: list[str]) -> dict[str, Any]:
    archive_root = output_root / "recovered_incomplete_rounds"
    archive_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    archive_dir = archive_root / f"{round_dir_path.name}_{stamp}"
    counter = 1
    while archive_dir.exists():
        counter += 1
        archive_dir = archive_root / f"{round_dir_path.name}_{stamp}_{counter:02d}"
    shutil.move(str(round_dir_path), str(archive_dir))
    payload = {
        "round_id": round_dir_path.name,
        "original_path": str(round_dir_path),
        "archived_path": str(archive_dir),
        "problems": list(problems),
        "recovered_at": datetime.now().isoformat(timespec="seconds"),
    }
    (archive_dir / "recovery_manifest.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return payload


def recover_resumable_round_payloads(
    output_root: Path,
    min_round_transitions: int,
    *,
    allow_proxy: bool = False,
    export_limit: int = 0,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    round_dirs = existing_round_dirs(output_root)
    if not round_dirs:
        return [], []

    valid_payloads: list[dict[str, Any]] = []
    recovered_rounds: list[dict[str, Any]] = []
    recovery_started = False
    for round_dir_path in round_dirs:
        problems = validate_round_artifacts(round_dir_path, min_round_transitions)
        if problems and round_has_repairable_raw_artifacts(round_dir_path):
            repaired, repair_problems = try_repair_round_artifacts(
                round_dir_path,
                min_round_transitions,
                allow_proxy=allow_proxy,
                export_limit=export_limit,
            )
            if repaired:
                problems = []
            else:
                problems = [*problems, *repair_problems]
        if not recovery_started and not problems:
            valid_payloads.append(load_round_payload(round_dir_path))
            continue

        recovery_started = True
        if not problems:
            problems = ["round appears after an incomplete predecessor and must be recollected"]
        recovered_rounds.append(archive_incomplete_round(output_root, round_dir_path, problems))

    expected_ids = [f"round_{index:04d}" for index in range(1, len(valid_payloads) + 1)]
    actual_ids = [payload["round_id"] for payload in valid_payloads]
    if actual_ids != expected_ids:
        raise SystemExit(
            "Existing valid rounds are not contiguous after recovery:\n"
            f"- expected={expected_ids}\n"
            f"- actual={actual_ids}"
        )
    return valid_payloads, recovered_rounds


def write_growth_summary(output_root: Path, payload: dict[str, Any]) -> None:
    path = output_root / "offline_growth_summary.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def ensure_local_merge(output_root: Path, round_payloads: list[dict[str, Any]]) -> dict[str, Any]:
    if not round_payloads:
        return {"merged_trace_jsonl": str(output_root / "tasam_article_trace.jsonl")}
    return merge_round_traces(output_root, round_payloads)


def normalize_decision_counts(payload: dict[str, Any] | None) -> dict[str, int]:
    counts = {label: 0 for label in DECISION_LABELS}
    for key, value in (payload or {}).items():
        label = str(key or "").upper()
        counts[label] = counts.get(label, 0) + int(value or 0)
    return counts


def aggregate_decision_counts(round_payloads: list[dict[str, Any]]) -> dict[str, int]:
    counts = {label: 0 for label in DECISION_LABELS}
    for payload in round_payloads:
        for key, value in normalize_decision_counts(payload.get("decision_counts")).items():
            counts[key] = counts.get(key, 0) + int(value or 0)
    return counts


def load_reference_summary(reference_root: str | Path | None) -> dict[str, Any]:
    raw_root = str(reference_root or "").strip()
    if not raw_root:
        return {
            "reference_root": "",
            "written_transitions_total": 0,
            "decision_counts_total": {label: 0 for label in DECISION_LABELS},
        }

    root = Path(raw_root)
    summary_path = root / "offline_collection_summary.json"
    if not summary_path.exists():
        raise SystemExit(f"reference collection summary not found: {summary_path}")

    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    return {
        "reference_root": str(root),
        "written_transitions_total": int(payload.get("written_transitions_total", 0) or 0),
        "decision_counts_total": normalize_decision_counts(payload.get("decision_counts_total")),
    }


def combine_decision_counts(local: dict[str, int], reference: dict[str, int]) -> dict[str, int]:
    combined = {label: 0 for label in DECISION_LABELS}
    for label in DECISION_LABELS:
        combined[label] = int(local.get(label, 0) or 0) + int(reference.get(label, 0) or 0)
    return combined


def controlled_targets(args: argparse.Namespace) -> dict[str, int]:
    return {
        "BLOCKED": max(0, int(args.target_blocked or 0)),
        "ALLOWED": max(0, int(args.target_allowed or 0)),
        "CONDITIONAL": max(0, int(args.target_conditional or 0)),
    }


def controlled_mode_enabled(args: argparse.Namespace) -> bool:
    return any(value > 0 for value in controlled_targets(args).values())


def remaining_decision_targets(current: dict[str, int], targets: dict[str, int]) -> dict[str, int]:
    return {
        key: max(0, int(targets.get(key, 0) or 0) - int(current.get(key, 0) or 0))
        for key in targets
    }


def determine_collection_phase(args: argparse.Namespace, decision_counts: dict[str, int]) -> tuple[str, str]:
    if not controlled_mode_enabled(args):
        return "free_growth", str(getattr(args, "collection_event_profile", "") or "")

    targets = controlled_targets(args)
    remaining = remaining_decision_targets(decision_counts, targets)
    if remaining.get("BLOCKED", 0) > 0:
        return "blocked_phase", str(args.blocked_control_profile)
    if remaining.get("CONDITIONAL", 0) > 0:
        return "borderline_phase", str(args.borderline_control_profile)
    if remaining.get("ALLOWED", 0) > 0:
        return "allowed_phase", str(args.allowed_control_profile)
    return "complete", ""


def target_reached(args: argparse.Namespace, current_total: int, decision_counts: dict[str, int]) -> bool:
    if controlled_mode_enabled(args):
        targets = controlled_targets(args)
        remaining = remaining_decision_targets(decision_counts, targets)
        return all(value <= 0 for value in remaining.values())
    return current_total >= int(args.target_transitions)


def build_growth_summary(
    args: argparse.Namespace,
    output_root: Path,
    *,
    blocks_run: int,
    current_total: int,
    effective_total: int,
    local_decision_counts: dict[str, int],
    decision_counts: dict[str, int],
    reference_summary: dict[str, Any],
    reference_transitions: int,
    reference_decision_counts: dict[str, int],
    recovered_rounds: list[dict[str, Any]],
    round_payloads: list[dict[str, Any]],
    merged_trace_jsonl: str,
) -> dict[str, Any]:
    phase_name, phase_profile = determine_collection_phase(args, decision_counts)
    decision_targets = controlled_targets(args)
    return {
        "schema": "greenran.tasam_article_offline_growth.v1",
        "output_root": str(output_root),
        "target_transitions": int(args.target_transitions),
        "controlled_mode": controlled_mode_enabled(args),
        "collection_phase": phase_name,
        "active_collection_event_profile": phase_profile,
        "rounds_per_block": int(args.rounds_per_block),
        "blocks_run": blocks_run,
        "reference_root": reference_summary.get("reference_root", ""),
        "reference_transitions": reference_transitions,
        "reference_decision_counts": reference_decision_counts,
        "local_transitions_current": current_total,
        "current_transitions": effective_total,
        "target_reached": target_reached(args, effective_total, decision_counts),
        "decision_targets": decision_targets,
        "local_decision_counts_current": local_decision_counts,
        "decision_counts_current": decision_counts,
        "effective_decision_counts_current": decision_counts,
        "decision_targets_remaining": remaining_decision_targets(decision_counts, decision_targets),
        "recovered_incomplete_rounds": recovered_rounds,
        "latest_round": round_payloads[-1]["round_id"] if round_payloads else None,
        "merged_trace_jsonl": merged_trace_jsonl,
        "offline_collection_summary": str(output_root / "offline_collection_summary.json"),
    }


def main() -> int:
    args = build_parser().parse_args()
    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    ns3_bin = resolve_ns3_bin(args.ns3_bin)
    base_config = load_base_config(Path(args.base_config))
    round_payloads, recovered_rounds = recover_resumable_round_payloads(
        output_root,
        int(args.min_round_transitions),
        allow_proxy=bool(args.allow_proxy),
        export_limit=int(args.export_limit),
    )
    for recovered in recovered_rounds:
        print(
            f"[growth] recovered incomplete {recovered['round_id']} -> {recovered['archived_path']} "
            f"problems={json.dumps(recovered['problems'], ensure_ascii=False)}",
            flush=True,
        )
    current_total = sum(int(payload.get("written_transitions", 0) or 0) for payload in round_payloads)
    local_decision_counts = aggregate_decision_counts(round_payloads)
    reference_summary = load_reference_summary(args.reference_root)
    reference_decision_counts = normalize_decision_counts(reference_summary.get("decision_counts_total"))
    reference_transitions = int(reference_summary.get("written_transitions_total", 0) or 0)
    decision_counts = combine_decision_counts(local_decision_counts, reference_decision_counts)
    effective_total = current_total + reference_transitions
    next_round_index = len(round_payloads) + 1
    blocks_run = 0
    if not args.dry_run:
        initial_merged = ensure_local_merge(output_root, round_payloads)
        write_growth_summary(
            output_root,
            build_growth_summary(
                args,
                output_root,
                blocks_run=blocks_run,
                current_total=current_total,
                effective_total=effective_total,
                local_decision_counts=local_decision_counts,
                decision_counts=decision_counts,
                reference_summary=reference_summary,
                reference_transitions=reference_transitions,
                reference_decision_counts=reference_decision_counts,
                recovered_rounds=recovered_rounds,
                round_payloads=round_payloads,
                merged_trace_jsonl=initial_merged["merged_trace_jsonl"],
            ),
        )

    while not target_reached(args, effective_total, decision_counts):
        if int(args.max_blocks) > 0 and blocks_run >= int(args.max_blocks):
            break
        blocks_run += 1
        phase_name, phase_profile = determine_collection_phase(args, decision_counts)
        args.collection_event_profile = phase_profile
        print(
            f"[growth] block={blocks_run} phase={phase_name} local_total={current_total} effective_total={effective_total} "
            f"target={int(args.target_transitions)} next_round={next_round_index} "
            f"decision_counts={json.dumps(decision_counts, ensure_ascii=False, sort_keys=True)}",
            flush=True,
        )
        for _ in range(int(args.rounds_per_block)):
            phase_name, phase_profile = determine_collection_phase(args, decision_counts)
            if phase_name == "complete":
                break
            args.collection_event_profile = phase_profile
            payload = collect_round(next_round_index, args, base_config, ns3_bin)
            if args.dry_run:
                next_round_index += 1
                continue
            round_dir_path = round_dir(output_root, next_round_index)
            problems = validate_round_artifacts(round_dir_path, int(args.min_round_transitions))
            if problems:
                recovered = archive_incomplete_round(output_root, round_dir_path, problems)
                recovered_rounds.append(recovered)
                print(
                    f"[growth] archived invalid {recovered['round_id']} -> {recovered['archived_path']} "
                    f"problems={json.dumps(recovered['problems'], ensure_ascii=False)}",
                    flush=True,
                )
                continue
            payload = load_round_payload(round_dir_path)
            round_payloads.append(payload)
            current_total += int(payload.get("written_transitions", 0) or 0)
            local_decision_counts = aggregate_decision_counts(round_payloads)
            decision_counts = combine_decision_counts(local_decision_counts, reference_decision_counts)
            effective_total = current_total + reference_transitions
            next_round_index += 1
            if target_reached(args, effective_total, decision_counts):
                break

        if args.dry_run:
            print("[growth] dry-run complete", flush=True)
            return 0

        merged = ensure_local_merge(output_root, round_payloads)
        growth_summary = build_growth_summary(
            args,
            output_root,
            blocks_run=blocks_run,
            current_total=current_total,
            effective_total=effective_total,
            local_decision_counts=local_decision_counts,
            decision_counts=decision_counts,
            reference_summary=reference_summary,
            reference_transitions=reference_transitions,
            reference_decision_counts=reference_decision_counts,
            recovered_rounds=recovered_rounds,
            round_payloads=round_payloads,
            merged_trace_jsonl=merged["merged_trace_jsonl"],
        )
        write_growth_summary(output_root, growth_summary)
        print(json.dumps(growth_summary, indent=2, ensure_ascii=False), flush=True)

    if not args.dry_run and recovered_rounds:
        merged = ensure_local_merge(output_root, round_payloads)
        growth_summary = build_growth_summary(
            args,
            output_root,
            blocks_run=blocks_run,
            current_total=current_total,
            effective_total=effective_total,
            local_decision_counts=local_decision_counts,
            decision_counts=decision_counts,
            reference_summary=reference_summary,
            reference_transitions=reference_transitions,
            reference_decision_counts=reference_decision_counts,
            recovered_rounds=recovered_rounds,
            round_payloads=round_payloads,
            merged_trace_jsonl=merged["merged_trace_jsonl"],
        )
        write_growth_summary(output_root, growth_summary)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
