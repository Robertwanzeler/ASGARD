"""Native-only handover and DU-sleep calibration for the V4 envelope.

This module deliberately does not know about the ASGARD actor.  It is used
only by the calibration arm, after the regular runtime has brought up ns-3,
the RIC and the TA-SAM actuator.  Keeping the emitter separate prevents a
periodic rApp decision from racing a controlled drain/commit transaction.
"""

from __future__ import annotations

import csv
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from greenran_control_bundle import (
    ControlBundleClient,
    V4_SCHEMA,
    validate_bundle,
)
from greenran_infra_budget import build_physical_budget


DU_CELLS = (2, 3, 4)
IMSIS = set(range(1, 21))
FLOOR_BP_BY_IMSI = {
    **{imsi: 825 for imsi in range(1, 4)},
    **{imsi: 218 for imsi in range(4, 16)},
    **{imsi: 326 for imsi in range(16, 21)},
}


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _epoch_ordinal(value: str) -> int:
    try:
        return int(str(value or "").rsplit("-", 1)[1])
    except (IndexError, ValueError):
        return -1


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def latest_association_snapshot(path: Path) -> dict[int, dict[str, Any]]:
    """Return the newest complete RRC snapshot for each managed DU.

    Association rows are emitted only on a topology change, while
    ``cell_snapshot`` rows are emitted on every native tick.  The snapshot's
    ordinal ties it to the last change event for that DU, so a stable mapping
    remains usable without rewriting all IMSIs on every tick.
    """
    events: dict[int, dict[int, set[int]]] = {cell: {} for cell in DU_CELLS}
    snapshots: dict[int, dict[str, Any]] = {}
    try:
        with path.open(newline="", encoding="utf-8", errors="replace") as handle:
            for row in csv.DictReader(handle):
                try:
                    cell = int(row.get("CellId", -1))
                except (TypeError, ValueError):
                    continue
                if cell not in DU_CELLS:
                    continue
                kind = str(row.get("ObservationKind") or "")
                epoch = str(row.get("AssociationEpoch") or "")
                ordinal = _epoch_ordinal(epoch)
                if kind == "ue_association":
                    try:
                        imsi = int(row.get("Imsi", -1))
                    except (TypeError, ValueError):
                        continue
                    if imsi in IMSIS and ordinal >= 0:
                        events[cell].setdefault(ordinal, set()).add(imsi)
                elif kind == "cell_snapshot":
                    try:
                        count = int(row.get("AttachedUeCount", -1))
                    except (TypeError, ValueError):
                        continue
                    if count < 0:
                        continue
                    snapshots[cell] = {
                        "time_s": _number(row.get("Time"), -1.0),
                        "count": count,
                        "ordinal": ordinal,
                        "sleep_transaction_id": str(row.get("SleepTransactionId") or ""),
                    }
    except OSError:
        return {}

    result: dict[int, dict[str, Any]] = {}
    for cell in DU_CELLS:
        snapshot = snapshots.get(cell)
        if not snapshot:
            continue
        choices = [key for key in events[cell] if key <= int(snapshot["ordinal"])]
        members = events[cell].get(max(choices), set()) if choices else set()
        # A non-empty snapshot must agree with its exact native membership.
        # An empty source is kept explicit; it is accepted only by the caller
        # after matching the sleep transaction.
        if snapshot["count"] and len(members) != snapshot["count"]:
            continue
        result[cell] = {**snapshot, "imsis": set(members)}
    return result


def _policy(imsi: int) -> dict[str, int]:
    return {
        "imsi": int(imsi),
        "min_dl_share_bp": int(FLOOR_BP_BY_IMSI[int(imsi)]),
        "min_ul_share_bp": 0,
        "surplus_weight_bp": 10_000,
    }


def _policies(imsis: set[int]) -> list[dict[str, int]]:
    return [_policy(imsi) for imsi in sorted(imsis)]


def choose_drain_plan(snapshot: dict[int, dict[str, Any]]) -> tuple[int, list[dict[str, int]]]:
    """Choose the lowest safe source and an MC destination for every source UE."""
    memberships = {cell: set(snapshot.get(cell, {}).get("imsis") or set()) for cell in DU_CELLS}
    if any(cell not in snapshot for cell in DU_CELLS):
        raise ValueError("native association snapshot is incomplete")
    if set().union(*memberships.values()) != IMSIS:
        raise ValueError("native association does not cover all 20 IMSIs")

    for source in DU_CELLS:
        source_imsis = memberships[source]
        if not source_imsis:
            continue
        candidates: list[tuple[int, int, int]] = []
        feasible = True
        for imsi in sorted(source_imsis):
            alternatives = sorted(cell for cell in DU_CELLS if cell != source and imsi in memberships[cell])
            if not alternatives:
                feasible = False
                break
            target = min(alternatives, key=lambda cell: (len(memberships[cell]), cell))
            candidates.append((imsi, target, source))
        if not feasible:
            continue
        projected = {cell: set(memberships[cell]) for cell in DU_CELLS if cell != source}
        for imsi, target, _ in candidates:
            projected[target].add(imsi)
        if any(sum(FLOOR_BP_BY_IMSI[imsi] for imsi in imsis) > 10_000 for imsis in projected.values()):
            continue
        return source, [
            {"imsi": imsi, "target_cell_id": target}
            for imsi, target, _ in candidates
        ]
    raise ValueError("no DU has a safe MC drain destination")


