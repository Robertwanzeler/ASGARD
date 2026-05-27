#!/usr/bin/env python3
"""
Runtime RL policy abstraction for GreenRAN.

This module isolates the legacy EE-DRL/A3C implementation behind a generic
policy interface so the runtime can migrate to new RL formulations, such as
SAC-based AI/RAN resource allocation inspired by [1], without hard-coding a
single algorithm or action semantic into the orchestrator.

[1] Lotfi, F., Rajoli, H. & Afghah, F. "Task-Specific Sharpness-Aware O-RAN
    Resource Management using Multi-Agent Reinforcement Learning".
    IEEE TMLCN, 2025. arXiv:2511.15002.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
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


class LegacyA3CPolicyAdapter(BaseRLPolicy):
    """Wrap the current SBiLSTM + A3C predictor behind the generic interface."""

    metadata = RLPolicyMetadata(
        policy_id="legacy_a3c_energy",
        family="legacy_energy_control",
        algorithm="SBiLSTM+A3C",
        decision_domain="energy_control",
        action_semantics="energy_command",
        legacy_runtime_compatible=True,
    )

    def __init__(self) -> None:
        self._predictor = None
        self.import_error = None
        try:
            from rapp_drl_predictor import DRLPredictor

            self._predictor = DRLPredictor()
        except ModuleNotFoundError as exc:
            self.import_error = exc
        self.loaded = False

    def load_models(self) -> bool:
        if self._predictor is None:
            raise RuntimeError(
                "Legacy A3C policy dependencies are unavailable. "
                f"Original import error: {self.import_error}"
            )
        self._predictor.load_models()
        self.loaded = True
        return True

    def predict(self, state_dict: Dict[str, Any]) -> Dict[str, Any]:
        if self._predictor is None:
            raise RuntimeError(
                "Legacy A3C policy is unavailable because its dependencies "
                f"could not be imported: {self.import_error}"
            )
        result = self._predictor.predict(state_dict)
        result.setdefault("rl_policy_id", self.metadata.policy_id)
        result.setdefault("rl_family", self.metadata.family)
        result.setdefault("rl_algorithm", self.metadata.algorithm)
        result.setdefault("rl_decision_domain", self.metadata.decision_domain)
        return result

    def reset_history(self) -> None:
        if self._predictor is not None and hasattr(self._predictor, "reset_history"):
            self._predictor.reset_history()

    def is_available(self) -> bool:
        return self._predictor is not None


class SACResourceAllocationPolicy(BaseRLPolicy):
    """Runtime adapter for the CAORA AI/RAN resource-allocation line."""

    def __init__(self, algorithm: str = "SAC") -> None:
        self.algorithm = (algorithm or "SAC").upper()
        policy_id = "caora_awac_resource_allocation" if self.algorithm == "AWAC" else "caora_sac_resource_allocation"
        self.metadata = RLPolicyMetadata(
            policy_id=policy_id,
            family="caora_ai_ran_coexistence",
            algorithm=self.algorithm,
            decision_domain="resource_allocation",
            action_semantics="resource_share_delta",
            legacy_runtime_compatible=False,
        )
        self.repo_root = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.model_dir = self.repo_root / "drlexp" / "models" / "sac"
        self.actor = None
        self.torch = None
        self.state_size = 5
        self.action_size = 2
        self.delta_step = 0.1
        self.loaded = False
        self.load_error = None
        default_ckpt = (
            self.repo_root / "runs" / "sac_bootstrap" / "offline_awac_20260524_refresh" / "sac_actor_offline.pt"
            if self.algorithm == "AWAC"
            else self.repo_root / "runs" / "sac_bootstrap" / "offline_sac_20260524_conservative" / "sac_actor_offline.pt"
        )
        self.checkpoint_path = Path(
            os.environ.get("GREENRAN_SAC_ACTOR_CHECKPOINT", str(default_ckpt))
        )
        self.runtime_blend = self._clamp_float(os.environ.get("GREENRAN_SAC_RUNTIME_BLEND", "0.85"), 0.0, 1.0)
        self.ran_completion_tolerance = self._clamp_float(
            os.environ.get("GREENRAN_SAC_RAN_COMPLETION_TOLERANCE", "0.03"),
            0.0,
            0.25,
        )

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @classmethod
    def _clamp_float(cls, value: Any, lower: float, upper: float) -> float:
        parsed = cls._safe_float(value, lower)
        if parsed < lower:
            return lower
        if parsed > upper:
            return upper
        return parsed

    def load_models(self) -> bool:
        try:
            import torch
            from torch import nn
        except ModuleNotFoundError as exc:
            self.load_error = (
                "PyTorch is unavailable in the current Python runtime. "
                "Use the project virtualenv when GREENRAN_RL_POLICY=awac/sac."
            )
            raise RuntimeError(self.load_error) from exc

        if not self.checkpoint_path.exists():
            self.load_error = f"Checkpoint not found: {self.checkpoint_path}"
            raise RuntimeError(self.load_error)

        class RuntimeGaussianActor(nn.Module):
            def __init__(self, state_size: int, action_size: int, hidden_size: int = 128) -> None:
                super().__init__()
                self.backbone = nn.Sequential(
                    nn.Linear(state_size, hidden_size),
                    nn.ReLU(),
                    nn.Linear(hidden_size, hidden_size),
                    nn.ReLU(),
                )
                self.mean_head = nn.Linear(hidden_size, action_size)
                self.log_std_head = nn.Linear(hidden_size, action_size)

            def deterministic(self, states):
                mean = self.mean_head(self.backbone(states))
                return torch.tanh(mean)

        checkpoint = torch.load(self.checkpoint_path, map_location="cpu")
        self.state_size = int(checkpoint.get("state_size", self.state_size))
        self.action_size = int(checkpoint.get("action_size", self.action_size))
        self.delta_step = float(checkpoint.get("delta_step", self.delta_step))
        actor = RuntimeGaussianActor(self.state_size, self.action_size)
        actor.load_state_dict(checkpoint["state_dict"])
        actor.eval()

        self.actor = actor
        self.torch = torch
        self.loaded = True
        self.load_error = None
        return True

    def is_available(self) -> bool:
        return self.loaded and self.actor is not None and self.torch is not None

    def _build_state_vector(self, baseline: Dict[str, Any], previous: Dict[str, Any]) -> list[float]:
        return [
            self._safe_float(baseline.get("d_ran", 0.0), 0.0),
            self._safe_float(baseline.get("d_ai", 0.0), 0.0),
            self._safe_float(previous.get("r_ran", baseline.get("r_ran", 0.5)), 0.5),
            self._safe_float(previous.get("r_ai", baseline.get("r_ai", 0.5)), 0.5),
            self._safe_float(baseline.get("usable_budget", baseline.get("resource_budget", 1.0)), 1.0),
        ]

    def _normalize_budget(self, r_ran: float, r_ai: float, usable_budget: float) -> tuple[float, float]:
        total = r_ran + r_ai
        if total > usable_budget and total > 0.0:
            scale = usable_budget / total
            return r_ran * scale, r_ai * scale
        return r_ran, r_ai

    def predict(self, state_dict: Dict[str, Any]) -> Dict[str, Any]:
        if not self.is_available():
            return {
                "rl_policy_id": self.metadata.policy_id,
                "rl_family": self.metadata.family,
                "rl_algorithm": self.metadata.algorithm,
                "rl_decision_domain": self.metadata.decision_domain,
                "final_decision": "UNAVAILABLE",
                "confidence": 0.0,
                "policy_action": None,
                "reason": self.load_error or "resource-allocation actor not loaded",
                "resource_allocation": {},
            }

        baseline = dict(state_dict.get("resource_allocation_baseline") or {})
        previous = dict(state_dict.get("previous_allocation") or {})
        if not baseline:
            return {
                "rl_policy_id": self.metadata.policy_id,
                "rl_family": self.metadata.family,
                "rl_algorithm": self.metadata.algorithm,
                "rl_decision_domain": self.metadata.decision_domain,
                "final_decision": "FALLBACK",
                "confidence": 0.0,
                "policy_action": None,
                "reason": "missing resource_allocation_baseline",
                "resource_allocation": {},
            }

        state_vec = self._build_state_vector(baseline, previous)
        tensor_state = self.torch.tensor(state_vec, dtype=self.torch.float32).unsqueeze(0)
        with self.torch.no_grad():
            action = self.actor.deterministic(tensor_state).squeeze(0).cpu().numpy()

        prev_r_ran = self._safe_float(previous.get("r_ran", baseline.get("r_ran", 0.5)), 0.5)
        prev_r_ai = self._safe_float(previous.get("r_ai", baseline.get("r_ai", 0.5)), 0.5)
        usable_budget = self._safe_float(baseline.get("usable_budget", baseline.get("resource_budget", 1.0)), 1.0)
        heuristic_r_ran = self._safe_float(baseline.get("r_ran", prev_r_ran), prev_r_ran)
        heuristic_r_ai = self._safe_float(baseline.get("r_ai", prev_r_ai), prev_r_ai)

        actor_r_ran = self._clamp_float(prev_r_ran + (float(action[0]) * self.delta_step), 0.0, usable_budget)
        actor_r_ai = self._clamp_float(prev_r_ai + (float(action[1]) * self.delta_step), 0.0, usable_budget)
        actor_r_ran, actor_r_ai = self._normalize_budget(actor_r_ran, actor_r_ai, usable_budget)

        blended_r_ran = (self.runtime_blend * actor_r_ran) + ((1.0 - self.runtime_blend) * heuristic_r_ran)
        blended_r_ai = (self.runtime_blend * actor_r_ai) + ((1.0 - self.runtime_blend) * heuristic_r_ai)
        blended_r_ran, blended_r_ai = self._normalize_budget(blended_r_ran, blended_r_ai, usable_budget)

        d_ran = self._safe_float(baseline.get("d_ran", 0.0), 0.0)
        d_ai = self._safe_float(baseline.get("d_ai", 0.0), 0.0)
        ran_completion = 1.0 if d_ran <= 1e-9 else self._clamp_float(blended_r_ran / d_ran, 0.0, 1.0)
        ai_completion = 1.0 if d_ai <= 1e-9 else self._clamp_float(blended_r_ai / d_ai, 0.0, 1.0)
        heuristic_ran_completion = self._safe_float(baseline.get("ran_completion_ratio", 1.0), 1.0)
        heuristic_ai_completion = self._safe_float(baseline.get("ai_completion_ratio", 1.0), 1.0)

        fallback = ran_completion + self.ran_completion_tolerance < heuristic_ran_completion
        if fallback:
            final_snapshot = baseline
            reason = (
                f"{self.algorithm} runtime fallback: ran_completion {ran_completion:.3f} "
                f"below heuristic {heuristic_ran_completion:.3f}"
            )
            confidence = 0.0
        else:
            final_snapshot = dict(baseline)
            final_snapshot.update(
                {
                    "controller_id": f"caora_{self.algorithm.lower()}_actor",
                    "target_policy_id": self.metadata.policy_id,
                    "decision_domain": self.metadata.decision_domain,
                    "action_semantics": self.metadata.action_semantics,
                    "r_ran": blended_r_ran,
                    "r_ai": blended_r_ai,
                    "delta_r_ran": blended_r_ran - prev_r_ran,
                    "delta_r_ai": blended_r_ai - prev_r_ai,
                    "ran_completion_ratio": ran_completion,
                    "ai_completion_ratio": ai_completion,
                    "utilization_ratio": self._clamp_float((blended_r_ran + blended_r_ai) / max(1e-9, self._safe_float(baseline.get("resource_budget", 1.0), 1.0)), 0.0, 1.0),
                    "runtime_blend": self.runtime_blend,
                    "policy_confidence": None,
                    "policy_raw_action": [float(action[0]), float(action[1])],
                }
            )
            confidence = self._clamp_float(
                0.55
                + (0.20 * min(1.0, heuristic_ran_completion))
                + (0.15 * max(0.0, heuristic_ai_completion - abs(heuristic_ai_completion - ai_completion)))
                + (0.10 * max(0.0, 1.0 - abs(heuristic_ran_completion - ran_completion))),
                0.0,
                0.99,
            )
            final_snapshot["policy_confidence"] = confidence
            reason = (
                f"{self.algorithm} resource allocation applied: "
                f"d_ran={d_ran:.3f}, d_ai={d_ai:.3f}, "
                f"r_ran={blended_r_ran:.3f}, r_ai={blended_r_ai:.3f}"
            )

        return {
            "rl_policy_id": self.metadata.policy_id,
            "rl_family": self.metadata.family,
            "rl_algorithm": self.metadata.algorithm,
            "rl_decision_domain": self.metadata.decision_domain,
            "final_decision": "RESOURCE_REALLOCATED" if not fallback else "FALLBACK_HEURISTIC",
            "confidence": confidence,
            "policy_action": "resource_share_delta",
            "reason": reason,
            "resource_allocation": final_snapshot,
        }


def build_runtime_rl_policy(policy_name: str | None = None) -> BaseRLPolicy:
    """Build the selected runtime RL policy."""

    selected = (policy_name or os.environ.get("GREENRAN_RL_POLICY", "legacy_a3c")).strip().lower()
    if selected in {"legacy", "legacy_a3c", "a3c", "eedrl"}:
        return LegacyA3CPolicyAdapter()
    if selected in {"sac", "caora_sac", "resource_sac"}:
        return SACResourceAllocationPolicy(algorithm="SAC")
    if selected in {"awac", "caora_awac", "resource_awac"}:
        return SACResourceAllocationPolicy(algorithm="AWAC")
    raise ValueError(f"Unsupported GREENRAN_RL_POLICY={selected!r}")
