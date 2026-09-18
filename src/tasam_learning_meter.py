"""Learning meter and applied-action comparison for TA-SAM campaigns.

The meter is deliberately conservative: shadow, rejected, safety-isolated and
no-op decisions can be displayed, but they cannot increase the learning or
economic score.  All economic values are simulation estimates.
"""

from __future__ import annotations

import math
import json
import statistics
from typing import Any, Iterable


def _number(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def _flag(value: Any) -> bool:
    return value in (True, 1, "1", "true", "True")


def _mean(values: Iterable[Any]) -> float | None:
    numeric = [_number(value, float("nan")) for value in values]
    numeric = [value for value in numeric if math.isfinite(value)]
    return round(statistics.fmean(numeric), 6) if numeric else None


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


def _applied(row: dict[str, Any]) -> bool:
    return str(row.get("economic_application_status") or "") == "applied" and (
        _flag(row.get("tasam_actuation_applied"))
        or _flag(row.get("ta_sam_actuation_applied"))
    )


def _economic_valid(row: dict[str, Any]) -> bool:
    if not _applied(row):
        return False
    if not _flag(row.get("economic_training_eligible")):
        return False
    if _flag(row.get("tasam_fallback_used")) or _flag(row.get("economic_safety_isolated")):
        return False
    armd_level = str(row.get("armd_safety_level") or "").upper()
    # Historical meter fixtures and reports predate the explicit provenance
    # fields.  Keep them readable; every new applied_action_v2 row carries the
    # fields and is therefore checked strictly.
    if "armd_safety_level" in row and armd_level not in {"CLEAR", "ADVISORY"}:
        return False
    if "tasam_operating_permission" in row and not _flag(row.get("tasam_operating_permission")):
        return False
    if str(row.get("economic_execution_mode") or "") == "diagnostic":
        return False
    if "actuation_confirmed" in row and not _flag(row.get("actuation_confirmed")):
        return False
    action = row.get("economic_action_json")
    if isinstance(action, str) and action.strip():
        try:
            action = json.loads(action)
        except (TypeError, ValueError, json.JSONDecodeError):
            action = {}
    if isinstance(action, dict):
        if "actuation_confirmed" in action and not _flag(action.get("actuation_confirmed")):
            return False
        if "tasam_operating_permission" in action and not _flag(action.get("tasam_operating_permission")):
            return False
        if str(action.get("economic_execution_mode") or "") == "diagnostic":
            return False
    if not _flag(row.get("tasam_checkpoint_valid")):
        return False
    if row.get("topology_valid") not in (None, 1, True):
        return False
    if row.get("tasam_evidence_valid") not in (None, 1, True):
        return False
    if row.get("realized_energy_saving_fraction") is None:
        return False
    if row.get("realized_allocation_saving_fraction") is None:
        return False
    if _number(row.get("tasam_sla_penalty"), 0.0) > 0.0:
        return False
    return True


def _promotion_positive(row: dict[str, Any]) -> bool:
    if not _economic_valid(row):
        return False
    if row.get("economic_promotion_eligible") not in (None, 1, True):
        return False
    return (
        _number(row.get("realized_energy_saving_fraction")) > 0.0
        and _number(row.get("realized_allocation_saving_fraction")) >= -0.001
    )


def _comparison(row: dict[str, Any]) -> dict[str, Any]:
    """Extract same-event rApp versus applied TA-SAM values."""
    action: dict[str, Any] = {}
    raw_action = row.get("economic_action_json")
    if isinstance(raw_action, dict):
        action = raw_action
    elif isinstance(raw_action, str) and raw_action.strip():
        try:
            decoded = json.loads(raw_action)
            if isinstance(decoded, dict):
                action = decoded
        except (TypeError, ValueError, json.JSONDecodeError):
            action = {}
    live = action.get("live_candidate") or action.get("live") or {}
    applied = action.get("applied") or {}
    live_power = row.get("live_power_w")
    # Shadow power is a counterfactual diagnostic and must never substitute
    # for the power of the action that was actually applied.
    treatment_power = row.get("applied_power_w")
    if treatment_power is None:
        treatment_power = applied.get("power_w")
    live_power = live_power or live.get("power_w")
    live_allocation = row.get("live_total_allocation") or live.get("total_allocation")
    treatment_allocation = row.get("applied_total_allocation") or applied.get("total_allocation")
    if treatment_allocation is None:
        treatment_allocation = _number(row.get("ran_allocation")) + _number(row.get("ai_allocation"))
    return {
        "live_power_w": _number(live_power, 0.0) if live_power is not None else None,
        "treatment_power_w": _number(treatment_power, 0.0) if treatment_power is not None else None,
        "live_allocation": _number(live_allocation, 0.0) if live_allocation is not None else None,
        "treatment_allocation": _number(treatment_allocation, 0.0) if treatment_allocation is not None else None,
        "live_ran_allocation": live.get("ran_allocation"),
        "treatment_ran_allocation": applied.get("ran_allocation", row.get("ran_allocation")),
        "live_ai_allocation": live.get("ai_allocation"),
        "treatment_ai_allocation": applied.get("ai_allocation", row.get("ai_allocation")),
        "live_budget": live.get("usable_budget"),
        "treatment_budget": applied.get("usable_budget"),
        "energy_saving_fraction": row.get("realized_energy_saving_fraction", row.get("energy_saving_fraction")),
        "allocation_saving_fraction": row.get("realized_allocation_saving_fraction", row.get("resource_saving_fraction")),
        "actuation_confirmed": row.get("actuation_confirmed"),
        "confirmation_source": row.get("actuation_confirmation_source"),
    }


def _candidate_improvement(state: dict[str, Any]) -> float:
    comparison = state.get("promotion_comparison") or {}
    candidate = comparison.get("candidate_quality") or []
    active = comparison.get("active_quality") or []
    if not candidate or not active:
        return 0.0
    try:
        candidate_score = sum(_number(value) for value in candidate)
        active_score = sum(_number(value) for value in active)
    except TypeError:
        return 0.0
    if abs(active_score) < 1e-12:
        return 1.0 if candidate_score > active_score else 0.0
    return _clamp((candidate_score - active_score) / abs(active_score), -1.0, 1.0)


def build_learning_meter(
    rows: list[dict[str, Any]],
    state: dict[str, Any] | None = None,
    *,
    target_transitions: int = 180,
) -> dict[str, Any]:
    """Build a conservative 0--100 learning and economic meter."""
    state = state or {}
    updates = int(state.get("updates_completed", 0) or 0)
    retrain_enabled = _flag(state.get("retrain_enabled")) and _flag(state.get("ml_enabled"))
    eligible = [row for row in rows if _economic_valid(row)]
    positive = [row for row in eligible if _promotion_positive(row)]
    applied = [row for row in rows if _applied(row)]
    promotions = [row for row in rows if _flag(row.get("economic_promotion_eligible"))]
    rewards = [_number(row.get("tasam_online_reward"), float("nan")) for row in eligible]
    rewards = [value for value in rewards if math.isfinite(value)]
    half = max(1, len(rewards) // 2) if rewards else 0
    reward_first = _mean(rewards[:half]) if half else None
    reward_last = _mean(rewards[-half:]) if half else None
    reward_trend = 0.0
    if reward_first is not None and reward_last is not None:
        reward_trend = _clamp((reward_last - reward_first + 1.0) / 2.0)
    energy_mean = _mean(row.get("realized_energy_saving_fraction") for row in eligible)
    allocation_mean = _mean(row.get("realized_allocation_saving_fraction") for row in eligible)
    positive_rate = len(positive) / max(len(eligible), 1)
    alignment = _mean(
        1.0 if row.get("economic_action_alignment_valid") in (True, 1) else 0.0
        for row in eligible
    )
    alignment = alignment if alignment is not None else 0.0
    coverage = _clamp(len(eligible) / max(target_transitions, 1))
    candidate_gain = _candidate_improvement(state)
    improvement_score = _clamp((candidate_gain + 1.0) / 2.0) if updates else 0.0
    energy_score = _clamp((_number(energy_mean) + 0.01) / 0.02) if energy_mean is not None else 0.0
    # Rejected/shadow rows are audit evidence, not economic outcomes.  They
    # must not turn the whole campaign into REGRESSING merely because they
    # carry a missing-data or SLA penalty.  Only actions that were actually
    # applied and admitted to the economic replay can regress the learner.
    economic_regressions = [
        row for row in eligible
        if _flag(row.get("tasam_fallback_used"))
        or str(row.get("economic_application_status") or "") in {"rollback", "reverted"}
        or _number(row.get("tasam_sla_penalty")) > 0.0
    ]
    safety_override_count = sum(
        1 for row in rows
        if _flag(row.get("economic_safety_isolated"))
        or str(row.get("economic_application_status") or "")
        in {"safety_override", "safety_isolated"}
    )
    safety_ok = not economic_regressions
    safety_intervened = safety_override_count > 0
    rejected_count = sum(
        1 for row in rows
        if str(row.get("economic_application_status") or "") != "applied"
    )
    score = 100.0 * (
        0.30 * improvement_score
        + 0.25 * reward_trend
        + 0.20 * energy_score
        + 0.15 * alignment
        + 0.10 * coverage
    )
    if not retrain_enabled:
        score = 0.0
        status = "DISABLED"
    elif updates <= 0:
        score = 0.0
        status = "NO_LEARNING"
    elif len(eligible) < 64:
        status = "INSUFFICIENT_DATA"
    elif state.get("candidate_shadow_evaluation_error"):
        # A technical inability to compare the candidate is not evidence that
        # the policy regressed.  Keep the learning score visible but block
        # promotion until the evaluator produces a valid same-state result.
        status = "EVALUATION_BLOCKED"
    elif not safety_ok:
        score = min(score, 20.0)
        status = "REGRESSING"
    elif safety_intervened:
        status = "SAFETY_INTERVENED"
    elif _flag(state.get("candidate_promoted")) and len(positive) >= target_transitions:
        status = "PROMOTABLE"
    elif score >= 60.0:
        status = "IMPROVING"
    else:
        status = "LEARNING"
    comparison_rows = [
        _comparison(row)
        for row in applied
        if _economic_valid(row)
    ]
    applied_action_rate = len(applied) / max(len(rows), 1)
    return {
        "schema": "greenran.tasam.learning_meter.v1",
        "learning_meter": round(_clamp(score / 100.0) * 100.0, 2),
        "status": status,
        "ml_enabled": bool(_flag(state.get("ml_enabled"))),
        "retrain_enabled": bool(_flag(state.get("retrain_enabled"))),
        "updates_completed": updates,
        "candidate_promoted": bool(_flag(state.get("candidate_promoted"))),
        "economic_transitions": len(eligible),
        "positive_transitions": len(positive),
        "promotion_eligible_transitions": len(promotions),
        "positive_rate": round(positive_rate, 6),
        "replay_coverage": round(coverage, 6),
        "reward_first_mean": reward_first,
        "reward_recent_mean": reward_last,
        "reward_trend_score": round(reward_trend, 6),
        "mean_realized_energy_saving_fraction": energy_mean,
        "mean_realized_allocation_saving_fraction": allocation_mean,
        # Stable names used by dashboards and the final paired report.
        "energy_gain_vs_rapp": energy_mean,
        "allocation_gain_vs_rapp": allocation_mean,
        "sla_delta_vs_rapp": _mean(
            -_number(row.get("tasam_sla_penalty"), 0.0) for row in eligible
        ) if eligible else None,
        "applied_action_rate": round(applied_action_rate, 6),
        "mean_action_alignment": round(alignment, 6),
        "candidate_improvement": round(candidate_gain, 6),
        "candidate_improvement_vs_parent": round(candidate_gain, 6),
        "safety_ok": safety_ok,
        "economic_regression_count": len(economic_regressions),
        "safety_intervention_count": safety_override_count,
        "rejected_or_nonapplied_count": rejected_count,
        "applied_action_count": len(applied),
        "comparison_sample_count": len(comparison_rows),
        "comparison": {
            "baseline": "rApp-only same-event live candidate",
            "treatment": "ARMD assist + TA-SAM applied + rApp Judge",
            "rows": comparison_rows,
            "energy_saving_is_realized_only": True,
        },
        "energy_interpretation": "estimativa relativa da simulacao ns-3; nao representa consumo fisico medido",
    }
