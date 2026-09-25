#!/usr/bin/env python3
"""Evaluate one frozen policy under the strict real-PDCP V2X contract.

The historical default remains the protected rApp-only capability check.
Article-policy evaluation is opt-in and accepts only frozen SAC-L2 or TA-SAM
arms, so official hold-out runs can never update a policy.
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

try:  # Works both as ``python scripts/...`` and as an imported test module.
    from scripts.greenran_v2x_binary_freshness import assert_v2x_binary_fresh
    from scripts.v2x_parallel_slots import SlotLease, assert_disk_capacity, resolve_slots
except ModuleNotFoundError:  # pragma: no cover - direct launcher path.
    from greenran_v2x_binary_freshness import assert_v2x_binary_fresh
    from v2x_parallel_slots import SlotLease, assert_disk_capacity, resolve_slots


ROOT = Path(__file__).resolve().parents[1]
ARM = ROOT / "scripts" / "run_tasam_online_arm.py"
CLEANUP = ROOT / "scripts" / "stop_on_decision_target.py"
_RELEASE_BINARY = ROOT / "ns-O-RAN-flexric/mmwave-LENA-oran/build/scratch/ns3.42-Energy_saving_with_cell_utilization_scenario"
DEFAULT_BINARY = (
    _RELEASE_BINARY
    if _RELEASE_BINARY.is_file()
    else ROOT / "ns-O-RAN-flexric/mmwave-LENA-oran/build/scratch/ns3.42-Energy_saving_with_cell_utilization_scenario-default"
)
DEFAULT_PROFILE = "tasam_training_balanced_v5_v2x_gbr_deadline_nonmc"
DEFAULT_INTERVALS = (4000, 6000, 8000, 12000, 16000)
V5_PROFILES = {
    "tasam_training_balanced_v5_v2x_gbr_deadline_nonmc",
    "tasam_training_balanced_v5_v2x_gbr_deadline_mc",
}
V6_PROFILES = {
    "tasam_training_balanced_v6_v2x_gbr_deadline_mc_fallback",
    "tasam_training_balanced_v6_1_v2x_gbr_deadline_mc_fallback_baseline_max",
}
STRICT_V2X_PROFILES = V5_PROFILES | V6_PROFILES
V2X_PROFILES = {
    "tasam_training_balanced_v4_v2x",
    "tasam_training_balanced_v4_v2x_gbr",
    "tasam_training_balanced_v4_v2x_gbr_priority",
    *V5_PROFILES,
    *V6_PROFILES,
}
SCHEDULER_TRACE_HEADER = [
    "Time", "CellId", "Rnti", "Imsi", "Cqi", "Mcs", "RlcQueueBytes",
    "GbrDlBps", "GbrCreditBytes", "RequestedSymbols", "GrantedSymbols",
    "GrantedTbBytes", "HarqNacks", "HarqMaxRetxDrops", "HarqRetxSymbols",
    "DeficitReason",
]
LINK_TRACE_HEADER = [
    "Time", "Imsi", "Rnti", "Connectivity", "ServingCellId", "ServingSinrDb",
    "BestMmWaveCellId", "BestMmWaveSinrDb", "SinrDeltaDb", "OutageThresholdDb",
    "Outage", "Cqi", "Mcs", "HarqNackStreak", "HarqMaxRetxDrops", "Event", "Reason",
]
CELL_SINR_TRACE_HEADER = ["Time", "Imsi", "CellId", "SinrDb"]
EVALUATION_ARM_MODES = {"rapp_only", "sac_l2_frozen", "tasam_v2x_frozen"}


def _native_trace_path(candidate_dir: Path, filename: str) -> Path:
    """Resolve native ns-3 traces without confusing them with proxy data.

    Current ns-3 writes the native V2X evidence bundle to ``ns3_energy``;
    older campaigns placed the same files in ``ns3_traces``. Prefer the
    current bundle while retaining read-only compatibility with old runs.
    """
    for dirname in ("ns3_energy", "ns3_traces"):
        path = candidate_dir / dirname / filename
        if path.is_file():
            return path
    return candidate_dir / "ns3_energy" / filename


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_revision(path: Path) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            cwd=ROOT, capture_output=True, text=True, timeout=10.0, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = completed.stdout.strip()
    return value if completed.returncode == 0 and value else None


def _profile_config(profile: str) -> Path | None:
    for path in sorted((ROOT / "config").glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and payload.get("profile") == profile:
            return path
    return None


def _provenance(args: argparse.Namespace) -> dict[str, Any]:
    checkpoint = Path(args.checkpoint).resolve()
    binary = Path(args.binary).resolve()
    profile_config = _profile_config(args.profile)
    submodules = {}
    for name in ("flexric", "ns-O-RAN-flexric", "ns3-base"):
        revision = _git_revision(ROOT / name)
        if revision:
            submodules[name] = revision
    return {
        "source_hashes": {
            "run_tasam_vehicle_feasibility.py": _sha256(Path(__file__).resolve()),
            "run_tasam_online_arm.py": _sha256(ARM),
            "profile_config": _sha256(profile_config) if profile_config else None,
            "mmwave_link_connector": _sha256(ROOT / "ns-O-RAN-flexric/mmwave-LENA-oran/src/mmwave/helper/mmwave-bearer-stats-connector.cc"),
            "vehicle_scenario": _sha256(ROOT / "ns-O-RAN-flexric/mmwave-LENA-oran/scratch/Energy_saving_with_cell_utilization_scenario.cc"),
        },
        "binary": {
            "path": str(binary),
            "sha256": _sha256(binary),
            "size": binary.stat().st_size if binary.is_file() else None,
        },
        "checkpoint": {
            "path": str(checkpoint),
            "meta_sha256": _sha256(checkpoint / "tasam_marl_checkpoint_meta.json"),
            "actors_sha256": _sha256(checkpoint / "tasam_marl_actors.pt"),
        },
        "submodules": submodules,
        "metric_contract": {
            "name": "per_pdu_cohort_v1" if args.profile in STRICT_V2X_PROFILES else "legacy_epoch_aggregate",
            "pdcp_source": "native_pdcp_pdu_tx_rx" if args.profile in STRICT_V2X_PROFILES else "native_pdcp_trace_unique_sim_epochs",
            "collector_mode": "pdcp_real",
            "proxy_allowed": False,
            "loss_grace_ms": int(args.loss_grace_seconds * 1000) if args.profile in STRICT_V2X_PROFILES else 0,
            "vehicle_imsis": [16, 17, 18, 19, 20],
            "packet_loss_percent_lt": 1.0,
            "latency_p95_ms_lt": 20.0,
            "min_tx_pdus_per_vehicle_window": int(args.min_tx_pdus),
        },
        "execution": {
            "smoke": bool(args.smoke),
            "connectivity_mode": args.connectivity_mode,
            "slot_id": args.execution_slot or "serial",
            "parallel_contract": "greenran.v2x.parallel_execution.v1" if args.execution_slot else None,
        },
        "link_metric_contract": "vehicle_link_state_v2" if args.profile in STRICT_V2X_PROFILES else None,
        "link_policy": {
            "outage_threshold_db": -5.0,
            "handover_sinr_margin_db": 3.0,
            "handover_ttt_min_ms": 5,
            "handover_ttt_max_ms": 15,
            "handover_cooldown_ms": 2000,
            "mmwave_return_threshold_db": 5.0,
            "mmwave_return_guard_ms": 100,
        } if args.profile in V6_PROFILES else None,
    }


def _native_vehicle_bearer_evidence(candidate_dir: Path, profile: str) -> tuple[bool, str, dict[str, Any] | None]:
    """Validate the native bearer manifest for the versioned V2X profile."""
    if profile not in {
        "tasam_training_balanced_v4_v2x",
        "tasam_training_balanced_v4_v2x_gbr",
        "tasam_training_balanced_v4_v2x_gbr_priority",
        *STRICT_V2X_PROFILES,
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
    is_gbr_profile = profile.endswith("_gbr") or profile.endswith("_gbr_priority") or profile in STRICT_V2X_PROFILES
    priority_ok = (
        not (profile.endswith("_gbr_priority") or profile in STRICT_V2X_PROFILES)
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
    if profile in STRICT_V2X_PROFILES:
        expected_connectivity = "lte_anchored_mc" if (profile.endswith("_mc") or profile in V6_PROFILES) else "mmwave_only"
        valid = valid and payload.get("metric_contract") == "per_pdu_cohort_v1" and \
            payload.get("scheduler_policy") == "gbr_debt_rr_v1" and \
            int(payload.get("loss_grace_ms", 0)) == 1000 and \
            payload.get("connectivity_mode") == expected_connectivity
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


def _write_window_stats(candidate_dir: Path, evidence: dict[str, Any]) -> None:
    """Persist one auditable per-window record without using collector data."""
    if evidence.get("metric_contract") != "per_pdu_cohort_v1":
        return
    path = candidate_dir / "VehiclePdcpWindowStats.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for window in evidence.get("windows", []):
            handle.write(json.dumps({
                "schema": "greenran.vehicle_pdcp_window_stats.v1",
                "metric_contract": "per_pdu_cohort_v1",
                "collector_mode": "pdcp_real",
                "proxy_allowed": False,
                **window,
            }, sort_keys=True) + "\n")


def _simulation_completion(candidate_dir: Path, required_sim_time: float) -> dict[str, Any]:
    """Verify that the arm reached the time needed by the scientific cohort."""
    manifest_path = candidate_dir / "arm_manifest.json"
    if not manifest_path.is_file():
        return {"valid": False, "reason": "arm_manifest_missing", "observed_sim_time_s": None}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"valid": False, "reason": "arm_manifest_invalid", "observed_sim_time_s": None}
    performance = manifest.get("simulation_performance") or {}
    observed = performance.get("sim_time_observed_s")
    try:
        observed = float(observed)
    except (TypeError, ValueError):
        observed = None
    stop_request = {}
    request_path = candidate_dir / "decision_target_stop.json"
    if request_path.is_file():
        try:
            stop_request = json.loads(request_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"valid": False, "reason": "decision_stop_request_invalid", "observed_sim_time_s": observed}
    if stop_request.get("reason") == "decision_target_reached":
        return {"valid": False, "reason": "decision_target_reached_before_scientific_end", "observed_sim_time_s": observed}
    valid = observed is not None and observed >= required_sim_time - 0.25
    return {
        "valid": valid,
        "reason": "ok" if valid else "simulation_ended_before_required_time",
        "observed_sim_time_s": observed,
        "required_sim_time_s": required_sim_time,
        "tolerance_s": 0.25,
    }


def _validate_scheduler_trace(candidate_dir: Path) -> tuple[bool, str]:
    """Reject malformed native scheduler telemetry before SLA classification."""
    path = candidate_dir / "ns3_energy" / "VehicleSchedulerTrace.csv"
    if not path.is_file() or path.stat().st_size == 0:
        return False, "vehicle_scheduler_trace_missing"
    try:
        raw = path.read_bytes()
        if any(byte < 32 and byte != 10 for byte in raw):
            return False, "vehicle_scheduler_trace_control_byte"
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.reader(handle)
            header = next(reader, None)
            if header != SCHEDULER_TRACE_HEADER:
                return False, "vehicle_scheduler_trace_header_invalid"
            seen: set[int] = set()
            for row in reader:
                if len(row) != len(SCHEDULER_TRACE_HEADER):
                    return False, "vehicle_scheduler_trace_column_count_invalid"
                try:
                    imsi = int(row[3])
                except (TypeError, ValueError):
                    return False, "vehicle_scheduler_trace_row_invalid"
                if imsi in {16, 17, 18, 19, 20}:
                    seen.add(imsi)
            if seen != {16, 17, 18, 19, 20}:
                return False, "vehicle_scheduler_trace_vehicle_coverage_incomplete"
    except (OSError, UnicodeError, StopIteration):
        return False, "vehicle_scheduler_trace_read_error"
    return True, "ok"


def _scheduler_telemetry_warning(candidate_dir: Path) -> dict[str, Any]:
    """Describe out-of-scope scheduler records without using them as SLA data.

    Multi-connectivity can legitimately emit scheduler rows before an IMSI is
    associated with the vehicle bearer.  Those rows are useful diagnostics,
    but only IMSIs 16--20 can prove (or violate) the V2X GBR reservation.
    """
    path = candidate_dir / "ns3_energy" / "VehicleSchedulerTrace.csv"
    warning = {
        "status": "unavailable",
        "vehicle_imsis": [16, 17, 18, 19, 20],
        "out_of_scope_rows": 0,
        "out_of_scope_imsis": [],
        "included_in_vehicle_sla": False,
    }
    if not path.is_file():
        return warning
    try:
        out_of_scope: set[int] = set()
        count = 0
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                try:
                    imsi = int(row.get("Imsi", ""))
                except (TypeError, ValueError):
                    continue
                if imsi not in {16, 17, 18, 19, 20}:
                    count += 1
                    out_of_scope.add(imsi)
        warning.update({
            "status": "warning" if count else "none",
            "out_of_scope_rows": count,
            "out_of_scope_imsis": sorted(out_of_scope),
        })
    except (OSError, UnicodeError, csv.Error):
        warning["status"] = "unreadable"
    return warning


def _validate_link_traces(candidate_dir: Path) -> tuple[bool, str]:
    """Validate v2 link telemetry before a radio result can be promoted."""
    state_path = _native_trace_path(candidate_dir, "VehicleLinkTrace.csv")
    raw_path = _native_trace_path(candidate_dir, "VehicleCellSinrTrace.csv")
    if not state_path.is_file() or state_path.stat().st_size == 0:
        return False, "vehicle_link_state_trace_missing"
    if not raw_path.is_file() or raw_path.stat().st_size == 0:
        return False, "vehicle_cell_sinr_trace_missing"
    try:
        for path, expected in ((state_path, LINK_TRACE_HEADER), (raw_path, CELL_SINR_TRACE_HEADER)):
            raw = path.read_bytes()
            if any(byte < 32 and byte != 10 for byte in raw):
                return False, f"{path.name.lower().replace('.', '_')}_control_byte"
            with path.open(newline="", encoding="utf-8") as handle:
                reader = csv.reader(handle)
                header = next(reader, None)
                if header != expected:
                    return False, f"{path.name.lower().replace('.', '_')}_header_invalid"
                seen: set[int] = set()
                seen_samples: set[tuple[int, float, str]] = set()
                for row in reader:
                    if len(row) != len(expected):
                        return False, f"{path.name.lower().replace('.', '_')}_column_count_invalid"
                    try:
                        imsi = int(row[1])
                        timestamp = float(row[0])
                    except (TypeError, ValueError):
                        return False, f"{path.name.lower().replace('.', '_')}_row_invalid"
                    if imsi in {16, 17, 18, 19, 20}:
                        seen.add(imsi)
                    if path == state_path:
                        if row[3] not in {"mmwave", "lte"}:
                            return False, "vehicle_link_state_connectivity_invalid"
                        try:
                            serving = int(row[4])
                            best = int(row[6])
                            serving_sinr = float(row[5])
                            best_sinr = float(row[7])
                            threshold = float(row[9])
                        except (TypeError, ValueError):
                            return False, "vehicle_link_state_numeric_invalid"
                        if serving <= 0 or best <= 0 or not all(math.isfinite(v) for v in (serving_sinr, best_sinr, threshold)):
                            return False, "vehicle_link_state_association_invalid"
                        expected_outage = serving_sinr < threshold
                        if row[10].lower() != str(expected_outage).lower():
                            return False, "vehicle_link_state_outage_inconsistent"
                        # A handover acknowledgement and the periodic
                        # measurement may legitimately share a timestamp.
                        # Duplicate evidence means the same UE/timestamp/
                        # event was emitted twice, not two different events
                        # at the same instant.
                        key = (imsi, timestamp, row[15])
                        if key in seen_samples:
                            return False, "vehicle_link_state_duplicate_sample"
                        seen_samples.add(key)
                if seen != {16, 17, 18, 19, 20}:
                    return False, f"{path.name.lower().replace('.', '_')}_vehicle_coverage_incomplete"
    except (OSError, UnicodeError, StopIteration):
        return False, "vehicle_link_trace_read_error"
    return True, "ok"


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


def _write_runner_status(candidate_dir: Path, **updates: Any) -> None:
    """Publish a PID-scoped heartbeat/final state for detached-run recovery."""
    path = candidate_dir / "feasibility_runner_status.json"
    current: dict[str, Any] = {}
    if path.is_file():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                current = payload
        except (OSError, json.JSONDecodeError):
            current = {}
    current.update(updates)
    current["updated_at"] = time.time()
    write_json(path, current)


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


def _greenran_cgroup_base() -> Path:
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
    raise SystemExit("execução paralela V2X exige GREENRAN_CGROUP_BASE ou /run/greenran-cgroup-root")


def _raw_pdcp_bins(candidate_dir: Path, warmup_seconds: float, window_seconds: float,
                   scored_windows: int, min_tx_pdus: int) -> tuple[list[dict[str, Any]], str | None]:
    """Read native PDCP epochs and score non-overlapping real-PDCP windows.

    The SQLite collector may repeat the same simulated epoch while polling.
    Feasibility therefore reads the native trace directly and keys each row by
    (epoch end, IMSI), never by wall-clock timestamp or collector row count.
    """
    trace = _native_trace_path(candidate_dir, "DlPdcpStats.txt")
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


def _per_pdu_cohort_bins(candidate_dir: Path, warmup_seconds: float, window_seconds: float,
                         scored_windows: int, min_tx_pdus: int,
                         loss_grace_seconds: float) -> tuple[list[dict[str, Any]], str | None]:
    """Evaluate the native V2X TX/RX stream by its TX cohort.

    The drain is part of the contract: an event sent in a scored window has
    until the end of that window plus ``loss_grace_seconds`` to arrive.  This
    prevents PDUs merely in flight at a window edge from being called lost.
    """
    trace = _native_trace_path(candidate_dir, "VehiclePdcpPduTrace.csv")
    if not trace.is_file():
        return [], "vehicle_pdcp_pdu_trace_missing"
    tx: dict[int, dict[str, Any]] = {}
    rx: dict[int, dict[str, Any]] = {}
    max_observed_ns = 0
    try:
        with trace.open(newline="", encoding="utf-8", errors="replace") as handle:
            reader = csv.DictReader(handle)
            required = {"Event", "EventId", "TxTimeNs", "RxTimeNs", "IMSI", "DelayNs", "CorrelationStatus"}
            if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
                return [], "vehicle_pdcp_pdu_trace_schema_invalid"
            for row in reader:
                try:
                    imsi, event_id = int(row["IMSI"]), int(row["EventId"])
                    tx_ns = int(row["TxTimeNs"])
                except (KeyError, TypeError, ValueError):
                    return [], "vehicle_pdcp_pdu_trace_row_invalid"
                if imsi not in {16, 17, 18, 19, 20}:
                    continue
                max_observed_ns = max(max_observed_ns, tx_ns)
                if row.get("Event") == "TX":
                    if event_id <= 0 or event_id in tx:
                        return [], "vehicle_pdcp_duplicate_or_invalid_tx_identifier"
                    tx[event_id] = {"imsi": imsi, "tx_ns": tx_ns}
                elif row.get("Event") == "RX":
                    if row.get("CorrelationStatus") != "matched" or event_id <= 0 or event_id in rx:
                        return [], "vehicle_pdcp_unmatched_or_duplicate_rx"
                    try:
                        rx_ns, delay_ns = int(row["RxTimeNs"]), int(row["DelayNs"])
                    except (TypeError, ValueError):
                        return [], "vehicle_pdcp_rx_timestamp_invalid"
                    if rx_ns < tx_ns or delay_ns < 0:
                        return [], "vehicle_pdcp_rx_before_tx"
                    max_observed_ns = max(max_observed_ns, rx_ns)
                    rx[event_id] = {"imsi": imsi, "tx_ns": tx_ns, "rx_ns": rx_ns, "delay_ns": delay_ns}
    except OSError as exc:
        return [], f"vehicle_pdcp_pdu_trace_read_error:{exc}"
    for event_id, delivery in rx.items():
        sent = tx.get(event_id)
        if sent is None or sent["imsi"] != delivery["imsi"] or sent["tx_ns"] != delivery["tx_ns"]:
            return [], "vehicle_pdcp_tx_rx_correlation_incomplete"

    bins: list[dict[str, Any]] = []
    grace_ns = int(loss_grace_seconds * 1_000_000_000)
    for index in range(scored_windows):
        left_ns = int((warmup_seconds + index * window_seconds) * 1_000_000_000)
        right_ns = int((warmup_seconds + (index + 1) * window_seconds) * 1_000_000_000)
        deadline_ns = right_ns + grace_ns
        if max_observed_ns < deadline_ns:
            # The candidate may still be running.  A partial drain is not a
            # violation and must never trigger the early-stop guard.
            continue
        rows: dict[str, Any] = {}
        invalid_reason: str | None = None
        for imsi in range(16, 21):
            cohort = [event_id for event_id, item in tx.items()
                      if item["imsi"] == imsi and left_ns <= item["tx_ns"] < right_ns]
            delivered = [rx[event_id] for event_id in cohort if event_id in rx and rx[event_id]["rx_ns"] <= deadline_ns]
            missing = len(cohort) - len(delivered)
            late = sum(1 for item in delivered if item["delay_ns"] > 20_000_000)
            delays = sorted(item["delay_ns"] / 1000.0 for item in delivered)
            p95 = delays[math.ceil(0.95 * len(delays)) - 1] if delays else None
            loss = (100.0 * missing / len(cohort)) if cohort else None
            rows[str(imsi)] = {
                "tx_pdus": len(cohort), "rx_pdus": len(delivered), "lost_pdus": missing,
                "late_pdus": late, "loss_percent": loss, "latency_p95_us": p95,
                "delay_histogram_us": {"le_20ms": len(delivered) - late, "gt_20ms": late},
                "source": "pdcp_real_native_pdu_cohort",
            }
            if len(cohort) < min_tx_pdus:
                invalid_reason = f"window={index}:insufficient_tx_imsi={imsi}:{len(cohort)}<{min_tx_pdus}"
            elif loss is None or loss >= 1.0:
                invalid_reason = f"window={index}:loss_imsi={imsi}:{loss}"
            elif p95 is None or p95 >= 20_000.0:
                invalid_reason = f"window={index}:latency_imsi={imsi}:{p95}"
        bins.append({"index": index, "start_s": left_ns / 1e9, "end_s": right_ns / 1e9,
                     "drain_until_s": deadline_ns / 1e9, "vehicles": rows,
                     "valid": invalid_reason is None, "invalid_reason": invalid_reason})
    return bins, None


def _candidate_metrics(candidate_dir: Path, warmup_seconds: float, window_seconds: float,
                       scored: int, min_tx_pdus: int, *, per_pdu: bool = False,
                       loss_grace_seconds: float = 1.0) -> dict[str, Any]:
    if per_pdu:
        bins, trace_error = _per_pdu_cohort_bins(candidate_dir, warmup_seconds, window_seconds,
                                                  scored, min_tx_pdus, loss_grace_seconds)
    else:
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
        "evidence_source": "native_pdcp_pdu_tx_rx" if per_pdu else "native_pdcp_trace_unique_sim_epochs",
        "metric_contract": "per_pdu_cohort_v1" if per_pdu else "legacy_epoch_aggregate",
    }


def _frozen_policy_evidence(candidate_dir: Path, arm_mode: str) -> tuple[bool, str, dict[str, Any]]:
    """Verify that a hold-out candidate used the requested immutable policy."""
    if arm_mode == "rapp_only":
        return True, "rapp_only", {}
    try:
        manifest = json.loads((candidate_dir / "arm_manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False, "arm_manifest_missing_or_invalid", {}
    contract = manifest.get("contract") if isinstance(manifest.get("contract"), dict) else {}
    if manifest.get("mode") != arm_mode:
        return False, "evaluation_arm_mode_mismatch", manifest
    if not bool(contract.get("frozen_checkpoint")) or bool(contract.get("controller_enabled")):
        return False, "evaluation_checkpoint_not_frozen", manifest
    if not bool(contract.get("actuation_enabled")) or not bool(manifest.get("e2_control_enabled")):
        return False, "evaluation_e2_actuation_not_enabled", manifest
    if manifest.get("checkpoint_frozen_verified") is not True:
        return False, "evaluation_checkpoint_fingerprint_missing", manifest
    if manifest.get("feedback_integrity_valid") is not True:
        return False, "evaluation_e2_ack_or_feedback_incomplete", manifest
    return True, "ok", manifest


def _early_vehicle_sla_violation(candidate_dir: Path, warmup_seconds: float,
                                 window_seconds: float, min_tx_pdus: int, *,
                                 per_pdu: bool = False, loss_grace_seconds: float = 1.0) -> str | None:
    """Return a deterministic reason as soon as a candidate cannot pass.

    A feasibility candidate is an all-windows criterion.  Once a complete
    post-warm-up window violates the hard vehicle SLA, continuing the ns-3
    run only consumes disk and wall time and cannot change the outcome.
    """
    if per_pdu:
        bins, _ = _per_pdu_cohort_bins(candidate_dir, warmup_seconds, window_seconds, 1000,
                                        min_tx_pdus, loss_grace_seconds)
    else:
        bins, _ = _raw_pdcp_bins(candidate_dir, warmup_seconds, window_seconds, 1000, min_tx_pdus)
    for item in bins:
        if item.get("index", 0) >= 0 and not item.get("valid"):
            return str(item.get("invalid_reason") or "vehicle_sla_invalid")
    return None


def run_candidate(args: argparse.Namespace, interval_us: int, candidate_dir: Path) -> dict[str, Any]:
    env = dict(os.environ)
    slot = None
    lease = None
    if args.execution_slot:
        slot = resolve_slots(args.execution_slot)[0]
        cgroup_base = _greenran_cgroup_base()
        if not cgroup_base.is_dir():
            raise SystemExit(f"cgroup base ausente para slot V2X: {cgroup_base}")
        assert_disk_capacity(ROOT / "runs", 1)
        env.update(slot.as_environment(cgroup_base))
        # The feasibility runner owns the slot lease for the whole child arm.
        # Tell the arm explicitly so it inherits the reservation instead of
        # trying to acquire the same flock a second time.
        env["GREENRAN_SLOT_LEASE_HELD"] = "1"
        lease = SlotLease(slot, candidate_dir, cgroup_base=cgroup_base)
        lease.__enter__()
    env.update({
        "GREENRAN_LOCAL_ONLY": "1",
        "GREENRAN_CGROUP_ENFORCE": "1",
        "GREENRAN_CGROUP_ALLOW_UNENFORCED": "0",
        "GREENRAN_REQUIRE_REAL_PDCP": "1",
        "GREENRAN_REAL_ONLY": "1",
        "GREENRAN_TASAM_EXPORT_ALLOW_PROXY": "0",
        "GREENRAN_DB_SNAPSHOT_ENABLED": "0",
        "GREENRAN_TASAM_EXPORT_ENABLED": "0",
        "GREENRAN_NS3_ACTIVE_CELLS": "3",
        "GREENRAN_NS3_VEHICLE_PACKET_INTERVAL_US": str(interval_us),
        "GREENRAN_NS3_USE_MC_UE_DEVICES": "true" if args.connectivity_mode == "lte_anchored_mc" else "false",
        "GREENRAN_NS3_SINGLE_RUN": "1",
        "GREENRAN_NS3_BIN": str(Path(args.binary).resolve()),
    })
    if args.energy_phase:
        # The strict functional gate fixes power by contract.  The only
        # exception is the explicitly gated post-SLA energy subphase, where
        # E2 may change power while topology/load/cell count remain fixed.
        env.pop("GREENRAN_FEASIBILITY_LOCK_POWER", None)
        env.pop("GREENRAN_NS3_FIXED_POWER_PERCENT", None)
        env["GREENRAN_TASAM_REWARD_CONTRACT"] = "greenran.tasam.v2x.reward_adaptive.v1"
        env["GREENRAN_TASAM_REWARD_ENERGY_ENABLED"] = "1"
    else:
        env["GREENRAN_NS3_FIXED_POWER_PERCENT"] = "100"
        env["GREENRAN_FEASIBILITY_LOCK_POWER"] = "1"
        if args.profile in STRICT_V2X_PROFILES:
            env["GREENRAN_TASAM_REWARD_CONTRACT"] = "greenran.tasam.v2x.reward_adaptive.v1"
            env["GREENRAN_TASAM_REWARD_ENERGY_ENABLED"] = "0"
    # The feasibility decision needs exactly the post-warm-up windows being
    # scored.  Running a fixed 600 s arm made the old 1,800 s wall limit
    # impossible at the measured native RTF and added unscored radio work.
    required_sim_time = (
        float(args.warmup_seconds)
        + float(args.scored_windows) * float(args.window_seconds)
        + float(args.loss_grace_seconds)
        + float(args.tail_seconds)
    )
    if args.profile in V2X_PROFILES and args.decision_target != 0:
        raise ValueError("campanhas V2X exigem decision-target=0")
    command = [
        sys.executable, str(ARM),
        "--mode", args.arm_mode,
        "--run-dir", str(candidate_dir),
        "--seed", str(args.seed),
        "--profile", args.profile,
        "--wall-time", str(args.wall_time),
        "--sim-time", f"{required_sim_time:.3f}",
        "--checkpoint", str(Path(args.checkpoint).resolve()),
        "--decision-target", str(args.decision_target),
        "--min-free-gib", str(args.min_free_gib),
        "--native-fidelity", "--performance-min-rtf",
        ("0.0" if args.smoke else "0.016"),
    ]
    if args.energy_phase:
        command.append("--energy-enabled")
    if args.smoke:
        command.append("--smoke")
    started = int(time.time())
    try:
        process = subprocess.Popen(command, cwd=ROOT, env=env)
    except BaseException:
        if lease is not None:
            lease.__exit__(*sys.exc_info())
        raise
    # The arm owns the empty run-dir handshake and writes arm_manifest.json
    # immediately after claiming it.  Do not put a lease or parent heartbeat
    # in the directory before that marker, otherwise the arm correctly rejects
    # its own run as pre-populated.
    claimed = False
    for _ in range(600):
        if (candidate_dir / "arm_manifest.json").is_file() or (candidate_dir / "wall_clock_status.json").is_file():
            claimed = True
            break
        if process.poll() is not None:
            break
        time.sleep(0.05)
    if not candidate_dir.is_dir():
        candidate_dir.mkdir(parents=True, exist_ok=True)
    startup_reason = None
    if not claimed and process.poll() is None:
        startup_reason = "arm_startup_handshake_timeout"
        process.terminate()
    if lease is not None and claimed:
        # The arm rejects a pre-populated run-dir.  Publish lease evidence only
        # after the child has claimed that empty directory.
        lease.publish_campaign_contract()
    if claimed:
        _write_runner_status(
            candidate_dir,
            schema="greenran.vehicle_feasibility_runner_status.v1",
            phase="running" if process.poll() is None else "starting_failed",
            pid=process.pid,
            started_at=started,
            interval_us=interval_us,
            command=command,
        )
    early_reason = startup_reason
    cleanup_result: dict[str, Any] = {}
    try:
        while process.poll() is None:
            early_reason = _early_vehicle_sla_violation(
                candidate_dir, args.warmup_seconds, args.window_seconds, args.min_tx_pdus,
                per_pdu=args.profile in STRICT_V2X_PROFILES, loss_grace_seconds=args.loss_grace_seconds,
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
                _write_runner_status(candidate_dir, phase="cancelling", reason=early_reason)
                break
            _write_runner_status(candidate_dir, phase="running", pid=process.pid)
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
        _write_runner_status(
            candidate_dir,
            phase="finished" if completed == 0 else "cancelled",
            exit_code=int(completed),
            finished_at=int(time.time()),
            reason=early_reason or ("arm_exit" if completed == 0 else "arm_nonzero_exit"),
        )
        if lease is not None:
            lease.__exit__(None, None, None)
    evidence = _candidate_metrics(
        candidate_dir, args.warmup_seconds, args.window_seconds,
        args.scored_windows, args.min_tx_pdus, per_pdu=args.profile in STRICT_V2X_PROFILES,
        loss_grace_seconds=args.loss_grace_seconds,
    )
    policy_valid, policy_reason, policy_manifest = _frozen_policy_evidence(candidate_dir, args.arm_mode)
    evidence["frozen_policy_valid"] = policy_valid
    evidence["frozen_policy_reason"] = policy_reason
    if not policy_valid:
        evidence.update({"valid": False, "reason": policy_reason})
    _write_window_stats(candidate_dir, evidence)
    evidence["simulation_completion"] = _simulation_completion(candidate_dir, required_sim_time)
    if not evidence["simulation_completion"]["valid"]:
        evidence.update({"valid": False, "reason": evidence["simulation_completion"]["reason"]})
    if args.profile in STRICT_V2X_PROFILES:
        scheduler_valid, scheduler_reason = _validate_scheduler_trace(candidate_dir)
        evidence["scheduler_trace_valid"] = scheduler_valid
        evidence["scheduler_trace_reason"] = scheduler_reason
        required_traces = {
            "vehicle_link_trace_missing": _native_trace_path(candidate_dir, "VehicleLinkTrace.csv"),
            "vehicle_cell_sinr_trace_missing": _native_trace_path(candidate_dir, "VehicleCellSinrTrace.csv"),
        }
        if not scheduler_valid:
            evidence.update({"valid": False, "reason": scheduler_reason})
        evidence["scheduler_telemetry_warning"] = _scheduler_telemetry_warning(candidate_dir)
        link_valid, link_reason = _validate_link_traces(candidate_dir)
        evidence["link_trace_valid"] = link_valid
        evidence["link_trace_reason"] = link_reason
        evidence["link_metric_contract"] = "vehicle_link_state_v2"
        if not link_valid:
            evidence.update({"valid": False, "reason": link_reason})
        for reason, path in required_traces.items():
            if not path.is_file() or path.stat().st_size == 0:
                evidence.update({"valid": False, "reason": reason})
                break
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
        "connectivity_mode": args.connectivity_mode,
        "metric_contract": evidence["metric_contract"],
        "evaluation_arm_mode": args.arm_mode,
        "evaluation_frozen": args.arm_mode != "rapp_only",
        "policy_contract": policy_manifest.get("contract", {}) if policy_manifest else {},
        "cleanup": cleanup_result,
        "parallel_execution": {
            "schema": "greenran.v2x.parallel_execution.v1",
            "slot_id": slot.slot_id if slot else "serial",
            "ports": {
                "e2_term": slot.e2_term_port if slot else 36421,
                "e2_xapp": slot.e2_xapp_port if slot else 36422,
                "e2_local_base": slot.e2_local_port if slot else 38470,
            },
            "app_port_offset": slot.app_port_offset if slot else 0,
            "cgroup_root": str(_greenran_cgroup_base() / slot.cgroup_relative_root)
            if slot else "",
            "lease": "released",
        },
    }
    if early_reason is not None:
        result["early_stop_reason"] = early_reason
    write_json(candidate_dir / "feasibility_result.json", result)
    return result


def _energy_report(output_root: Path, results: list[dict[str, Any]], *, energy_phase: bool = False) -> dict[str, Any]:
    """Summarize simulator energy without presenting it as physical consumption."""
    intervals: dict[str, dict[str, Any]] = {}
    native_e2_intervals: dict[str, dict[str, Any]] = {}
    for result in results:
        interval_us = int(result["interval_us"])
        energy_dir = output_root / f"interval_{interval_us}us" / "ns3_energy"
        cells: dict[str, float] = {}
        for path in sorted(energy_dir.glob("energyfilecell*.csv")):
            last_net_energy = None
            try:
                with path.open(newline="", encoding="utf-8") as handle:
                    for row in csv.DictReader(handle):
                        value = row.get("NetEnergy")
                        if value not in (None, ""):
                            last_net_energy = float(value)
            except (OSError, ValueError):
                continue
            if last_net_energy is not None:
                cells[path.stem.removeprefix("energyfilecell")] = last_net_energy
        intervals[str(interval_us)] = {
            "status": "available" if cells else "unavailable",
            "cells": cells,
            "integrated_relative_energy": sum(cells.values()) if cells else None,
        }
        control_trace = output_root / f"interval_{interval_us}us" / "ns3_energy" / "TasamControlObservations.csv"
        observed_rows = 0
        valid_rows = 0
        if control_trace.is_file():
            try:
                with control_trace.open(newline="", encoding="utf-8", errors="replace") as handle:
                    for row in csv.DictReader(handle):
                        observed_rows += 1
                        if row.get("ObservationKind") in {"power_readback", "state_snapshot"} and row.get("TxPowerPercent") not in (None, ""):
                            valid_rows += 1
            except (OSError, csv.Error):
                pass
        native_e2_intervals[str(interval_us)] = {
            "trace": str(control_trace),
            "observed_rows": observed_rows,
            "valid_rows": valid_rows,
            "native_e2_observed": bool(valid_rows),
        }
    available = [item for item in intervals.values() if item["status"] == "available"]
    return {
        "status": "available" if available else "unavailable",
        "unit": "simulator_relative_energy_units",
        "physical_consumption": bool(energy_phase and any(item["native_e2_observed"] for item in native_e2_intervals.values())),
        "energy_claim_eligible": bool(energy_phase and native_e2_intervals and all(item["native_e2_observed"] for item in native_e2_intervals.values())),
        "native_e2_observation": native_e2_intervals,
        "intervals": intervals,
    }


def _actuation_report(output_root: Path, results: list[dict[str, Any]]) -> dict[str, Any]:
    """Count proposed/vetoed/applied/confirmed actions from campaign-local logs."""
    counts = {"proposed": 0, "vetoed": 0, "applied": 0, "confirmed": 0}
    for result in results:
        path = output_root / f"interval_{int(result['interval_us'])}us" / "rapp_decisions.jsonl"
        if not path.is_file():
            continue
        try:
            with path.open(encoding="utf-8") as handle:
                for line in handle:
                    try:
                        decision = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    counts["proposed"] += 1
                    if (
                        str(decision.get("decision", "")).upper() in {"BLOCKED", "VETO", "VETOED"}
                        or decision.get("preventive_block") is True
                    ):
                        counts["vetoed"] += 1
                    if decision.get("ta_sam_actuation_applied") is True:
                        counts["applied"] += 1
                    action = decision.get("economic_action") or {}
                    if action.get("actuation_confirmed") is True:
                        counts["confirmed"] += 1
        except OSError:
            continue
    return {"status": "available", **counts}


def _comparison_report() -> dict[str, Any]:
    return {
        "status": "not_run",
        "arms": ["rApp-only", "ASGARD"],
        "reason": "paired comparison is blocked until the 30-window strict baseline passes",
    }


def _candidate_classification(candidate_dir: Path, evidence: dict[str, Any]) -> str:
    """Keep metric corruption distinct from scheduler and radio infeasibility."""
    if evidence.get("metric_contract") == "per_pdu_cohort_v1" and not evidence.get("valid"):
        reason = str(evidence.get("reason", ""))
        if (
            evidence.get("complete_windows", 0) < evidence.get("required_scored_windows", 0)
            or any(token in reason for token in (
                "trace_", "correlation", "identifier", "unmatched", "timestamp",
                "complete_windows", "simulation_ended", "decision_target", "arm_manifest",
                "link_trace", "outage", "association", "connectivity",
            ))
        ):
            return "metric_invalid"
    scheduler_trace = candidate_dir / "ns3_energy" / "VehicleSchedulerTrace.csv"
    if scheduler_trace.is_file():
        try:
            shortfall_count = 0
            streak = 0
            max_streak = 0
            with scheduler_trace.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            for row in rows:
                try:
                    imsi = int(row.get("Imsi", ""))
                except (TypeError, ValueError):
                    return "metric_invalid"
                if imsi not in {16, 17, 18, 19, 20}:
                    continue
                if row.get("DeficitReason") == "scheduler_capacity_shortfall":
                    shortfall_count += 1
                    streak += 1
                    max_streak = max(max_streak, streak)
                else:
                    streak = 0
            if shortfall_count >= 5 or max_streak >= 5:
                return "scheduler_infeasible"
        except (OSError, csv.Error):
            return "metric_invalid"
    if not evidence.get("valid"):
        return "radio_infeasible" if evidence.get("metric_contract") == "per_pdu_cohort_v1" else "rejected"
    return "approved"


def _lowest_valid_candidate(results: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the shortest interval with complete, valid native evidence."""
    approved = [
        result for result in results
        if result.get("exit_code") == 0 and (result.get("evidence") or {}).get("valid") is True
    ]
    return min(approved, key=lambda result: int(result["interval_us"])) if approved else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--candidate-dir", type=Path, default=None,
                        help="diretório direto de um único candidato; usado pelo orquestrador paralelo")
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--binary", type=Path, default=DEFAULT_BINARY)
    parser.add_argument("--profile", default=DEFAULT_PROFILE)
    parser.add_argument(
        "--arm-mode", choices=sorted(EVALUATION_ARM_MODES), default="rapp_only",
        help="política congelada avaliada; o padrão mantém a baseline rApp-only histórica",
    )
    parser.add_argument("--seed", type=int, default=47)
    parser.add_argument("--intervals-us", default=",".join(str(value) for value in DEFAULT_INTERVALS))
    parser.add_argument(
        "--wall-time", type=float, default=43200.0,
        help="limite real por intervalo; 12 h permite concluir as janelas no RTF nativo",
    )
    parser.add_argument("--decision-target", type=int, default=0)
    parser.add_argument("--warmup-seconds", type=float, default=30.0)
    parser.add_argument("--window-seconds", type=float, default=10.0)
    parser.add_argument("--scored-windows", type=int, default=30)
    parser.add_argument("--loss-grace-seconds", type=float, default=1.0)
    parser.add_argument("--tail-seconds", type=float, default=0.5,
                        help="tempo adicional após a drenagem; 0,5 s é o contrato científico")
    parser.add_argument("--connectivity-mode", choices=("auto", "mmwave_only", "lte_anchored_mc"), default="auto")
    parser.add_argument(
        "--energy-phase", action="store_true",
        help="subfase pós-gate: libera somente potência E2; não altera topologia, carga ou células",
    )
    parser.add_argument("--execution-slot", choices=("slot-a", "slot-b"), default=None,
                        help="slot isolado para execução concorrente V2X; omitido mantém modo serial")
    parser.add_argument("--smoke", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--evaluate-all-intervals", action="store_true",
        help="executa todos os intervalos e seleciona o menor aprovado; usado pela matriz científica",
    )
    parser.add_argument("--min-tx-pdus", type=int, default=500)
    # Kept only as a migration guard for old queued commands.  Wall-clock
    # timestamps and collector-row counts are no longer valid evidence.
    parser.add_argument("--warmup-windows", type=int, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--min-free-gib", type=float, default=10.0)
    args = parser.parse_args()
    args.output_root = args.output_root.resolve()
    if args.candidate_dir is not None:
        args.candidate_dir = args.candidate_dir.resolve()
    try:
        args.output_root.relative_to(ROOT / "runs")
    except ValueError as exc:
        raise SystemExit(f"output-root precisa estar em {ROOT / 'runs'}") from exc
    args.checkpoint = args.checkpoint.resolve()
    args.binary = _local_path(args.binary, "binário ns-3", executable=True)
    if args.profile in V2X_PROFILES and args.decision_target != 0:
        raise SystemExit("campanhas V2X exigem --decision-target 0")
    if args.energy_phase and args.profile not in STRICT_V2X_PROFILES:
        raise SystemExit("--energy-phase só existe para a subfase V2X após o gate estrito")
    if args.profile in STRICT_V2X_PROFILES:
        # Do this before creating the campaign root.  Otherwise a stale ns-3
        # binary can run for hours yet omit the contract traces entirely.
        assert_v2x_binary_fresh(args.binary)
    # A smoke is the fixed 30--40 s diagnostic, not a truncated version of
    # the 30-window campaign.  Keep the one scored cohort and four seconds
    # of tail after its 1 s drain so the simulator reaches the contractual
    # 45 s endpoint before evidence is evaluated.
    if args.smoke:
        args.scored_windows = 1
        args.tail_seconds = max(args.tail_seconds, 4.0)
    if args.loss_grace_seconds != 1.0 and args.profile in STRICT_V2X_PROFILES:
        raise SystemExit("per_pdu_cohort_v1 exige exatamente 1 s de drenagem")
    if args.tail_seconds < 0.5:
        raise SystemExit("a campanha exige ao menos 0,5 s após a drenagem")
    expected_connectivity = (
        "lte_anchored_mc"
        if args.profile.endswith("_mc") or args.profile in V6_PROFILES
        else "mmwave_only"
    )
    if args.connectivity_mode == "auto":
        args.connectivity_mode = expected_connectivity
    if args.connectivity_mode != expected_connectivity:
        raise SystemExit(f"perfil {args.profile} exige connectivity_mode={expected_connectivity}")
    if not args.checkpoint.is_dir():
        raise SystemExit(f"checkpoint ausente: {args.checkpoint}")
    if args.output_root.exists() and any(args.output_root.iterdir()):
        raise SystemExit(f"output-root já contém artefatos: {args.output_root}")
    if args.candidate_dir is not None:
        try:
            args.candidate_dir.relative_to(ROOT / "runs")
        except ValueError as exc:
            raise SystemExit(f"candidate-dir precisa estar em {ROOT / 'runs'}") from exc
        if args.candidate_dir != args.output_root:
            raise SystemExit("candidate-dir deve coincidir com output-root")
        if len(tuple(int(value.strip()) for value in str(args.intervals_us).split(",") if value.strip())) != 1:
            raise SystemExit("candidate-dir exige exatamente um intervalo")
    args.output_root.mkdir(parents=True, exist_ok=False)
    intervals = tuple(int(value.strip()) for value in str(args.intervals_us).split(",") if value.strip())
    results = []
    selected = None
    for interval_us in intervals:
        candidate_dir = args.candidate_dir or args.output_root / f"interval_{interval_us}us"
        result = run_candidate(args, interval_us, candidate_dir)
        result["classification"] = _candidate_classification(candidate_dir, result["evidence"])
        write_json(candidate_dir / "feasibility_result.json", result)
        results.append(result)
        if result["exit_code"] == 0 and result["evidence"].get("valid") and not args.evaluate_all_intervals:
            selected = result
            break
    if args.evaluate_all_intervals:
        selected = _lowest_valid_candidate(results)
    provenance = _provenance(args)
    campaign_status = (
        ("smoke_passed" if selected else "smoke_failed")
        if args.smoke else ("passed" if selected else "baseline_infeasible")
    )
    scientific_decision = (
        "blocked" if args.smoke else ("approved" if selected else "baseline_infeasible")
    )
    if args.execution_slot:
        manifest_slot = resolve_slots(args.execution_slot)[0]
        parallel_execution = {
            "schema": "greenran.v2x.parallel_execution.v1",
            "slot_id": manifest_slot.slot_id,
            "ports": {
                "e2_term": manifest_slot.e2_term_port,
                "e2_xapp": manifest_slot.e2_xapp_port,
                "e2_local_base": manifest_slot.e2_local_port,
            },
            "app_port_offset": manifest_slot.app_port_offset,
            "cgroup_root": str(_greenran_cgroup_base() / manifest_slot.cgroup_relative_root),
            "lease": "released",
        }
    else:
        parallel_execution = {
            "schema": "greenran.v2x.parallel_execution.v1",
            "slot_id": "serial",
            "ports": {"e2_term": 36421, "e2_xapp": 36422, "e2_local_base": 38470},
            "app_port_offset": 0,
            "cgroup_root": "",
            "lease": "not_applicable",
        }
    manifest = {
        "schema": "greenran.autonomous_vehicle_feasibility.v3",
        "manifest_version": 3,
        "profile": args.profile,
        "seed": args.seed,
        "evaluation_arm_mode": args.arm_mode,
        "evaluation_frozen": args.arm_mode != "rapp_only",
        "topology": {"ue_count": 20, "du_count": 3, "vehicle_imsis": [16, 17, 18, 19, 20]},
        "metric_contract": "per_pdu_cohort_v1" if args.profile in STRICT_V2X_PROFILES else "legacy_epoch_aggregate",
        "link_metric_contract": "vehicle_link_state_v2" if args.profile in STRICT_V2X_PROFILES else None,
        "connectivity_mode": args.connectivity_mode,
        "scheduler_policy": "gbr_debt_rr_v1" if args.profile in STRICT_V2X_PROFILES else "legacy",
        "loss_grace_ms": int(args.loss_grace_seconds * 1000) if args.profile in STRICT_V2X_PROFILES else 0,
        "sla": {"packet_loss_percent_lt": 1.0, "latency_p95_ms_lt": 20.0},
        "interval_candidates_us": list(intervals),
        "evaluated_all_intervals": bool(args.evaluate_all_intervals),
        "results": results,
        "selected": selected,
        "parallel_execution": (
            selected.get("parallel_execution", {"schema": "greenran.v2x.parallel_execution.v1", "slot_id": "serial"})
            if isinstance(selected, dict)
            else parallel_execution
        ),
        "status": campaign_status,
        "scientific_decision": scientific_decision,
        "selected_interval_us": selected["interval_us"] if selected else None,
        "energy_interpretation": "simulated_relative_reference_not_physical_consumption",
        "energy_phase": bool(args.energy_phase),
        "reward_contract": "greenran.tasam.v2x.reward_adaptive.v1" if args.profile in STRICT_V2X_PROFILES else "legacy",
        "provenance": provenance,
        "validity": {
            "real_pdcp_only": True,
            "proxy_rows_allowed": False,
            "all_vehicle_ues_required": True,
            "selected_interval_requires_all_windows": not args.smoke,
        },
    }
    write_json(args.output_root / "selected_vehicle_profile.json", manifest)
    write_json(args.output_root / "campaign_report.json", {
        "schema": "greenran.campaign_report.v1",
        "report_type": "vehicle_feasibility",
        "campaign_dir": str(args.output_root),
        "profile": args.profile,
        "seed": args.seed,
        "status": manifest["status"],
        "scientific_decision": scientific_decision,
        "validity": manifest["validity"],
        "provenance": provenance,
        "parallel_execution": manifest["parallel_execution"],
        "sla": manifest["sla"],
        "selected_interval_us": manifest["selected_interval_us"],
        "results": results,
        "energy_integrated": _energy_report(args.output_root, results, energy_phase=args.energy_phase),
        "actuations": _actuation_report(args.output_root, results),
        "comparison": _comparison_report(),
        "rejection_reason": None if selected else ("smoke_failed" if args.smoke else "baseline_infeasible"),
    })
    write_json(args.output_root / "campaign_manifest.json", {
        "schema": "greenran.campaign_manifest.v1",
        "kind": "vehicle_feasibility",
        "profile": args.profile,
        "seed": args.seed,
        "status": manifest["status"],
        "scientific_decision": scientific_decision,
        "selected_interval_us": manifest["selected_interval_us"],
        "block_reason": None if selected else ("smoke_failed" if args.smoke else "baseline_infeasible"),
        "campaign_report": str(args.output_root / "campaign_report.json"),
        "provenance": provenance,
        "parallel_execution": manifest["parallel_execution"],
    })
    return 0 if selected else 3


if __name__ == "__main__":
    raise SystemExit(main())