def make_sleep_bundle(
    *,
    phase: str,
    sequence: int,
    sleep_transaction_id: str,
    source_cell_id: int,
    snapshot: dict[int, dict[str, Any]],
    handover_plan: list[dict[str, int]],
) -> dict[str, Any]:
    """Build one validated V4 drain, commit or wake bundle."""
    phase = str(phase).lower()
    if phase not in {"drain", "commit", "wake"}:
        raise ValueError("unknown sleep phase")
    cells: list[dict[str, Any]] = []
    power: dict[str, int] = {}
    for cell in DU_CELLS:
        imsis = set(snapshot.get(cell, {}).get("imsis") or set())
        cell_power = 100
        if phase == "drain" and cell == source_cell_id:
            cell_power = 25
        if phase == "commit" and cell == source_cell_id:
            cell_power = 0
            imsis = set()
        power[str(cell)] = cell_power
        cells.append({
            "cell_id": cell,
            "tx_power_percent": cell_power,
            "ue_policies": _policies(imsis),
            "max_discretionary_dl_symbols_bp": 10_000,
        })
    transition: dict[str, Any] = {
        "phase": phase,
        "sleep_transaction_id": sleep_transaction_id,
        "source_cell_id": source_cell_id,
        "handover_plan": handover_plan,
    }
    if phase == "commit":
        transition.update({
            "handover_confirmed": True,
            "association_valid": True,
            "pdcp_window_valid": True,
            "pdcp_window_s": 10.0,
        })
    bundle = {
        "schema": V4_SCHEMA,
        "policy_id": f"native-sleep-{phase}-{sleep_transaction_id}",
        "sequence": int(sequence),
        "issued_at_ns": time.time_ns(),
        "ttl_ms": 5_000,
        "mode": "economic_action_v3_per_du_sleep",
        "economic_action_contract": "economic_action_v3_per_du_sleep",
        "association_mode": "native_rrc_mc_overlap",
        "sim_time_s": max(_number(value.get("time_s"), 0.0) for value in snapshot.values()),
        "power_percent_by_cell": power,
        "cells": cells,
        "sleep_transition": transition,
        "infra": build_physical_budget(1.0, unrestricted=True),
    }
    return validate_bundle(bundle)


def _pdcp_window_valid(path: Path, *, after_s: float) -> bool:
    """Require fresh real PDCP evidence for every vehicle IMSI after drain.

    The association gate covers all twenty IMSIs.  The native vehicle-PDCP
    trace is the SLA authority for IMSIs 16--20 in this scenario, and its
    header is ``IMSI`` (uppercase) in the ns-3 producer.
    """
    seen: set[int] = set()
    try:
        with path.open(newline="", encoding="utf-8", errors="replace") as handle:
            for row in csv.DictReader(handle):
                if _number(row.get("Time"), -1.0) < after_s:
                    continue
                try:
                    imsi = int(row.get("IMSI", row.get("Imsi", -1)))
                except (TypeError, ValueError):
                    continue
                if 16 <= imsi <= 20:
                    seen.add(imsi)
    except OSError:
        return False
    return seen == set(range(16, 21))


def _sleep_readback_present(path: Path, *, source: int, sleep_id: str) -> bool:
    try:
        with path.open(newline="", encoding="utf-8", errors="replace") as handle:
            return any(
                int(_number(row.get("CellId"), -1)) == source
                and int(round(_number(row.get("TxPowerPercent"), -1))) == 0
                and str(row.get("SleepTransactionId") or "") == sleep_id
                and str(row.get("ObservationKind") or "") == "power_readback"
                for row in csv.DictReader(handle)
            )
    except OSError:
        return False


