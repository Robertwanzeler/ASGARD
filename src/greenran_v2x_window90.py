"""Shared fail-closed validation for the non-promotable V2X window90 pilot."""

from __future__ import annotations

from typing import Any

REWARD_CONTRACT = "greenran.tasam.v2x.reward_adaptive.v1"


def validate_online_transition(
    row: dict[str, Any], *, require_adaptive_reward: bool = True,
    require_judge: bool = True,
) -> tuple[bool, list[str]]:
    """Validate one live transition without manufacturing missing evidence.

    The controller and the post-run stage selector use the same contract.  A
    row may be exported while evidence is pending, but it cannot enter a
    replay until every required native observation is present.
    """
    reasons: list[str] = []
    quality = row.get("collection_quality")
    quality = quality if isinstance(quality, dict) else {}
    decision = row.get("decision")
    decision = decision if isinstance(decision, dict) else {}
    feedback = row.get("judge_feedback")
    feedback = feedback if isinstance(feedback, dict) else {}

    if row.get("scenario_control_override") is not False:
        reasons.append("scenario_control_override")
    if quality.get("valid_for_training") is not True:
        reasons.append("collection_quality_invalid")
    if quality.get("collector_mode") != "pdcp_real" or quality.get("pdcp_real") is not True:
        reasons.append("pdcp_not_native")
    try:
        if float(quality.get("proxy_latency_sample_count", 0) or 0) != 0.0:
            reasons.append("proxy_samples_present")
    except (TypeError, ValueError):
        reasons.append("proxy_count_invalid")
    if quality.get("metric_alignment_valid") is not True:
        reasons.append("metric_alignment_invalid")
    if quality.get("sim_reset"):
        reasons.append("simulation_reset")
    if row.get("next_metrics") is None:
        reasons.append("next_pdcp_metrics_missing")
    if require_judge and row.get("judge_feedback_observed") is not True:
        reasons.append("judge_feedback_pending")
    if decision.get("e2_ack_complete") is not True:
        reasons.append("e2_ack_missing")
    if decision.get("native_readback_observed") is not True:
        reasons.append("native_readback_missing")
    if not (
        row.get("action_correlation_valid") is True
        or quality.get("decision_correlation_valid") is True
    ):
        reasons.append("action_correlation_invalid")
    if require_judge and not feedback.get("outcome_observed"):
        reasons.append("judge_outcome_not_observed")

    if require_adaptive_reward:
        if row.get("reward_contract") != REWARD_CONTRACT:
            reasons.append("adaptive_reward_contract_missing")
        snapshot = row.get("adaptive_reward")
        if not isinstance(snapshot, dict) or snapshot.get("reward_contract") != REWARD_CONTRACT:
            reasons.append("adaptive_reward_snapshot_missing")
        if require_judge and feedback.get("tasam_reward_source") not in {
            "observed_real_metrics", "v2x_adaptive_real_metrics",
        }:
            reasons.append("adaptive_reward_not_observed")

    return not reasons, reasons
