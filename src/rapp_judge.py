#!/usr/bin/env python3
"""Deterministic rApp judge for the ARMD + TA-SAM assistant architecture.

The judge intentionally receives *proposals*, not raw radio metrics.  Metric
interpretation remains in the assistants and in the existing policy layer;
this module only applies the network priorities, safety order and arbitration
contract agreed for the GreenRAN experiment.
"""

from __future__ import annotations

from copy import deepcopy
import os
from typing import Any, Dict, Iterable, Optional

try:
    from greenran_v2x_adaptive_reward import REWARD_CONTRACT, compose_adaptive_reward
except ImportError:  # pragma: no cover - package import fallback
    from .greenran_v2x_adaptive_reward import REWARD_CONTRACT, compose_adaptive_reward


SEVERITY_RANK = {
    "UNKNOWN": 0,
    "ALLOWED": 1,
    "CONDITIONAL": 2,
    "BLOCKED": 3,
}

# Nominal curriculum labels are kept separate from the category observed in
# the next real network snapshot.  The latter remains the source of reward;
# this map is only used to audit whether the scenario itself is calibrated.
STAGE_EXPECTED_VERDICTS = {
    "allowed_bootstrap": "ALLOWED",
    "allowed_stable": "ALLOWED",
    "camera_conditional": "CONDITIONAL",
    "camera_blocked": "BLOCKED",
    "vehicle_conditional": "CONDITIONAL",
    "vehicle_blocked": "BLOCKED",
    "app2_conditional": "CONDITIONAL",
    "app2_blocked": "BLOCKED",
    "allowed_recovery": "ALLOWED",
}


def expected_verdict_for_stage(stage: Any) -> str:
    """Return the nominal curriculum category for an authoritative stage."""
    key = str(stage or "").strip().lower()
    return STAGE_EXPECTED_VERDICTS.get(key, "UNKNOWN")


