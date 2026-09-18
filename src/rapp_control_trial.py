"""Guarded controllers for the joint ARMD + TA-SAM runtime trials."""

from __future__ import annotations

import hashlib
import json
import math
import os
import time
from collections import deque
from pathlib import Path
from typing import Any, Dict


CRITICAL_VIOLATIONS = {
    "THROUGHPUT",
    "LATENCY",
    "APP2_MTC_CRITICAL",
    "VEHICLE_CRITICAL",
}


def _float(value: Any, default: float = 0.0) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def _bool(value: Any) -> bool:
    return bool(value) if not isinstance(value, str) else value.strip().lower() in {"1", "true", "yes", "on"}


class JointControlTrial:
    """Persisted controller for joint and assistant-only runtime trials.

    ``assistant_only_control`` is strict: ARMD supplies the safety envelope
    and TA-SAM supplies the resource allocation.  The caller must invalidate
    the run when the pair is unavailable; this mode never silently falls back
    to the heuristic/live allocator.
    """

    def __init__(self, config: Dict[str, Any] | None = None) -> None:
        config = config or {}
        project_root = Path(__file__).resolve().parent.parent
        default_state = project_root / "runs" / "tasam_greenran_control_trial_20260809" / "control_trial_state.json"
        requested_mode = str(os.environ.get("GREENRAN_TASAM_ADVISOR_MODE", "") or "").strip().lower()
        self.full_control = requested_mode in {"tasam_full_control", "tasam-full-control"}
        self.tasam_only = requested_mode in {"tasam_only_control", "tasam-only-control"}
        self.assistant_only = requested_mode in {"assistant_only_control", "assistant-only-control"}
        self.enabled = _bool(os.environ.get("GREENRAN_CONTROL_TRIAL_ENABLED", config.get("enabled", False)))
        if self.full_control or self.tasam_only or self.assistant_only:
            self.enabled = True
        # Full-control mode is intentionally all-or-nothing.  Assistant-only
        # control, however, must honor the configured canary fraction so a
        # newly approved policy cannot silently replace the live allocator on
        # every decision before its rollback guard has evidence.
        self.fraction = 1.0 if self.full_control else min(max(_float(os.environ.get("GREENRAN_CONTROL_TRIAL_FRACTION", config.get("fraction", 0.10)), 0.10), 0.0), 1.0)
        # Online economic adaptation advances its rollout in a separate
        # controller.  The old trial object kept the bootstrap fraction
        # (10%) forever, even after online_rollout.json reached 25%/50%.
        # Read that state file at decision time so the safety canary and the
        # economic rollout have one source of truth.  A malformed or missing
        # manifest falls back to the conservative bootstrap fraction.
        manifest_value = os.environ.get("GREENRAN_TASAM_ONLINE_ROLLOUT_MANIFEST", "").strip()
        self.rollout_manifest = Path(manifest_value) if manifest_value else None
        self.max_rollout_fraction = min(
            max(_float(os.environ.get("GREENRAN_TASAM_MAX_ROLLOUT_FRACTION", "1.0"), 1.0), 0.0),
            1.0,
        )
        self.target_decisions = max(1, int(_float(os.environ.get("GREENRAN_CONTROL_TRIAL_TARGET_DECISIONS", config.get("target_decisions", 300)), 300)))
        self.rollback_window = max(1, int(_float(os.environ.get("GREENRAN_CONTROL_TRIAL_ROLLING_WINDOW", config.get("rolling_window", 30)), 30)))
        self.critical_streak_limit = max(1, int(_float(os.environ.get("GREENRAN_CONTROL_TRIAL_CRITICAL_STREAK", config.get("critical_streak", 3)), 3)))
        self.min_confidence = min(
            max(_float(os.environ.get("GREENRAN_CONTROL_TRIAL_MIN_CONFIDENCE", config.get("min_confidence", 0.60))), 0.60),
            1.0,
        )
        self.min_ran_delta = _float(os.environ.get("GREENRAN_CONTROL_TRIAL_MIN_RAN_DELTA", config.get("min_ran_delta", -0.01)), -0.01)
        self.min_ai_delta = _float(os.environ.get("GREENRAN_CONTROL_TRIAL_MIN_AI_DELTA", config.get("min_ai_delta", -0.01)), -0.01)
        # Metrics are rounded before persistence.  Ignore sub-epsilon negative
        # score noise, but keep rollback for a material degradation.
        self.score_delta_epsilon = max(
            0.0,
            _float(
                os.environ.get(
                    "GREENRAN_CONTROL_TRIAL_SCORE_DELTA_EPSILON",
                    config.get("score_delta_epsilon", 1e-4),
                ),
                1e-4,
            ),
        )
        self.state_path = Path(os.environ.get("GREENRAN_CONTROL_TRIAL_STATE", config.get("state_path", default_state)))
        self.state = self._load_state()

    def _load_state(self) -> dict:
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                payload.setdefault("recent_outcomes", [])
                return payload
        except (OSError, json.JSONDecodeError):
            pass
        return {
            "schema": "greenran.joint_control_trial_state.v1",
            "status": "armed" if self.enabled else "disabled",
            "rollback": False,
            "rollback_reason": "",
            "started_at": int(time.time()),
            "total_decisions": 0,
            "eligible_decisions": 0,
            "canary_decisions": 0,
            "applied_decisions": 0,
            "critical_streak": 0,
            "recent_outcomes": [],
            "last_applied": False,
            "last_decision_id": "",
        }

    def _persist(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        temporary.write_text(json.dumps(self.state, indent=2, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self.state_path)

    def _eligible(self, decision: Dict[str, Any]) -> tuple[bool, str]:
        armd = decision.get("armd_analysis") or {}
        tasam = decision.get("tasam_advisor") or {}
        violation = str(decision.get("priority_violation", "") or "")
        if self.full_control:
            tasam_confidence = _float(tasam.get("confidence"), 0.0)
            tasam_valid = bool(tasam.get("valid")) or (
                str(tasam.get("source", "") or "") in {"checkpoint", "mixed"}
                and bool((tasam.get("resource_advice") or {}).get("enabled", False))
                and tasam_confidence >= self.min_confidence
            )
            if not tasam_valid:
                return False, "TA-SAM proposal unavailable in full-control mode"
            return True, "TA-SAM full-control eligible"
        if self.tasam_only:
            tasam_confidence = _float(tasam.get("confidence"), 0.0)
            tasam_checkpoint_ready = str(tasam.get("source", "") or "") in {"checkpoint", "mixed"}
            tasam_valid = bool(tasam.get("valid")) or (
                tasam_checkpoint_ready
                and bool((tasam.get("resource_advice") or {}).get("enabled", False))
                and tasam_confidence >= self.min_confidence
            )
            if not tasam_valid:
                return False, (
                    "TA-SAM proposal unavailable: "
                    f"valid={bool(tasam.get('valid'))} source={tasam.get('source', '')} "
                    f"confidence={tasam_confidence:.4f} min={self.min_confidence:.4f}"
                )
            if violation in CRITICAL_VIOLATIONS or decision.get("preventive_block"):
                return False, "critical SLA protection kept on live allocator"
            if self.state.get("rollback"):
                return False, "automatic rollback active"
            if self.state.get("eligible_decisions", 0) >= self.target_decisions:
                return False, "trial target reached"
            return True, "TA-SAM-only eligible"

        if self.assistant_only:
            judge_result = decision.get("rapp_judge_result") or {}
            if judge_result and (
                decision.get("control_trial_mode") != "assistant_judge"
                or judge_result.get("selected_advocate") not in {"armd", "ta_sam", "joint"}
                or not decision.get("proposal_applied_exactly", False)
            ):
                return False, "rApp judge did not select an assistant proposal exactly"
            armd_valid = bool(armd.get("available")) and _float(armd.get("confidence"), 0.0) >= 0.85
            armd_valid = armd_valid or (
                bool(armd.get("enabled"))
                and bool(armd.get("loaded"))
                and not decision.get("assistant_only_failure")
            )
            tasam_confidence = _float(tasam.get("confidence"), 0.0)
            tasam_checkpoint_ready = str(tasam.get("source", "") or "") in {"checkpoint", "mixed"}
            tasam_valid = bool(tasam.get("valid")) or (
                tasam_checkpoint_ready
                and bool((tasam.get("resource_advice") or {}).get("enabled", False))
                and tasam_confidence >= self.min_confidence
            )
            if not armd_valid:
                return False, "ARMD proposal unavailable in assistant-only mode"
            if not tasam_valid or not (tasam.get("resource_advice") or {}).get("enabled", False):
                return False, "TA-SAM resource proposal unavailable in assistant-only mode"
            shadow = (decision.get("resource_allocation") or {}).get("marl_shadow") or {}
            gate = tasam.get("control_gate") or shadow.get("control_gate") or {}
            if not bool(gate.get("allow_control_trial", False)):
                return False, "control gate does not authorize assistant-only mode"
            if self.state.get("rollback"):
                return False, "assistant-only controller is invalid after rollback"
            if self.state.get("eligible_decisions", 0) >= self.target_decisions:
                return False, "assistant-only target reached"
            # Critical states are not rejected here. ARMD already applied its
            # protection and TA-SAM allocates resources inside that envelope.
            return True, "ARMD envelope + TA-SAM allocation eligible"

        armd_valid = bool(armd.get("available")) and _float(armd.get("confidence"), 0.0) >= 0.85
        # ARMD's safe no-op is a valid proposal when it is loaded and no
        # validated scenario requires an escalation.  Critical states never
        # use this path and are rejected below.
        armd_noop_valid = bool(armd.get("enabled")) and bool(armd.get("loaded")) and not decision.get("preventive_block")
        armd_valid = armd_valid or armd_noop_valid
        tasam_confidence = _float(tasam.get("confidence"), 0.0)
        tasam_min_confidence = self.min_confidence
        tasam_checkpoint_ready = str(tasam.get("source", "") or "") in {"checkpoint", "mixed"}
        tasam_valid = bool(tasam.get("valid")) or (
            tasam_checkpoint_ready
            and bool(tasam.get("resource_advice", {}).get("enabled", False))
            and tasam_confidence >= tasam_min_confidence
        )
        if not armd_valid or not tasam_valid:
            if not armd_valid:
                return False, (
                    "ARMD proposal unavailable: "
                    f"available={bool(armd.get('available'))} "
                    f"loaded={bool(armd.get('loaded'))} "
                    f"enabled={bool(armd.get('enabled'))} "
                    f"violation={violation or 'none'}"
                )
            return False, (
                "TA-SAM proposal unavailable: "
                f"valid={bool(tasam.get('valid'))} source={tasam.get('source', '')} "
                f"confidence={tasam_confidence:.4f} min={self.min_confidence:.4f}"
            )
        if violation in CRITICAL_VIOLATIONS or decision.get("preventive_block"):
            return False, "critical SLA protection kept on live allocator/ARMD"
        shadow = (decision.get("resource_allocation") or {}).get("marl_shadow") or {}
        gate = tasam.get("control_gate") or shadow.get("control_gate") or {}
        if not bool(gate.get("allow_control_trial", False)):
            return False, "control gate does not authorize trial"
        if self.state.get("rollback"):
            return False, "automatic rollback active"
        if self.state.get("eligible_decisions", 0) >= self.target_decisions:
            return False, "trial target reached"
        return True, "both proposals valid"

    def _is_canary(self, decision_id: str) -> bool:
        if self.full_control:
            return True
        digest = hashlib.sha256(decision_id.encode("utf-8")).hexdigest()
        bucket = int(digest[:8], 16) / 0xFFFFFFFF
        return bucket < self._effective_fraction()

    def _effective_fraction(self) -> float:
        """Return the controller-approved rollout fraction for this decision.

        The manifest is written atomically by the online controller.  Only a
        numeric fraction in [0, max_rollout_fraction] is accepted; every
        malformed/read-failed value keeps the conservative bootstrap value.
        Full-control and legacy trials retain their existing behaviour.
        """
        if self.full_control or self.rollout_manifest is None:
            return self.fraction
        try:
            payload = json.loads(self.rollout_manifest.read_text(encoding="utf-8"))
            rollout = payload.get("rollout") if isinstance(payload, dict) else None
            value = float(rollout.get("fraction")) if isinstance(rollout, dict) else self.fraction
            if not math.isfinite(value):
                return self.fraction
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return self.fraction
        return min(max(value, 0.0), self.max_rollout_fraction)

    @staticmethod
    def comparison(decision: Dict[str, Any]) -> dict:
        resource = decision.get("resource_allocation") or {}
        marl = resource.get("marl_shadow") or {}
        return (marl.get("comparison") or {}) if isinstance(marl, dict) else {}

    @staticmethod
    def _priority_for_decision(decision: Dict[str, Any], comparison: Dict[str, Any]) -> str:
        """Resolve the live service priority for the rolling control guard."""
        explicit = str(comparison.get("priority", "") or "").strip().lower()
        if explicit in {"ran_camera", "ai_guarded", "ran_balanced"}:
            return explicit
        violation = str(decision.get("priority_violation", "") or "").strip().upper()
        reason = str(decision.get("reason", "") or "").strip().lower()
        if any(token in reason for token in ("camera", "câmera", "throughput", "latência", "latency")) or violation in {
            "THROUGHPUT", "THROUGHPUT_WARNING", "LATENCY", "LATENCY_WARNING", "THROUGHPUT_WARMUP"
        }:
            return "ran_camera"
        if any(token in reason for token in ("vehicle", "veículo", "app3", "app2", "mtc")) or violation.startswith("VEHICLE") or violation.startswith("APP2"):
            return "ai_guarded"
        return explicit or "mixed"

    def _threshold_violation(self, outcomes: list[dict]) -> str:
        if self.full_control:
            return ""
        if not outcomes:
            return ""
        critical_streak = 0
        for outcome in outcomes:
            if outcome.get("critical"):
                critical_streak += 1
            else:
                critical_streak = 0
        # In assistant-only operation, a BLOCKED priority state is an expected
        # ARMD safety-envelope outcome, not evidence that TA-SAM fell back or
        # that the assistant pair degraded the network.  The strict path still
        # invalidates on unavailable proposals and material metric deltas, but
        # must not confuse the protected service state with a control failure.
        if critical_streak >= self.critical_streak_limit and not self.assistant_only:
            return f"{self.critical_streak_limit} consecutive critical SLA violations"
        if len(outcomes) >= self.rollback_window:
            avg_score = sum(_float(row.get("causal_score_delta", row.get("score_delta"))) for row in outcomes[-self.rollback_window:]) / self.rollback_window
            avg_ran = sum(_float(row.get("ran_delta")) for row in outcomes[-self.rollback_window:]) / self.rollback_window
            avg_ai = sum(_float(row.get("ai_delta")) for row in outcomes[-self.rollback_window:]) / self.rollback_window
            # Assistant-only control is priority-aware. A camera/vehicle
            # protection window can intentionally reduce background/AI
            # completion while improving the service that is currently
            # protected. Treating the composite score as a global hard gate
            # made a valid RAN-priority decision look like a failed assistant
            # and stopped the whole rApp. Check the priority-aligned domain;
            # only genuinely mixed windows use the composite score.
            if self.assistant_only:
                priority_deltas = []
                mixed_deltas = []
                for row in outcomes[-self.rollback_window:]:
                    priority = str(row.get("priority", "mixed") or "mixed")
                    if priority == "ran_camera":
                        priority_deltas.append(_float(row.get("ran_delta"), 0.0))
                    elif priority == "ai_guarded":
                        priority_deltas.append(_float(row.get("ai_delta"), 0.0))
                    else:
                        mixed_deltas.append(_float(row.get("causal_score_delta", row.get("score_delta")), 0.0))
                if mixed_deltas and (sum(mixed_deltas) / len(mixed_deltas)) < -self.score_delta_epsilon:
                    avg_mixed = sum(mixed_deltas) / len(mixed_deltas)
                    return f"rolling {self.rollback_window}-decision score delta negative: {avg_mixed:.6f}"
                avg_priority = sum(priority_deltas) / len(priority_deltas) if priority_deltas else 0.0
                if avg_priority < -self.score_delta_epsilon:
                    return f"rolling priority-aligned delta negative: {avg_priority:.6f}"
                return ""

            if avg_score < -self.score_delta_epsilon:
                return f"rolling {self.rollback_window}-decision score delta negative: {avg_score:.6f}"

            if avg_ran < self.min_ran_delta:
                return f"rolling RAN delta below limit: {avg_ran:.6f} < {self.min_ran_delta:.6f}"
            if avg_ai < self.min_ai_delta:
                return f"rolling IA delta below limit: {avg_ai:.6f} < {self.min_ai_delta:.6f}"
        return ""

    def decide(self, decision: Dict[str, Any], decision_id: str) -> dict:
        self.state["total_decisions"] = int(self.state.get("total_decisions", 0)) + 1
        self.state["last_decision_id"] = decision_id
        if not self.enabled:
            result = self._result(False, False, False, "control trial disabled")
            self._persist()
            return result

        eligible, eligibility_reason = self._eligible(decision)
        if eligible:
            self.state["eligible_decisions"] = int(self.state.get("eligible_decisions", 0)) + 1
        canary = bool(eligible and self._is_canary(decision_id))
        if canary:
            self.state["canary_decisions"] = int(self.state.get("canary_decisions", 0)) + 1

        comparison = self.comparison(decision)
        candidate = {
            "score_delta": _float(comparison.get("score_delta"), 0.0),
            "causal_score_delta": _float(comparison.get("causal_score_delta", comparison.get("score_delta")), 0.0),
            "ran_delta": _float(comparison.get("shadow_ran_completion_est"), 0.0) - _float(comparison.get("live_ran_completion_est"), 0.0),
            "ai_delta": _float(comparison.get("shadow_ai_completion_est"), 0.0) - _float(comparison.get("live_ai_completion_est"), 0.0),
            "priority": self._priority_for_decision(decision, comparison),
        }
        # A full rolling window is checked before allowing the next application.
        outcomes = list(self.state.get("recent_outcomes", []))
        projected = outcomes + ([candidate] if canary else [])
        threshold = self._threshold_violation(projected)
        if threshold:
            self.state["rollback"] = True
            self.state["status"] = "rolled_back"
            self.state["rollback_reason"] = threshold
            canary = False
            eligibility_reason = threshold

        applied = bool(canary and eligible and not self.state.get("rollback"))
        self.state["last_applied"] = applied
        if applied:
            self.state["applied_decisions"] = int(self.state.get("applied_decisions", 0)) + 1
        self.state["status"] = "canary_active" if applied else ("armed" if not self.state.get("rollback") else "rolled_back")
        result = self._result(eligible, canary, applied, "canary selected" if applied else eligibility_reason)
        result["candidate_metrics"] = candidate
        self._persist()
        return result

    def observe(self, decision: Dict[str, Any], trial_result: dict) -> dict:
        critical = str(decision.get("priority_violation", "") or "") in CRITICAL_VIOLATIONS
        if self.state.get("last_applied"):
            candidate = trial_result.get("candidate_metrics") or self.comparison(decision)
            outcome = {
                "timestamp": int(time.time()),
                "score_delta": _float(candidate.get("score_delta"), 0.0),
                "causal_score_delta": _float(candidate.get("causal_score_delta", candidate.get("score_delta")), 0.0),
                "ran_delta": _float(candidate.get("ran_delta"), 0.0),
                "ai_delta": _float(candidate.get("ai_delta"), 0.0),
                "priority": str(candidate.get("priority", "mixed") or "mixed"),
                "critical": critical,
            }
            outcomes = deque(self.state.get("recent_outcomes", []), maxlen=self.rollback_window)
            outcomes.append(outcome)
            self.state["recent_outcomes"] = list(outcomes)
            self.state["critical_streak"] = (int(self.state.get("critical_streak", 0)) + 1) if critical else 0
            threshold = self._threshold_violation(list(outcomes))
            if threshold:
                self.state["rollback"] = True
                self.state["status"] = "rolled_back"
                self.state["rollback_reason"] = threshold
        self.state["last_applied"] = False
        if not self.state.get("rollback") and int(self.state.get("eligible_decisions", 0)) >= self.target_decisions:
            self.state["status"] = "completed"
        self._persist()
        return self.status()

    def _result(self, eligible: bool, canary: bool, applied: bool, reason: str) -> dict:
        return {
            "enabled": self.enabled,
            "mode": (
                "tasam_full_control" if applied and self.full_control
                else "tasam_full_control_invalid" if self.full_control
                else
                "assistant_only_control" if applied and self.assistant_only
                else "assistant_only_invalid" if self.assistant_only
                else "tasam_only_control" if applied and self.tasam_only
                else "joint_control_trial" if applied
                else "rollback" if self.state.get("rollback")
                else "live_fallback"
            ),
            "eligible": eligible,
            "canary": canary,
            "applied": applied,
            "fraction": self._effective_fraction(),
            "reason": reason,
            "rollback": bool(self.state.get("rollback")),
            "rollback_reason": self.state.get("rollback_reason", ""),
            "target_decisions": self.target_decisions,
            "total_decisions": int(self.state.get("total_decisions", 0)),
            "eligible_decisions": int(self.state.get("eligible_decisions", 0)),
            "canary_decisions": int(self.state.get("canary_decisions", 0)),
            "applied_decisions": int(self.state.get("applied_decisions", 0)),
        }

    def status(self) -> dict:
        return {
            **self.state,
            "enabled": self.enabled,
            "fraction": self._effective_fraction(),
            "target_decisions": self.target_decisions,
            "rollback_window": self.rollback_window,
            "critical_streak_limit": self.critical_streak_limit,
            "min_confidence": self.min_confidence,
            "score_delta_epsilon": self.score_delta_epsilon,
            "tasam_only": self.tasam_only,
            "assistant_only": self.assistant_only,
            "full_control": self.full_control,
        }


__all__ = ["JointControlTrial", "CRITICAL_VIOLATIONS"]
