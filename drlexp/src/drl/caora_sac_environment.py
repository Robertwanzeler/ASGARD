#!/usr/bin/env python3
"""
CAORA-style SAC environment for shared AI/RAN resource allocation.

This replaces the legacy energy-control formulation with the problem structure
used by the target article [1]: two competing demands, one shared resource budget,
and actions defined as allocation deltas rather than power commands.

[1] Lotfi, F., Rajoli, H. & Afghah, F. "Task-Specific Sharpness-Aware O-RAN
    Resource Management using Multi-Agent Reinforcement Learning".
    IEEE TMLCN, 2025. arXiv:2511.15002.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List

import numpy as np

try:
    import gymnasium as gym
    from gymnasium import spaces
    _GYM_IMPORT_ERROR = None
except ModuleNotFoundError as exc:
    gym = None
    spaces = None
    _GYM_IMPORT_ERROR = exc


@dataclass(frozen=True)
class WorkloadPoint:
    d_ran: float
    d_ai: float
    usable_budget: float = 1.0


def load_workload_trace(csv_path: str | Path) -> List[WorkloadPoint]:
    """Load a real workload trace with d_ran, d_ai and optional usable_budget."""

    path = Path(csv_path)
    rows: List[WorkloadPoint] = []
    with path.open("r", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            rows.append(
                WorkloadPoint(
                    d_ran=float(row["d_ran"]),
                    d_ai=float(row["d_ai"]),
                    usable_budget=float(row.get("usable_budget", 1.0) or 1.0),
                )
            )
    if not rows:
        raise ValueError(f"No workload rows found in {path}")
    return rows


class CAORASACEnv(gym.Env if gym is not None else object):
    """
    Shared-resource environment aligned to the SAC article.

    Observation:
      [d_ran(t), d_ai(t), r_ran(t-1), r_ai(t-1), usable_budget(t)]

    Action:
      [delta_r_ran, delta_r_ai] in [-1, 1]

    Resource constraint:
      r_ran(t) + r_ai(t) <= r_max
    """

    metadata = {"render_modes": ["human"]}

    def __init__(
        self,
        workload_trace: Iterable[WorkloadPoint],
        r_max: float = 1.0,
        delta_step: float = 0.1,
        w_ran: float = 2.0,
        w_ai: float = 1.0,
        w_utilization: float = 0.5,
        render_mode: str | None = None,
        reward_fn: str = "article",
        alpha_ran: float = 4.0,
        alpha_ai: float = 2.5,
        beta_res: float = 2.0,
        gamma_minqos: float = 5.0,
        qos_min_ran: float = 0.7,
        qos_min_ai: float = 0.5,
    ) -> None:
        if _GYM_IMPORT_ERROR is not None:
            raise RuntimeError(
                "gymnasium is required for CAORASACEnv. "
                f"Original import error: {_GYM_IMPORT_ERROR}"
            )
        super().__init__()
        self.workload_trace = list(workload_trace)
        if not self.workload_trace:
            raise ValueError("workload_trace must not be empty")
        self.r_max = float(r_max)
        self.delta_step = float(delta_step)
        self.w_ran = float(w_ran)
        self.w_ai = float(w_ai)
        self.w_utilization = float(w_utilization)
        self.render_mode = render_mode
        self.reward_fn = str(reward_fn)
        self.alpha_ran = float(alpha_ran)
        self.alpha_ai = float(alpha_ai)
        self.beta_res = float(beta_res)
        self.gamma_minqos = float(gamma_minqos)
        self.qos_min_ran = float(qos_min_ran)
        self.qos_min_ai = float(qos_min_ai)

        self.action_space = spaces.Box(low=-1.0, high=1.0, shape=(2,), dtype=np.float32)
        self.observation_space = spaces.Box(low=0.0, high=np.inf, shape=(5,), dtype=np.float32)

        self._index = 0
        self._prev_alloc = np.array([0.5 * self.r_max, 0.5 * self.r_max], dtype=np.float32)

    def reset(self, *, seed: int | None = None, options: dict | None = None):
        super().reset(seed=seed)
        self._index = 0
        self._prev_alloc = np.array([0.5 * self.r_max, 0.5 * self.r_max], dtype=np.float32)
        obs = self._build_obs(self.workload_trace[self._index])
        return obs, {}

    def step(self, action):
        point = self.workload_trace[self._index]
        usable_budget = float(np.clip(point.usable_budget, 0.0, self.r_max))
        clipped = np.clip(np.asarray(action, dtype=np.float32), -1.0, 1.0)
        delta = clipped * self.delta_step

        candidate = np.clip(self._prev_alloc + delta, 0.0, self.r_max)
        total = float(candidate.sum())
        if total > usable_budget and total > 0:
            candidate = (candidate / total) * usable_budget

        served_ran = min(point.d_ran, float(candidate[0]))
        served_ai = min(point.d_ai, float(candidate[1]))
        ran_completion = served_ran / max(point.d_ran, 1e-6)
        ai_completion = served_ai / max(point.d_ai, 1e-6)
        utilization = float(candidate.sum()) / max(self.r_max, 1e-6)

        if self.reward_fn == "article":
            sig_ran = 1.0 / (1.0 + np.exp(-self.alpha_ran * ran_completion))
            sig_ai = 1.0 / (1.0 + np.exp(-self.alpha_ai * ai_completion))
            qos_term = (sig_ran + sig_ai) / 2.0
            usado = float(candidate.sum())
            excesso = max(0.0, usado - usable_budget)
            res_penalty = -self.beta_res * excesso
            abaixo = (max(0.0, self.qos_min_ran - ran_completion)
                      + max(0.0, self.qos_min_ai - ai_completion))
            min_qos_penalty = -self.gamma_minqos * abaixo
            reward = qos_term + res_penalty + min_qos_penalty
        else:
            reward = (
                (self.w_ran * ran_completion)
                + (self.w_ai * ai_completion)
                + (self.w_utilization * utilization)
            )
            if served_ran + 1e-9 < min(point.d_ran, self.r_max):
                reward -= 1.0

        self._prev_alloc = candidate.astype(np.float32)
        self._index += 1
        terminated = self._index >= len(self.workload_trace)
        next_obs = (
            self._build_obs(self.workload_trace[min(self._index, len(self.workload_trace) - 1)])
            if not terminated
            else self._build_obs(point)
        )

        info = {
            "d_ran": point.d_ran,
            "d_ai": point.d_ai,
            "usable_budget": usable_budget,
            "r_ran": float(candidate[0]),
            "r_ai": float(candidate[1]),
            "ran_completion": ran_completion,
            "ai_completion": ai_completion,
            "utilization": utilization,
        }
        return next_obs, float(reward), terminated, False, info

    def _build_obs(self, point: WorkloadPoint) -> np.ndarray:
        return np.array(
            [
                point.d_ran,
                point.d_ai,
                float(self._prev_alloc[0]),
                float(self._prev_alloc[1]),
                float(np.clip(point.usable_budget, 0.0, self.r_max)),
            ],
            dtype=np.float32,
        )
