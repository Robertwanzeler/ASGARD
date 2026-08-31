#!/usr/bin/env python3
"""Recompute GreenRAN TA-SAM rewards without recollecting the ns-3 trace."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "drlexp" / "src"))
sys.path.insert(0, str(ROOT / "drlexp"))
sys.path.insert(0, str(ROOT))

from drl.online_greenran_marl_env import build_balanced_vehicle_energy_reward


def _resource_action(payload: dict[str, Any], slice_state: dict[str, dict[str, Any]]) -> dict[str, Any]:
    global_state = payload.get("global_state") or {}
    snapshot = global_state.get("snapshot") or {}
    usable_budget = global_state.get("usable_budget", snapshot.get("usable_budget", 1.0))
    return {
        "usable_budget": usable_budget,
        "slice_allocation": {
            sid: float((slice_state.get(sid) or {}).get("allocation", 0.0) or 0.0)
            for sid in ("eMBB", "mMTC", "URLLC")
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-jsonl", required=True, type=Path)
    parser.add_argument("--output-jsonl", required=True, type=Path)
    parser.add_argument("--summary-json", required=True, type=Path)
    args = parser.parse_args()
    rewards: list[float] = []
    old_rewards: list[float] = []
    rows = 0
    cvar_reference_us = 0.0
    vehicle_metrics_missing = 0
    args.output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with args.input_jsonl.open(encoding="utf-8") as source, args.output_jsonl.open("w", encoding="utf-8") as target:
        for line in source:
            if not line.strip():
                continue
            payload = json.loads(line)
            slice_state = payload.get("slice_state") or payload.get("next_slice_state") or {}
            metrics = dict(payload.get("metrics") or {})
            cvar_us = float(metrics.get("cvar_per_ue_us", 0.0) or 0.0)
            if cvar_us > 0.0 and not cvar_reference_us:
                cvar_reference_us = cvar_us
            if cvar_us > 0.0:
                metrics["cvar_reference_ms"] = cvar_reference_us / 1000.0
                # These historical traces do not export vehicle-specific
                # latency.  Do not mistake global CVaR for vehicle latency;
                # URLLC completion and global tail risk remain in the reward.
                if "vehicle_latency_ms" not in metrics:
                    metrics["vehicle_latency_ms"] = 0.0
                    vehicle_metrics_missing += 1
                cvar_reference_us = (0.90 * cvar_reference_us) + (0.10 * cvar_us)
            reward, components = build_balanced_vehicle_energy_reward(
                slice_state,
                metrics,
                _resource_action(payload, slice_state),
            )
            old_rewards.append(float(payload.get("reward_hint", 0.0) or 0.0))
            rewards.append(reward)
            payload["reward_hint"] = reward
            payload["reward_components"] = components
            payload["metrics"] = metrics
            payload["reward_profile"] = "greenran_balanced_vehicle_tail_v3"
            target.write(json.dumps(payload, ensure_ascii=False) + "\n")
            rows += 1
    summary = {
        "schema": "greenran.tasam_reward_reweight.v1",
        "input_jsonl": str(args.input_jsonl.resolve()),
        "output_jsonl": str(args.output_jsonl.resolve()),
        "rows": rows,
        "reward_profile": "greenran_balanced_vehicle_tail_v3",
        "vehicle_metrics_missing_rows": vehicle_metrics_missing,
        "old_reward_mean": statistics.fmean(old_rewards) if old_rewards else 0.0,
        "new_reward_mean": statistics.fmean(rewards) if rewards else 0.0,
        "new_reward_min": min(rewards) if rewards else 0.0,
        "new_reward_max": max(rewards) if rewards else 0.0,
    }
    args.summary_json.parent.mkdir(parents=True, exist_ok=True)
    args.summary_json.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
