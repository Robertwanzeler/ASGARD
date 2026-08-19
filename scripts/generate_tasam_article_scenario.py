#!/usr/bin/env python3
"""Generate a synthetic TA-SAM MARL trace matching the paper scenario."""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from export_tasam_article_dataset import compute_reward  # noqa: E402

SLICE_ORDER = ("eMBB", "mMTC", "URLLC")
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "tasam_article_scenario.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "runs" / "tasam_article_reproduction" / "scenario" / "tasam_article_trace.jsonl"
DU_MIXES = (
    ("du_0_embb_dense", "eMBB", {"eMBB": 0.78, "mMTC": 0.14, "URLLC": 0.08}),
    ("du_1_embb_mixed", "eMBB", {"eMBB": 0.62, "mMTC": 0.26, "URLLC": 0.12}),
    ("du_2_mmtc_dense", "mMTC", {"eMBB": 0.18, "mMTC": 0.70, "URLLC": 0.12}),
    ("du_3_mmtc_mixed", "mMTC", {"eMBB": 0.26, "mMTC": 0.58, "URLLC": 0.16}),
    ("du_4_urllc_edge", "URLLC", {"eMBB": 0.16, "mMTC": 0.16, "URLLC": 0.68}),
    ("du_5_urllc_mixed", "URLLC", {"eMBB": 0.24, "mMTC": 0.18, "URLLC": 0.58}),
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate paper-like TA-SAM MARL trace")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Article scenario config JSON")
    parser.add_argument("--output-jsonl", default=str(DEFAULT_OUTPUT), help="Output trace JSONL")
    parser.add_argument("--summary-json", default=None, help="Optional summary JSON")
    parser.add_argument("--steps", type=int, default=600, help="Number of transitions")
    parser.add_argument("--seed", type=int, default=42, help="Deterministic RNG seed")
    parser.add_argument("--users", type=int, default=None, help="Override UE count")
    parser.add_argument("--du-count", type=int, default=None, help="Override DU count")
    parser.add_argument("--scenario-id", default="article_reproduction", help="Trace scenario id")
    return parser


def clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, float(value)))


def normalize(raw: list[float]) -> list[float]:
    cleaned = [max(0.0, float(v)) for v in raw]
    total = sum(cleaned)
    if total <= 1e-12:
        return [1.0 / len(cleaned)] * len(cleaned)
    return [v / total for v in cleaned]


