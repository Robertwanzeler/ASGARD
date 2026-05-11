#!/usr/bin/env python3
"""
Export A3C checkpoint into runtime actor/critic weight files.
"""

import argparse
from pathlib import Path

import torch


def main():
    parser = argparse.ArgumentParser(description="Export A3C runtime weights")
    parser.add_argument(
        "--checkpoint",
        default="drlexp/models/a3c/checkpoint.pt",
        help="Path to A3C checkpoint.pt",
    )
    parser.add_argument(
        "--output-dir",
        default="drlexp/models/a3c",
        help="Directory where actor_v7.pt and critic_v7.pt will be written",
    )
    parser.add_argument(
        "--actor-name",
        default="actor_v7.pt",
        help="Runtime actor filename",
    )
    parser.add_argument(
        "--critic-name",
        default="critic_v7.pt",
        help="Runtime critic filename",
    )
    args = parser.parse_args()

    checkpoint_path = Path(args.checkpoint)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if not checkpoint_path.exists():
        raise FileNotFoundError(f"Checkpoint not found: {checkpoint_path}")

    checkpoint = torch.load(checkpoint_path, map_location="cpu")
    actor_state = checkpoint.get("actor")
    critic_state = checkpoint.get("critic")

    if actor_state is None or critic_state is None:
        raise KeyError("Checkpoint missing 'actor' or 'critic' state dict")

    actor_path = output_dir / args.actor_name
    critic_path = output_dir / args.critic_name

    torch.save(actor_state, actor_path)
    torch.save(critic_state, critic_path)

    print(f"Exported actor runtime weights to {actor_path}")
    print(f"Exported critic runtime weights to {critic_path}")


if __name__ == "__main__":
    main()
