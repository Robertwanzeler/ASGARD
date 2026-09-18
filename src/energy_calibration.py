#!/usr/bin/env python3
"""Calibrated RU/mmWave power model used by GreenRAN runtime and reports.

This is a calibrated simulation model, not a direct wattmeter reading.  The
model treats the configured idle power as fixed and scales only the dynamic
power with the actuator power level.  This keeps ``25%`` from incorrectly
meaning that a live RU consumes zero power.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any, Iterable


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "energy_calibration.json"
NANOSECONDS = 1_000_000_000
SUPPORTED_SCHEMAS = {
    "greenran.energy_calibration.v1",
    "greenran.energy_calibration.v2",
    "greenran.energy_calibration.v3",
}


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def _component(raw: dict[str, Any], name: str) -> dict[str, float]:
    value = raw.get(name) or {}
    idle = max(0.0, _finite(value.get("idle_w")))
    active = max(idle, _finite(value.get("active_w")))
    return {"idle_w": idle, "active_w": active}


def load_calibration(path: Path | str | None = None) -> dict[str, Any]:
    """Load and validate the versioned calibration file."""
    selected = path or os.environ.get("GREENRAN_ENERGY_CALIBRATION_PATH")
    target = Path(selected) if selected else DEFAULT_CONFIG_PATH
    if os.environ.get("GREENRAN_LOCAL_ONLY", "0").strip().lower() in {"1", "true", "yes", "on"}:
        project_root = Path(__file__).resolve().parents[1]
        resolved_target = target.expanduser().resolve()
        if "/run/media/" in str(resolved_target) or project_root not in resolved_target.parents:
            raise ValueError(f"GREENRAN_LOCAL_ONLY rejeitou calibração fora do projeto: {resolved_target}")
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"calibração energética inválida ou ausente: {target}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema") not in SUPPORTED_SCHEMAS:
        raise ValueError(f"schema de calibração não suportado: {target}")
    components = payload.get("components")
    if not isinstance(components, dict):
        raise ValueError(f"components ausente na calibração: {target}")
    normalized = dict(payload)
    normalized["components"] = {
        name: _component(components, name) for name in ("ru", "mmwave")
    }
    for name, values in normalized["components"].items():
        if values["active_w"] <= 0.0 and normalized["schema"] == "greenran.energy_calibration.v1":
            raise ValueError(f"potência ativa inválida para {name}: {target}")
    normalized["calibration_path"] = str(target.resolve())
    normalized["schema"] = str(payload.get("schema"))
    normalized["absolute_scale_valid"] = bool(payload.get("absolute_scale_valid", False))
    normalized["physical_wattmeter_available"] = bool(
        payload.get("physical_wattmeter_available", False)
    )
    normalized["energy_reference_source"] = str(
        payload.get("energy_reference_source", "calibrated_model")
    )
    normalized["calibration_rank_valid"] = bool(payload.get("calibration_rank_valid", True))
    return normalized


def state_power_w(calibration: dict[str, Any], ru_count: int, mmwave_count: int, power_percent: float) -> float:
    """Return the modeled instantaneous RU/mmWave power in watts."""
    level = max(0.0, min(100.0, _finite(power_percent, 100.0))) / 100.0
    total = 0.0
    for name, count in (("ru", ru_count), ("mmwave", mmwave_count)):
        units = max(0, int(_finite(count, 0.0)))
        values = calibration["components"][name]
        dynamic = values["active_w"] - values["idle_w"]
        total += units * (values["idle_w"] + dynamic * level)
    return total


def observed_radio_power_w(calibration: dict[str, Any], power_percent: float) -> float:
    """Model the configured combined radio unit for native observations.

    The current promoted-for-relative-use corpus identifies one combined
    radio system, not independent RU and mmWave coefficients.  Native cell
    traces still prove the power level and active cells; they must not be
    multiplied by an invented RU count.  Separate-component calibrations keep
    the historical state model and require explicit counts at their caller.
    """
    combined = calibration.get("combined_model") or {}
    source = str((calibration.get("components") or {}).get("mmwave", {}).get("source", ""))
    if combined and ("combined" in source or calibration.get("status") == "experimental_combined_model"):
        level = max(0.0, min(100.0, _finite(power_percent, 100.0))) / 100.0
        idle = max(0.0, _finite(combined.get("idle_w")))
        dynamic = max(0.0, _finite(combined.get("dynamic_w")))
        return idle + dynamic * level
    raise ValueError("calibração não possui modelo combinado para observação nativa")


def sleep_state_power_w(
    calibration: dict[str, Any],
    *,
    active_cells: int,
    power_percent: float,
) -> float:
    """Return the calibrated relative power for a native DU sleep state.

    Sleep power is read from the calibration corpus; it is never inferred as
    zero.  The result remains a simulation reference, not a physical-meter
    measurement.
    """
    if active_cells < 1 or active_cells > 3:
        raise ValueError("active_cells must be between 1 and 3")
    states = calibration.get("sleep_states") or {}
    key = str(int(active_cells))
    state = states.get(key)
    if not isinstance(state, dict):
        raise ValueError(f"missing calibrated sleep state for {active_cells} active cells")
    idle = float(state.get("idle_w"))
    dynamic = float(state.get("dynamic_w"))
    level = max(0.0, min(100.0, float(power_percent))) / 100.0
    return idle + dynamic * level


def sleep_state_power_by_cell_w(
    calibration: dict[str, Any],
    power_percent_by_cell: dict[Any, Any],
) -> float:
    """Model a per-DU action using the calibrated active-cell state.

    The v3 corpus identifies the combined radio state for two or three active
    DUs, not independent physical wattmeters.  We therefore use the explicit
    calibrated state for the observed active-cell count and the mean observed
    power of those active cells.  A sleeping DU is represented by the state
    selection, never by an invented 0 W component.
    """
    if not isinstance(power_percent_by_cell, dict):
        raise ValueError("power_percent_by_cell ausente")
    values = []
    for cell_id in (2, 3, 4):
        raw = power_percent_by_cell.get(str(cell_id), power_percent_by_cell.get(cell_id))
        if raw is None:
            raise ValueError(f"potência observada ausente para célula {cell_id}")
        value = float(raw)
        if not math.isfinite(value) or value < 0.0 or value > 100.0:
            raise ValueError(f"potência observada inválida para célula {cell_id}")
        values.append(value)
    active = [value for value in values if value > 0.0]
    if len(active) not in {2, 3}:
        raise ValueError("o modelo v3 exige duas ou três DUs ativas")
    return sleep_state_power_w(
        calibration,
        active_cells=len(active),
        power_percent=sum(active) / len(active),
    )


def _event_timestamp_ns(event: Any) -> int:
    if isinstance(event, dict):
        raw = event.get("timestamp_ns")
        if raw is not None:
            return max(0, int(_finite(raw)))
        raw = event.get("timestamp")
    else:
        # sqlite3.Row exposes the nanosecond column directly.  Do not scale
        # it again: only the legacy second-resolution ``timestamp`` needs the
        # conversion to nanoseconds.
        keys = event.keys()
        if "timestamp_ns" in keys and event["timestamp_ns"] is not None:
            return max(0, int(_finite(event["timestamp_ns"])))
        raw = event["timestamp"]
    return max(0, int(_finite(raw)) * NANOSECONDS)


def integrate_energy_events(
    events: Iterable[Any],
    calibration: dict[str, Any],
    *,
    end_timestamp_ns: int | None = None,
) -> dict[str, Any]:
    """Integrate piecewise-constant power states and return joules and W."""
    rows = sorted(list(events), key=_event_timestamp_ns)
    if not rows:
        return {
            "valid": False,
            "reason": "no_energy_state_events",
            "energy_j": 0.0,
            "average_power_w": 0.0,
            "duration_s": 0.0,
            "event_count": 0,
        }
    first = _event_timestamp_ns(rows[0])
    last = _event_timestamp_ns(rows[-1])
    end = int(end_timestamp_ns or last)
    if end <= last:
        # A single state still represents one measured control interval when
        # no later runtime snapshot is available.
        end = last + NANOSECONDS
    energy_j = 0.0
    state_seconds: dict[str, float] = {}
    for index, row in enumerate(rows):
        start = _event_timestamp_ns(row)
        stop = _event_timestamp_ns(rows[index + 1]) if index + 1 < len(rows) else end
        if stop <= start:
            continue
        if isinstance(row, dict):
            ru = row.get("ru_count", 0)
            mmwave = row.get("mmwave_count", 0)
            level = row.get("power_percent", 100)
        else:
            ru = row["ru_count"]
            mmwave = row["mmwave_count"]
            level = row["power_percent"]
        power = state_power_w(calibration, int(_finite(ru)), int(_finite(mmwave)), _finite(level, 100.0))
        seconds = (stop - start) / NANOSECONDS
        energy_j += power * seconds
        state_key = f"ru={int(_finite(ru))},mmwave={int(_finite(mmwave))},power={int(_finite(level, 100.0))}%"
        state_seconds[state_key] = state_seconds.get(state_key, 0.0) + seconds
    duration_s = max(0.0, (end - first) / NANOSECONDS)
    return {
        "valid": duration_s > 0.0 and energy_j >= 0.0,
        "reason": "ok" if duration_s > 0.0 else "non_positive_duration",
        "energy_j": energy_j,
        "average_power_w": energy_j / duration_s if duration_s > 0 else 0.0,
        "duration_s": duration_s,
        "event_count": len(rows),
        "state_seconds": state_seconds,
        "calibration_version": calibration.get("calibration_version", "unknown"),
        "calibration_status": calibration.get("status", "unspecified"),
        "calibration_path": calibration.get("calibration_path", ""),
        "energy_reference_source": calibration.get("energy_reference_source", "calibrated_model"),
        "absolute_scale_valid": bool(calibration.get("absolute_scale_valid", False)),
        "physical_wattmeter_available": bool(calibration.get("physical_wattmeter_available", False)),
    }


def integrate_native_energy_samples(samples: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Integrate native ns-3 samples expressed as cumulative joules.

    Native ns-3 energy is a relative simulation reference.  The function is
    intentionally independent of the provisional RU/mmWave watt model.
    """
    rows = sorted(list(samples), key=lambda row: _finite(row.get("timestamp_s")))
    if len(rows) < 2:
        return {"valid": False, "reason": "insufficient_native_samples", "energy_j": 0.0}
    first = _finite(rows[0].get("timestamp_s"))
    last = _finite(rows[-1].get("timestamp_s"))
    cumulative = [_finite(row.get("energy_j")) for row in rows]
    if last <= first or any(value < 0 for value in cumulative):
        return {"valid": False, "reason": "invalid_native_timestamps_or_energy", "energy_j": 0.0}
    energy = max(0.0, cumulative[-1] - cumulative[0])
    duration = last - first
    return {
        "valid": energy >= 0.0 and duration > 0.0,
        "reason": "ok",
        "energy_j": energy,
        "average_power_w": energy / duration if duration else 0.0,
        "duration_s": duration,
        "sample_count": len(rows),
        "energy_reference_source": "ns3_device_energy_model",
        "absolute_scale_valid": False,
        "physical_wattmeter_available": False,
    }