def execute_native_sleep_calibration(
    run_dir: Path,
    socket_dir: Path,
    stop: Any,
    result: dict[str, Any],
    *,
    timeout_s: float = 720.0,
) -> None:
    """Perform one fail-closed native drain/commit transaction in a live arm."""
    association_path = run_dir / "ns3_energy" / "TasamAssociationTrace.csv"
    control_path = run_dir / "ns3_energy" / "TasamControlObservations.csv"
    pdcp_path = run_dir / "ns3_energy" / "VehiclePdcpPduTrace.csv"
    evidence_path = run_dir / "sleep_calibration_evidence.json"
    client = ControlBundleClient(
        socket_path=socket_dir / "tasam_control.sock",
        shadow_path=run_dir / "xapp_intents" / "tasam_control_bundle.json",
        ack_path=run_dir / "xapp_intents" / "tasam_control_ack.json",
        audit_path=run_dir / "xapp_intents" / "tasam_control_audit.jsonl",
        timeout_seconds=5.0,
    )
    deadline = time.monotonic() + float(timeout_s)
    sleep_id = f"sleep-cal-{uuid.uuid4().hex}"
    result.update({"schema": "greenran.tasam.native_sleep_calibration.v1", "status": "waiting", "sleep_transaction_id": sleep_id})
    _write_json(evidence_path, result)
    source = None
    drain_snapshot: dict[int, dict[str, Any]] = {}
    handover_plan: list[dict[str, int]] = []
    empty_since = -1.0
    sequence = int(time.time_ns() // 1_000_000)
    try:
        while not stop.is_set() and time.monotonic() < deadline:
            snapshot = latest_association_snapshot(association_path)
            sim_time = max((_number(item.get("time_s"), -1.0) for item in snapshot.values()), default=-1.0)
            if source is None:
                if sim_time < 30.0:
                    time.sleep(0.25)
                    continue
                try:
                    source, handover_plan = choose_drain_plan(snapshot)
                except ValueError:
                    time.sleep(0.25)
                    continue
                drain_snapshot = snapshot
                drain = make_sleep_bundle(
                    phase="drain", sequence=sequence, sleep_transaction_id=sleep_id,
                    source_cell_id=source, snapshot=snapshot, handover_plan=handover_plan,
                )
                client.send(drain)
                result.update({
                    "status": "draining", "source_cell_id": source,
                    "drain_sequence": sequence, "handover_plan": handover_plan,
                    "drain_sim_time_s": sim_time,
                    "association_coverage_imsis": sorted(set().union(*(
                        set(snapshot.get(cell, {}).get("imsis") or set())
                        for cell in DU_CELLS
                    ))),
                })
                _write_json(evidence_path, result)
                sequence += 1
                time.sleep(0.25)
                continue

            source_snapshot = snapshot.get(source) or {}
            target_coverage = set().union(
                *(set(snapshot.get(cell, {}).get("imsis") or set()) for cell in DU_CELLS if cell != source)
            )
            source_empty = (
                int(source_snapshot.get("count", -1)) == 0
                and str(source_snapshot.get("sleep_transaction_id") or "") == sleep_id
            )
            if source_empty and target_coverage == IMSIS:
                empty_since = max(empty_since, _number(source_snapshot.get("time_s"), -1.0))
            if empty_since >= 0 and sim_time >= empty_since + 10.0 and _pdcp_window_valid(pdcp_path, after_s=empty_since):
                commit = make_sleep_bundle(
                    phase="commit", sequence=sequence, sleep_transaction_id=sleep_id,
                    source_cell_id=source, snapshot=snapshot, handover_plan=handover_plan,
                )
                client.send(commit)
                result.update({
                    "status": "committed_waiting_native", "commit_sequence": sequence,
                    "empty_source_sim_time_s": empty_since, "pdcp_window_s": 10.0,
                    "association_coverage_imsis": sorted(target_coverage),
                    "pdcp_required_imsis": list(range(16, 21)),
                })
                _write_json(evidence_path, result)
                sequence += 1
                commit_deadline = time.monotonic() + 60.0
                while not stop.is_set() and time.monotonic() < commit_deadline:
                    confirmed = latest_association_snapshot(association_path).get(source) or {}
                    if (
                        int(confirmed.get("count", -1)) == 0
                        and str(confirmed.get("sleep_transaction_id") or "") == sleep_id
                        and _sleep_readback_present(control_path, source=source, sleep_id=sleep_id)
                    ):
                        result.update({
                            "status": "completed",
                            "native_commit_confirmed": True,
                            "association_coverage_imsis": sorted(target_coverage),
                            "pdcp_required_imsis": list(range(16, 21)),
                        })
                        _write_json(evidence_path, result)
                        return
                    time.sleep(0.25)
                raise RuntimeError("sleep commit missing native readback or empty source snapshot")
            time.sleep(0.25)
        raise RuntimeError("sleep calibration timed out before a valid commit")
    except Exception as exc:
        result.update({"status": "failed", "error": f"{type(exc).__name__}: {exc}"})
        if source is not None:
            try:
                wake_snapshot = latest_association_snapshot(association_path) or drain_snapshot
                wake = make_sleep_bundle(
                    phase="wake", sequence=sequence, sleep_transaction_id=sleep_id,
                    source_cell_id=source, snapshot=wake_snapshot, handover_plan=handover_plan,
                )
                client.send(wake)
                result["wake_sequence"] = sequence
            except Exception as wake_exc:
                result["wake_error"] = f"{type(wake_exc).__name__}: {wake_exc}"
        _write_json(evidence_path, result)
