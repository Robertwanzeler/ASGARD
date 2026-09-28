#!/usr/bin/env python3
"""Adaptive energy envelope for the ASGARD V2X energy pilot.

The dynamic floor is deliberately *not* a second controller. ASGARD keeps
producing the per-DU power proposal and this module only advances the minimum
power that the proposal is allowed to use. The safety-isolation path remains
outside this contract and may always replace the result with 100%.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Iterable, Mapping

DYNAMIC_FLOOR_CONTRACT = "greenran.tasam.adaptive_energy_envelope.v2"
BASELINE_SIGNATURE_SCHEMA = "greenran.tasam.strict_pair.v1"
SAFE_POWER_FLOOR_LEDGER_SCHEMA = "greenran.tasam.adaptive_energy_envelope.v2"
LEGACY_SAFE_POWER_FLOOR_LEDGER_SCHEMA = "greenran.tasam.v2x.safe_power_floor.v2"

DU_CELL_IDS = (2, 3, 4)
MIN_FLOOR_PERCENT = 25
DESCENT_STEP_PP = 5
DESCENT_HEALTHY_WINDOWS = 5
RETREAT_STEP_PP = 10
RETREAT_STABLE_WINDOWS = 10


class DynamicFloorError(ValueError):
    """Raised when the dynamic-floor evidence contract is violated."""


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _power_map(value: Any, *, field: str, allow_zero: bool = False) -> dict[int, int]:
    if isinstance(value, Mapping):
        raw = {cell: value.get(cell, value.get(str(cell))) for cell in DU_CELL_IDS}
    else:
        raw = {cell: value for cell in DU_CELL_IDS}
    result: dict[int, int] = {}
    for cell, item in raw.items():
        try:
            number = float(item)
        except (TypeError, ValueError) as exc:
            raise DynamicFloorError(f"{field} missing or non-numeric for cell {cell}") from exc
        if allow_zero and number == 0.0:
            result[cell] = 0
            continue
        if not math.isfinite(number) or number < MIN_FLOOR_PERCENT or number > 100:
            raise DynamicFloorError(f"{field} outside [25,100] for cell {cell}")
        quantized = int(math.ceil(number / 5.0) * 5)
        if quantized > 100:
            raise DynamicFloorError(f"{field} cannot be represented for cell {cell}")
        result[cell] = quantized
    return result


def load_baseline_signature(
    path: str | Path,
    *,
    expected_seed: int | None = None,
    expected_profile: str | None = None,
    strict_contract: bool = False,
) -> set[tuple[int, int, str]]:
    """Load ``(window, imsi, reason)`` keys from a strict-pair report."""
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DynamicFloorError(f"baseline signature unreadable: {source}") from exc
    if not isinstance(payload, dict):
        raise DynamicFloorError(f"baseline signature must be an object: {source}")
    if strict_contract and payload.get("schema") != BASELINE_SIGNATURE_SCHEMA:
        raise DynamicFloorError(
            f"baseline signature schema must be {BASELINE_SIGNATURE_SCHEMA}"
        )
    if expected_seed is not None and int(payload.get("seed", -1)) != int(expected_seed):
        raise DynamicFloorError("baseline signature seed mismatch")
    if expected_profile is not None:
        experiment = payload.get("experiment_contract") or {}
        report_profile = (
            payload.get("profile")
            or (payload.get("baseline") or {}).get("profile")
            or experiment.get("profile")
        )
        if report_profile in (None, "") and strict_contract:
            manifest_ref = str(experiment.get("baseline_manifest") or "")
            candidates = []
            if manifest_ref:
                reference = Path(manifest_ref)
                if reference.is_absolute():
                    candidates.append(reference)
                else:
                    candidates.extend(parent / reference for parent in source.parents)
            manifest_path = next((item for item in candidates if item.is_file()), None)
            if manifest_path is None:
                raise DynamicFloorError("baseline signature profile provenance missing")
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise DynamicFloorError("baseline signature manifest is unreadable") from exc
            report_profile = manifest.get("profile") if isinstance(manifest, dict) else None
            if expected_seed is not None and int(
                (manifest or {}).get("seed", -1)
            ) != int(expected_seed):
                raise DynamicFloorError("baseline signature manifest seed mismatch")
        if report_profile != expected_profile:
            raise DynamicFloorError("baseline signature profile mismatch")
        if strict_contract and not experiment.get("baseline_manifest"):
            raise DynamicFloorError("baseline signature provenance missing")
    signature = (payload.get("sla_signature") or {}).get("baseline_violation_keys")
    if signature is None:
        raise DynamicFloorError(f"baseline signature missing in {source}")
    try:
        return {
            (int(window), int(imsi), str(reason))
            for window, imsi, reason in signature
        }
    except (TypeError, ValueError) as exc:
        raise DynamicFloorError("baseline signature contains malformed keys") from exc


def load_dynamic_floor_ledger(
    path: str | Path,
    *,
    expected_seed: int | None = None,
    expected_profile: str | None = None,
    baseline_signature_path: str | Path | None = None,
) -> dict[str, Any]:
    """Validate and normalize the immutable safe-floor ledger v2."""
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DynamicFloorError(f"dynamic-floor ledger unreadable: {source}") from exc
    if not isinstance(payload, dict) or payload.get("schema") not in {
        SAFE_POWER_FLOOR_LEDGER_SCHEMA,
        LEGACY_SAFE_POWER_FLOOR_LEDGER_SCHEMA,
    }:
        raise DynamicFloorError(
            f"adaptive-envelope ledger schema must be {SAFE_POWER_FLOOR_LEDGER_SCHEMA}"
        )
    if payload.get("status") not in {"candidate", "validated"}:
        raise DynamicFloorError("dynamic-floor ledger status must be candidate or validated")
    if expected_seed is not None and int(payload.get("seed", -1)) != int(expected_seed):
        raise DynamicFloorError("dynamic-floor ledger seed mismatch")
    if expected_profile is not None and str(payload.get("profile") or "") != expected_profile:
        raise DynamicFloorError("dynamic-floor ledger profile mismatch")

    initial = _power_map(
        payload.get("initial_floor_percent_by_cell"),
        field="initial_floor_percent_by_cell",
    )
    minimum = _power_map(
        payload.get("minimum_floor_percent_by_cell"),
        field="minimum_floor_percent_by_cell",
    )
    validated = _power_map(
        payload.get("previous_validated_floor_percent_by_cell"),
        field="previous_validated_floor_percent_by_cell",
    )
    if any(minimum[cell] > initial[cell] for cell in DU_CELL_IDS):
        raise DynamicFloorError("minimum floor cannot exceed initial floor")
    if any(validated[cell] < initial[cell] for cell in DU_CELL_IDS):
        raise DynamicFloorError("previous validated floor must preserve the conservative provenance")
    if any(initial[cell] != 25 or minimum[cell] != 25 for cell in DU_CELL_IDS):
        raise DynamicFloorError("adaptive campaign requires an exact 25% initial/minimum active floor")
    if any(validated[cell] != 60 for cell in DU_CELL_IDS):
        raise DynamicFloorError("seed-43 dynamic campaign must preserve the validated 60% provenance")

    physics = payload.get("physics_evidence") or {}
    try:
        reduction = float(physics.get("energy_reduction_fraction"))
        candidate_power = int(physics.get("candidate_power_percent"))
    except (TypeError, ValueError) as exc:
        raise DynamicFloorError("dynamic-floor physics evidence missing") from exc
    if physics.get("status") != "validated" or reduction <= 0.0:
        raise DynamicFloorError("adaptive-envelope physics evidence is not a positive validated reduction")
    if candidate_power != min(initial.values()) or candidate_power != min(minimum.values()):
        raise DynamicFloorError("physics candidate must match the campaign initial/minimum floor")
    if physics.get("curve_power_percent") != [100, 70, 45, 25]:
        raise DynamicFloorError("adaptive-envelope physics must contain the complete 100/70/45/25 curve")
    for hash_field in (
        "baseline_sha256", "intermediate_sha256", "candidate_sha256", "low_power_sha256",
    ):
        if len(str(physics.get(hash_field) or "")) != 64:
            raise DynamicFloorError(f"dynamic-floor physics hash missing: {hash_field}")
    sleep = payload.get("sleep_evidence") or {}
    if not isinstance(sleep, dict) or sleep.get("status") != "validated":
        raise DynamicFloorError("adaptive-envelope requires validated handover/sleep evidence")
    if len(str(sleep.get("run_sha256") or "")) != 64:
        raise DynamicFloorError("adaptive-envelope sleep evidence hash missing")

    signature_hash = str(payload.get("baseline_signature_sha256") or "")
    if len(signature_hash) != 64:
        raise DynamicFloorError("baseline signature SHA-256 missing from ledger")
    if baseline_signature_path is not None and sha256_file(baseline_signature_path) != signature_hash:
        raise DynamicFloorError("baseline signature SHA-256 mismatch")
    if not str(payload.get("initial_checkpoint") or "") or len(
        str(payload.get("initial_checkpoint_sha256") or "")
    ) != 64:
        raise DynamicFloorError("initial r26 checkpoint provenance is missing")

    return {
        **payload,
        "schema": SAFE_POWER_FLOOR_LEDGER_SCHEMA,
        "initial_floor_percent_by_cell": {str(k): v for k, v in initial.items()},
        "minimum_floor_percent_by_cell": {str(k): v for k, v in minimum.items()},
        "previous_validated_floor_percent_by_cell": {
            str(k): v for k, v in validated.items()
        },
        "ledger_sha256": sha256_file(source),
    }


def attributable_violations(
    current: Iterable[tuple[int, int, str]],
    baseline_signature: Iterable[tuple[int, int, str]],
) -> set[tuple[int, int, str]]:
    """Return only SLA keys absent from the immutable r26 signature."""
    signature = {
        (int(window), int(imsi), str(reason))
        for window, imsi, reason in baseline_signature
    }
    return {
        (int(window), int(imsi), str(reason))
        for window, imsi, reason in current
    } - signature


def project_asgard_power(
    requested_power_by_cell: Mapping[Any, Any],
    floor_percent_by_cell: Mapping[Any, Any],
    *,
    isolated: bool = False,
) -> tuple[dict[int, int], dict[str, Any]]:
    """Clamp ASGARD's proposal while preserving every request above floor."""
    requested = _power_map(
        requested_power_by_cell, field="ASGARD power proposal", allow_zero=True
    )
    floor = _power_map(floor_percent_by_cell, field="dynamic floor")
    selected = (
        {cell: 100 for cell in DU_CELL_IDS}
        if isolated else
        {cell: max(requested[cell], floor[cell]) for cell in DU_CELL_IDS}
    )
    actor_preserved_cells = [
        cell for cell in DU_CELL_IDS
        if not isolated and selected[cell] == requested[cell]
    ]
    # Equality with the floor proves preservation, but not causal influence:
    # the envelope alone would have selected the same value.  A proposal only
    # counts toward the acceptance criterion when it raises a DU above floor.
    actor_cells = [
        cell for cell in actor_preserved_cells
        if requested[cell] > floor[cell]
    ]
    return selected, {
        "requested_power_percent_by_cell": {str(k): v for k, v in requested.items()},
        "floor_percent_by_cell": {str(k): v for k, v in floor.items()},
        "selected_power_percent_by_cell": {str(k): v for k, v in selected.items()},
        "actor_preserved_cells": actor_preserved_cells,
        "actor_influenced_cells": actor_cells,
        "actor_influenced": bool(actor_cells),
        "isolated": bool(isolated),
    }


