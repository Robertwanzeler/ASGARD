#!/usr/bin/env python3
"""
Export a vehicle-focused conflict dataset and launch the direct GraphSAGE trainer.
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from greenran_paths import RAPP_DB_PATH  # noqa: E402


DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "runs" / "vehicle_graphsage"
DEFAULT_TRAINER_PYTHON = PROJECT_ROOT / "drlexp" / ".venv" / "bin" / "python"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the vehicle-domain ARMD-GreenRAN export + GraphSAGE training pipeline."
    )
    parser.add_argument("--db", default=str(RAPP_DB_PATH), help="SQLite Data Lake path")
    parser.add_argument("--hours", type=float, default=24.0, help="lookback window in hours")
    parser.add_argument("--since-ts", type=int, default=0, help="inclusive lower timestamp bound")
    parser.add_argument("--until-ts", type=int, default=0, help="inclusive upper timestamp bound")
    parser.add_argument("--until-exclusive-ts", type=int, default=0, help="exclusive upper timestamp bound")
    parser.add_argument("--limit", type=int, default=0, help="optional max rows from the newest window")
    parser.add_argument(
        "--output-root",
        default="",
        help="directory for exported dataset, graph, and training artifacts; defaults to runs/vehicle_graphsage/<timestamp>",
    )
    parser.add_argument(
        "--trainer-python",
        default="",
        help="Python executable for GraphSAGE training; defaults to drlexp/.venv/bin/python when available",
    )
    parser.add_argument("--scenario-label", default="vehicle_conflicts", help="logical label for the direct training case")
    parser.add_argument("--epochs", default="50,100,200,400,600,800,1000", help="comma-separated training checkpoints")
    parser.add_argument("--hidden-dim", type=int, default=16, help="hidden dimension for GraphSAGE")
    parser.add_argument("--embed-dim", type=int, default=16, help="embedding dimension for GraphSAGE")
    parser.add_argument("--dropout", type=float, default=0.10, help="dropout rate")
    parser.add_argument("--learning-rate", type=float, default=0.001, help="optimizer learning rate")
    parser.add_argument("--weight-decay", type=float, default=1e-4, help="optimizer weight decay")
    parser.add_argument("--threshold", type=float, default=0.5, help="edge threshold for reconstruction")
    parser.add_argument("--seed", type=int, default=42, help="random seed")
    parser.add_argument(
        "--direct-test-fraction",
        type=float,
        default=0.25,
        help="fraction of the direct dataset reserved for evaluation",
    )
    parser.add_argument(
        "--direct-min-test-rows",
        type=int,
        default=1,
        help="minimum held-out rows in direct dataset mode",
    )
    parser.add_argument(
        "--min-vehicle-rows",
        type=int,
        default=10,
        help="minimum number of real vehicle conflict rows required to proceed",
    )
    parser.add_argument(
        "--export-only",
        action="store_true",
        help="only export the vehicle dataset/graph; skip GraphSAGE training",
    )
    return parser.parse_args()


def resolve_output_root(raw: str) -> Path:
    if raw:
        return Path(raw).resolve()
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    return (DEFAULT_OUTPUT_ROOT / timestamp).resolve()


def run_command(command: list[str]) -> None:
    subprocess.run(command, check=True)


def count_dataset_rows(dataset_path: Path) -> int:
    with dataset_path.open(newline="", encoding="utf-8") as f:
        return sum(1 for _ in csv.DictReader(f))


def resolve_trainer_python(raw: str) -> str:
    if raw:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        return str(path)
    if DEFAULT_TRAINER_PYTHON.exists():
        return str(DEFAULT_TRAINER_PYTHON)
    return sys.executable


def ensure_trainer_has_torch(trainer_python: str) -> None:
    probe = subprocess.run(
        [trainer_python, "-c", "import torch"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if probe.returncode == 0:
        return
    raise SystemExit(
        "training python does not provide 'torch': "
        f"{trainer_python}. Install torch there or rerun with --trainer-python <python-with-torch>."
    )


def main() -> int:
    args = parse_args()
    if args.until_ts and args.until_exclusive_ts:
        raise SystemExit("--until-ts and --until-exclusive-ts are mutually exclusive")
    if args.since_ts and args.until_ts and args.until_ts < args.since_ts:
        raise SystemExit("--until-ts must be greater than or equal to --since-ts")
    if args.since_ts and args.until_exclusive_ts and args.until_exclusive_ts < args.since_ts:
        raise SystemExit("--until-exclusive-ts must be greater than or equal to --since-ts")

    output_root = resolve_output_root(args.output_root)
    trainer_python = resolve_trainer_python(args.trainer_python)
    output_root.mkdir(parents=True, exist_ok=True)

    dataset_path = output_root / "vehicle_conflict_dataset.csv"
    graph_path = output_root / "vehicle_conflict_graph.json"
    training_output_dir = output_root / "graphsage_training"

    export_cmd = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "export_conflict_dataset.py"),
        "--db",
        args.db,
        "--dataset",
        str(dataset_path),
        "--graph",
        str(graph_path),
        "--focus",
        "vehicle",
        "--hours",
        str(args.hours),
    ]
    if args.since_ts:
        export_cmd.extend(["--since-ts", str(args.since_ts)])
    if args.until_ts:
        export_cmd.extend(["--until-ts", str(args.until_ts)])
    if args.until_exclusive_ts:
        export_cmd.extend(["--until-exclusive-ts", str(args.until_exclusive_ts)])
    if args.limit:
        export_cmd.extend(["--limit", str(args.limit)])

    run_command(export_cmd)
    exported_rows = count_dataset_rows(dataset_path)

    train_cmd = [
        trainer_python,
        str(PROJECT_ROOT / "training" / "train_graphsage_conflicts.py"),
        "--dataset-path",
        str(dataset_path),
        "--graph-path",
        str(graph_path),
        "--scenario-label",
        args.scenario_label,
        "--output-dir",
        str(training_output_dir),
        "--epochs",
        args.epochs,
        "--hidden-dim",
        str(args.hidden_dim),
        "--embed-dim",
        str(args.embed_dim),
        "--dropout",
        str(args.dropout),
        "--learning-rate",
        str(args.learning_rate),
        "--weight-decay",
        str(args.weight_decay),
        "--threshold",
        str(args.threshold),
        "--seed",
        str(args.seed),
        "--direct-test-fraction",
        str(args.direct_test_fraction),
        "--direct-min-test-rows",
        str(args.direct_min_test_rows),
    ]

    training_summary_path = training_output_dir / args.scenario_label / "training_summary.json"
    aggregate_summary_path = training_output_dir / "aggregate_report.json"

    manifest = {
        "generated_at": int(time.time()),
        "generated_at_iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "db": str(Path(args.db).resolve()),
        "output_root": str(output_root),
        "dataset_path": str(dataset_path),
        "graph_path": str(graph_path),
        "training_output_dir": str(training_output_dir),
        "training_summary_path": str(training_summary_path),
        "aggregate_summary_path": str(aggregate_summary_path),
        "trainer_python": trainer_python,
        "exported_rows": exported_rows,
        "min_vehicle_rows": args.min_vehicle_rows,
        "export_only": args.export_only,
        "scenario_label": args.scenario_label,
        "export_command": export_cmd,
        "train_command": train_cmd,
    }

    manifest_path = output_root / "vehicle_graphsage_pipeline_manifest.json"
    with manifest_path.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    if exported_rows < args.min_vehicle_rows:
        raise SystemExit(
            f"vehicle dataset too small for training: rows={exported_rows}, "
            f"required>={args.min_vehicle_rows}. "
            "The Data Lake does not currently contain enough real App3/vehicle conflicts."
        )

    if not args.export_only:
        ensure_trainer_has_torch(trainer_python)
        run_command(train_cmd)

    print(
        json.dumps(
            {
                "output_root": str(output_root),
                "dataset_path": str(dataset_path),
                "graph_path": str(graph_path),
                "training_output_dir": str(training_output_dir),
                "training_summary_path": str(training_summary_path),
                "aggregate_summary_path": str(aggregate_summary_path),
                "manifest_path": str(manifest_path),
                "trainer_python": trainer_python,
                "exported_rows": exported_rows,
                "min_vehicle_rows": args.min_vehicle_rows,
                "export_only": args.export_only,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
