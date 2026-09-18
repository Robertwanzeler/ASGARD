#!/usr/bin/env python3
"""Build a clean TA-SAM bootstrap checkpoint from a synthetic category head.

The policy and value networks are initialized from a fresh deterministic model.
Only the ordinal category head is imported from the curriculum checkpoint.
No replay, trace, optimizer state, or previous online policy is copied.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "drlexp" / "src"))

from drl.ta_sam_marl_sac import TASAMArticleSACTrainer


REQUIRED_DIMS = {"du_count": 3, "du_state_dim": 13, "global_state_dim": 13}
COMPONENTS = (
    "tasam_marl_actors.pt",
    "tasam_marl_global_actor.pt",
    "tasam_marl_critic1.pt",
    "tasam_marl_critic2.pt",
    "tasam_marl_target_critic1.pt",
    "tasam_marl_target_critic2.pt",
    "tasam_marl_category_head.pt",
)


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build(args: argparse.Namespace) -> dict[str, Any]:
    source = args.source_checkpoint.resolve()
    output = args.output_checkpoint.resolve()
    if not source.is_dir():
        raise ValueError(f"curriculum checkpoint not found: {source}")
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"output checkpoint is not empty: {output}")

    metadata = read_json(source / "tasam_marl_checkpoint_meta.json")
    actual_dims = {key: metadata.get(key) for key in REQUIRED_DIMS}
    if actual_dims != REQUIRED_DIMS:
        raise ValueError(f"incompatible checkpoint dimensions: {actual_dims}")
    source_head = source / "tasam_marl_category_head.pt"
    if not source_head.is_file():
        raise ValueError(f"curriculum category head missing: {source_head}")

    actor_hidden = tuple(int(x) for x in (metadata.get("actor_hidden_dims") or (300, 400, 400)))
    critic_hidden = tuple(int(x) for x in (metadata.get("critic_hidden_dims") or (300, 400, 400)))
    activation = str(metadata.get("activation") or "tanh")
    trainer = TASAMArticleSACTrainer(
        du_count=3,
        du_state_dim=13,
        global_state_dim=13,
        actor_hidden_dims=actor_hidden,
        critic_hidden_dims=critic_hidden,
        activation=activation,
        category_head_hidden_dim=int(metadata.get("category_head_hidden_dim") or 64),
        category_loss_weight=float(metadata.get("category_loss_weight") or 0.5),
        category_head_lr=float(metadata.get("category_head_lr") or 0.001),
        category_head_steps=int(metadata.get("category_head_steps") or 10),
        allocation_head_output_dim=3 if args.economic_output_dim else 2,
        seed=int(args.seed),
    )
    category_state = torch.load(source_head, map_location="cpu", weights_only=False)
    trainer.category_head.load_state_dict(category_state)

    output.mkdir(parents=True, exist_ok=True)
    curriculum = metadata.get("category_head_pretraining")
    if not isinstance(curriculum, dict):
        curriculum = {}
    initialization = {
        "initialization_source": "clean_curriculum_only",
        "seed": int(args.seed),
        "policy_reinitialized": True,
        "actors_reinitialized": True,
        "critics_reinitialized": True,
        "optimizers_reinitialized": True,
        "replay_copied": False,
        "historical_trace_copied": False,
        "category_head_source": str(source_head),
        "category_head_source_sha256": sha256(source_head),
        "state_contract": REQUIRED_DIMS,
    }
    trainer.export_checkpoint(
        output,
        metadata={
            "initialization_source": "clean_curriculum_only",
            "initialization": initialization,
            "trace_jsonl": "",
            "replay_source": "none",
            "historical_replay_enabled": False,
            "category_head_pretraining": curriculum,
            "category_head_pretrained": True,
            "category_head_training_source": "synthetic_curriculum",
            "actor_hidden_dims": list(actor_hidden),
            "critic_hidden_dims": list(critic_hidden),
            "activation": activation,
            "economic_allocation_head": bool(args.economic_output_dim),
            "parent_checkpoint": str(source),
            "parent_checkpoint_sha256": sha256(source_head),
        },
    )

    summary = {
        "schema": "greenran.tasam_marl_clean_bootstrap.v1",
        "initialization_source": "clean_curriculum_only",
        "completed_epochs": 0,
        "trace_jsonl": "",
        "replay_source": "none",
        "historical_replay_enabled": False,
        "category_head_pretrained": True,
        "category_curriculum": curriculum,
        "initialization": initialization,
        "final_metrics": {
            "category_accuracy": float(((curriculum.get("training") or {}).get("best_validation") or {}).get("accuracy", 0.0) or 0.0),
            "conditional_recall": float((((curriculum.get("training") or {}).get("best_validation") or {}).get("per_category") or {}).get("CONDITIONAL", {}).get("recall", 0.0) or 0.0),
            "conditional_f1": float((((curriculum.get("training") or {}).get("best_validation") or {}).get("per_category") or {}).get("CONDITIONAL", {}).get("f1", 0.0) or 0.0),
            "training_reward_mean": 0.0,
        },
    }
    (output / "tasam_marl_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    hashes = {name: sha256(output / name) for name in COMPONENTS}
    initialization["component_sha256"] = hashes
    (output / "tasam_clean_initialization_manifest.json").write_text(
        json.dumps(initialization, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    meta_path = output / "tasam_marl_checkpoint_meta.json"
    final_meta = read_json(meta_path)
    final_meta["initialization"] = initialization
    final_meta["initialization_source"] = "clean_curriculum_only"
    final_meta["trace_jsonl"] = ""
    final_meta["replay_source"] = "none"
    final_meta["historical_replay_enabled"] = False
    meta_path.write_text(json.dumps(final_meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"output_checkpoint": str(output), "initialization": initialization}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-checkpoint", type=Path, required=True)
    parser.add_argument("--output-checkpoint", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=47)
    parser.add_argument("--economic-output-dim", action="store_true", help="export the 3-output economic allocation head")
    args = parser.parse_args()
    print(json.dumps(build(args), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
