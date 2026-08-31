#!/usr/bin/env python3
"""
Runtime RL policy abstraction for GreenRAN.

The DRL stack is restricted to the TA-SAM MARL family.  The legacy RL hook
still exposes a baseline object for compatibility, while the effective live
resource decision is produced by the ARMD envelope + TA-SAM Judge path.

[1] Lotfi, F., Rajoli, H. & Afghah, F. "Task-Specific Sharpness-Aware O-RAN
    Resource Management using Multi-Agent Reinforcement Learning".
    IEEE TMLCN, 2025. arXiv:2511.15002.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any, Dict


@dataclass(frozen=True)
class RLPolicyMetadata:
    policy_id: str
    family: str
    algorithm: str
    decision_domain: str
    action_semantics: str
    legacy_runtime_compatible: bool


class BaseRLPolicy:
    """Minimal runtime contract for RL-backed policies."""

    metadata: RLPolicyMetadata

    def load_models(self) -> bool:
        raise NotImplementedError

    def predict(self, state_dict: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

    def reset_history(self) -> None:
        return None

    def is_available(self) -> bool:
        return True


class HeuristicResourcePolicy(BaseRLPolicy):
    """Compatibility baseline used before the effective TA-SAM decision."""

    metadata = RLPolicyMetadata(
        policy_id="heuristic_resource_allocator",
        family="greenran_runtime_baseline",
        algorithm="HEURISTIC",
        decision_domain="resource_allocation",
        action_semantics="none",
        legacy_runtime_compatible=False,
    )

    def load_models(self) -> bool:
        return True

    def predict(self, state_dict: Dict[str, Any]) -> Dict[str, Any]:
        baseline = dict(state_dict.get("resource_allocation_baseline") or {})
        return {
            "rl_policy_id": self.metadata.policy_id,
            "rl_family": self.metadata.family,
            "rl_algorithm": self.metadata.algorithm,
            "rl_decision_domain": self.metadata.decision_domain,
            "final_decision": "FALLBACK_HEURISTIC",
            "confidence": 0.0,
            "policy_action": None,
            "reason": "legacy baseline hook; effective allocation is decided by ARMD + TA-SAM Judge",
            "resource_allocation": baseline,
        }


def build_runtime_rl_policy(policy_name: str | None = None) -> BaseRLPolicy:
    """Build the selected runtime RL policy."""

    selected = (policy_name or os.environ.get("GREENRAN_RL_POLICY", "heuristic")).strip().lower()
    if selected in {
        "",
        "heuristic",
        "shadow_only",
        "shadow-only",
        "ta_sam_shadow",
        "tasam_shadow",
        "ta-sam-shadow",
        "tasam-shadow",
        "none",
        "disabled",
    }:
        return HeuristicResourcePolicy()
    if selected in {
        "legacy",
        "legacy_a3c",
        "a3c",
        "eedrl",
        "sac",
        "caora_sac",
        "resource_sac",
        "awac",
        "caora_awac",
        "resource_awac",
    }:
        raise ValueError(
            f"Deprecated GREENRAN_RL_POLICY={selected!r}: only the TA-SAM MARL "
            "family remains supported. Use 'heuristic' for the live allocator "
            "or 'ta_sam_shadow' to keep the live allocator heuristic while "
            "TA-SAM runs through shadow/control-gate."
        )
    raise ValueError(f"Unsupported GREENRAN_RL_POLICY={selected!r}")
