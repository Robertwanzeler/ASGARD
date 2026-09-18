#!/usr/bin/env python3
"""Find the highest feasible real-PDCP load for autonomous vehicles.

This is a protected rApp-only capability check.  It never enables TA-SAM or
ARMD and it does not change the canonical 20-UE/3-DU topology.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ARM = ROOT / "scripts" / "run_tasam_online_arm.py"
CLEANUP = ROOT / "scripts" / "stop_on_decision_target.py"
DEFAULT_BINARY = ROOT / "ns-O-RAN-flexric/mmwave-LENA-oran/build/scratch/ns3.42-Energy_saving_with_cell_utilization_scenario-default"
DEFAULT_PROFILE = "tasam_training_balanced_v4_v2x_gbr_priority"
DEFAULT_INTERVALS = (4000, 6000, 8000, 12000, 16000)


def _native_vehicle_bearer_evidence(candidate_dir: Path, profile: str) -> tuple[bool, str, dict[str, Any] | None]:
    """Validate the native bearer manifest for the versioned V2X profile."""
    if profile not in {
        "tasam_training_balanced_v4_v2x",
        "tasam_training_balanced_v4_v2x_gbr",
        "tasam_training_balanced_v4_v2x_gbr_priority",
    }:
        return True, "not_required_for_profile", None
    path = candidate_dir / "ns3_energy" / "VehicleBearerManifest.json"
    if not path.is_file():
        return False, "native_vehicle_bearer_manifest_missing", None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False, "native_vehicle_bearer_manifest_invalid", None
    vehicles = payload.get("vehicles")
    imsIs = sorted(int(item.get("imsi")) for item in vehicles or [] if isinstance(item, dict) and item.get("imsi") is not None)
    is_gbr_profile = profile.endswith("_gbr") or profile.endswith("_gbr_priority")
    priority_ok = (
        not profile.endswith("_gbr_priority")
        or payload.get("scheduler_gbr_priority") is True
    )
    gbr_ok = (
        not is_gbr_profile
        or (int(payload.get("gbr_dl_bps", 0)) > 0 and int(payload.get("mbr_dl_bps", 0)) >= int(payload.get("gbr_dl_bps", 0)))
    )
    valid = (
        payload.get("schema") == "greenran.ns3.vehicle_bearer_manifest.v1"
        and payload.get("profile") == profile
        and payload.get("qci") == "GBR_V2X_MESSAGES"
        and int(payload.get("priority", 0)) == 25
        and gbr_ok
        and priority_ok
        and int(payload.get("cell_id", 0)) == 4
        and imsIs == [16, 17, 18, 19, 20]
        and all(item.get("qci") == "GBR_V2X_MESSAGES" and int(item.get("cell_id", 0)) == 4 for item in vehicles)
    )
    return valid, "ok" if valid else "native_vehicle_bearer_manifest_mismatch", payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    last_error: OSError | None = None
    for attempt in range(4):
        try:
            path.write_text(data, encoding="utf-8")
            return
        except OSError as exc:
            last_error = exc
            if exc.errno != 24 or attempt == 3:
                raise
            # A collector can release its last descriptors just after the
            # process tree has been signalled.  Retry only this bounded,
            # local manifest write; never hide a persistent filesystem error.
            time.sleep(0.25 * (attempt + 1))
    if last_error is not None:
        raise last_error


def _cleanup_candidate(candidate_dir: Path) -> dict[str, Any]:
    """Drain only the candidate's PID-file-scoped workers.

    ``run_tasam_online_arm.py`` normally performs its own shutdown, but an
    early feasibility rejection can terminate the wrapper while a collector
    or watcher survives.  Running the existing idempotent cleanup helper here
    prevents those workers from leaking into the next interval.  The helper
    never uses process names or a broad kill.
    """
    db_path = candidate_dir / "rapp_data_lake.db"
    command = [
        sys.executable,
        str(CLEANUP),
        "--state-dir", str(candidate_dir),
        "--db", str(db_path),
        "--target", "1",
        "--cleanup-only",
        "--poll-seconds", "0.2",
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env=dict(os.environ),
            capture_output=True,
            text=True,
            timeout=90.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "error": str(exc), "command": command}
    result: dict[str, Any] = {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "command": command,
    }
    if completed.stdout:
        result["stdout"] = completed.stdout[-4000:]
    if completed.stderr:
        result["stderr"] = completed.stderr[-2000:]
    return result


def _local_path(path: Path, label: str, *, executable: bool = False) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(ROOT)
    except ValueError as exc:
        raise SystemExit(f"{label} fora do workspace local: {resolved}") from exc
    if "/run/media/" in str(resolved):
        raise SystemExit(f"{label} no HD externo é proibido: {resolved}")
    if not resolved.is_file() or (executable and not os.access(resolved, os.X_OK)):
        raise SystemExit(f"{label} ausente ou não executável: {resolved}")
    return resolved


def _raw_pdcp_bins(candidate_dir: Path, warmup_seconds: float, window_seconds: float,
                   scored_windows: int, min_tx_pdus: int) -> tuple[list[dict[str, Any]], str | None]:
    """Read native PDCP epochs and score non-overlapping real-PDCP windows.

    The SQLite collector may repeat the same simulated epoch while polling.
    Feasibility therefore reads the native trace directly and keys each row by
    (epoch end, IMSI), never by wall-clock timestamp or collector row count.
    """
    trace = candidate_dir / "ns3_traces" / "DlPdcpStats.txt"
    if not trace.is_file():
        return [], "pdcp_trace_missing"
    epochs: dict[tuple[float, int], tuple[int, int, float]] = {}
    try:
        with trace.open(encoding="utf-8", errors="replace") as handle:
            for raw in handle:
                line = raw.strip()
                if not line or line.startswith("%"):
                    continue
                fields = line.split()
                if len(fields) < 11:
                    continue
                try:
                    start, end = float(fields[0]), float(fields[1])
                    imsi = int(fields[3])
                    tx, rx = int(fields[6]), int(fields[8])
                    delay_s = float(fields[10])
                except (ValueError, IndexError):
                    continue
                if imsi not in {16, 17, 18, 19, 20} or not all(math.isfinite(v) for v in (start, end, delay_s)):
                    continue
                if end <= start or tx < 0 or rx < 0:
                    continue
                epochs[(round(end, 6), imsi)] = (tx, rx, delay_s * 1_000_000.0)
    except OSError as exc:
        return [], f"pdcp_trace_read_error:{exc}"

    bins: list[dict[str, Any]] = []
    vehicles = {16, 17, 18, 19, 20}
    for index in range(scored_windows):
        left = warmup_seconds + index * window_seconds
        right = left + window_seconds
        per_imsi: dict[int, list[tuple[int, int, float]]] = {imsi: [] for imsi in vehicles}
        per_imsi_latest_end: dict[int, float] = {imsi: float("-inf") for imsi in vehicles}
        for (end, imsi), values in epochs.items():
            if left < end <= right:
                per_imsi[imsi].append(values)
                per_imsi_latest_end[imsi] = max(per_imsi_latest_end[imsi], end)
        # A polling cycle can see the first row of a new window long before
        # the 10 s interval is complete.  Do not score or fail that partial
        # window: its small TX count is not evidence of a QoS violation.
        # Native PDCP epochs are 100 ms in this scenario, so allow one epoch
        # of rounding slack at the right edge.
        complete_window = all(
            per_imsi_latest_end[imsi] >= right - 0.2 for imsi in vehicles
        )
        if not complete_window:
            continue
        if set(imsi for imsi, rows in per_imsi.items() if rows) != vehicles:
            continue
        rows: dict[str, Any] = {}
        invalid_reason: str | None = None
        for imsi in sorted(vehicles):
            samples = per_imsi[imsi]
            tx = sum(v[0] for v in samples)
            rx = sum(v[1] for v in samples)
            delays = sorted(v[2] for v in samples if math.isfinite(v[2]))
            if tx < min_tx_pdus:
                invalid_reason = f"window={index}:insufficient_tx_imsi={imsi}:{tx}<{min_tx_pdus}"
            if not delays:
                invalid_reason = f"window={index}:latency_missing_imsi={imsi}"
            loss = max(0.0, float(tx - rx)) / tx * 100.0 if tx else None
            rank = max(0, min(len(delays) - 1, math.ceil(0.95 * len(delays)) - 1)) if delays else 0
            p95 = delays[rank] if delays else None
            rows[str(imsi)] = {
                "tx_pdus": tx, "rx_pdus": rx, "loss_percent": loss,
                "latency_p95_us": p95, "source": "pdcp_real_native_trace"
            }
            if loss is None or loss >= 1.0:
                invalid_reason = f"window={index}:loss_imsi={imsi}:{loss}"
            if p95 is None or p95 >= 20_000.0:
                invalid_reason = f"window={index}:latency_imsi={imsi}:{p95}"
        bins.append({"index": index, "start_s": left, "end_s": right,
                     "vehicles": rows, "valid": invalid_reason is None,
                     "invalid_reason": invalid_reason})
    return bins, None


def _candidate_metrics(candidate_dir: Path, warmup_seconds: float, window_seconds: float,
                       scored: int, min_tx_pdus: int) -> dict[str, Any]:
    bins, trace_error = _raw_pdcp_bins(candidate_dir, warmup_seconds, window_seconds, scored, min_tx_pdus)
    violations = [b["invalid_reason"] for b in bins if not b["valid"] and b.get("invalid_reason")]
    valid = len(bins) >= scored and not violations
    return {
        "valid": valid,
        "reason": "ok" if valid else (trace_error or (violations[0] if violations else "insufficient_complete_windows")),
        "complete_windows": len(bins), "valid_windows": sum(1 for b in bins if b["valid"]),
        "required_scored_windows": scored, "min_tx_pdus_per_vehicle_window": min_tx_pdus,
        "warmup_seconds": warmup_seconds, "window_seconds": window_seconds,
        "violations": violations[:20], "windows": bins[:scored],
        "evidence_source": "native_pdcp_trace_unique_sim_epochs",
    }


def _early_vehicle_sla_violation(candidate_dir: Path, warmup_seconds: float,
                                 window_seconds: float, min_tx_pdus: int) -> str | None:
    """Return a deterministic reason as soon as a candidate cannot pass.

    A feasibility candidate is an all-windows criterion.  Once a complete
    post-warm-up window violates the hard vehicle SLA, continuing the ns-3
    run only consumes disk and wall time and cannot change the outcome.
    """
    bins, _ = _raw_pdcp_bins(candidate_dir, warmup_seconds, window_seconds, 1000, min_tx_pdus)
    for item in bins:
        if item.get("index", 0) >= 0 and not item.get("valid"):
            return str(item.get("invalid_reason") or "vehicle_sla_invalid")
    return None


def run_candidate(args: argparse.Namespace, interval_us: int, candidate_dir: Path) -> dict[str, Any]:
    candidate_dir.mkdir(parents=True, exist_ok=False)
    env = dict(os.environ)
    env.update({
        "GREENRAN_LOCAL_ONLY": "1",
        "GREENRAN_CGROUP_ENFORCE": "1",
        "GREENRAN_CGROUP_ALLOW_UNENFORCED": "0",
        "GREENRAN_REQUIRE_REAL_PDCP": "1",
        "GREENRAN_REAL_ONLY": "1",
        "GREENRAN_TASAM_EXPORT_ALLOW_PROXY": "0",
        "GREENRAN_DB_SNAPSHOT_ENABLED": "0",
        "GREENRAN_TASAM_EXPORT_ENABLED": "0",
        "GREENRAN_NS3_FIXED_POWER_PERCENT": "100",
        "GREENRAN_NS3_ACTIVE_CELLS": "3",
        "GREENRAN_NS3_VEHICLE_PACKET_INTERVAL_US": str(interval_us),
        "GREENRAN_NS3_USE_MC_UE_DEVICES": "false",
        "GREENRAN_FEASIBILITY_LOCK_POWER": "1",
        "GREENRAN_NS3_SINGLE_RUN": "1",
        "GREENRAN_NS3_BIN": str(Path(args.binary).resolve()),
    })
    # The feasibility decision needs exactly the post-warm-up windows being
    # scored.  Running a fixed 600 s arm made the old 1,800 s wall limit
    # impossible at the measured native RTF and added unscored radio work.
    required_sim_time = (
        float(args.warmup_seconds)
        + float(args.scored_windows) * float(args.window_seconds)
        + 0.5
    )
    command = [
        sys.executable, str(ARM),
        "--mode", "rapp_only",
        "--run-dir", str(candidate_dir),
        "--seed", str(args.seed),
        "--profile", args.profile,
        "--wall-time", str(args.wall_time),
        "--sim-time", f"{required_sim_time:.3f}",
        "--checkpoint", str(Path(args.checkpoint).resolve()),
        "--decision-target", str(args.decision_target),
        "--min-free-gib", str(args.min_free_gib),
        "--native-fidelity", "--performance-min-rtf", "0.016",
    ]
    started = int(time.time())
    process = subprocess.Popen(command, cwd=ROOT, env=env)
    early_reason = None
    cleanup_result: dict[str, Any] = {}
    try:
        while process.poll() is None:
            early_reason = _early_vehicle_sla_violation(
                candidate_dir, args.warmup_seconds, args.window_seconds, args.min_tx_pdus
            )
            if early_reason is not None:
                write_json(
                    candidate_dir / "decision_target_stop.json",
                    {
                        "schema": "greenran.decision_target_stop.v1",
                        "reason": "early_vehicle_sla_violation",
                        "detail": early_reason,
                        "source": "vehicle_feasibility_guard",
                        "requested_at": time.time(),
                    },
                )
                process.terminate()
                break
            time.sleep(1.0)
    finally:
        try:
            completed = process.wait(timeout=45.0)
        except subprocess.TimeoutExpired:
            process.kill()
            completed = process.wait()
        # Always drain state-scoped helpers before inspecting the DB or
        # starting the next interval.  This is intentionally after the arm
        # gets its bounded chance to finalize its own manifest.
        cleanup_result = _cleanup_candidate(candidate_dir)
    evidence = _candidate_metrics(
        candidate_dir, args.warmup_seconds, args.window_seconds,
        args.scored_windows, args.min_tx_pdus
    )
    bearer_valid, bearer_reason, bearer_manifest = _native_vehicle_bearer_evidence(candidate_dir, args.profile)
    evidence["native_vehicle_bearer_valid"] = bearer_valid
    evidence["native_vehicle_bearer_reason"] = bearer_reason
    evidence["native_vehicle_bearer_manifest"] = bearer_manifest
    if not bearer_valid:
        evidence["valid"] = False
        evidence["reason"] = bearer_reason
    result = {
        "interval_us": interval_us,
        "command": command,
        "started_at": started,
        "finished_at": int(time.time()),
        "exit_code": int(completed),
        "evidence": evidence,
        "power_lock": "100_percent",
        "pdcp_required": True,
        "cleanup": cleanup_result,
    }
    if early_reason is not None:
        result["early_stop_reason"] = early_reason
    write_json(candidate_dir / "feasibility_result.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--binary", type=Path, default=DEFAULT_BINARY)
    parser.add_argument("--profile", default=DEFAULT_PROFILE)
    parser.add_argument("--seed", type=int, default=47)
    parser.add_argument("--intervals-us", default=",".join(str(value) for value in DEFAULT_INTERVALS))
    parser.add_argument(
        "--wall-time", type=float, default=43200.0,
        help="limite real por intervalo; 12 h permite concluir as janelas no RTF nativo",
    )
    parser.add_argument("--decision-target", type=int, default=150)
    parser.add_argument("--warmup-seconds", type=float, default=30.0)
    parser.add_argument("--window-seconds", type=float, default=10.0)
    parser.add_argument("--scored-windows", type=int, default=30)
    parser.add_argument("--min-tx-pdus", type=int, default=500)
    # Kept only as a migration guard for old queued commands.  Wall-clock
    # timestamps and collector-row counts are no longer valid evidence.
    parser.add_argument("--warmup-windows", type=int, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--min-free-gib", type=float, default=10.0)
    args = parser.parse_args()
    args.output_root = args.output_root.resolve()
    try:
        args.output_root.relative_to(ROOT / "runs")
    except ValueError as exc:
        raise SystemExit(f"output-root precisa estar em {ROOT / 'runs'}") from exc
    args.checkpoint = args.checkpoint.resolve()
    args.binary = _local_path(args.binary, "binário ns-3", executable=True)
    if not args.checkpoint.is_dir():
        raise SystemExit(f"checkpoint ausente: {args.checkpoint}")
    if args.output_root.exists() and any(args.output_root.iterdir()):
        raise SystemExit(f"output-root já contém artefatos: {args.output_root}")
    args.output_root.mkdir(parents=True, exist_ok=False)
    intervals = tuple(int(value.strip()) for value in str(args.intervals_us).split(",") if value.strip())
    results = []
    selected = None
    for interval_us in intervals:
        result = run_candidate(args, interval_us, args.output_root / f"interval_{interval_us}us")
        results.append(result)
        if result["exit_code"] == 0 and result["evidence"].get("valid"):
            selected = result
            break
    manifest = {
        "schema": "greenran.autonomous_vehicle_feasibility.v1",
        "profile": args.profile,
        "seed": args.seed,
        "topology": {"ue_count": 20, "du_count": 3, "vehicle_imsis": [16, 17, 18, 19, 20]},
        "sla": {"packet_loss_percent_lt": 1.0, "latency_p95_ms_lt": 20.0},
        "interval_candidates_us": list(intervals),
        "results": results,
        "selected": selected,
        "status": "passed" if selected else "baseline_infeasible",
        "selected_interval_us": selected["interval_us"] if selected else None,
        "energy_interpretation": "simulated_relative_reference_not_physical_consumption",
    }
    write_json(args.output_root / "selected_vehicle_profile.json", manifest)
    return 0 if selected else 3


if __name__ == "__main__":
    raise SystemExit(main())