def _clamp(value: Any, low: float = 0.0, high: float = 1.0, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(low, min(high, number))


def _verdict(value: Any) -> str:
    candidate = str(value or "UNKNOWN").upper().strip()
    return candidate if candidate in SEVERITY_RANK else "UNKNOWN"


def _distance(left: Any, right: Any) -> int:
    return abs(SEVERITY_RANK.get(_verdict(left), 0) - SEVERITY_RANK.get(_verdict(right), 0))


def training_category_signal(predicted: Any, observed: Any, valid: bool = True) -> Dict[str, float]:
    """Return the stronger directional signal used only by online training.

    The canonical category credit remains in ``_proposal_error`` for historical
    comparison.  This separate signal makes a wrong ordinal state expensive:
    under-severity is ``-1`` and over-severity is ``-2`` for adjacent states;
    two-level and invalid proposals are always ``-2``.
    """
    predicted_verdict = _verdict(predicted)
    observed_verdict = _verdict(observed)
    if not valid or predicted_verdict == "UNKNOWN" or observed_verdict == "UNKNOWN":
        penalty = 2.0
        credit = -2.0
    else:
        predicted_rank = SEVERITY_RANK[predicted_verdict]
        observed_rank = SEVERITY_RANK[observed_verdict]
        distance = abs(predicted_rank - observed_rank)
        if distance == 0:
            penalty = 0.0
            credit = 1.0
        elif distance >= 2 or predicted_rank > observed_rank:
            penalty = 2.0
            credit = -2.0
        else:
            penalty = 1.0
            credit = -1.0
    return {
        "credit": credit,
        "penalty": penalty,
    }


def _proposal_error(proposal: Dict[str, Any], correct: str, outcome: Dict[str, Any]) -> Dict[str, Any]:
    """Score one assistant proposal against the observed network outcome."""
    verdict = _verdict(proposal.get("verdict"))
    valid = bool(proposal.get("valid", False)) and verdict != "UNKNOWN"
    distance = _distance(verdict, correct)
    critical_underreaction = bool(outcome.get("critical_violation")) and verdict != "BLOCKED"
    priority_violation = bool(outcome.get("priority_violation"))

    if not valid:
        category_penalty = 1.0
        classification = "invalid_proposal"
    else:
        # The category signal is ordinal and directional.  For a one-level
        # error, overblocking (predicting a more severe state than the one
        # observed) is penalized more heavily than underblocking.  Errors of
        # two levels remain maximally severe in either direction.  Resource
        # quality is evaluated separately and cannot erase this signal.
        predicted_rank = SEVERITY_RANK.get(verdict, 0)
        observed_rank = SEVERITY_RANK.get(_verdict(correct), 0)
        if distance == 0:
            category_penalty = 0.0
        elif distance >= 2:
            category_penalty = 1.0
        elif predicted_rank > observed_rank:
            category_penalty = 1.0
        else:
            category_penalty = 0.5
        classification = "correct" if distance == 0 else (
            "critical_error" if critical_underreaction else "state_error"
        )

    category_credit = 1.0 if valid and distance == 0 else -category_penalty
    training_signal = training_category_signal(verdict, correct, valid=valid)

    return {
        "verdict": verdict,
        "valid": valid,
        "distance": distance,
        "penalty": round(category_penalty, 6),
        "category_penalty": round(category_penalty, 6),
        "category_credit": round(category_credit, 6),
        "category_error": bool(not valid or distance > 0),
        "training_category_credit": round(training_signal["credit"], 6),
        "training_category_penalty": round(training_signal["penalty"], 6),
        "predicted_verdict": verdict,
        "observed_verdict": correct,
        "classification": classification,
        "critical_underreaction": critical_underreaction,
        "priority_violation": priority_violation,
    }


class RAppJudge:
    """Choose a final rApp state from normalized ARMD/TA-SAM proposals.

    Production arbitration is deterministic.  There is no random coin flip
    in this class; exploration belongs to offline training and can never be
    enabled by accident in the live rApp.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        config = config if isinstance(config, dict) else {}
        self.enabled = bool(config.get("enabled", True))
        self.production = bool(config.get("production", True))
        # ``competitive`` preserves the existing winner-takes-one-proposal
        # experiment.  ``cooperative_hierarchy`` composes both specialists:
        # ARMD owns hard safety/floor constraints and TA-SAM owns optimization
        # inside that envelope.
        self.composition_mode = str(
            config.get("composition_mode", config.get("mode", "competitive"))
            or "competitive"
        ).strip().lower()
        self.exploration_epsilon = 0.0 if self.production else _clamp(
            config.get("exploration_epsilon", 0.0)
        )
        self.priorities = deepcopy(config.get("priorities") or {})
        precedence = config.get("assistant_precedence") or {}
        self.tasam_noncritical_precedence = bool(
            precedence.get("tasam_noncritical", True)
        )
        self.fallback_only_without_valid_assistant = bool(
            precedence.get("fallback_only_without_valid_assistant", True)
        )
        arbitration = config.get("arbitration") or {}
        self.tie_margin = _clamp(arbitration.get("tie_margin", 0.02), 0.0, 1.0, 0.02)
        self.external_last_resort_winner = str(
            arbitration.get("external_last_resort_winner", "ta_sam") or "ta_sam"
        ).lower()
        self.external_policy_id = str(arbitration.get("external_policy_id", "") or "")

    @staticmethod
    def _cooperative_resource_allocation(
        armd: Dict[str, Any], tasam: Dict[str, Any]
    ) -> tuple[Dict[str, Any], bool]:
        """Compose the ARMD safety envelope with TA-SAM's allocation."""
        armd_alloc = armd.get("resource_allocation") or armd.get("resource_advice") or {}
        tasam_alloc = tasam.get("resource_allocation") or tasam.get("resource_advice") or {}
        merged = deepcopy(tasam_alloc)
        for key, value in armd_alloc.items():
            merged.setdefault(key, deepcopy(value))

        def number(mapping: Dict[str, Any], key: str, default: float = 0.0) -> float:
            try:
                return max(0.0, float(mapping.get(key, default) or default))
            except (TypeError, ValueError):
                return default

        usable_budget = number(
            tasam_alloc, "usable_budget", number(armd_alloc, "usable_budget", 1.0)
        )
        floor_ran = max(
            number(armd_alloc, "floor_total_ran"),
            number(tasam_alloc, "floor_total_ran"),
        )
        floor_ai = max(
            number(armd_alloc, "floor_total_ai"),
            number(tasam_alloc, "floor_total_ai"),
        )
        # Only a genuinely critical ARMD proposal can raise allocation above
        # the common floor.  Normal/conditional ARMD advice must not inflate
        # energy merely because it is conservative.
        if armd.get("safety_veto") or armd.get("verdict") == "BLOCKED":
            floor_ran = max(floor_ran, number(armd_alloc, "r_ran"))
            floor_ai = max(floor_ai, number(armd_alloc, "r_ai"))

        target_ran = max(number(tasam_alloc, "r_ran", floor_ran), floor_ran)
        target_ai = max(number(tasam_alloc, "r_ai", floor_ai), floor_ai)
        floor_total = floor_ran + floor_ai
        feasible = floor_total <= usable_budget + 1e-9

        if feasible and target_ran + target_ai > usable_budget + 1e-9:
            extra_budget = max(0.0, usable_budget - floor_total)
            extra_ran = max(0.0, target_ran - floor_ran)
            extra_ai = max(0.0, target_ai - floor_ai)
            extra_total = extra_ran + extra_ai
            scale = extra_budget / extra_total if extra_total > 1e-12 else 0.0
            target_ran = floor_ran + extra_ran * min(1.0, scale)
            target_ai = floor_ai + extra_ai * min(1.0, scale)

        merged.update({
            "r_ran": target_ran,
            "r_ai": target_ai,
            "usable_budget": usable_budget,
            "floor_total_ran": floor_ran,
            "floor_total_ai": floor_ai,
            "allocation_composition": "armd_safety_envelope_tasam_optimization",
            "armd_envelope_applied": True,
            "tasam_optimization_applied": True,
            "controller_id": "armd_tasam_hierarchical",
            "source": "cooperative_hierarchy",
        })
        return merged, feasible

    def _build_cooperative_proposal(
        self, armd: Dict[str, Any], tasam: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Create one final package from two valid specialist proposals."""
        allocation, resource_feasible = self._cooperative_resource_allocation(armd, tasam)
        hard_armd_guard = bool(armd.get("safety_veto"))
        verdict = armd["verdict"] if hard_armd_guard else tasam["verdict"]
        action = armd["action"] if hard_armd_guard else tasam["action"]
        return {
            "proposal_id": f"joint:{armd.get('proposal_id', '')}:{tasam.get('proposal_id', '')}",
            "source": "joint",
            "available": True,
            "valid": bool(armd.get("valid") and tasam.get("valid") and resource_feasible),
            "feasible": bool(armd.get("feasible") and tasam.get("feasible") and resource_feasible),
            "verdict": verdict,
            "action": action,
            "confidence": min(
                float(armd.get("confidence", 0.0) or 0.0),
                float(tasam.get("confidence", 0.0) or 0.0),
            ),
            "proposal_score": round(
                0.55 * float(armd.get("priority_score", armd.get("proposal_score", 0.0)) or 0.0)
                + 0.45 * float(tasam.get("resource_score", tasam.get("proposal_score", 0.0)) or 0.0),
                6,
            ),
            "priority_score": float(armd.get("priority_score", 0.0) or 0.0),
            "resource_score": float(tasam.get("resource_score", 0.0) or 0.0),
            "resource_allocation": allocation,
            "resource_advice": deepcopy(tasam.get("resource_advice") or {}),
            "sla_protection": deepcopy(armd.get("sla_protection") or {}),
            "priority_constraints": deepcopy(armd.get("priority_constraints") or {}),
            "scenario": tasam.get("scenario") or armd.get("scenario", ""),
            "proposal_kind": "cooperative_hierarchy",
            "composition_mode": "cooperative_hierarchy",
            "armd_envelope_applied": True,
            "tasam_optimization_applied": True,
            "safety_veto": hard_armd_guard,
            "reason": "ARMD envelope + TA-SAM optimization",
            "evidence": list(dict.fromkeys(
                list(armd.get("evidence") or []) + list(tasam.get("evidence") or [])
            )),
        }

    @staticmethod
    def normalize_proposal(source: str, proposal: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Convert an assistant result to the judge's small proposal contract."""
        proposal = proposal if isinstance(proposal, dict) else {}
        source = str(source or proposal.get("source", "unknown")).lower()
        energy = proposal.get("energy_advice") or {}
        resource = (
            proposal.get("resource_allocation")
            or proposal.get("resource_advice")
            or proposal.get("resource_proposal")
            or {}
        )

        # TA-SAM's resource proposal historically carried the network state
        # under ``resource_advice.allocation_state`` while ARMD usually
        # exposed it as ``verdict``.  Both are the same state dimension for
        # judging purposes.  Accepting the nested form is important because
        # otherwise a complete TA-SAM proposal is silently scored as
        # UNKNOWN/invalid and receives the maximum penalty.
        verdict = _verdict(
            proposal.get("verdict")
            or proposal.get("expected_energy_saver")
            or proposal.get("decision")
            or energy.get("decision")
            or resource.get("allocation_state")
            or resource.get("state")
        )
        action = str(
            proposal.get("action")
            or proposal.get("expected_action")
            or energy.get("action")
            or ""
        )
        available = bool(proposal.get("available", proposal.get("enabled", False)))
        valid = bool(proposal.get("valid", available))
        feasible = bool(proposal.get("feasible", resource.get("feasible", True)))
        confidence = _clamp(proposal.get("confidence", 0.0))
        safety_veto = bool(
            proposal.get("safety_veto", False)
            or proposal.get("critical", False)
            or proposal.get("critical_violation", False)
        )
        if source == "armd" and verdict == "BLOCKED" and available:
            # ARMD is the validated safety advocate.  A BLOCKED proposal is
            # therefore a veto unless the producer explicitly marks it as a
            # non-critical advisory.
            safety_veto = bool(proposal.get("safety_veto", True))

        safety_level = str(proposal.get("armd_safety_level", "") or "").upper()
        if source == "armd" and safety_level not in {"CLEAR", "ADVISORY", "HARD_VETO", "UNKNOWN"}:
            safety_level = "HARD_VETO" if safety_veto else (
                "CLEAR" if verdict == "ALLOWED" else "ADVISORY" if verdict == "CONDITIONAL" else "UNKNOWN"
            )
        if source == "armd" and safety_level in {"CLEAR", "ADVISORY", "UNKNOWN"}:
            safety_veto = False
        advisory_only = source == "armd" and safety_level in {"CLEAR", "ADVISORY"}

        evidence = proposal.get("evidence", [])
        if not isinstance(evidence, list):
            evidence = [str(evidence)] if evidence else []

        return {
            "proposal_id": str(proposal.get("proposal_id", "") or ""),
            "score_explicit": "proposal_score" in proposal,
            "source": source,
            "verdict": verdict,
            "action": action,
            "confidence": confidence,
            "available": available,
            "valid": valid and available and verdict != "UNKNOWN",
            "feasible": feasible,
            "safety_veto": safety_veto,
            "armd_safety_level": safety_level,
            "armd_role": "safety_enforcer" if safety_level == "HARD_VETO" else "advisory" if source == "armd" else "",
            "armd_advisory_only": advisory_only,
            "priority_score": _clamp(proposal.get("priority_score", 0.0)),
            "resource_score": float(proposal.get("resource_score", resource.get("score", 0.0)) or 0.0),
            "proposal_score": _clamp(
                proposal.get(
                    "proposal_score",
                    proposal.get("priority_score", 0.0) * 0.55
                    + proposal.get("resource_score", resource.get("score", 0.0)) * 0.25
                    + confidence * 0.20,
                )
            ),
            "resource_advice": deepcopy(resource),
            "resource_allocation": deepcopy(resource),
            "sla_protection": deepcopy(proposal.get("sla_protection") or {}),
            "priority_constraints": deepcopy(proposal.get("priority_constraints") or {}),
            "scenario": str(proposal.get("scenario", "") or ""),
            "proposal_kind": str(proposal.get("proposal_kind", "") or ""),
            "priority_violation": str(proposal.get("priority_violation", "") or ""),
            "proactive_sla_guard": bool(proposal.get("proactive_sla_guard", False)),
            "evidence": evidence,
            "reason": str(proposal.get("reason", "") or energy.get("reason", "")),
        }

    def decide(
        self,
        priorities: Optional[Dict[str, Any]],
        armd_proposal: Optional[Dict[str, Any]],
        tasam_proposal: Optional[Dict[str, Any]],
        fallback_proposal: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Return a final verdict and a complete arbitration audit record."""
        priorities = deepcopy(priorities or self.priorities)
        armd = self.normalize_proposal("armd", armd_proposal)
        tasam = self.normalize_proposal("ta_sam", tasam_proposal)
        fallback = self.normalize_proposal("rapp_policy", fallback_proposal)

        proposals = {"armd": armd, "ta_sam": tasam, "rapp_policy": fallback}
        # In the explicit v9 contract, normal ARMD proposals are advisory
        # metadata and must never win arbitration or force a cooperative
        # allocation.  Keep the legacy behavior when older callers omit the
        # field so historical tests and reports remain readable.
        explicit_armd_level = "armd_safety_level" in (armd_proposal or {})
        valid_assistants = [
            item for item in (armd, tasam)
            if item["valid"] and item["feasible"]
            and not (
                explicit_armd_level
                and item["source"] == "armd"
                and item.get("armd_safety_level") in {"CLEAR", "ADVISORY"}
            )
        ]
        selected = None
        conflict_type = "no_valid_assistant"
        reason = "no valid assistant proposal; rApp policy fallback"

        if not self.enabled:
            selected = fallback
            conflict_type = "judge_disabled"
            reason = "judge disabled; existing rApp policy retained"
        else:
            # A safety flag is an input to proposal quality, not an automatic
            # win.  The rApp must compare both complete proposals.  A critical
            # ARMD proposal can still win because its calibrated score should
            # be higher, but a merely conservative ARMD proposal can lose to a
            # better TA-SAM proposal and will later be penalized if reality
            # proves the network was healthy.
            if valid_assistants:
                if (
                    self.composition_mode in {"cooperative_hierarchy", "hierarchical", "cooperative"}
                    and len(valid_assistants) == 2
                ):
                    selected = self._build_cooperative_proposal(armd, tasam)
                    if selected["valid"]:
                        conflict_type = "cooperative_hierarchy"
                        reason = "ARMD applied the safety envelope and TA-SAM optimized inside it"
                    else:
                        selected = None
                        conflict_type = "infeasible_cooperative_package"
                        reason = "cooperative package failed the ARMD floor/budget feasibility check"
                elif len(valid_assistants) == 1:
                    selected = valid_assistants[0]
                    conflict_type = "single_valid_assistant"
                    reason = f"only valid proposal from {selected['source']}"
                elif armd["verdict"] == tasam["verdict"]:
                    # Agreement on the state is not a license for an ARMD
                    # tie-break.  If the scores are equal, TA-SAM is retained
                    # so ARMD can win only with a strictly better proposal.
                    selected = (
                        armd
                        if armd["proposal_score"] > tasam["proposal_score"]
                        else tasam
                    )
                    conflict_type = "agreement"
                    reason = "assistants agree on the final state"
                else:
                    score_delta = abs(armd["proposal_score"] - tasam["proposal_score"])
                    if score_delta > self.tie_margin:
                        selected = max(
                            valid_assistants,
                            key=lambda item: (item["proposal_score"], item["confidence"]),
                        )
                        conflict_type = "score_precedence"
                        reason = "better calibrated assistant proposal exceeded the configured tie margin"
                    elif self.external_last_resort_winner in {"armd", "ta_sam"}:
                        requested = armd if self.external_last_resort_winner == "armd" else tasam
                        # The external policy may resolve a technical tie, but
                        # it cannot promote ARMD over an equal-or-better
                        # TA-SAM proposal.  ARMD is eligible here only when
                        # its score is strictly higher, even if the difference
                        # is inside the configured tie margin.
                        if (
                            requested["source"] == "armd"
                            and armd["proposal_score"] <= tasam["proposal_score"]
                        ):
                            selected = tasam
                            conflict_type = "tie_tasam_protection"
                            reason = "ARMD was not selected because its proposal did not exceed TA-SAM"
                        else:
                            selected = requested
                            conflict_type = "external_last_resort"
                            reason = "external policy resolved a technical tie"
                    else:
                        selected = None
                        conflict_type = "unresolved_tie"
                        reason = "technical tie without a valid external last-resort winner"

        if selected is None and fallback is not None and fallback["valid"]:
            selected = fallback if fallback["valid"] else {
                "source": "rapp_policy",
                "verdict": "CONDITIONAL",
                "action": "FULL_POWER_GUARD",
                "confidence": 0.0,
                "valid": True,
                "feasible": True,
                "safety_veto": False,
                "resource_advice": {},
                "evidence": [],
                "reason": "safe conditional fallback",
            }

        if selected is None:
            selected = {
                "source": "none",
                "verdict": "UNKNOWN",
                "action": "HOLD_LAST_ACCEPTED",
                "confidence": 0.0,
                "valid": False,
                "feasible": False,
                "safety_veto": False,
                "resource_advice": {},
                "evidence": [],
                "reason": reason,
                "proposal_score": 0.0,
            }

        armd_rank = SEVERITY_RANK.get(armd["verdict"], 0)
        immediate_penalty = 0.0
        if armd["safety_veto"] and selected["source"] not in {"armd", "joint"}:
            immediate_penalty = 1.0

        resource_winner = "none"
        if selected.get("source") == "joint":
            resource_winner = "joint"
        elif selected.get("proactive_sla_guard") and selected.get("source") == "armd":
            # The guard is a complete ARMD proposal, including the resource
            # vector.  Do not report TA-SAM as the resource winner merely
            # because its proposal contained the original optimization.
            resource_winner = "armd"
        elif tasam["valid"] and tasam["feasible"] and tasam["resource_advice"]:
            resource_winner = "ta_sam"
        elif armd["valid"] and armd["resource_advice"]:
            resource_winner = "armd"

        fallback_used = selected.get("source") == "rapp_policy"
        tasam_precedence_applied = (
            selected.get("source") == "ta_sam"
            and conflict_type == "tasam_precedence_conflict"
        )
        return {
            "mode": "rapp_judge_v1",
            "composition_mode": (
                "cooperative_hierarchy"
                if selected.get("source") == "joint"
                else "competitive"
            ),
            "assistants_cooperated": selected.get("source") == "joint",
            "armd_envelope_applied": bool(selected.get("armd_envelope_applied", False)),
            "tasam_optimization_applied": bool(selected.get("tasam_optimization_applied", False)),
            "armd_safety_level": armd.get("armd_safety_level", "UNKNOWN"),
            "armd_role": armd.get("armd_role", "advisory"),
            "enabled": self.enabled,
            "production_deterministic": self.production,
            "judge_verdict": selected["verdict"],
            "judge_action": selected["action"],
            "judge_confidence": selected["confidence"],
            "selected_advocate": selected["source"],
            "winner": selected["source"],
            "resource_winner": resource_winner,
            "conflict_type": conflict_type,
            "reason": reason,
            "safety_override": conflict_type == "safety_veto",
            "proactive_sla_guard": bool(selected.get("proactive_sla_guard", False)),
            "proactive_sla_guard_reason": (
                selected.get("reason", "")
                if selected.get("proactive_sla_guard") else ""
            ),
            "fallback_used": fallback_used,
            "tasam_precedence_applied": tasam_precedence_applied,
            "severity_penalty": immediate_penalty,
            "immediate_constraint_penalty": immediate_penalty,
            "outcome_reward": 0.0,
            "armd_credit": 0.0,
            "tasam_credit": 0.0,
            "outcome_observed": False,
            "priorities": priorities,
            "armd_proposal": armd,
            "tasam_proposal": tasam,
            "fallback_proposal": fallback,
            "selected_proposal": selected,
            "external_last_resort_used": conflict_type == "external_last_resort",
            "external_policy_id": self.external_policy_id,
            "armd_rank": armd_rank,
        }

    @staticmethod
    def derive_observed_outcome(observation: Optional[Dict[str, Any]], config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Classify the state observed after an assistant decision.

        A proposal is not considered correct merely because it agreed with a
        predictive warning.  The credit signal is derived from the next real
        observation: hard SLA breach => BLOCKED, warning margin =>
        CONDITIONAL, and healthy service => ALLOWED.  Missing service data is
        intentionally left unresolved so a warm-up cycle cannot punish ARMD
        or TA-SAM for guessing.
        """
        observation = observation if isinstance(observation, dict) else {}
        config = config if isinstance(config, dict) else {}
        shared = config.get("shared_resources") if isinstance(config.get("shared_resources"), dict) else config

        def number(value: Any, default: float = 0.0) -> float:
            try:
                return float(value)
            except (TypeError, ValueError):
                return default

        camera_cfg = {
            "throughput_target": number(shared.get("camera_throughput_target_mbps", 25.0), 25.0),
            "throughput_warning": number(shared.get("camera_throughput_guard_mbps", 30.0), 30.0),
            "latency_target": number(shared.get("camera_latency_target_ms", 100.0), 100.0),
            "latency_warning": number(shared.get("camera_latency_warning_ms", 80.0), 80.0),
        }
        vehicle_cfg = {
            "latency_target": number(shared.get("vehicle_latency_target_ms", 20.0), 20.0),
            "latency_warning": number(shared.get("vehicle_latency_warning_ms", 10.0), 10.0),
            "loss_target": number(shared.get("vehicle_loss_target_pct", 1.0), 1.0),
            "loss_warning": number(shared.get("vehicle_loss_warning_pct", 0.5), 0.5),
        }
        cvar_target_ms = number(shared.get("cvar_target_ms", 120.0), 120.0)

        hard_reasons: list[str] = []
        warning_reasons: list[str] = []
        unresolved = False

        camera = observation.get("camera_metrics") or {}
        active_cameras = number(camera.get("active_cameras"), 0.0)
        if active_cameras > 0.0:
            throughput_ready = bool(camera.get("throughput_ready", False))
            throughput = number(camera.get("throughput_mbps"), 0.0)
            latency = number(camera.get("latency_ms"), 0.0)
            if not throughput_ready and throughput <= 0.0 and latency <= 0.0:
                unresolved = True
            else:
                if throughput < camera_cfg["throughput_target"]:
                    hard_reasons.append(
                        f"camera throughput {throughput:.2f} < {camera_cfg['throughput_target']:.2f} Mbps"
                    )
                elif throughput < camera_cfg["throughput_warning"]:
                    warning_reasons.append("camera throughput em faixa de alerta")
                if latency >= camera_cfg["latency_target"]:
                    hard_reasons.append(
                        f"camera latência {latency:.2f} >= {camera_cfg['latency_target']:.2f} ms"
                    )
                elif latency >= camera_cfg["latency_warning"]:
                    warning_reasons.append("camera latência em faixa de alerta")

        vehicle = observation.get("vehicle_metrics") or {}
        if bool(vehicle.get("available")) and number(vehicle.get("total_vehicles"), 0.0) > 0.0:
            latency = number(vehicle.get("max_latency_ms"), 0.0)
            loss = number(vehicle.get("max_packet_loss_percent"), 0.0)
            if latency >= vehicle_cfg["latency_target"]:
                hard_reasons.append("latência veicular acima do SLA crítico")
            elif latency >= vehicle_cfg["latency_warning"]:
                warning_reasons.append("latência veicular em faixa de alerta")
            if loss >= vehicle_cfg["loss_target"]:
                hard_reasons.append("perda veicular acima do SLA crítico")
            elif loss >= vehicle_cfg["loss_warning"]:
                warning_reasons.append("perda veicular em faixa de alerta")
            if number(vehicle.get("high_risk_vehicles"), 0.0) > 0.0 or number(vehicle.get("degraded_autonomy_vehicles"), 0.0) > 0.0:
                hard_reasons.append("veículo autônomo em risco crítico")

        app2 = observation.get("app2_metrics") or {}
        if app2:
            if number(app2.get("packet_loss_percent"), 0.0) >= 10.0:
                hard_reasons.append("perda de sensores acima de 10%")
            elif number(app2.get("packet_loss_percent"), 0.0) >= 5.0:
                warning_reasons.append("perda de sensores em faixa de alerta")
            delivery = number(app2.get("delivery_success_percent"), 100.0)
            if delivery < 90.0:
                hard_reasons.append("entrega de sensores abaixo de 90%")
            elif delivery < 95.0:
                warning_reasons.append("entrega de sensores em faixa de alerta")
            latency = number(app2.get("avg_latency_ms"), 0.0)
            if latency >= 1000.0:
                hard_reasons.append("latência de sensores acima de 1000 ms")
            elif latency >= 500.0:
                warning_reasons.append("latência de sensores em faixa de alerta")

        network = observation.get("network_health") or {}
        cvar_ms = number(network.get("cvar_us"), 0.0) / 1000.0
        if cvar_ms > 0.0:
            if cvar_ms >= cvar_target_ms:
                hard_reasons.append(f"CVaR {cvar_ms:.2f} ms acima do limite")
            elif cvar_ms >= cvar_target_ms * 0.8:
                warning_reasons.append("CVaR em faixa de alerta")

        if unresolved:
            correct = "UNKNOWN"
            reason = "aguardando observação real suficiente para classificar o estado"
        elif hard_reasons:
            correct = "BLOCKED"
            reason = hard_reasons[0]
        elif warning_reasons:
            correct = "CONDITIONAL"
            reason = warning_reasons[0]
        else:
            correct = "ALLOWED"
            reason = "SLAs observados e margens dentro da faixa normal"

        return {
            "correct_verdict": correct,
            "observed": correct != "UNKNOWN",
            "critical_violation": bool(hard_reasons),
            "degraded": bool(warning_reasons),
            "priority_violation": bool(hard_reasons or warning_reasons),
            "hard_reasons": hard_reasons,
            "warning_reasons": warning_reasons,
            "reason": reason,
        }

    @staticmethod
    def compute_observed_error(
        previous_decision: Optional[Dict[str, Any]],
        current_observation: Optional[Dict[str, Any]],
        config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Convert the next real network snapshot into a continuous signal.

        The signal is deliberately independent of the categorical Judge
        credit.  Missing domains are excluded from the weighted average and
        are recorded as unavailable, while larger observed violations always
        increase ``tasam_observed_error`` and therefore reduce reward.
        """
        previous = previous_decision if isinstance(previous_decision, dict) else {}
        observation = current_observation if isinstance(current_observation, dict) else {}
        config = config if isinstance(config, dict) else {}
        shared = config.get("shared_resources") if isinstance(config.get("shared_resources"), dict) else config

        # New V2X article arms opt into the common compositor explicitly.
        # Leaving this branch opt-in preserves every historical reward
        # contract, including ARMD and economic replay diagnostics.
        reward_contract = str(
            shared.get("reward_contract")
            or os.environ.get("GREENRAN_TASAM_REWARD_CONTRACT", "")
        )
        reward_config = dict(shared)
        reward_config["reward_contract"] = reward_contract
        if "reward" not in reward_config and isinstance(config.get("reward"), dict):
            reward_config["reward"] = config["reward"]
        if reward_contract == REWARD_CONTRACT:
            adaptive = compose_adaptive_reward(
                previous,
                observation,
                reward_config,
                energy_enabled=shared.get(
                    "energy_enabled",
                    os.environ.get("GREENRAN_TASAM_REWARD_ENERGY_ENABLED", "0") == "1",
                ),
                energy_evidence=observation.get("energy_evidence"),
            )
            return {
                "tasam_observed_error": adaptive["observed_error"],
                "tasam_continuous_reward": adaptive["continuous_reward"],
                "tasam_reward_source": "v2x_adaptive_real_metrics",
                "tasam_error_components": {
                    "v2x": adaptive["raw_components"]["v2x"],
                    "equity": adaptive["raw_components"]["equity"],
                    "adaptive_risk_v2x": adaptive["risk_v2x"],
                    "adaptive_risk_equity": adaptive["risk_equity"],
                    "energy_cost": adaptive["energy_cost"],
                    "safety_reasons": adaptive["safety_reasons"],
                },
                "tasam_adaptive_reward": adaptive,
                "reward_contract": REWARD_CONTRACT,
                "reward_weight_snapshot": adaptive["snapshot"],
                "energy_eligible": adaptive["energy_eligible"],
                "adaptive_reward_state": adaptive["adaptive_reward_state"],
            }

        def number(value: Any, default: float = 0.0) -> float:
            try:
                value = float(value)
            except (TypeError, ValueError):
                return default
            return value if value == value and abs(value) != float("inf") else default

        def excess(value: Any, target: float, scale: float | None = None) -> float:
            target = max(number(target, 0.0), 1e-9)
            distance = max(0.0, number(value, 0.0) - target)
            return _clamp(distance / max(number(scale, target) if scale is not None else target, 1e-9))

        def shortfall(value: Any, target: float) -> float:
            target = max(number(target, 1.0), 1e-9)
            return _clamp((target - number(value, 0.0)) / target)

        camera = observation.get("camera_metrics") or {}
        vehicle = observation.get("vehicle_metrics") or {}
        sensors = observation.get("app2_metrics") or {}
        network = observation.get("network_health") or {}
        # Resource error belongs to the action that produced this outcome,
        # i.e. the previous decision, not the action selected for the next
        # cycle that happens to be present in the current observation.
        resource = previous.get("resource_allocation") or observation.get("resource_allocation") or {}
        slice_state = observation.get("slice_state") or {}

        camera_available = number(camera.get("active_cameras"), 0.0) > 0.0 and bool(camera)
        camera_errors = []
        if camera_available:
            camera_errors = [
                shortfall(camera.get("throughput_mbps"), number(shared.get("camera_throughput_target_mbps", 25.0), 25.0)),
                excess(camera.get("latency_ms"), number(shared.get("camera_latency_target_ms", 100.0), 100.0)),
            ]
        camera_error = max(camera_errors, default=0.0)

        vehicle_available = bool(vehicle.get("available")) and number(vehicle.get("total_vehicles"), 0.0) > 0.0
        vehicle_errors = []
        if vehicle_available:
            vehicle_errors = [
                excess(vehicle.get("max_latency_ms"), number(shared.get("vehicle_latency_target_ms", 20.0), 20.0)),
                excess(vehicle.get("max_packet_loss_percent"), number(shared.get("vehicle_loss_target_pct", 1.0), 1.0)),
            ]
            total_vehicles = max(number(vehicle.get("total_vehicles"), 1.0), 1.0)
            risky = number(vehicle.get("high_risk_vehicles"), 0.0) + number(vehicle.get("degraded_autonomy_vehicles"), 0.0)
            vehicle_errors.append(_clamp(risky / total_vehicles))
        vehicle_error = max(vehicle_errors, default=0.0)

        sensors_available = bool(sensors)
        sensor_errors = []
        if sensors_available:
            sensor_errors = [
                shortfall(sensors.get("delivery_success_percent", 100.0), 90.0),
                excess(sensors.get("packet_loss_percent"), 1.0, 10.0),
                excess(sensors.get("avg_latency_ms"), 1000.0),
            ]
        sensor_error = max(sensor_errors, default=0.0)

        background = observation.get("background_metrics") or observation.get("background") or {}
        if not background and isinstance(slice_state, dict):
            background = slice_state.get("background") or slice_state.get("eMBB") or {}
        background_available = bool(background) and any(
            key in background for key in ("completion_ratio", "min_qos_met", "qos_violation", "sla_violation")
        )
        background_error = 0.0
        if background_available:
            if background.get("qos_violation") or background.get("sla_violation"):
                background_error = 1.0
            else:
                background_error = shortfall(
                    background.get("completion_ratio", background.get("min_qos_met", 1.0)),
                    1.0,
                )

        cvar_ms = number(network.get("cvar_us"), 0.0) / 1000.0
        p95_ms = number(network.get("p95_us"), 0.0) / 1000.0
        cvar_target = number(shared.get("cvar_target_ms", 120.0), 120.0)
        p95_target = number(shared.get("p95_target_ms", cvar_target), cvar_target)
        latency_available = cvar_ms > 0.0 or p95_ms > 0.0
        latency_error = max(
            excess(cvar_ms, cvar_target) if cvar_ms > 0.0 else 0.0,
            excess(p95_ms, p95_target) if p95_ms > 0.0 else 0.0,
        )

        packet_loss = network.get("global_packet_loss_rate")
        if packet_loss is None:
            packet_loss = network.get("packet_loss_percent")
        packet_loss = number(packet_loss, -1.0)
        if packet_loss > 1.0:
            packet_loss /= 100.0
        loss_target = number(shared.get("global_packet_loss_target", 0.01), 0.01)
        loss_available = packet_loss >= 0.0
        loss_error = excess(packet_loss, loss_target, max(loss_target * 10.0, 0.1)) if loss_available else 0.0

        demands = resource.get("demands") or {}
        ran_demand = number(resource.get("ran_demand", resource.get("d_ran", demands.get("ran", 0.0))), 0.0)
        ai_demand = number(resource.get("ai_demand", resource.get("d_ai", demands.get("ai", 0.0))), 0.0)
        ran_allocation = number(resource.get("r_ran", resource.get("ran_allocation", 0.0)), 0.0)
        ai_allocation = number(resource.get("r_ai", resource.get("ai_allocation", 0.0)), 0.0)
        usable_budget = number(resource.get("usable_budget", resource.get("resource_budget")), 0.0)
        resource_available = usable_budget > 0.0 or ran_demand > 0.0 or ai_demand > 0.0
        shortages = [
            shortfall(ran_allocation, ran_demand) if ran_demand > 0.0 else 0.0,
            shortfall(ai_allocation, ai_demand) if ai_demand > 0.0 else 0.0,
        ]
        over_budget = (
            _clamp((ran_allocation + ai_allocation - usable_budget) / max(usable_budget, 1e-9))
            if usable_budget > 0.0 else 0.0
        )
        resource_error = max(max(shortages, default=0.0), over_budget)

        ran_completion = number(
            resource.get("ran_completion_ratio"),
            min(1.0, ran_allocation / ran_demand) if ran_demand > 0.0 else 1.0,
        )
        ai_completion = number(
            resource.get("ai_completion_ratio"),
            min(1.0, ai_allocation / ai_demand) if ai_demand > 0.0 else 1.0,
        )
        ran_target, ai_target = 0.95, 0.75
        completion_shortfall = _clamp(
            (0.60 * max(0.0, ran_target - ran_completion) / ran_target)
            + (0.40 * max(0.0, ai_target - ai_completion) / ai_target)
        )
        underallocation_penalty = max(resource_error, completion_shortfall)
        power_raw = resource.get(
            "power_percent",
            previous.get("tasam_power_percent", previous.get("energy_power_level", 100.0)),
        )
        power_aliases = {
            "POWER_DOWN_ECO": 25.0,
            "REDUCE_POWER": 60.0,
            "CONDITIONAL_REDUCE": 60.0,
            "FULL_POWER": 100.0,
        }
        try:
            power_percent = max(25.0, min(100.0, float(power_raw)))
        except (TypeError, ValueError):
            power_percent = power_aliases.get(str(power_raw or "").strip().upper(), 100.0)
        observed_verdict = str(observation.get("correct_verdict", "") or "").upper()

        service_values = {
            "camera_sla": (camera_error, camera_available),
            "vehicle_sla": (vehicle_error, vehicle_available),
            "sensor_sla": (sensor_error, sensors_available),
            "background_sla": (background_error, background_available),
        }
        weights = {"camera_sla": 0.35, "vehicle_sla": 0.35, "sensor_sla": 0.20, "background_sla": 0.10}
        available_weight = sum(weights[key] for key, (_, available) in service_values.items() if available)
        service_error = (
            sum(weights[key] * value for key, (value, available) in service_values.items() if available)
            / available_weight
            if available_weight > 0.0 else 0.0
        )
        power_safe = (
            observed_verdict != "BLOCKED"
            and ran_completion >= ran_target
            and ai_completion >= ai_target
            and service_error <= 1e-9
            and latency_error <= 1e-9
            and loss_error <= 1e-9
        )
        power_cost_penalty = ((power_percent - 25.0) / 75.0) if power_safe else 0.0
        components = {
            "camera_sla_error": round(camera_error, 6),
            "vehicle_sla_error": round(vehicle_error, 6),
            "sensor_sla_error": round(sensor_error, 6),
            "background_sla_error": round(background_error, 6),
            "service_error": round(service_error, 6),
            "tail_latency_error": round(latency_error, 6),
            "packet_loss_error": round(loss_error, 6),
            "resource_error": round(resource_error, 6),
            "resource_shortage_ran": round(shortages[0], 6),
            "resource_shortage_ai": round(shortages[1], 6),
            "resource_over_budget": round(over_budget, 6),
            "ran_completion": round(ran_completion, 6),
            "ai_completion": round(ai_completion, 6),
            "ran_completion_target": ran_target,
            "ai_completion_target": ai_target,
            "completion_shortfall_penalty": round(completion_shortfall, 6),
            "underallocation_penalty": round(underallocation_penalty, 6),
            "power_percent": round(power_percent, 6),
            "power_cost_penalty": round(power_cost_penalty, 6),
            "power_penalty_gated_by_service": bool(power_safe),
            "camera_available": camera_available,
            "vehicle_available": vehicle_available,
            "sensors_available": sensors_available,
            "background_available": background_available,
            "latency_available": latency_available,
            "packet_loss_available": loss_available,
            "resource_available": resource_available,
        }
        observed_error = _clamp(
            0.25 * service_error
            + 0.10 * _clamp(latency_error)
            + 0.05 * _clamp(loss_error)
            + 0.35 * completion_shortfall
            + 0.15 * underallocation_penalty
            + 0.05 * _clamp(over_budget)
            + 0.05 * power_cost_penalty,
            0.0,
            1.0,
        )
        return {
            "tasam_observed_error": round(observed_error, 6),
            "tasam_continuous_reward": round(_clamp(1.0 - observed_error, -1.0, 1.0), 6),
            "tasam_reward_source": "observed_real_metrics",
            "tasam_error_components": components,
        }

    def evaluate_outcome(
        self,
        judge_result: Optional[Dict[str, Any]],
        outcome: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Score a completed decision after real outcome metrics arrive.

        ``outcome`` is intentionally normalized.  The caller may provide a
        known ``correct_verdict``; otherwise the method derives one only from
        boolean SLA flags, never from raw metric vectors.
        """
        result = deepcopy(judge_result or {})
        outcome = outcome if isinstance(outcome, dict) else {}
        correct = _verdict(outcome.get("correct_verdict"))
        if correct == "UNKNOWN":
            if outcome.get("critical_violation"):
                correct = "BLOCKED"
            elif outcome.get("priority_violation") or outcome.get("degraded"):
                correct = "CONDITIONAL"
            elif outcome.get("observed"):
                correct = "ALLOWED"

        if correct == "UNKNOWN":
            result.update({"outcome_observed": False, "feedback_status": "awaiting_observation"})
            return result

        proposals = [
            ("armd", result.get("armd_proposal") or {}),
            ("ta_sam", result.get("tasam_proposal") or {}),
        ]
        errors = {
            source: _proposal_error(proposal, correct, outcome)
            for source, proposal in proposals
        }
        penalties = {source: details["category_penalty"] for source, details in errors.items()}
        exact = [source for source, details in errors.items() if details["valid"] and details["distance"] == 0]
        if len(exact) == len(proposals) and exact:
            # Both assistants agreed with the observed correct state.
            credits = {source: 1.0 for source, _ in proposals}
            credit_assignment = "joint_correct"
        elif exact:
            # Only the assistants that predicted the observed state receive
            # positive state credit; the other is judged by error severity.
            credits = {
                source: (1.0 if source in exact else -details["penalty"])
                for source, details in errors.items()
            }
            credit_assignment = "individual_correctness"
        else:
            # Agreement on a wrong state is a shared failure.  No assistant
            # receives positive credit merely for agreeing with the other.
            credits = {source: -details["penalty"] for source, details in errors.items()}
            credit_assignment = "joint_error" if len({details["distance"] for details in errors.values()}) == 1 else "individual_error"

        # This snapshot is the pure categorical signal and must not be
        # modified by the resource component below.
        state_credits = dict(credits)
        selected = result.get("selected_proposal") or {}
        selected_source = str(selected.get("source", "") or "")
        selected_error = errors.get(selected_source, {"penalty": 1.0})
        if selected_source == "joint":
            # A cooperative package is evaluated from both specialists.  The
            # individual credits remain available for DRL learning, while
            # the joint reward reflects the quality of the complete package.
            reward = _clamp(
                0.5 * credits.get("armd", 0.0)
                + 0.5 * credits.get("ta_sam", 0.0),
                -1.0,
                1.0,
            )
            penalty = _clamp(1.0 - reward)
            credit_assignment = f"{credit_assignment}_cooperative"
        else:
            penalty = selected_error["penalty"]
            reward = 1.0 - penalty

        # Resource success is TA-SAM's domain and is additive to its state
        # credit, but only after the safety verdict has been judged.
        resource_reward = _clamp(outcome.get("resource_reward", 0.0), -1.0, 1.0)
        if resource_reward > 0.0:
            tasam_state_credit = state_credits.get("ta_sam", 0.0)
            if errors.get("ta_sam", {}).get("category_error"):
                # Any category error remains a negative training signal.
                credits["ta_sam"] = tasam_state_credit
            else:
                credits["ta_sam"] = _clamp(
                    tasam_state_credit + resource_reward, -1.0, 1.0
                )
        if selected_source == "joint":
            reward = _clamp(
                0.5 * credits.get("armd", 0.0)
                + 0.5 * credits.get("ta_sam", 0.0),
                -1.0,
                1.0,
            )
            penalty = _clamp(1.0 - reward)

        result.update({
            "correct_verdict": correct,
            "severity_penalty": penalty,
            "outcome_reward": reward,
            "armd_credit": round(credits.get("armd", 0.0), 6),
            "tasam_credit": round(credits.get("ta_sam", 0.0), 6),
            "armd_state_credit": round(state_credits.get("armd", 0.0), 6),
            "tasam_state_credit": round(state_credits.get("ta_sam", 0.0), 6),
            "tasam_resource_credit": round(resource_reward, 6),
            "tasam_category_credit": round(errors.get("ta_sam", {}).get("category_credit", -1.0), 6),
            "tasam_category_penalty": round(errors.get("ta_sam", {}).get("category_penalty", 1.0), 6),
            "tasam_category_error": bool(errors.get("ta_sam", {}).get("category_error", True)),
            "tasam_training_category_credit": round(
                errors.get("ta_sam", {}).get("training_category_credit", -2.0), 6
            ),
            "tasam_training_category_penalty": round(
                errors.get("ta_sam", {}).get("training_category_penalty", 2.0), 6
            ),
            # The continuous component arrives with the delayed real-network
            # observation.  The orchestrator fills this field then; keep a
            # strong categorical fallback available for direct judge users.
            "tasam_training_reward": round(
                errors.get("ta_sam", {}).get("training_category_credit", -2.0), 6
            ),
            "tasam_predicted_verdict": errors.get("ta_sam", {}).get("predicted_verdict", "UNKNOWN"),
            "tasam_observed_verdict": correct,
            "joint_credit": round(
                0.5 * credits.get("armd", 0.0)
                + 0.5 * credits.get("ta_sam", 0.0),
                6,
            ) if selected_source == "joint" else 0.0,
            "proposal_errors": errors,
            "proposal_penalties": penalties,
            "credit_assignment": credit_assignment,
            "resource_reward": resource_reward,
            "outcome_observed": True,
            "feedback_status": "observed",
        })
        return result


__all__ = [
    "RAppJudge",
    "SEVERITY_RANK",
    "STAGE_EXPECTED_VERDICTS",
    "expected_verdict_for_stage",
    "training_category_signal",
]
