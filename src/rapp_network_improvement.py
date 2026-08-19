"""Helpers for deriving network-improvement percentages in the rApp."""

from __future__ import annotations

from typing import Any


NETWORK_IMPROVEMENT_BASELINE = {
    "label": "pre_fix_degraded_baseline",
    "source": "greenran_shadow_3du_20260630_screen_pre_fix",
    "captured_before": "2026-07-01T15:30:45",
    "baseline_cvar_us": 484700.0,
    "baseline_p95_us": 484700.0,
}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if out != out or out in (float("inf"), float("-inf")):
        return default
    return out


def _improvement_pct(baseline_value: float, current_value: float) -> float:
    baseline = max(_safe_float(baseline_value, 0.0), 0.0)
    current = max(_safe_float(current_value, 0.0), 0.0)
    if baseline <= 0.0 or current <= 0.0:
        return 0.0
    return max(0.0, min(100.0, ((baseline - current) / baseline) * 100.0))


def build_network_improvement(
    network_health: dict[str, Any] | None,
    *,
    source: str,
    baseline: dict[str, Any] | None = None,
) -> dict[str, Any]:
    baseline = dict(NETWORK_IMPROVEMENT_BASELINE if baseline is None else baseline)
    network_health = network_health or {}

    current_cvar_us = _safe_float(network_health.get("cvar_us", 0.0), 0.0)
    current_p95_us = _safe_float(network_health.get("p95_us", 0.0), 0.0)
    baseline_cvar_us = _safe_float(baseline.get("baseline_cvar_us", 0.0), 0.0)
    baseline_p95_us = _safe_float(baseline.get("baseline_p95_us", 0.0), 0.0)

    cvar_improvement_pct = _improvement_pct(baseline_cvar_us, current_cvar_us)
    p95_improvement_pct = _improvement_pct(baseline_p95_us, current_p95_us)
    improvement_valid = baseline_cvar_us > 0.0 and current_cvar_us > 0.0

    return {
        "baseline_label": str(baseline.get("label", "unknown")),
        "baseline_source": str(baseline.get("source", "unknown")),
        "baseline_captured_before": str(baseline.get("captured_before", "")),
        "baseline_cvar_us": baseline_cvar_us,
        "baseline_p95_us": baseline_p95_us,
        "improvement_source": str(source or "unknown"),
        "improvement_valid": bool(improvement_valid),
        "network_improvement_pct": cvar_improvement_pct,
        "cvar_improvement_pct": cvar_improvement_pct,
        "p95_improvement_pct": p95_improvement_pct,
    }
