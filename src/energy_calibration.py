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
from pathlib import Path
from typing import Any, Iterable


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "energy_calibration.json"
NANOSECONDS = 1_000_000_000


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
    target = Path(path) if path else DEFAULT_CONFIG_PATH
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"calibração energética inválida ou ausente: {target}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema") != "greenran.energy_calibration.v1":
        raise ValueError(f"schema de calibração não suportado: {target}")
    components = payload.get("components")
    if not isinstance(components, dict):
        raise ValueError(f"components ausente na calibração: {target}")
    normalized = dict(payload)
    normalized["components"] = {
        name: _component(components, name) for name in ("ru", "mmwave")
    }
    for name, values in normalized["components"].items():
        if values["active_w"] <= 0.0:
            raise ValueError(f"potência ativa inválida para {name}: {target}")
    normalized["calibration_path"] = str(target.resolve())
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
    }