def project_discretionary_symbol_budget(
    total_budget_fraction: Any,
    ran_share: Any,
    *,
    active_cells: Iterable[int] = DU_CELL_IDS,
) -> tuple[dict[int, int], dict[str, Any]]:
    """Translate the learned global action into a native per-DU DL cap.

    The cap only applies after HARQ, GBR and verified UE floors.  It is thus
    an energy-saving authority over discretionary symbols, never permission
    to erode the SLA floor.  Every active DU receives the same fraction; the
    scheduler's existing weighted UE policy distributes that local budget.
    """
    try:
        total = float(total_budget_fraction)
        ran = float(ran_share)
    except (TypeError, ValueError) as exc:
        raise DynamicFloorError("ASGARD total_budget_fraction and ran_share are required") from exc
    if not math.isfinite(total) or not math.isfinite(ran):
        raise DynamicFloorError("ASGARD resource budget must be finite")
    requested = max(0.0, min(1.0, total)) * max(0.0, min(1.0, ran))
    cells = tuple(sorted({int(cell) for cell in active_cells if int(cell) in DU_CELL_IDS}))
    if not cells:
        raise DynamicFloorError("at least one active DU is required for an ASGARD resource budget")
    bp = int(round(requested * 10_000.0))
    budget = {cell: bp for cell in cells}
    return budget, {
        "requested_total_budget_fraction": max(0.0, min(1.0, total)),
        "requested_ran_share": max(0.0, min(1.0, ran)),
        "requested_discretionary_dl_fraction": requested,
        "requested_discretionary_dl_symbols_bp_by_cell": {
            str(cell): bp for cell in cells
        },
    }


