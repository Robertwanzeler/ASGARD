#!/usr/bin/env python3
"""
Run a simple 300-epoch curriculum for GraphSAGE conflicts:

50 samples  -> 100 epochs
150 samples -> 100 epochs, warm-start from 50
450 samples -> 100 epochs, warm-start from 150
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TRAINER = PROJECT_ROOT / "training" / "train_graphsage_conflicts.py"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a 300-epoch curriculum warm start for GraphSAGE conflicts.")
    parser.add_argument("--scenario", default="recuperacao", help="scenario slug")
    parser.add_argument(
        "--experiment-dir",
        default=str(PROJECT_ROOT / "runs" / "experimentos_conflitos" / "experimento_principal"),
        help="experiment directory with exported conflict subsets",
    )
    parser.add_argument(
        "--output-root",
        required=True,
        help="root directory where curriculum stages will be written",
    )
    parser.add_argument("--python", default=sys.executable, help="python interpreter used to call the trainer")
    parser.add_argument("--threshold", type=float, default=0.5)
    parser.add_argument("--hidden-dim", type=int, default=128)
    parser.add_argument("--embed-dim", type=int, default=64)
    parser.add_argument("--dropout", type=float, default=0.14)
    parser.add_argument("--learning-rate", type=float, default=0.00035)
    parser.add_argument("--weight-decay", type=float, default=0.0008)
    parser.add_argument("--seed", type=int, default=45)
    return parser.parse_args()


def run_stage(
    *,
    python_bin: str,
    experiment_dir: str,
    scenario: str,
    subset_size: int,
    output_dir: Path,
    threshold: float,
    hidden_dim: int,
    embed_dim: int,
    dropout: float,
    learning_rate: float,
    weight_decay: float,
    seed: int,
    init_model: Path | None,
) -> None:
    cmd = [
        python_bin,
        str(TRAINER),
        "--experiment-dir",
        experiment_dir,
        "--scenario",
        scenario,
        "--subset-sizes",
        str(subset_size),
        "--epochs",
        "50,100",
        "--split-by-rounds",
        "--threshold",
        str(threshold),
        "--hidden-dim",
        str(hidden_dim),
        "--embed-dim",
        str(embed_dim),
        "--dropout",
        str(dropout),
        "--learning-rate",
        str(learning_rate),
        "--weight-decay",
        str(weight_decay),
        "--seed",
        str(seed),
        "--output-dir",
        str(output_dir),
    ]
    if init_model is not None:
        cmd.extend(["--init-model", str(init_model)])
    subprocess.run(cmd, check=True)


def main() -> int:
    args = parse_args()
    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    stage_50 = output_root / "stage_50"
    stage_150 = output_root / "stage_150"
    stage_450 = output_root / "stage_450"

    run_stage(
        python_bin=args.python,
        experiment_dir=args.experiment_dir,
        scenario=args.scenario,
        subset_size=50,
        output_dir=stage_50,
        threshold=args.threshold,
        hidden_dim=args.hidden_dim,
        embed_dim=args.embed_dim,
        dropout=args.dropout,
        learning_rate=args.learning_rate,
        weight_decay=args.weight_decay,
        seed=args.seed,
        init_model=None,
    )
    init_50 = stage_50 / args.scenario / "subset_50" / "best_model.pt"

    run_stage(
        python_bin=args.python,
        experiment_dir=args.experiment_dir,
        scenario=args.scenario,
        subset_size=150,
        output_dir=stage_150,
        threshold=args.threshold,
        hidden_dim=args.hidden_dim,
        embed_dim=args.embed_dim,
        dropout=args.dropout,
        learning_rate=args.learning_rate,
        weight_decay=args.weight_decay,
        seed=args.seed,
        init_model=init_50,
    )
    init_150 = stage_150 / args.scenario / "subset_150" / "best_model.pt"

    run_stage(
        python_bin=args.python,
        experiment_dir=args.experiment_dir,
        scenario=args.scenario,
        subset_size=450,
        output_dir=stage_450,
        threshold=args.threshold,
        hidden_dim=args.hidden_dim,
        embed_dim=args.embed_dim,
        dropout=args.dropout,
        learning_rate=args.learning_rate,
        weight_decay=args.weight_decay,
        seed=args.seed,
        init_model=init_150,
    )

    print(f"Curriculum complete: {output_root}")
    print(f"Final summary: {stage_450 / args.scenario / 'subset_450' / 'training_summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