def load_config(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def demand_profile(step: int, steps: int, rng: random.Random) -> dict[str, float]:
    phase = (2.0 * math.pi * step) / max(steps, 1)
    embb = 0.34 + 0.14 * math.sin(phase) + rng.uniform(-0.025, 0.025)
    mmtc = 0.30 + 0.10 * math.sin(phase + 2.1) + rng.uniform(-0.020, 0.020)
    urllc = 0.24 + 0.16 * math.sin((phase * 1.7) + 0.6) + rng.uniform(-0.030, 0.030)
    return dict(zip(SLICE_ORDER, normalize([embb, mmtc, urllc])))


def action_from_demand(demand: dict[str, float], prev_action: dict[str, float], rng: random.Random) -> dict[str, float]:
    # The paper uses continuous SAC output with thresholding/relaxation for RB allocation.
    raw = []
    for sid in SLICE_ORDER:
        urgency = demand[sid]
        inertia = prev_action.get(sid, 1.0 / 3.0)
        raw.append((0.72 * urgency) + (0.22 * inertia) + rng.uniform(0.0, 0.035))
    values = normalize(raw)
    return dict(zip(SLICE_ORDER, values))


def slice_states(demand: dict[str, float], allocation: dict[str, float], users: int, rng: random.Random) -> dict[str, dict[str, Any]]:
    priorities = {"eMBB": 1.0, "mMTC": 0.5, "URLLC": 2.0}
    out: dict[str, dict[str, Any]] = {}
    for sid in SLICE_ORDER:
        ratio = allocation[sid] / max(demand[sid], 1e-9)
        pressure = clamp(1.0 - ratio + rng.uniform(-0.04, 0.04))
        completion = clamp(ratio - 0.06 * pressure + rng.uniform(-0.03, 0.03))
        out[sid] = {
            "slice_id": sid,
            "ue_count": max(1, round(users * demand[sid])),
            "demand": round(demand[sid], 6),
            "allocation": round(allocation[sid], 6),
            "qos_pressure": round(pressure, 6),
            "completion_ratio": round(completion, 6),
            "min_qos_met": 1.0 if completion >= (0.78 if sid == "URLLC" else 0.70) else 0.0,
            "budget_share": round(allocation[sid], 6),
            "priority": priorities[sid],
        }
    return out


def du_states(slice_state: dict[str, dict[str, Any]], users: int, du_count: int) -> list[dict[str, Any]]:
    out = []
    total_demand = sum(float(slice_state[sid]["demand"]) for sid in SLICE_ORDER)
    total_alloc = sum(float(slice_state[sid]["allocation"]) for sid in SLICE_ORDER)
    for idx, (du_id, primary, mix) in enumerate(DU_MIXES[:du_count]):
        demand_share = sum(float(slice_state[sid]["demand"]) * mix[sid] for sid in SLICE_ORDER)
        alloc_share = sum(float(slice_state[sid]["allocation"]) * mix[sid] for sid in SLICE_ORDER)
        pressure = sum(float(slice_state[sid]["qos_pressure"]) * mix[sid] for sid in SLICE_ORDER)
        ue_count = max(1, round(users * demand_share / max(total_demand, 1e-9)))
        out.append({
            "du_id": du_id,
            "role": primary.lower(),
            "primary_slice": primary,
            "ue_count": ue_count,
            "demand_share": round(demand_share, 6),
            "allocation_share": round(alloc_share / max(total_alloc, 1e-9), 6),
            "slice_mix": dict(mix),
            "state_vector": [
                round(mix["eMBB"], 6),
                round(mix["mMTC"], 6),
                round(mix["URLLC"], 6),
                round(demand_share, 6),
                round(alloc_share, 6),
                round(pressure, 6),
                round(ue_count / max(users, 1), 6),
                round(float(slice_state[primary]["completion_ratio"]), 6),
                round(float(slice_state[primary]["qos_pressure"]), 6),
                round(idx / max(du_count - 1, 1), 6),
            ],
        })
    return out


def global_state(step: int, steps: int, slice_state: dict[str, dict[str, Any]], du_count: int, users: int) -> dict[str, Any]:
    demand = [float(slice_state[sid]["demand"]) for sid in SLICE_ORDER]
    alloc = [float(slice_state[sid]["allocation"]) for sid in SLICE_ORDER]
    pressure = [float(slice_state[sid]["qos_pressure"]) for sid in SLICE_ORDER]
    completion = [float(slice_state[sid]["completion_ratio"]) for sid in SLICE_ORDER]
    return {
        "timestamp": step,
        "datetime": f"synthetic-step-{step}",
        "topology_id": "article_6du_200ue",
        "logical_du_count": du_count,
        "total_demand": round(sum(demand), 6),
        "usable_budget": 1.0,
        "state_vector": [
            *[round(v, 6) for v in demand],
            *[round(v, 6) for v in alloc],
            *[round(v, 6) for v in pressure],
            *[round(v, 6) for v in completion],
            round(users / 200.0, 6),
            round(step / max(steps, 1), 6),
        ],
        "snapshot": {"users": users, "du_count": du_count},
    }


def metrics(slice_state: dict[str, dict[str, Any]], users: int) -> dict[str, Any]:
    embb = slice_state["eMBB"]
    urllc = slice_state["URLLC"]
    mmtc = slice_state["mMTC"]
    throughput = 120000.0 * float(embb["completion_ratio"]) * (users / 200.0)
    latency_us = 120000.0 * (1.0 - float(urllc["completion_ratio"])) + 8000.0
    loss = max(0.0, (1.0 - float(mmtc["completion_ratio"])) * 0.01)
    return {
        "sim_time_s": 0.1,
        "throughput_kbps": round(throughput, 6),
        "latency_p95_us": round(latency_us, 6),
        "cvar_per_ue_us": round(latency_us * 1.12, 6),
        "total_active_ues": users,
        "total_active_cameras": round(users * float(embb["demand"])),
        "total_critical_ues": round(users * float(urllc["demand"])),
        "global_packet_loss_rate": round(loss, 8),
        "collector_mode": "article_synthetic",
    }


def build_snapshot(step: int, steps: int, users: int, du_count: int, rng: random.Random, prev_action: dict[str, float]) -> tuple[dict[str, Any], dict[str, float]]:
    demand = demand_profile(step, steps, rng)
    allocation = action_from_demand(demand, prev_action, rng)
    slices = slice_states(demand, allocation, users, rng)
    resource_action = {
        "usable_budget": 1.0,
        "r_ran": round(allocation["eMBB"] + allocation["URLLC"], 6),
        "r_ai": round(allocation["mMTC"], 6),
        "d_ran": round(demand["eMBB"] + demand["URLLC"], 6),
        "d_ai": round(demand["mMTC"], 6),
        "slice_allocation": allocation,
    }
    snapshot = {
        "timestamp": step,
        "datetime": f"synthetic-step-{step}",
        "topology_id": "article_6du_200ue",
        "scenario_stage": "article_reproduction",
        "global_state": global_state(step, steps, slices, du_count, users),
        "slice_state": slices,
        "du_states": du_states(slices, users, du_count),
        "action": resource_action,
        "decision": {"decision": "SYNTHETIC_ARTICLE"},
        "metrics": metrics(slices, users),
        "armd_context": {"enabled": False, "mode": "not_used_in_article_reproduction"},
        "conflict_context": {"recent_count": 0, "by_type": {}, "latest": None},
        "shadow_comparison": {},
    }
    return snapshot, allocation


def transition(current: dict[str, Any], nxt: dict[str, Any]) -> dict[str, Any]:
    reward = compute_reward(current)
    return {
        "schema": "greenran.tasam_article_transition.v1",
        "timestamp": current["timestamp"],
        "datetime": current["datetime"],
        "next_timestamp": nxt["timestamp"],
        "topology_id": current["topology_id"],
        "scenario_stage": current["scenario_stage"],
        "global_state": current["global_state"],
        "slice_state": current["slice_state"],
        "du_states": current["du_states"],
        "action": current["action"],
        "decision": current["decision"],
        "metrics": current["metrics"],
        "armd_context": current["armd_context"],
        "conflict_context": current["conflict_context"],
        "shadow_comparison": current["shadow_comparison"],
        "reward_hint": reward["reward"],
        "reward_components": reward["components"],
        "next_global_state": nxt["global_state"],
        "next_slice_state": nxt["slice_state"],
        "next_du_states": nxt["du_states"],
        "next_metrics": nxt["metrics"],
        "collection_quality": {
            "collector_mode": "article_synthetic",
            "real_latency_sample_count": 0,
            "proxy_latency_sample_count": 0,
            "pdcp_real": False,
            "has_proxy": False,
            "timestamp_gap_s": 1,
            "sim_reset": False,
            "valid_for_training": True,
        },
    }


def main() -> int:
    args = build_parser().parse_args()
    cfg = load_config(args.config)
    users = int(args.users or cfg["scenario"]["users"])
    du_count = int(args.du_count or cfg["scenario"]["du_count"])
    if du_count > len(DU_MIXES):
        raise SystemExit(f"du-count cannot exceed {len(DU_MIXES)} for this article scenario generator")
    rng = random.Random(args.seed)
    out_path = Path(args.output_jsonl)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    prev_action = {sid: 1.0 / 3.0 for sid in SLICE_ORDER}
    snapshots = []
    for step in range(args.steps + 1):
        snap, prev_action = build_snapshot(step, args.steps, users, du_count, rng, prev_action)
        snapshots.append(snap)
    with out_path.open("w", encoding="utf-8") as fh:
        for current, nxt in zip(snapshots, snapshots[1:]):
            fh.write(json.dumps(transition(current, nxt), ensure_ascii=False) + "\n")
    summary = {
        "schema": "greenran.tasam_article_synthetic_summary.v1",
        "scenario_id": args.scenario_id,
        "output_jsonl": str(out_path),
        "steps": args.steps,
        "users": users,
        "du_count": du_count,
        "slices": list(SLICE_ORDER),
        "source_config": str(Path(args.config)),
        "armd_policy": "not_used_in_article_reproduction",
    }
    if args.summary_json:
        summary_path = Path(args.summary_json)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
