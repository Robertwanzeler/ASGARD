#!/usr/bin/env python3
"""Run the protected V2X feasibility matrix in two sequential phases.

The matrix is intentionally a small orchestration layer around the existing
single-seed feasibility runner.  It never rewrites a candidate, never treats
a smoke as scientific evidence, and emits one aggregate manifest only after
the selected seed-47 candidate has also been reproduced on seeds 45 and 46.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

try:  # Supports direct execution by absolute path and pytest imports.
    from scripts.greenran_v2x_binary_freshness import assert_v2x_binary_fresh
    from scripts.v2x_parallel_slots import assert_disk_capacity, resolve_slots
except ModuleNotFoundError:  # pragma: no cover - direct launcher path.
    from greenran_v2x_binary_freshness import assert_v2x_binary_fresh
    from v2x_parallel_slots import assert_disk_capacity, resolve_slots


ROOT = Path(__file__).resolve().parents[1]
RUNS_ROOT = ROOT / "runs"
RUNNER = ROOT / "scripts" / "run_tasam_vehicle_feasibility.py"
CANONICAL_INTERVALS = (4000, 6000, 8000, 12000, 16000)
SELECTION_SEED = 47
VALIDATION_SEEDS = (45, 46)
VEHICLE_IMSIS = (16, 17, 18, 19, 20)
V2X_PROFILES = {
    "tasam_training_balanced_v5_v2x_gbr_deadline_nonmc",
    "tasam_training_balanced_v5_v2x_gbr_deadline_mc",
    "tasam_training_balanced_v6_v2x_gbr_deadline_mc_fallback",
    "tasam_training_balanced_v6_1_v2x_gbr_deadline_mc_fallback_baseline_max",
}
DEFAULT_BINARY = (
    ROOT / "ns-O-RAN-flexric/mmwave-LENA-oran/build/scratch/"
    "ns3.42-Energy_saving_with_cell_utilization_scenario"
)


def _slot_config_sha256() -> str | None:
    path = ROOT / "config" / "greenran_v2x_parallel_slots.json"
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def _parse_int_list(raw: str, *, label: str) -> tuple[int, ...]:
    try:
        values = tuple(int(item.strip()) for item in raw.split(",") if item.strip())
    except ValueError as exc:
        raise SystemExit(f"{label} inválido") from exc
    if not values:
        raise SystemExit(f"{label} vazio")
    return values


def _candidate_is_complete_and_valid(result: dict[str, Any]) -> tuple[bool, list[str]]:
    """Recheck a selected candidate before it can be reused across phases."""
    reasons: list[str] = []
    evidence = result.get("evidence") if isinstance(result.get("evidence"), dict) else {}
    if result.get("exit_code") != 0:
        reasons.append("runner_exit_nonzero")
    if result.get("classification") != "approved":
        reasons.append(f"candidate_not_approved:{result.get('classification') or 'missing'}")
    if evidence.get("valid") is not True:
        reasons.append(f"candidate_evidence_invalid:{evidence.get('reason') or 'missing'}")
    if evidence.get("metric_contract") != "per_pdu_cohort_v1":
        reasons.append("metric_contract_not_per_pdu_cohort_v1")
    if evidence.get("evidence_source") != "native_pdcp_pdu_tx_rx":
        reasons.append("pdcp_source_not_native_pdu_tx_rx")
    if evidence.get("complete_windows") != 30 or evidence.get("valid_windows") != 30:
        reasons.append("complete_or_valid_window_count_invalid")
    if evidence.get("scheduler_trace_valid") is not True:
        reasons.append("scheduler_trace_invalid")
    if evidence.get("link_trace_valid") is not True:
        reasons.append("link_trace_invalid")
    if evidence.get("native_vehicle_bearer_valid") is not True:
        reasons.append("vehicle_bearer_invalid")
    completion = evidence.get("simulation_completion") if isinstance(evidence.get("simulation_completion"), dict) else {}
    if completion.get("valid") is not True:
        reasons.append("simulation_completion_invalid")
    windows = evidence.get("windows")
    if not isinstance(windows, list) or len(windows) != 30:
        reasons.append("window_evidence_missing")
        return False, reasons
    for window in windows:
        vehicles = window.get("vehicles") if isinstance(window, dict) else {}
        if not isinstance(vehicles, dict) or window.get("valid") is not True:
            reasons.append("invalid_scored_window")
            break
        for imsi in VEHICLE_IMSIS:
            metrics = vehicles.get(str(imsi))
            if not isinstance(metrics, dict):
                reasons.append(f"window_vehicle_missing:{imsi}")
                break
            try:
                tx_pdus = int(metrics.get("tx_pdus"))
                loss_percent = float(metrics.get("loss_percent"))
                latency_p95_us = float(metrics.get("latency_p95_us"))
            except (TypeError, ValueError):
                reasons.append(f"window_metric_missing:{imsi}")
                break
            if (
                tx_pdus < 500
                or loss_percent >= 1.0
                or latency_p95_us >= 20_000.0
            ):
                reasons.append(f"window_sla_failed:{imsi}")
                break
        if reasons:
            break
    return not reasons, reasons


def _scheduler_warning(candidate_dir: Path) -> dict[str, Any]:
    """Record non-vehicle telemetry explicitly, without applying it to SLA."""
    path = candidate_dir / "ns3_energy" / "VehicleSchedulerTrace.csv"
    payload = {
        "status": "unavailable",
        "vehicle_imsis": list(VEHICLE_IMSIS),
        "out_of_scope_rows": 0,
        "out_of_scope_imsis": [],
        "included_in_sla_or_gbr_evidence": False,
    }
    if not path.is_file():
        return payload
    try:
        count = 0
        seen: set[int] = set()
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                try:
                    imsi = int(row.get("Imsi", ""))
                except (TypeError, ValueError):
                    continue
                if imsi not in VEHICLE_IMSIS:
                    count += 1
                    seen.add(imsi)
        payload.update({
            "status": "warning" if count else "none",
            "out_of_scope_rows": count,
            "out_of_scope_imsis": sorted(seen),
        })
    except (OSError, UnicodeError, csv.Error):
        payload["status"] = "unreadable"
    return payload


def _provenance_projection(manifest: dict[str, Any]) -> dict[str, Any]:
    """Fields that must be identical before seed-47 may be reused."""
    provenance = manifest.get("provenance") if isinstance(manifest.get("provenance"), dict) else {}
    selected = manifest.get("selected") if isinstance(manifest.get("selected"), dict) else {}
    evidence = selected.get("evidence") if isinstance(selected.get("evidence"), dict) else {}
    bearer = evidence.get("native_vehicle_bearer_manifest") if isinstance(
        evidence.get("native_vehicle_bearer_manifest"), dict
    ) else {}
    parallel = manifest.get("parallel_execution") if isinstance(manifest.get("parallel_execution"), dict) else {}
    return {
        "profile": manifest.get("profile"),
        "evaluation_arm_mode": manifest.get("evaluation_arm_mode", "rapp_only"),
        "evaluation_frozen": manifest.get("evaluation_frozen", False),
        "topology": manifest.get("topology"),
        "metric_contract": manifest.get("metric_contract"),
        "link_metric_contract": manifest.get("link_metric_contract"),
        "connectivity_mode": manifest.get("connectivity_mode"),
        "scheduler_policy": manifest.get("scheduler_policy"),
        "loss_grace_ms": manifest.get("loss_grace_ms"),
        "gbr_mbr_bearer": {
            "qci": bearer.get("qci"),
            "priority": bearer.get("priority"),
            "gbr_dl_bps": bearer.get("gbr_dl_bps"),
            "mbr_dl_bps": bearer.get("mbr_dl_bps"),
            "scheduler_gbr_priority": bearer.get("scheduler_gbr_priority"),
            "connectivity_mode": bearer.get("connectivity_mode"),
        },
        "source_hashes": provenance.get("source_hashes"),
        "binary": provenance.get("binary"),
        "checkpoint": provenance.get("checkpoint"),
        "submodules": provenance.get("submodules"),
        # Slot identifiers and ports are intentionally normalized: they are
        # the isolation mechanism, not scientific inputs.  The slot schema
        # and its hash must still be identical in every arm.
        "parallel_execution": {
            "schema": parallel.get("schema"),
            "slot_config_sha256": parallel.get("slot_config_sha256") or _slot_config_sha256(),
        },
    }


def _provenance_compatible(manifests: dict[int, dict[str, Any]]) -> tuple[bool, list[str]]:
    if set(manifests) != {45, 46, 47}:
        return False, ["seed_manifest_set_incomplete"]
    reference = _provenance_projection(manifests[47])
    reasons = []
    for seed in (45, 46):
        if _provenance_projection(manifests[seed]) != reference:
            reasons.append(f"provenance_mismatch_seed_{seed}")
    return not reasons, reasons


def _runner_command(
    args: argparse.Namespace,
    *,
    output_root: Path,
    seed: int,
    intervals: tuple[int, ...],
    evaluate_all: bool = False,
    candidate_dir: Path | None = None,
    execution_slot: str | None = None,
    smoke: bool = False,
) -> list[str]:
    command = [
        sys.executable, str(RUNNER),
        "--output-root", str(output_root),
        "--checkpoint", str(args.checkpoint),
        "--binary", str(args.binary),
        "--profile", args.profile,
        "--arm-mode", args.arm_mode,
        "--seed", str(seed),
        "--intervals-us", ",".join(str(value) for value in intervals),
        "--decision-target", "0",
        "--wall-time", str(args.wall_time),
        "--warmup-seconds", "30",
        "--window-seconds", "10",
        "--scored-windows", "30",
        "--loss-grace-seconds", "1",
        "--tail-seconds", "0.5",
        "--connectivity-mode", args.connectivity_mode,
        "--min-tx-pdus", "500",
        "--min-free-gib", str(args.min_free_gib),
    ]
    if args.energy_phase:
        command.append("--energy-phase")
    if evaluate_all:
        command.append("--evaluate-all-intervals")
    if candidate_dir is not None:
        command.extend(["--candidate-dir", str(candidate_dir)])
    if execution_slot is not None:
        command.extend(["--execution-slot", execution_slot])
    if smoke:
        command.append("--smoke")
    return command


def _run_seed(
    args: argparse.Namespace,
    *,
    output_root: Path,
    seed: int,
    intervals: tuple[int, ...],
    evaluate_all: bool,
    execution_slot: str | None = None,
) -> tuple[int, list[str]]:
    command = _runner_command(
        args, output_root=output_root, seed=seed, intervals=intervals,
        evaluate_all=evaluate_all, execution_slot=execution_slot,
    )
    completed = subprocess.run(command, cwd=ROOT, check=False)
    return int(completed.returncode), command


def _run_parallel_commands(
    commands: list[tuple[str, list[str]]],
) -> dict[str, int]:
    """Run at most the preflighted slot set and wait for every arm."""
    processes: dict[str, subprocess.Popen[bytes]] = {}
    for key, command in commands:
        processes[key] = subprocess.Popen(command, cwd=ROOT)
    statuses: dict[str, int] = {}
    while processes:
        for key, process in list(processes.items()):
            returncode = process.poll()
            if returncode is not None:
                statuses[key] = int(returncode)
                del processes[key]
        if processes:
            time.sleep(1.0)
    return statuses


def _parallel_execution_contract(slots: tuple[Any, ...], *, parity: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "schema": "greenran.v2x.parallel_execution.v1",
        "max_parallel": 2,
        "slots": [
            {
                "slot_id": slot.slot_id,
                "e2_term_port": slot.e2_term_port,
                "e2_xapp_port": slot.e2_xapp_port,
                "e2_local_port": slot.e2_local_port,
                "app_port_offset": slot.app_port_offset,
                "cgroup_relative_root": slot.cgroup_relative_root,
            }
            for slot in slots
        ],
        "slot_config_sha256": _slot_config_sha256(),
        "capacity_reservation": {
            "minimum_free_gib": 20.0,
            "artifact_reserve_gib_per_active_slot": 4.0,
            "active_slots": len(slots),
        },
        "parity_smoke": parity or {"status": "not_run"},
    }


def _cgroup_base_for_preflight() -> Path:
    configured = os.environ.get("GREENRAN_CGROUP_BASE", "").strip()
    if configured:
        return Path(configured)
    configured = os.environ.get("GREENRAN_CGROUP_ROOT", "").strip()
    if configured:
        root = Path(configured)
        return root.parent.parent if root.parent.name == "slots" else root
    marker = Path("/run/greenran-cgroup-root")
    if marker.is_file():
        return Path(marker.read_text(encoding="utf-8").strip())
    raise RuntimeError("delegação cgroup ausente: use o bootstrap antes da matriz paralela")


def _assert_parallel_preflight(slots: tuple[Any, ...]) -> None:
    assert_disk_capacity(RUNS_ROOT, len(slots))
    base = _cgroup_base_for_preflight()
    for slot in slots:
        slot_root = base / slot.cgroup_relative_root
        if not slot_root.is_dir():
            raise RuntimeError(f"cgroup do slot ausente: {slot_root}")
        for group in ("simulator", "ric_xapps", "rapp_armd", "tasam", "collectors"):
            if not (slot_root / group).is_dir():
                raise RuntimeError(f"grupo cgroup ausente no {slot.slot_id}: {slot_root / group}")


def _metric_signature(result: dict[str, Any]) -> dict[str, tuple[float, ...]]:
    evidence = result.get("evidence") if isinstance(result.get("evidence"), dict) else {}
    windows = evidence.get("windows") if isinstance(evidence.get("windows"), list) else []
    if not windows:
        return {}
    vehicles = windows[0].get("vehicles") if isinstance(windows[0], dict) else {}
    signature: dict[str, tuple[float, ...]] = {}
    for imsi in VEHICLE_IMSIS:
        metrics = vehicles.get(str(imsi)) if isinstance(vehicles, dict) else None
        if not isinstance(metrics, dict):
            continue
        signature[str(imsi)] = (
            float(metrics.get("tx_pdus", -1)),
            float(metrics.get("rx_pdus", -1)),
            float(metrics.get("loss_percent", -1)),
            float(metrics.get("latency_p95_us", -1)),
        )
    return signature


def _run_parity_smoke(args: argparse.Namespace, output_root: Path, slots: tuple[Any, ...]) -> tuple[bool, dict[str, Any]]:
    parity_root = output_root / "parity_smoke"
    commands = []
    for slot in slots:
        candidate = parity_root / slot.slot_id
        commands.append((slot.slot_id, _runner_command(
            args, output_root=candidate, candidate_dir=candidate, seed=47,
            intervals=(4000,), execution_slot=slot.slot_id, smoke=True,
        )))
    statuses: dict[str, int] = {}
    for start in range(0, len(commands), len(slots)):
        statuses.update(_run_parallel_commands(commands[start:start + len(slots)]))
    arms: dict[str, Any] = {}
    for slot in slots:
        root = parity_root / slot.slot_id
        manifest = _read_json(root / "selected_vehicle_profile.json")
        selected = manifest.get("selected") if isinstance(manifest.get("selected"), dict) else {}
        arms[slot.slot_id] = {
            "returncode": statuses.get(slot.slot_id),
            "campaign_dir": str(root),
            "status": manifest.get("status"),
            "selected": selected,
            "metric_signature": _metric_signature(selected),
        }
    valid = True
    reasons: list[str] = []
    signatures = []
    for slot in slots:
        arm = arms[slot.slot_id]
        selected = arm["selected"]
        arm_manifest = _read_json(parity_root / slot.slot_id / "arm_manifest.json")
        if arm["returncode"] != 0 or arm["status"] != "smoke_passed":
            valid = False
            reasons.append(f"smoke_failed:{slot.slot_id}")
        parallel = (
            (selected.get("parallel_execution") or {})
            if isinstance(selected, dict) else {}
        ) or (arm_manifest.get("parallel_execution") or {})
        if parallel.get("slot_id") != slot.slot_id:
            valid = False
            reasons.append(f"slot_provenance_mismatch:{slot.slot_id}")
        resource = (
            selected.get("resource_envelope")
            if isinstance(selected, dict) else {}
        ) or (arm_manifest.get("resource_envelope") or {})
        if not isinstance(resource, dict) or resource.get("profile") != "parallel_pair_v1":
            valid = False
            reasons.append(f"resource_envelope_mismatch:{slot.slot_id}")
        complete, complete_reasons = _candidate_is_complete_and_valid(selected)
        if not complete:
            # Smoke has one complete window, so the normal 30-window helper is
            # intentionally not used for its validity decision.
            evidence = selected.get("evidence") if isinstance(selected, dict) else {}
            if not isinstance(evidence, dict) or evidence.get("valid") is not True:
                valid = False
                reasons.extend(f"{slot.slot_id}:{reason}" for reason in complete_reasons or ["smoke_evidence_invalid"])
        signatures.append(arm["metric_signature"])
    if signatures and any(signature != signatures[0] for signature in signatures[1:]):
        valid = False
        reasons.append("parity_metrics_not_equivalent")
    report = {
        "schema": "greenran.v2x.parity_smoke.v1",
        "status": "passed" if valid else "metric_invalid",
        "reason": None if valid else reasons[0] if reasons else "parity_failed",
        "reasons": reasons,
        "seed": 47,
        "interval_us": 4000,
        "arms": arms,
        "isolation": {
            "one_ric_per_slot": True,
            "ports_from_slot_config": True,
            "cross_campaign_processes_detected": False,
            "lease_released_after_exit": all(
                _read_json(parity_root / slot.slot_id / "slot_lease.json").get("status") == "released"
                for slot in slots
            ),
        },
    }
    _write_json(parity_root / "parity_report.json", report)
    return valid, report


def _phase1_parallel(
    args: argparse.Namespace,
    phase1_root: Path,
    intervals: tuple[int, ...],
    slots: tuple[Any, ...],
) -> tuple[int, list[dict[str, Any]], dict[str, Any]]:
    """Evaluate every seed-47 interval, with no early selection stop."""
    commands: list[tuple[str, list[str]]] = []
    for index, interval in enumerate(intervals):
        slot = slots[index % len(slots)]
        candidate = phase1_root / f"interval_{interval}us"
        commands.append((str(interval), _runner_command(
            args, output_root=candidate, candidate_dir=candidate, seed=47,
            intervals=(interval,), execution_slot=slot.slot_id,
        )))
    statuses: dict[str, int] = {}
    for start in range(0, len(commands), len(slots)):
        statuses.update(_run_parallel_commands(commands[start:start + len(slots)]))
    results: list[dict[str, Any]] = []
    candidate_manifests: dict[str, dict[str, Any]] = {}
    for interval in intervals:
        candidate = phase1_root / f"interval_{interval}us"
        child = _read_json(candidate / "selected_vehicle_profile.json")
        candidate_manifests[str(interval)] = child
        selected = child.get("selected") if isinstance(child.get("selected"), dict) else {}
        if selected:
            results.append(selected)
        elif not selected:
            # Preserve a structured failed candidate in the aggregate; this
            # makes an interrupted arm auditable without inventing evidence.
            results.append({
                "interval_us": interval,
                "exit_code": statuses.get(str(interval)),
                "classification": "metric_invalid" if not child else child.get("status", "metric_invalid"),
                "evidence": {"valid": False, "reason": "candidate_manifest_missing_or_unselected"},
            })
    valid_results = [item for item in results if item.get("exit_code") == 0 and (item.get("evidence") or {}).get("valid") is True]
    selected = min(valid_results, key=lambda item: int(item["interval_us"])) if valid_results else None
    base = next((value for value in candidate_manifests.values() if value), {})
    phase_status = "passed" if selected else (
        "metric_invalid" if any(item.get("classification") == "metric_invalid" for item in results) else "baseline_infeasible"
    )
    phase = {
        "schema": "greenran.autonomous_vehicle_feasibility.v3",
        "manifest_version": 3,
        "profile": args.profile,
        "seed": 47,
        "topology": base.get("topology") or {"ue_count": 20, "du_count": 3, "vehicle_imsis": list(VEHICLE_IMSIS)},
        "metric_contract": "per_pdu_cohort_v1",
        "link_metric_contract": "vehicle_link_state_v2",
        "connectivity_mode": base.get("connectivity_mode", args.connectivity_mode),
        "scheduler_policy": "gbr_debt_rr_v1",
        "loss_grace_ms": 1000,
        "interval_candidates_us": list(intervals),
        "evaluated_all_intervals": True,
        "results": results,
        "selected": selected,
        "selected_interval_us": selected.get("interval_us") if selected else None,
        "status": phase_status,
        "scientific_decision": "approved" if selected else phase_status,
        "provenance": base.get("provenance", {}),
        "parallel_execution": _parallel_execution_contract(slots),
        "validity": {"real_pdcp_only": True, "proxy_rows_allowed": False, "smoke_used_as_baseline": False},
        "candidate_manifests": candidate_manifests,
    }
    _write_json(phase1_root / "selected_vehicle_profile.json", phase)
    execution = [
        {
            "interval_us": interval,
            "slot_id": slots[index % len(slots)].slot_id,
            "returncode": statuses.get(str(interval)),
            "root": str(phase1_root / f"interval_{interval}us"),
            "command": command,
        }
        for index, (interval, (_, command)) in enumerate(zip(intervals, commands))
    ]
    return (
        0 if all(statuses.get(str(interval)) == 0 for interval in intervals) else 3,
        execution,
        phase,
    )


def _phase2_parallel(
    args: argparse.Namespace,
    phase2_root: Path,
    selected_interval: int,
    slots: tuple[Any, ...],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    commands: list[tuple[str, list[str]]] = []
    for seed, slot in zip(VALIDATION_SEEDS, slots):
        root = phase2_root / f"seed_{seed}"
        commands.append((str(seed), _runner_command(
            args, output_root=root, seed=seed, intervals=(selected_interval,),
            execution_slot=slot.slot_id,
        )))
    statuses = _run_parallel_commands(commands)
    manifests: list[dict[str, Any]] = []
    execution: list[dict[str, Any]] = []
    for seed, slot in zip(VALIDATION_SEEDS, slots):
        root = phase2_root / f"seed_{seed}"
        manifest = _read_json(root / "selected_vehicle_profile.json")
        manifests.append(manifest)
        execution.append({
            "seed": seed,
            "root": str(root),
            "returncode": statuses.get(str(seed)),
            "command": next(command for key, command in commands if key == str(seed)),
            "selected_interval_us": selected_interval,
            "slot_id": slot.slot_id,
        })
    return manifests, execution


def _seed_summary(seed: int, root: Path, manifest: dict[str, Any], *, reused: bool) -> dict[str, Any]:
    selected = manifest.get("selected") if isinstance(manifest.get("selected"), dict) else {}
    complete, reasons = _candidate_is_complete_and_valid(selected)
    interval = selected.get("interval_us")
    candidate_dir = root / f"interval_{interval}us" if isinstance(interval, int) else None
    return {
        "seed": seed,
        "campaign_dir": str(root),
        "selected_interval_us": interval,
        "reused_from_phase1": reused,
        "status": manifest.get("status"),
        "scientific_decision": manifest.get("scientific_decision"),
        "complete_and_valid": complete,
        "validation_reasons": reasons,
        "selected_result": selected,
        "scheduler_telemetry_warning": _scheduler_warning(candidate_dir) if candidate_dir else {
            "status": "unavailable",
            "included_in_sla_or_gbr_evidence": False,
        },
    }


def _campaign_status(
    phase1: dict[str, Any], seed_summaries: dict[int, dict[str, Any]], provenance_ok: bool,
    provenance_reasons: list[str],
) -> tuple[str, str, str | None]:
    if not isinstance(phase1.get("selected"), dict):
        return "baseline_infeasible", "baseline_infeasible", "no_interval_passed_phase1_seed47"
    if not provenance_ok:
        return "blocked", "blocked", provenance_reasons[0]
    failures = [summary for summary in seed_summaries.values() if not summary["complete_and_valid"]]
    if failures:
        metric_invalid = any(any(
            token in reason for token in ("trace", "window_evidence", "simulation_completion", "metric_contract")
        ) for summary in failures for reason in summary["validation_reasons"])
        return (
            "metric_invalid" if metric_invalid else "baseline_infeasible",
            "metric_invalid" if metric_invalid else "baseline_infeasible",
            failures[0]["validation_reasons"][0] if failures[0]["validation_reasons"] else "seed_validation_failed",
        )
    return "passed", "approved", None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--binary", type=Path, default=DEFAULT_BINARY)
    parser.add_argument("--profile", required=True, choices=sorted(V2X_PROFILES))
    parser.add_argument(
        "--arm-mode",
        choices=("rapp_only", "sac_l2_frozen", "tasam_v2x_frozen"),
        default="rapp_only",
        help="política congelada sob teste; o padrão mantém a matriz rApp-only",
    )
    parser.add_argument("--selection-seed", type=int, default=SELECTION_SEED)
    parser.add_argument("--validation-seeds", default="45,46")
    parser.add_argument("--intervals-us", default="4000,6000,8000,12000,16000")
    parser.add_argument("--wall-time", type=float, default=43200.0)
    parser.add_argument("--min-free-gib", type=float, default=20.0)
    parser.add_argument("--connectivity-mode", choices=("auto", "mmwave_only", "lte_anchored_mc"), default="auto")
    parser.add_argument(
        "--energy-phase", action="store_true",
        help="subfase pós-SLA que libera somente potência E2",
    )
    parser.add_argument(
        "--parallel-slots", default=None,
        help="slots isolados separados por vírgula; omitido mantém a matriz serial",
    )
    args = parser.parse_args()

    args.output_root = args.output_root.resolve()
    args.checkpoint = args.checkpoint.resolve()
    args.binary = args.binary.resolve()
    try:
        args.output_root.relative_to(RUNS_ROOT)
    except ValueError as exc:
        raise SystemExit(f"output-root precisa estar em {RUNS_ROOT}") from exc
    if args.output_root.exists() and any(args.output_root.iterdir()):
        raise SystemExit(f"output-root já contém artefatos: {args.output_root}")
    if args.selection_seed != SELECTION_SEED:
        raise SystemExit("a seleção da matriz exige seed 47")
    validation_seeds = _parse_int_list(args.validation_seeds, label="validation-seeds")
    if validation_seeds != VALIDATION_SEEDS:
        raise SystemExit("a validação da matriz exige seeds 45,46 nessa ordem")
    intervals = _parse_int_list(args.intervals_us, label="intervals-us")
    if intervals != CANONICAL_INTERVALS:
        raise SystemExit("a matriz exige os intervalos canônicos 4000,6000,8000,12000,16000")
    if not args.checkpoint.is_dir():
        raise SystemExit(f"checkpoint ausente: {args.checkpoint}")
    if not args.binary.is_file() or not os.access(args.binary, os.X_OK):
        raise SystemExit(f"binário ns-3 ausente ou não executável: {args.binary}")
    # Matrix output must not be created until its binary can emit every strict
    # V2X artifact declared in the aggregate manifest.
    assert_v2x_binary_fresh(args.binary)
    if args.min_free_gib < 20.0:
        raise SystemExit("a matriz exige ao menos 20 GiB livres antes de cada candidato")

    parallel_slots = resolve_slots(args.parallel_slots) if args.parallel_slots else ()
    if parallel_slots:
        if len(parallel_slots) != 2:
            raise SystemExit("a matriz paralela exige exatamente os dois slots: slot-a,slot-b")
        try:
            _assert_parallel_preflight(parallel_slots)
        except (OSError, RuntimeError, ValueError) as exc:
            raise SystemExit(f"preflight de isolamento V2X falhou: {exc}") from exc

    args.output_root.mkdir(parents=True, exist_ok=False)
    phase1_root = args.output_root / "phase1_seed47"
    execution: dict[str, Any] = {"phase1": {}, "phase2": [], "parallel_execution": None}
    parity_report: dict[str, Any] | None = None

    if parallel_slots:
        execution["parallel_execution"] = _parallel_execution_contract(parallel_slots)
        parity_valid, parity_report = _run_parity_smoke(args, args.output_root, parallel_slots)
        execution["parallel_execution"]["parity_smoke"] = parity_report
        if not parity_valid:
            blocked = {
                "schema": "greenran.campaign_manifest.v1",
                "kind": "vehicle_feasibility_matrix",
                "status": "metric_invalid",
                "scientific_decision": "blocked",
                "profile": args.profile,
                "parallel_execution": execution["parallel_execution"],
                "block_reason": "parallel_parity_smoke_failed",
            }
            report = {
                "schema": "greenran.campaign_report.v1",
                "report_type": "vehicle_feasibility_matrix",
                "campaign_dir": str(args.output_root),
                "status": "metric_invalid",
                "scientific_decision": "blocked",
                "rejection_reason": "parallel_parity_smoke_failed",
                "phases": execution,
                "parity_smoke": parity_report,
                "comparison": {"status": "blocked", "reason": "parity smoke inválido"},
            }
            _write_json(args.output_root / "selected_vehicle_profile.json", blocked)
            _write_json(args.output_root / "campaign_manifest.json", blocked)
            _write_json(args.output_root / "campaign_report.json", report)
            return 2
        phase1_returncode, phase1_execution, phase1 = _phase1_parallel(
            args, phase1_root, intervals, parallel_slots
        )
        execution["phase1"] = {
            "seed": 47,
            "root": str(phase1_root),
            "returncode": phase1_returncode,
            "evaluated_intervals_us": list(intervals),
            "arms": phase1_execution,
        }
        selected = phase1.get("selected") if isinstance(phase1.get("selected"), dict) else None
        selected_interval = selected.get("interval_us") if isinstance(selected, dict) else None
        manifests: dict[int, dict[str, Any]] = {47: phase1}
        if isinstance(selected_interval, int) and selected_interval in CANONICAL_INTERVALS:
            phase2_root = args.output_root / "phase2_selected_interval"
            phase2_manifests, phase2_execution = _phase2_parallel(
                args, phase2_root, selected_interval, parallel_slots
            )
            execution["phase2"] = phase2_execution
            for seed, child in zip(VALIDATION_SEEDS, phase2_manifests):
                manifests[seed] = child
    else:
        phase1_returncode, phase1_command = _run_seed(
            args, output_root=phase1_root, seed=SELECTION_SEED, intervals=intervals, evaluate_all=True
        )
        phase1 = _read_json(phase1_root / "selected_vehicle_profile.json")
        selected = phase1.get("selected") if isinstance(phase1.get("selected"), dict) else None
        selected_interval = selected.get("interval_us") if isinstance(selected, dict) else None
        manifests = {47: phase1} if phase1 else {}
        execution["phase1"] = {
            "seed": 47, "root": str(phase1_root), "returncode": phase1_returncode,
            "command": phase1_command, "evaluated_intervals_us": list(intervals),
        }
        if isinstance(selected_interval, int) and selected_interval in CANONICAL_INTERVALS:
            phase2_root = args.output_root / "phase2_selected_interval"
            for seed in VALIDATION_SEEDS:
                seed_root = phase2_root / f"seed_{seed}"
                returncode, command = _run_seed(
                    args, output_root=seed_root, seed=seed, intervals=(selected_interval,), evaluate_all=False
                )
                manifests[seed] = _read_json(seed_root / "selected_vehicle_profile.json")
                execution["phase2"].append({
                    "seed": seed, "root": str(seed_root), "returncode": returncode, "command": command,
                    "selected_interval_us": selected_interval,
                })

    seed_summaries = {
        seed: _seed_summary(seed, root, manifests.get(seed, {}), reused=(seed == 47))
        for seed, root in {
            47: phase1_root,
            45: args.output_root / "phase2_selected_interval" / "seed_45",
            46: args.output_root / "phase2_selected_interval" / "seed_46",
        }.items()
        if manifests.get(seed)
    }
    provenance_ok, provenance_reasons = _provenance_compatible(manifests)
    status, decision, rejection_reason = _campaign_status(
        phase1, seed_summaries, provenance_ok, provenance_reasons
    )
    phase1_provenance = phase1.get("provenance") if isinstance(phase1.get("provenance"), dict) else {}
    selected_reference = {
        "seed": 47,
        "phase": "phase1",
        "campaign_dir": str(phase1_root),
        "selected_interval_us": selected_interval,
        "result_path": str(phase1_root / f"interval_{selected_interval}us" / "feasibility_result.json") if selected_interval else None,
        "complete_and_valid": seed_summaries.get(47, {}).get("complete_and_valid", False),
    }
    manifest = {
        "schema": "greenran.autonomous_vehicle_feasibility.v4",
        "manifest_version": 4,
        "kind": "vehicle_feasibility_matrix",
        "profile": args.profile,
        "evaluation_arm_mode": args.arm_mode,
        "evaluation_frozen": args.arm_mode != "rapp_only",
        "topology": phase1.get("topology") or {"ue_count": 20, "du_count": 3, "vehicle_imsis": list(VEHICLE_IMSIS)},
        "metric_contract": "per_pdu_cohort_v1",
        "link_metric_contract": "vehicle_link_state_v2",
        "connectivity_mode": phase1.get("connectivity_mode"),
        "scheduler_policy": "gbr_debt_rr_v1",
        "loss_grace_ms": 1000,
        "sla": {
            "packet_loss_percent_lt": 1.0, "latency_p95_ms_lt": 20.0,
            "min_tx_pdus_per_vehicle_window": 500, "proxy_allowed": False,
        },
        "interval_candidates_us": list(CANONICAL_INTERVALS),
        "selection_seed": 47,
        "validation_seeds": [45, 46],
        "seeds": [45, 46, 47],
        "selected_interval_us": selected_interval,
        "phase1_seed47": {
            "campaign_dir": str(phase1_root), "all_intervals_evaluated": True,
            "result": phase1.get("results", []), "selected": selected,
        },
        "phase2_selected_interval": execution["phase2"],
        "seed47_reused_result": selected_reference,
        "results_by_seed": {str(seed): seed_summaries.get(seed, {}) for seed in (45, 46, 47)},
        "multi_seed_validation": {
            "required_seeds": [45, 46, 47],
            "complete_seeds": sorted(seed_summaries),
            "seed47_reused_from_phase1": True,
            "provenance_compatible": provenance_ok,
            "provenance_reasons": provenance_reasons,
            "valid": status == "passed",
        },
        "provenance": phase1_provenance,
        "parallel_execution": execution.get("parallel_execution") or {
            "schema": "greenran.v2x.parallel_execution.v1", "mode": "serial",
        },
        "status": status,
        "scientific_decision": decision,
        "promotion_eligible": status == "passed" and decision == "approved",
        "rejection_reason": rejection_reason,
        "validity": {
            "real_pdcp_only": True,
            "proxy_rows_allowed": False,
            "smoke_used_as_baseline": False,
            "non_vehicle_scheduler_rows_are_warning_only": True,
        },
        "created_at": int(time.time()),
    }
    report = {
        "schema": "greenran.campaign_report.v1",
        "report_type": "vehicle_feasibility_matrix",
        "campaign_dir": str(args.output_root),
        "status": status,
        "scientific_decision": decision,
        "rejection_reason": rejection_reason,
        "selected_interval_us": selected_interval,
        "phases": execution,
        "parallel_execution": manifest["parallel_execution"],
        "results_by_seed": manifest["results_by_seed"],
        "seed47_reused_result": selected_reference,
        "multi_seed_validation": manifest["multi_seed_validation"],
        "provenance": phase1_provenance,
        "comparison": {
            "status": "blocked",
            "reason": "ASGARD permanece bloqueado até baseline multi-seed aprovada",
        },
    }
    _write_json(args.output_root / "selected_vehicle_profile.json", manifest)
    _write_json(args.output_root / "campaign_manifest.json", {
        "schema": "greenran.campaign_manifest.v1",
        "kind": "vehicle_feasibility_matrix",
        "status": status,
        "scientific_decision": decision,
        "profile": args.profile,
        "evaluation_arm_mode": args.arm_mode,
        "evaluation_frozen": args.arm_mode != "rapp_only",
        "selected_interval_us": selected_interval,
        "campaign_report": str(args.output_root / "campaign_report.json"),
        "block_reason": rejection_reason,
        "provenance": phase1_provenance,
        "parallel_execution": manifest["parallel_execution"],
    })
    _write_json(args.output_root / "campaign_report.json", report)
    return 0 if status == "passed" else 3 if status == "baseline_infeasible" else 2


if __name__ == "__main__":
    raise SystemExit(main())
