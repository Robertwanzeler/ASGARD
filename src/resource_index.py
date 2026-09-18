"""Relative host-resource index for paired GreenRAN measurements."""

from __future__ import annotations

import math
from typing import Any

SCHEMA = "greenran.infrastructure.resource_index.v1"
WEIGHTS = {"cpu": 0.50, "memory": 0.30, "io": 0.20}


def _positive(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed >= 0.0 else None


def resource_index(metrics: dict[str, Any], baseline: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a normalized index; baseline is required for a valid ratio."""
    totals = metrics.get("totals") if isinstance(metrics.get("totals"), dict) else metrics
    values = {
        "cpu": _positive(totals.get("cpu_usage_usec")),
        "memory": _positive(totals.get("memory_byte_seconds")),
        "io": _positive(totals.get("io_bytes")),
    }
    result: dict[str, Any] = {
        "schema": SCHEMA,
        "valid": False,
        "index": None,
        "ratios": {},
        "weights": WEIGHTS.copy(),
        "reason": "baseline_required",
    }
    if any(value is None for value in values.values()):
        result["reason"] = "invalid_resource_counter"
        return result
    if baseline is None:
        return result
    base_totals = baseline.get("totals") if isinstance(baseline.get("totals"), dict) else baseline
    base_values = {
        "cpu": _positive(base_totals.get("cpu_usage_usec")),
        "memory": _positive(base_totals.get("memory_byte_seconds")),
        "io": _positive(base_totals.get("io_bytes")),
    }
    if any(value is None or value <= 0 for value in base_values.values()):
        result["reason"] = "invalid_baseline_counter"
        return result
    ratios = {name: values[name] / base_values[name] for name in values}
    index = sum(WEIGHTS[name] * ratios[name] for name in WEIGHTS)
    result.update({
        "valid": math.isfinite(index) and index >= 0.0,
        "index": index,
        "ratios": ratios,
        "baseline_index": 1.0,
        "saving_fraction": 1.0 - index,
        "reason": "ok",
    })
    return result