def dynamic_floor_candidate(
    state: Mapping[Any, Any] | None,
    *,
    current_violations: Iterable[tuple[int, int, str]] = (),
    baseline_signature: Iterable[tuple[int, int, str]] = (),
    start_percent: int | Mapping[Any, Any] = 100,
    minimum_percent: int | Mapping[Any, Any] = MIN_FLOOR_PERCENT,
    window_id: str | int | None = None,
    power_transaction_id: str | int | None = None,
    window_healthy: bool | None = None,
    isolation_reason: str = "",
    affected_cells: Iterable[int] | None = None,
) -> tuple[dict[int, int], dict[str, Any]]:
    """Advance the floor once for a unique completed observation window."""
    previous = dict(state or {})
    previous_contract = previous.get("contract")
    if previous_contract not in (None, "", DYNAMIC_FLOOR_CONTRACT):
        raise DynamicFloorError("dynamic-floor state contract mismatch")

    initial = _power_map(start_percent, field="start_percent")
    minimum = _power_map(minimum_percent, field="minimum_percent")
    if any(minimum[cell] > initial[cell] for cell in DU_CELL_IDS):
        raise DynamicFloorError("minimum floor cannot exceed start floor")
    floor = _power_map(
        previous.get("floor_percent_by_cell", previous.get("floor_percent", initial)),
        field="floor_percent_by_cell",
    )
    if any(floor[cell] < minimum[cell] for cell in DU_CELL_IDS):
        raise DynamicFloorError("floor is below campaign minimum")

    normalized_window = None if window_id is None else str(window_id)
    processed_window_ids = [
        str(value) for value in previous.get("processed_window_ids", [])
    ]
    if normalized_window is not None and normalized_window in set(processed_window_ids):
        return floor, previous

    current = {
        (int(window), int(imsi), str(reason))
        for window, imsi, reason in current_violations
    }
    attributable = attributable_violations(current, baseline_signature)
    seen = {
        (int(window), int(imsi), str(reason))
        for window, imsi, reason in previous.get("seen_attributable_violation_keys", [])
    }
    unseen_attributable = attributable - seen
    descend_streak = int(previous.get("descend_streak", 0) or 0)
    stable_streak = int(previous.get("stable_streak", 0) or 0)
    retreats = int(previous.get("retreats", 0) or 0)
    descent_enabled = bool(previous.get("descent_enabled", True))
    healthy = bool(not current) if window_healthy is None else bool(window_healthy)

    normalized_affected = tuple(sorted({
        int(cell) for cell in (affected_cells or DU_CELL_IDS) if int(cell) in DU_CELL_IDS
    })) or DU_CELL_IDS
    if unseen_attributable:
        floor = {
            cell: min(100, value + RETREAT_STEP_PP) if cell in normalized_affected else value
            for cell, value in floor.items()
        }
        retreats += 1
        descend_streak = 0
        stable_streak = 0
        descent_enabled = False
        seen.update(unseen_attributable)
    elif not healthy:
        # Baseline-covered degradation is not blamed on ASGARD, but it also
        # cannot earn a lower floor.
        descend_streak = 0
        stable_streak = 0
    elif not descent_enabled:
        stable_streak += 1
        if stable_streak >= RETREAT_STABLE_WINDOWS:
            descent_enabled = True
            descend_streak = 0
            # The tenth recovery window only re-arms descent.
    else:
        stable_streak += 1
        descend_streak += 1
        if descend_streak >= DESCENT_HEALTHY_WINDOWS:
            floor = {
                cell: max(minimum[cell], value - DESCENT_STEP_PP)
                for cell, value in floor.items()
            }
            descend_streak = 0

    scalar_floor = min(floor.values()) if len(set(floor.values())) == 1 else None
    if normalized_window is not None:
        processed_window_ids.append(normalized_window)
    window_sequence_id = int(previous.get("window_sequence_id", 0) or 0) + 1
    next_state = {
        **previous,
        "contract": DYNAMIC_FLOOR_CONTRACT,
        "floor_percent_by_cell": {str(k): v for k, v in floor.items()},
        "floor_percent": scalar_floor,
        "minimum_floor_percent_by_cell": {str(k): v for k, v in minimum.items()},
        "descend_streak": descend_streak,
        "stable_streak": stable_streak,
        "retreats": retreats,
        "descent_enabled": descent_enabled,
        "window_sequence_id": window_sequence_id,
        "processed_window_ids": processed_window_ids,
        "last_processed_window_id": normalized_window,
        "last_power_transaction_id": power_transaction_id,
        "last_window_healthy": healthy,
        "last_isolation_reason": str(isolation_reason or ""),
        "affected_cells_last_window": list(normalized_affected),
        "current_violation_keys": sorted(current),
        "attributable_last_window": sorted(attributable),
        "new_attributable_last_window": sorted(unseen_attributable),
        "seen_attributable_violation_keys": sorted(seen),
    }
    return floor, next_state


def atomic_write_state(path: str | Path, state: Mapping[str, Any]) -> None:
    """Atomically persist one JSON state snapshot in the arm directory."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(dict(state), handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
