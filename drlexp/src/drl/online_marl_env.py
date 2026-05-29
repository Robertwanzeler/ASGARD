#!/usr/bin/env python3
"""
Artigo-aligned Online MARL Gymnasium environment.

Gera workloads sinteticos via cadeia de Markov + ruido Gaussiano,
com recompensa σ(α·Q) + P_res + P_minQ (Lotfi et al. 2025).

Nao depende de Data Lake, ns-3, CSV ou qualquer recurso externo.
"""

from __future__ import annotations

from typing import List, Tuple

import gymnasium as gym
import numpy as np
from gymnasium import spaces

STAGE_KEYS: List[str] = [
    "baseline_healthy",
    "transition",
    "recovery_window",
    "background_overload",
    "camera_overload",
    "camera_overload_repeat",
    "mixed_overload",
]

STAGE_BASES: dict = {
    "baseline_healthy":        (0.225, 0.171, 0.682),
    "transition":              (0.320, 0.150, 0.707),
    "recovery_window":         (0.448, 0.171, 0.756),
    "background_overload":     (0.832, 0.198, 0.893),
    "camera_overload":         (0.899, 0.171, 0.907),
    "camera_overload_repeat":  (0.902, 0.171, 0.908),
    "mixed_overload":          (0.906, 0.316, 0.958),
}

TRANSITION_MATRIX: dict = {
    "baseline_healthy": {"healthy": 0.90, "camera": 0.10},
    "camera_overload":  {"camera": 0.85, "mixed": 0.05, "recovery": 0.10},
    "mixed_overload":   {"mixed": 0.90, "background": 0.05, "recovery": 0.05},
    "background_overload": {"background": 0.88, "recovery": 0.10, "cam_repeat": 0.02},
    "recovery_window":     {"recovery": 0.80, "cam_repeat": 0.20},
    "camera_overload_repeat": {"cam_repeat": 0.80, "camera": 0.10, "recovery": 0.10},
    "transition":            {"recovery": 0.50, "cam_repeat": 0.50},
}

STAGE_KEYS_WITH_ALIAS: dict = {
    "healthy": "baseline_healthy",
    "camera": "camera_overload",
    "mixed": "mixed_overload",
    "background": "background_overload",
    "recovery": "recovery_window",
    "cam_repeat": "camera_overload_repeat",
}


def _resolve_stage(alias: str) -> str:
    return STAGE_KEYS_WITH_ALIAS.get(alias, alias)


class MarkovWorkloadGenerator:
    """Gera workloads sinteticos usando cadeia de Markov + ruido Gaussiano."""

    def __init__(
        self,
        sigma_ran: float = 0.03,
        sigma_ai: float = 0.02,
        sigma_budget: float = 0.01,
        seed: int | None = None,
    ) -> None:
        self.sigma_ran = sigma_ran
        self.sigma_ai = sigma_ai
        self.sigma_budget = sigma_budget
        self.rng = np.random.default_rng(seed)
        self.stage = "baseline_healthy"

    def reset(self) -> Tuple[float, float, float]:
        self.stage = "baseline_healthy"
        return self._sample()

    def step(self) -> Tuple[float, float, float]:
        probs_map = TRANSITION_MATRIX.get(self.stage, {"healthy": 1.0})
        aliases = list(probs_map.keys())
        probs = list(probs_map.values())
        probs = np.array(probs) / sum(probs)
        alias = self.rng.choice(aliases, p=probs)
        self.stage = _resolve_stage(alias)
        return self._sample()

    def _sample(self) -> Tuple[float, float, float]:
        base_ran, base_ai, base_budget = STAGE_BASES.get(
            self.stage, (0.5, 0.2, 0.8)
        )
        d_ran = float(np.clip(base_ran + self.rng.normal(0, self.sigma_ran), 0.05, 1.0))
        d_ai = float(np.clip(base_ai + self.rng.normal(0, self.sigma_ai), 0.05, 1.0))
        budget = float(np.clip(base_budget + self.rng.normal(0, self.sigma_budget), 0.1, 1.0))
        return d_ran, d_ai, budget


