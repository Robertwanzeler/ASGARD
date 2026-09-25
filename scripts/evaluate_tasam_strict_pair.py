#!/usr/bin/env python3
"""Fail-closed acceptance gate for the causal 600 s TA-SAM pilot."""

from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
from pathlib import Path
from typing import Any


IMSIS = tuple(range(1, 21))
INFRA_KEYS = (
    "cpu_usage_usec",
    "memory_byte_seconds",
    "memory_peak_bytes",
    "management_tx_bytes",
    "io_bytes",
    "artifact_bytes",
)


def _number(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def _loss(row: sqlite3.Row) -> float:
    if "packet_loss_percent" in row.keys() and row["packet_loss_percent"] is not None:
        return max(0.0, _number(row["packet_loss_percent"], 100.0))
    tx = _number(row["tx_pdus"] if "tx_pdus" in row.keys() else 0)
    rx = _number(row["rx_pdus"] if "rx_pdus" in row.keys() else 0)
    return 100.0 if tx <= 0 else 100.0 * max(0.0, 1.0 - rx / tx)


def _real_row(row: sqlite3.Row) -> bool:
    keys = set(row.keys())
    required = {"latency_is_proxy", "pdcp_provenance", "has_latency_samples"}
    return (
        required.issubset(keys)
        and int(row["latency_is_proxy"] or 0) == 0
        and str(row["pdcp_provenance"] or "") == "pdcp_real"
        and int(row["has_latency_samples"] or 0) == 1
    )


def _sla_reasons(row: sqlite3.Row) -> list[str]:
    imsi = int(row["imsi"] or 0)
    reasons: list[str] = []
    if not _real_row(row):
        reasons.append("non_real_or_missing_pdcp")
    throughput = _number(row["throughput_kbps"])
    p95 = _number(row["latency_p95_us"] if "latency_p95_us" in row.keys()
                  else row["latency_us"])
    maximum = _number(row["latency_max_us"] if "latency_max_us" in row.keys()
                      else row["latency_us"])
    loss = _loss(row)
    tx_pdus = _number(row["tx_pdus"] if "tx_pdus" in row.keys() else 0)
    rx_pdus = _number(row["rx_pdus"] if "rx_pdus" in row.keys() else 0)
    backlog = _number(row["backlog_bytes"] if "backlog_bytes" in row.keys() else 0)
    if tx_pdus <= 0 or rx_pdus <= 0 or (backlog > 0 and throughput <= 0):
        reasons.append("disconnected_or_unserved")
    if 1 <= imsi <= 3:
        if throughput < 25_000:
            reasons.append("camera_throughput")
        if p95 > 80_000:
            reasons.append("camera_p95")
    elif 4 <= imsi <= 15:
        delivery = 0.0 if tx_pdus <= 0 else 100.0 * rx_pdus / tx_pdus
        if delivery < 95.0:
            reasons.append("sensor_delivery")
        if loss > 5.0:
            reasons.append("sensor_loss")
        if p95 > 500_000:
            reasons.append("sensor_p95")
    elif 16 <= imsi <= 20:
        if p95 > 20_000:
            reasons.append("vehicle_p95_latency")
        if loss > 1.0:
            reasons.append("vehicle_loss")
    else:
        reasons.append("invalid_imsi")
    return reasons


def evaluate_ue_windows(run_dir: Path, *, warmup_s: int = 30,
                        duration_s: int = 600,
                        imsis: tuple[int, ...] = IMSIS) -> dict[str, Any]:
    db = run_dir / "rapp_data_lake.db"
    if not db.is_file():
        return {"valid": False, "reason": "missing_database", "violations": []}
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        columns = {row[1] for row in conn.execute("pragma table_info(ue_metrics)")}
        if "sim_time_s" not in columns:
            return {"valid": False, "reason": "missing_sim_time_s", "violations": []}
        rows = conn.execute(
            "select * from ue_metrics where sim_time_s >= ? and sim_time_s < ? "
            "order by sim_time_s, id", (warmup_s, duration_s)
        ).fetchall()
    finally:
        conn.close()
    by_window: dict[tuple[int, int], sqlite3.Row] = {}
    for row in rows:
        window = int(math.floor(_number(row["sim_time_s"], -1)))
        imsi = int(row["imsi"] or 0)
        if warmup_s <= window < duration_s and imsi in imsis:
            by_window[(window, imsi)] = row
    violations: list[dict[str, Any]] = []
    for window in range(warmup_s, duration_s):
        for imsi in imsis:
            row = by_window.get((window, imsi))
            reasons = ["missing_ue_window"] if row is None else _sla_reasons(row)
            if reasons:
                violations.append({"window_s": window, "imsi": imsi, "reasons": reasons})
    expected = (duration_s - warmup_s) * len(imsis)
    return {
        "valid": not violations and len(by_window) == expected,
        "expected_ue_windows": expected,
        "observed_ue_windows": len(by_window),
        "violation_count": len(violations),
        "violations": violations[:200],
        "violations_truncated": max(0, len(violations) - 200),
    }


def causal_energy(run_dir: Path, *, warmup_s: int = 30,
                  duration_s: int = 600) -> dict[str, Any]:
    candidates = list((run_dir / "ns3_traces").glob("energyfilecell*.csv"))
    candidates += list(run_dir.glob("energyfilecell*.csv"))
    files = sorted(set(path.resolve() for path in candidates))
    total = 0.0
    valid_cells = 0
    for path in files:
        samples: list[tuple[float, float]] = []
        try:
            with path.open(newline="", encoding="utf-8") as handle:
                for row in csv.DictReader(handle):
                    samples.append((_number(row.get("Time"), -1),
                                    _number(row.get("NetEnergy"), -1)))
        except OSError:
            continue
        start = min((sample for sample in samples if sample[0] >= warmup_s), default=None)
        end = max((sample for sample in samples if sample[0] <= duration_s), default=None)
        if start is None or end is None or end[0] < duration_s - 1 or end[1] < start[1]:
            continue
        total += end[1] - start[1]
        valid_cells += 1
    return {
        "valid": valid_cells > 0 and valid_cells == len(files),
        "kind": "ns3_mmwave_causal_energy",
        "energy_j": total,
        "cell_count": valid_cells,
        "files": [str(path) for path in files],
    }


def scheduler_symbols(run_dir: Path, *, warmup_s: int = 30,
                      duration_s: int = 600) -> dict[str, Any]:
    path = run_dir / "ns3_traces" / "EnbSchedAllocTraces.txt"
    symbols = allocations = 0
    first_time = math.inf
    last_time = -math.inf
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            parts = line.split("\t")
            if len(parts) < 9 or parts[0].lower() == "frame":
                continue
            try:
                frame, subframe = int(parts[0]), int(parts[1])
                rnti, count = int(parts[3]), int(parts[5])
            except ValueError:
                continue
            sim_time = frame * 0.01 + subframe * 0.001
            first_time = min(first_time, sim_time)
            last_time = max(last_time, sim_time)
            if warmup_s <= sim_time < duration_s and rnti > 0:
                symbols += max(0, count)
                allocations += 1
    except OSError:
        pass
    complete = first_time <= 0.01 and last_time >= duration_s - 0.02
    return {"valid": allocations > 0 and complete, "symbols": symbols,
            "allocations": allocations, "first_time_s": first_time,
            "last_time_s": last_time, "source": str(path)}


def _json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def infrastructure(run_dir: Path) -> dict[str, Any]:
    payload = _json(run_dir / "infrastructure_metrics.json")
    metrics = payload.get("totals") if isinstance(payload.get("totals"), dict) else {}
    valid = payload.get("schema") == "greenran.infrastructure.metrics.v1" and payload.get("complete") is True and all(
        key in metrics and _number(metrics[key], -1) >= 0 for key in INFRA_KEYS
    )
    return {"valid": valid, **{key: _number(metrics.get(key), -1) for key in INFRA_KEYS}}


def e2_audit(run_dir: Path, *, warmup_s: int = 30) -> dict[str, Any]:
    paths = [run_dir / "xapp_intents" / "tasam_control_audit.jsonl",
             run_dir / "tasam_control_audit.jsonl"]
    path = next((candidate for candidate in paths if candidate.is_file()), paths[0])
    rows = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            value = json.loads(line)
            if isinstance(value, dict):
                rows.append(value)
    except (OSError, json.JSONDecodeError):
        pass
    evaluation_rows = [
        row for row in rows
        if _number(row.get("sim_time_s"), warmup_s) >= warmup_s
    ]
    confirmed = [row for row in evaluation_rows if row.get("ack") and row.get("applied")
                 and int(row.get("observed_confirmations", 0)) > 0]
    fallback = [row for row in evaluation_rows if row.get("fallback") or row.get("mode") == "failsafe"]
    observation_path = run_dir / "ns3_traces" / "TasamControlObservations.csv"
    observations: list[dict[str, Any]] = []
    try:
        with observation_path.open(newline="", encoding="utf-8") as handle:
            observations = list(csv.DictReader(handle))
    except OSError:
        pass
    scheduler_phy_confirmed = 0
    observation_failures: list[dict[str, Any]] = []
    for row in evaluation_rows:
        sequence = int(row.get("sequence", -1))
        start = _number(row.get("sim_time_s"), -1)
        deadline = start + _number(row.get("ttl_ms"), 0) / 1000.0
        requested_cells = row.get("requested_cells") if isinstance(row.get("requested_cells"), list) else []
        missing_cells = []
        for cell in requested_cells:
            cell_id = int(cell.get("cell_id", -1))
            power = int(cell.get("tx_power_percent", -1))
            expected_ues = int(cell.get("expected_ues", 0))
            observed = any(
                int(_number(sample.get("CellId"), -1)) == cell_id
                and int(_number(sample.get("TransactionId"), -1)) == sequence
                and int(_number(sample.get("TxPowerPercent"), -1)) == power
                and int(_number(sample.get("ActiveUes"), -1)) == expected_ues
                and start <= _number(sample.get("Time"), -1) <= deadline
                for sample in observations
            )
            if not observed:
                missing_cells.append(cell_id)
        if requested_cells and not missing_cells:
            scheduler_phy_confirmed += 1
        else:
            observation_failures.append({"sequence": sequence, "missing_cells": missing_cells})
    valid = (
        bool(evaluation_rows)
        and len(confirmed) == len(evaluation_rows)
        and scheduler_phy_confirmed == len(evaluation_rows)
        and not fallback
    )
    return {"valid": valid,
            "transactions": len(evaluation_rows), "warmup_transactions": len(rows) - len(evaluation_rows), "confirmed": len(confirmed),
            "scheduler_phy_confirmed": scheduler_phy_confirmed,
            "observation_failures": observation_failures[:100],
            "fallbacks": len(fallback), "source": str(path),
            "observation_source": str(observation_path)}


def experiment_contract(
    baseline_dir: Path,
    combined_dir: Path,
    *,
    duration_s: int = 600,
    expected_seed: int | None = 47,
    expected_profile: str | None = None,
    expected_checkpoint_sha256: str | None = None,
    expected_baseline_mode: str | None = None,
) -> dict[str, Any]:
    baseline = _json(baseline_dir / "arm_manifest.json")
    combined = _json(combined_dir / "arm_manifest.json")
    baseline_contract = baseline.get("contract") if isinstance(baseline.get("contract"), dict) else {}
    combined_contract = combined.get("contract") if isinstance(combined.get("contract"), dict) else {}
    before = str(combined.get("checkpoint_sha256_before", ""))
    after = str(combined.get("checkpoint_sha256_after", ""))
    same_profile = bool(baseline.get("profile")) and baseline.get("profile") == combined.get("profile")
    if expected_profile is not None:
        same_profile = same_profile and baseline.get("profile") == expected_profile
    seed_matches = baseline.get("seed") == combined.get("seed")
    if expected_seed is not None:
        seed_matches = seed_matches and baseline.get("seed") == expected_seed
    checkpoint_frozen = (
        combined_contract.get("frozen_checkpoint") is True
        and combined.get("checkpoint_frozen_verified") is True
        and bool(before)
        and before == after
    )
    if expected_checkpoint_sha256 is not None:
        checkpoint_frozen = checkpoint_frozen and before == expected_checkpoint_sha256
    checks = {
        "manifests_present": bool(baseline) and bool(combined),
        "expected_seed": seed_matches,
        "same_profile": same_profile,
        "duration_600": _number(baseline.get("sim_time_s"), -1) == duration_s and
                        _number(combined.get("sim_time_s"), -1) == duration_s,
        "same_schedule": bool(baseline.get("pairing_schedule_id")) and
                         baseline.get("pairing_schedule_id") == combined.get("pairing_schedule_id"),
        "arms_finished": baseline.get("status") == "finished" and combined.get("status") == "finished",
        # The actuating rApp control uses the same E2 actuator as ASGARD,
        # while keeping both ARMD and TA-SAM disabled.  A passive historical
        # rApp arm remains readable but cannot satisfy an active-control gate.
        "rapp_only_isolated": baseline.get("mode") in {"rapp_only", "rapp_only_actuating"} and
                              not baseline_contract.get("tasam_enabled") and
                              baseline_contract.get("armd_mode") == "off",
        "combined_contract": combined.get("mode") == "combined" and
                             combined_contract.get("tasam_enabled") is True and
                             combined_contract.get("armd_mode") == "assist" and
                             combined_contract.get("actuation_enabled") is True,
        "checkpoint_frozen": checkpoint_frozen,
        "same_instrumentation": baseline.get("energy_model") == combined.get("energy_model"),
    }
    if expected_baseline_mode is not None:
        checks["expected_baseline_mode"] = (
            baseline.get("mode") == expected_baseline_mode
            and (
                expected_baseline_mode != "rapp_only_actuating"
                or baseline_contract.get("actuation_enabled") is True
            )
        )
    return {"valid": all(checks.values()), "checks": checks,
            "baseline_manifest": str(baseline_dir / "arm_manifest.json"),
            "combined_manifest": str(combined_dir / "arm_manifest.json")}


def evaluate_pair(
    baseline_dir: Path,
    combined_dir: Path,
    *,
    warmup_s: int = 30,
    duration_s: int = 600,
    expected_seed: int | None = 47,
    expected_profile: str | None = None,
    expected_checkpoint_sha256: str | None = None,
    expected_baseline_mode: str | None = None,
) -> dict[str, Any]:
    contract = experiment_contract(
        baseline_dir,
        combined_dir,
        duration_s=duration_s,
        expected_seed=expected_seed,
        expected_profile=expected_profile,
        expected_checkpoint_sha256=expected_checkpoint_sha256,
        expected_baseline_mode=expected_baseline_mode,
    )
    baseline = {
        "sla": evaluate_ue_windows(baseline_dir, warmup_s=warmup_s, duration_s=duration_s),
        "energy": causal_energy(baseline_dir, warmup_s=warmup_s, duration_s=duration_s),
        "radio": scheduler_symbols(baseline_dir, warmup_s=warmup_s, duration_s=duration_s),
        "infra": infrastructure(baseline_dir),
        "e2": e2_audit(baseline_dir, warmup_s=warmup_s),
    }
    combined = {
        "sla": evaluate_ue_windows(combined_dir, warmup_s=warmup_s, duration_s=duration_s),
        "energy": causal_energy(combined_dir, warmup_s=warmup_s, duration_s=duration_s),
        "radio": scheduler_symbols(combined_dir, warmup_s=warmup_s, duration_s=duration_s),
        "infra": infrastructure(combined_dir),
        "e2": e2_audit(combined_dir, warmup_s=warmup_s),
    }
    infra_non_worse = all(combined["infra"][key] <= baseline["infra"][key]
                          for key in INFRA_KEYS)
    infra_strict = any(combined["infra"][key] < baseline["infra"][key]
                       for key in INFRA_KEYS)
    criteria = {
        "experiment_contract_valid": contract["valid"],
        "zero_sla_violations": baseline["sla"]["valid"] and combined["sla"]["valid"],
        "causal_energy_complete": baseline["energy"]["valid"] and combined["energy"]["valid"],
        "energy_strictly_lower": combined["energy"]["energy_j"] < baseline["energy"]["energy_j"],
        "radio_complete": baseline["radio"]["valid"] and combined["radio"]["valid"],
        "symbols_strictly_lower": combined["radio"]["symbols"] < baseline["radio"]["symbols"],
        "infrastructure_complete": baseline["infra"]["valid"] and combined["infra"]["valid"],
        "infrastructure_non_worse_each": infra_non_worse,
        "infrastructure_strict_improvement": infra_strict,
        "all_control_e2_transactions_observed": (
            baseline["e2"]["valid"] if expected_baseline_mode == "rapp_only_actuating" else True
        ),
        "all_e2_transactions_observed": combined["e2"]["valid"],
    }
    return {"schema": "greenran.tasam.strict_pair.v1", "seed": expected_seed,
            "warmup_s": warmup_s, "duration_s": duration_s,
            "experiment_contract": contract,
            "baseline": baseline, "combined": combined,
            "criteria": criteria, "passed": all(criteria.values())}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("baseline", type=Path)
    parser.add_argument("combined", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = evaluate_pair(args.baseline.resolve(), args.combined.resolve())
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
