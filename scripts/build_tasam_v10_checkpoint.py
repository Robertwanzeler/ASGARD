#!/usr/bin/env python3
"""Build an explicit v10 warm-start checkpoint from an existing TA-SAM copy.

The parent is never modified.  The global economic actor is widened from the
legacy three outputs to five outputs (power DU2/DU3/DU4, RAN share and total
budget).  The new policy starts at full power and full total budget; its DU
slice actors and auxiliary heads are reused only when their shapes match.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

import torch

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "drlexp/src"))
from drl.ta_sam_marl_sac import TASAMArticleSACTrainer  # noqa: E402


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=47)
    args = parser.parse_args()
    parent = args.parent.resolve()
    output = args.output.resolve()
    if output.exists():
        raise SystemExit(f"output already exists: {output}")
    meta_path = parent / "tasam_marl_checkpoint_meta.json"
    if not meta_path.is_file():
        raise SystemExit(f"parent metadata missing: {meta_path}")
    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    expected = {"du_count": 3, "du_state_dim": 13, "global_state_dim": 13, "joint_action_dim": 12}
    if {key: metadata.get(key) for key in expected} != expected:
        raise SystemExit("parent must be the legacy 3-D global-action checkpoint")
    output.mkdir(parents=True)

    trainer = TASAMArticleSACTrainer(
        du_count=3,
        du_state_dim=13,
        global_state_dim=13,
        allocation_head_output_dim=3,
        global_action_dim=5,
        seed=args.seed,
    )

    def load_if_compatible(module, filename: str) -> bool:
        source = parent / filename
        if not source.is_file():
            return False
        try:
            module.load_state_dict(torch.load(source, map_location="cpu", weights_only=False))
            return True
        except (RuntimeError, TypeError, OSError):
            return False

    load_if_compatible(trainer.actors, "tasam_marl_actors.pt")
    load_if_compatible(trainer.category_head, "tasam_marl_category_head.pt")
    load_if_compatible(trainer.power_head, "tasam_marl_power_head.pt")
    if not load_if_compatible(trainer.allocation_head, "tasam_marl_allocation_head.pt"):
        pass

    # Initialize DU2/3/4 power and total budget at the upper operating bound.
    with torch.no_grad():
        trainer.global_actor.alpha_head.bias[:3].fill_(100.0)
        trainer.global_actor.beta_head.bias[:3].fill_(-100.0)
        trainer.global_actor.alpha_head.bias[3].fill_(0.0)
        trainer.global_actor.beta_head.bias[3].fill_(0.0)
        trainer.global_actor.alpha_head.bias[4].fill_(100.0)
        trainer.global_actor.beta_head.bias[4].fill_(-100.0)

    trainer.export_checkpoint(
        output,
        metadata={
            "model_version": "tasam_asgard_v10",
            "economic_action_contract": "economic_action_v3_per_du_sleep",
            "allocation_target_contract": "capacity_budget_with_sla_floor_v1",
            "total_budget_fraction_bounds": [0.0, 1.0],
            "global_action_dim": 5,
            "global_action_layout": [
                "power_du_2", "power_du_3", "power_du_4", "ran_share", "total_budget_fraction"
            ],
            "joint_action_dim": 14,
            "power_levels": [0, *range(25, 101, 5)],
            "economic_sleep_contract": "handover_pdcp_window_10s_one_du_v1",
            "initial_action": {
                "power_percent_by_cell": {"2": 100, "3": 100, "4": 100},
                "total_budget_fraction": 1.0,
            },
            "parent_checkpoint": str(parent),
            "parent_checkpoint_sha256": sha256(parent / "tasam_marl_actors.pt"),
            "parent_metadata_sha256": sha256(meta_path),
            "warm_start": True,
            "parent_was_promoted": False,
            "replay_imported": False,
            "calibration_required": "sim_v3_sleep",
        },
    )
    # export_checkpoint writes the actor/heads and metadata, but the online
    # campaign contract also requires a compact summary beside them so that
    # candidate validation can distinguish a complete warm-start from a
    # partially copied directory.
    (output / "tasam_marl_summary.json").write_text(
        json.dumps({
            "schema": "greenran.tasam.v10_checkpoint_summary.v1",
            "model_version": "tasam_asgard_v10",
            "economic_action_contract": "economic_action_v3_per_du_sleep",
            "du_count": 3,
            "du_state_dim": 13,
            "global_state_dim": 13,
            "global_action_dim": 5,
            "joint_action_dim": 14,
            "warm_start": True,
            "parent_was_promoted": False,
            "replay_imported": False,
            "calibration_required": "sim_v3_sleep",
            "final_metrics": {},
        }, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "schema": "greenran.tasam.v10_checkpoint.v1",
        "checkpoint": str(output),
        "joint_action_dim": 14,
        "economic_action_contract": "economic_action_v3_per_du_sleep",
        "parent_checkpoint_sha256": sha256(parent / "tasam_marl_actors.pt"),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