class OnlineMARLEnv(gym.Env):
    """
    Ambiente Gymnasium para treino online SAC+SAM alinhado ao artigo.

    Estado (5 dims): [d_ran, d_ai, r_ran_prev, r_ai_prev, usable_budget]
    Acao (2 cont):   [delta_r_ran, delta_r_ai] em [-1, 1]
    Recompensa:      σ(α·Q) + P_res + P_minQ  (artigo secao IV-A)
    """

    metadata = {"render_modes": ["human"]}

    def __init__(
        self,
        r_max: float = 1.0,
        delta_step: float = 0.1,
        alpha_ran: float = 4.0,
        alpha_ai: float = 2.5,
        alpha_adapt_beta: float = 2.0,
        beta_res: float = 2.0,
        gamma_minqos: float = 5.0,
        qos_min_ran: float = 0.7,
        qos_min_ai: float = 0.5,
        max_steps: int = 200,
        sigma_ran: float = 0.03,
        sigma_ai: float = 0.02,
        sigma_budget: float = 0.01,
        seed: int | None = None,
    ) -> None:
        super().__init__()
        self.r_max = float(r_max)
        self.delta_step = float(delta_step)
        self.alpha_ran = float(alpha_ran)
        self.alpha_ai = float(alpha_ai)
        self.alpha_adapt_beta = float(alpha_adapt_beta)
        self.beta_res = float(beta_res)
        self.gamma_minqos = float(gamma_minqos)
        self.qos_min_ran = float(qos_min_ran)
        self.qos_min_ai = float(qos_min_ai)
        self.max_steps = int(max_steps)

        self.generator = MarkovWorkloadGenerator(
            sigma_ran=sigma_ran,
            sigma_ai=sigma_ai,
            sigma_budget=sigma_budget,
            seed=seed,
        )

        self.action_space = spaces.Box(low=-1.0, high=1.0, shape=(2,), dtype=np.float32)
        self.observation_space = spaces.Box(low=0.0, high=np.inf, shape=(5,), dtype=np.float32)

        self._step_count = 0
        self._prev_alloc = np.array([0.5 * self.r_max, 0.5 * self.r_max], dtype=np.float32)
        self._current_d_ran = 0.0
        self._current_d_ai = 0.0
        self._current_budget = 0.0

    def reset(
        self, *, seed: int | None = None, options: dict | None = None
    ) -> Tuple[np.ndarray, dict]:
        super().reset(seed=seed)
        self._step_count = 0
        self._prev_alloc = np.array([0.5 * self.r_max, 0.5 * self.r_max], dtype=np.float32)
        d_ran, d_ai, budget = self.generator.reset()
        self._current_d_ran, self._current_d_ai, self._current_budget = d_ran, d_ai, budget
        obs = self._build_obs(d_ran, d_ai, budget)
        return obs, {}

    def step(self, action: np.ndarray) -> Tuple[np.ndarray, float, bool, bool, dict]:
        d_ran, d_ai, usable_budget = self._current_d_ran, self._current_d_ai, self._current_budget
        clipped = np.clip(np.asarray(action, dtype=np.float32), -1.0, 1.0)
        delta = clipped * self.delta_step

        candidate = np.clip(self._prev_alloc + delta, 0.0, self.r_max)
        total = float(candidate.sum())
        if total > usable_budget and total > 0:
            candidate = (candidate / total) * usable_budget

        served_ran = min(d_ran, float(candidate[0]))
        served_ai = min(d_ai, float(candidate[1]))
        ran_completion = served_ran / max(d_ran, 1e-6)
        ai_completion = served_ai / max(d_ai, 1e-6)

        alpha_ran_eff = self.alpha_ran * (
            1.0 + self.alpha_adapt_beta * max(0.0, self.qos_min_ran - ran_completion)
        )
        alpha_ai_eff = self.alpha_ai * (
            1.0 + self.alpha_adapt_beta * max(0.0, self.qos_min_ai - ai_completion)
        )
        sig_ran = 1.0 / (1.0 + np.exp(-alpha_ran_eff * ran_completion))
        sig_ai = 1.0 / (1.0 + np.exp(-alpha_ai_eff * ai_completion))
        qos_term = (sig_ran + sig_ai) / 2.0

        usado = float(candidate.sum())
        excesso = max(0.0, usado - usable_budget)
        res_penalty = -self.beta_res * excesso

        abaixo = (
            max(0.0, self.qos_min_ran - ran_completion)
            + max(0.0, self.qos_min_ai - ai_completion)
        )
        min_qos_penalty = -self.gamma_minqos * abaixo

        reward = qos_term + res_penalty + min_qos_penalty

        self._prev_alloc = candidate.astype(np.float32)
        self._step_count += 1
        terminated = self._step_count >= self.max_steps

        next_d_ran, next_d_ai, next_budget = self.generator.step()
        self._current_d_ran, self._current_d_ai, self._current_budget = next_d_ran, next_d_ai, next_budget
        next_obs = self._build_obs(next_d_ran, next_d_ai, next_budget)

        info = {
            "d_ran": d_ran,
            "d_ai": d_ai,
            "usable_budget": usable_budget,
            "r_ran": float(candidate[0]),
            "r_ai": float(candidate[1]),
            "ran_completion": ran_completion,
            "ai_completion": ai_completion,
            "stage": self.generator.stage,
            "qos_term": float(qos_term),
            "res_penalty": float(res_penalty),
            "min_qos_penalty": float(min_qos_penalty),
        }
        return next_obs, float(reward), terminated, False, info

    def _build_obs(
        self, d_ran: float, d_ai: float, budget: float
    ) -> np.ndarray:
        return np.array(
            [
                d_ran,
                d_ai,
                float(self._prev_alloc[0]),
                float(self._prev_alloc[1]),
                float(np.clip(budget, 0.0, self.r_max)),
            ],
            dtype=np.float32,
        )
