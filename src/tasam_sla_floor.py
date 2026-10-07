"""SLA-only power floor for the isolated ASGARD V2X pilot.

This module deliberately has no dependency on the historical safe-power
ledger.  It is a small, deterministic state machine so that the controller,
tests and campaign manifest use the same contract.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping


CONTRACT = "greenran.tasam.v2x.sla_floor.v1"
DU_CELL_IDS = (2, 3, 4)
INITIAL_PERCENT = 100
MINIMUM_PERCENT = 25
STEP_PERCENT = 10
HEALTHY_REQUIRED = 3
UPPER_MULTIPLIER = 1.15


def _as_cell_map(values: Mapping[Any, Any], *, default: int | None = None) -> dict[int, int]:
    result: dict[int, int] = {}
    for cell in DU_CELL_IDS:
        value = values.get(cell, values.get(str(cell), default))
        if value is None:
            raise ValueError(f"potência ausente para DU {cell}")
        try:
            number = int(round(float(value)))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"potência inválida para DU {cell}: {value!r}") from exc
        if number < 0 or number > 100:
            raise ValueError(f"potência fora de 0..100 para DU {cell}: {number}")
        result[cell] = number
    return result


def _json_cell_map(values: Mapping[Any, Any]) -> dict[str, int]:
    return {str(cell): int(values[cell]) for cell in DU_CELL_IDS}


def new_state() -> dict[str, Any]:
    return {
        "schema": CONTRACT,
        "contract": CONTRACT,
        "floor_percent_by_cell": _json_cell_map({cell: INITIAL_PERCENT for cell in DU_CELL_IDS}),
        "minimum_floor_percent": MINIMUM_PERCENT,
        "step_percent": STEP_PERCENT,
        "healthy_required": HEALTHY_REQUIRED,
        "upper_multiplier": UPPER_MULTIPLIER,
        "healthy_streak": 0,
        "processed_windows": {},
        "processed_native_sequences": [],
        "observed_windows": 0,
        "last_classification": "hold",
        "last_reason": "initial_state",
        "last_window_id": None,
        "last_native_control_sequence": None,
        "last_window_healthy": False,
    }


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return new_state()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"estado do piso SLA inválido: {path}") from exc
    if not isinstance(payload, dict) or payload.get("contract") != CONTRACT:
        raise ValueError("estado do piso SLA usa contrato incompatível")
    state = new_state()
    state.update(payload)
    state["floor_percent_by_cell"] = _json_cell_map(
        _as_cell_map(state.get("floor_percent_by_cell") or {}, default=INITIAL_PERCENT)
    )
    state["healthy_streak"] = max(0, int(state.get("healthy_streak", 0) or 0))
    state["processed_windows"] = dict(state.get("processed_windows") or {})
    state["processed_native_sequences"] = [
        str(value) for value in (state.get("processed_native_sequences") or [])
    ]
    return state


def persist_state(path: Path, state: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(dict(state), handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _floor_map(state: Mapping[str, Any]) -> dict[int, int]:
    return _as_cell_map(state.get("floor_percent_by_cell") or {}, default=INITIAL_PERCENT)


def upper_bound_by_cell(state: Mapping[str, Any]) -> dict[int, int]:
    floor = _floor_map(state)
    multiplier = float(state.get("upper_multiplier", UPPER_MULTIPLIER) or UPPER_MULTIPLIER)
    return {cell: min(100, int(floor[cell] * multiplier)) for cell in DU_CELL_IDS}


def project_power(
    requested_power_by_cell: Mapping[Any, Any],
    state: Mapping[str, Any],
    *,
    blocked: bool = False,
) -> tuple[dict[int, int], dict[str, Any]]:
    """Clamp a TA-SAM proposal to the current SLA-governed envelope."""
    requested = _as_cell_map(requested_power_by_cell)
    floor = _floor_map(state)
    upper = upper_bound_by_cell(state)
    if blocked:
        selected = {cell: INITIAL_PERCENT for cell in DU_CELL_IDS}
        classification = "restore_full_power"
        reason = "blocked_or_sla_critical"
    else:
        selected = {}
        probe_cells = []
        for cell in DU_CELL_IDS:
            value = requested[cell]
            if floor[cell] < INITIAL_PERCENT and value == INITIAL_PERCENT:
                selected[cell] = floor[cell]
                probe_cells.append(cell)
            else:
                selected[cell] = min(upper[cell], max(floor[cell], value))
        classification = "hold"
        reason = "sla_floor_envelope"
        if probe_cells:
            classification = "hold"
            reason = "sla_floor_probe"
    return selected, {
        "contract": CONTRACT,
        "floor_percent_by_cell": _json_cell_map(floor),
        "upper_percent_by_cell": _json_cell_map(upper),
        "requested_power_percent_by_cell": _json_cell_map(requested),
        "selected_power_percent_by_cell": _json_cell_map(selected),
        "probe_cells": probe_cells if not blocked else [],
        "action_kind": "sla_floor_probe" if (not blocked and any(
            requested[cell] == INITIAL_PERCENT and floor[cell] < INITIAL_PERCENT
            for cell in DU_CELL_IDS
        )) else "actor_clamped",
        "classification": classification,
        "reason": reason,
    }


def observe_window(
    state: Mapping[str, Any],
    *,
    window_id: Any,
    native_control_sequence: Any,
    healthy: bool,
    reason: str = "",
    evidence: Mapping[str, Any] | None = None,
    metric_invalid: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Consume one post-action window exactly once and update the floor."""
    next_state = dict(state)
    floor_before = _floor_map(next_state)
    window_key = str(window_id)
    sequence_key = str(native_control_sequence)
    processed_windows = dict(next_state.get("processed_windows") or {})
    processed_sequences = [str(value) for value in (next_state.get("processed_native_sequences") or [])]
    duplicate = window_key in processed_windows or sequence_key in processed_sequences
    if duplicate:
        healthy_streak = 0
        floor_after = {cell: INITIAL_PERCENT for cell in DU_CELL_IDS}
        classification = "metric_invalid"
        final_reason = "duplicate_window_or_native_sequence"
    elif metric_invalid:
        healthy_streak = 0
        floor_after = {cell: INITIAL_PERCENT for cell in DU_CELL_IDS}
        classification = "metric_invalid"
        final_reason = reason or "incomplete_or_invalid_sla_evidence"
    elif not healthy:
        healthy_streak = 0
        floor_after = {cell: INITIAL_PERCENT for cell in DU_CELL_IDS}
        classification = "restore_full_power"
        final_reason = reason or "sla_window_unhealthy"
    else:
        healthy_streak = int(next_state.get("healthy_streak", 0) or 0) + 1
        if healthy_streak >= int(next_state.get("healthy_required", HEALTHY_REQUIRED) or HEALTHY_REQUIRED):
            floor_after = {
                cell: max(
                    int(next_state.get("minimum_floor_percent", MINIMUM_PERCENT) or MINIMUM_PERCENT),
                    floor_before[cell] - int(next_state.get("step_percent", STEP_PERCENT) or STEP_PERCENT),
                )
                for cell in DU_CELL_IDS
            }
            healthy_streak = 0
            classification = "healthy_descend" if floor_after != floor_before else "hold"
            final_reason = "three_consecutive_healthy_sla_windows" if classification == "healthy_descend" else "minimum_floor_reached"
        else:
            floor_after = floor_before
            classification = "hold"
            final_reason = "healthy_window_streak"
    processed_windows[window_key] = {
        "native_control_sequence": sequence_key,
        "healthy": bool(healthy),
        "classification": classification,
    }
    next_state.update({
        "floor_percent_by_cell": _json_cell_map(floor_after),
        "healthy_streak": healthy_streak,
        "processed_windows": processed_windows,
        "processed_native_sequences": (processed_sequences + [sequence_key])[-512:],
        "observed_windows": int(next_state.get("observed_windows", 0) or 0) + 1,
        "last_classification": classification,
        "last_reason": final_reason,
        "last_window_id": window_key,
        "last_native_control_sequence": sequence_key,
        "last_window_healthy": bool(healthy),
    })
    observation = {
        "contract": CONTRACT,
        "window_id": window_key,
        "native_control_sequence": sequence_key,
        "floor_before_percent_by_cell": _json_cell_map(floor_before),
        "floor_after_percent_by_cell": _json_cell_map(floor_after),
        "healthy_streak": healthy_streak,
        "healthy": bool(healthy),
        "duplicate": duplicate,
        "classification": classification,
        "reason": final_reason,
        "evidence": dict(evidence or {}),
    }
    return next_state, observation
