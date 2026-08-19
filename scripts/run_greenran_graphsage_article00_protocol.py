#!/usr/bin/env python3
"""
Official GreenRAN GraphSAGE protocol aligned with ARTICLE00.

This runner freezes the validated GreenRAN conflict collections and orchestrates
GraphSAGE training with the ARTICLE00-style evaluation envelope:

- multiseed execution (42-46);
- subsets 50/150/450;
- thresholds 0.2/0.5/0.9;
- official success target at epoch 200;
- optional extended checkpoints up to 1000 for audit/fallback.

It does not collect new runtime data. It only consumes the validated conflict
protocol runs already present under runs/experimentos_conflitos.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNS_ROOT = PROJECT_ROOT / "runs" / "experimentos_conflitos"
RUNS_BASE = PROJECT_ROOT / "runs"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "runs" / "graphsage_article00_protocol"
DEFAULT_TRAINER_PYTHON = PROJECT_ROOT / "drlexp" / ".venv" / "bin" / "python"
DEFAULT_SEEDS = (42, 43, 44, 45, 46)
DEFAULT_THRESHOLDS = (0.2, 0.5, 0.9)
DEFAULT_SUBSET_SIZES = (50, 150, 450)
DEFAULT_EPOCHS = (50, 100, 200, 400, 600, 800, 1000)
DEFAULT_TARGET_EPOCH = 200


@dataclass(frozen=True)
class ScenarioSpec:
    scenario: str
    experiment_dir: str
    domain: str
    note: str


@dataclass(frozen=True)
class DiscardedSpec:
    experiment_dir: str
    scenario: str
    reason: str


OFFICIAL_SCENARIOS = (
    ScenarioSpec(
        scenario="vehicle_warning",
        experiment_dir="20260514_112557_conflict_protocol",
        domain="app3",
        note="veicular warning validado",
    ),
    ScenarioSpec(
        scenario="vehicle_critical",
        experiment_dir="experimentos_conflitos_vehicle_clean/20260515_194647_conflict_protocol",
        domain="app3",
        note="veicular critical rerodado com warmup e sem contaminacao de warning",
    ),
    ScenarioSpec(
        scenario="vehicle_implicito",
        experiment_dir="experimentos_conflitos_vehicle_clean/20260515_194647_conflict_protocol",
        domain="app3",
        note="veicular implicito rerodado com warmup e sem contaminacao de critical",
    ),
    ScenarioSpec(
        scenario="vehicle_recovery",
        experiment_dir="experimentos_conflitos_vehicle_recovery_clean/20260516_101015_conflict_protocol",
        domain="app3",
        note="veicular recovery rerodado com warmup e subset_450 completo",
    ),
    ScenarioSpec(
        scenario="app1_throughput",
        experiment_dir="experimentos_conflitos_app1_throughput_clean/20260516_122522_conflict_protocol",
        domain="app1",
        note="App1 throughput rerodado em regime unico blocked sem mistura guard",
    ),
    ScenarioSpec(
        scenario="app1_latencia",
        experiment_dir="experimentos_conflitos_app12_clean/20260515_224240_conflict_protocol",
        domain="app1",
        note="App1 latencia rerodado com warmup e sem contaminacao de throughput",
    ),
    ScenarioSpec(
        scenario="app2_degradado_leve",
        experiment_dir="experimentos_conflitos_app12_clean/20260515_224240_conflict_protocol",
        domain="app2",
        note="App2 degradado leve rerodado focando connected_ratio sem packet-loss spurio",
    ),
    ScenarioSpec(
        scenario="app2_degradado_critico",
        experiment_dir="experimentos_conflitos_app12_clean/20260515_224240_conflict_protocol",
        domain="app2",
        note="App2 degradado critico rerodado focando connected_ratio sem guard residual",
    ),
    ScenarioSpec(
        scenario="conflito_implicito",
        experiment_dir="20260514_201216_conflict_protocol",
        domain="multiapp",
        note="bloco multiapp implicito validado",
    ),
    ScenarioSpec(
        scenario="recuperacao",
        experiment_dir="20260515_010148_conflict_protocol",
        domain="multiapp",
        note="recovery multiapp corrigido sem weak",
    ),
)


DISCARDED_RUNS = (
    DiscardedSpec(
        experiment_dir="20260514_011612_conflict_protocol",
        scenario="vehicle_*",
        reason="persistencia veicular foi interrompida no meio da coleta",
    ),
    DiscardedSpec(
        experiment_dir="20260514_201216_conflict_protocol",
        scenario="recuperacao",
        reason="recuperacao multiapp ficou com weak=5 recorrente e dominancia indevida de App1",
    ),
)


def parse_csv_ints(raw: str) -> tuple[int, ...]:
    values = tuple(sorted({int(piece.strip()) for piece in raw.split(",") if piece.strip()}))
    if not values:
        raise SystemExit("no integer values requested")
    return values


def parse_csv_floats(raw: str) -> tuple[float, ...]:
    values = tuple(sorted({float(piece.strip()) for piece in raw.split(",") if piece.strip()}))
    if not values:
        raise SystemExit("no float values requested")
    return values


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the official GreenRAN GraphSAGE protocol aligned with ARTICLE00."
    )
    parser.add_argument(
        "--output-root",
        default=str(DEFAULT_OUTPUT_ROOT),
        help="root directory for protocol artifacts",
    )
    parser.add_argument(
        "--trainer-python",
        default="",
        help="python executable used to invoke training/train_graphsage_conflicts.py",
    )
    parser.add_argument(
        "--scenario",
        action="append",
        dest="scenarios",
        default=[],
        help="optional official scenario slug filter; may be passed multiple times",
    )
    parser.add_argument(
        "--seeds",
        default="42,43,44,45,46",
        help="comma-separated training seeds",
    )
    parser.add_argument(
        "--thresholds",
        default="0.2,0.5,0.9",
        help="comma-separated reconstruction thresholds",
    )
    parser.add_argument(
        "--subset-sizes",
        default="50,150,450",
        help="comma-separated dataset subset sizes",
    )
    parser.add_argument(
        "--epochs",
        default="50,100,200,400,600,800,1000",
        help="comma-separated epoch checkpoints. Keep 200 present because it is the official target.",
    )
    parser.add_argument(
        "--target-epoch",
        type=int,
        default=DEFAULT_TARGET_EPOCH,
        help="official success target epoch",
    )
    parser.add_argument(
        "--goal-f1",
        type=float,
        default=1.0,
        help="official desired score at the target epoch",
    )
    parser.add_argument("--hidden-dim", type=int, default=16)
    parser.add_argument("--embed-dim", type=int, default=16)
    parser.add_argument("--dropout", type=float, default=0.10)
    parser.add_argument("--learning-rate", type=float, default=0.001)
    parser.add_argument("--weight-decay", type=float, default=1e-4)
    parser.add_argument(
        "--selection-mode",
        choices=("f1", "fp_penalized"),
        default="f1",
        help="selection mode passed through to the GreenRAN trainer",
    )
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="only write the protocol manifest and print the planned commands",
    )
    return parser.parse_args()


def resolve_trainer_python(raw: str) -> str:
    if raw:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = (Path.cwd() / path).resolve()
        return str(path)
    if DEFAULT_TRAINER_PYTHON.exists():
        return str(DEFAULT_TRAINER_PYTHON)
    return sys.executable


def threshold_slug(value: float) -> str:
    return str(value).replace(".", "_")


def output_dir_for_run(output_root: Path, threshold: float, seed: int, scenario: str) -> Path:
    return (
        output_root
        / f"threshold_{threshold_slug(threshold)}"
        / f"seed_{seed}"
        / scenario
    )


def resolve_experiment_dir(spec: ScenarioSpec) -> Path:
    raw = Path(spec.experiment_dir)
    if raw.is_absolute():
        return raw
    if len(raw.parts) > 1:
        return RUNS_BASE / raw
    return RUNS_ROOT / raw


def selected_scenarios(filters: list[str]) -> list[ScenarioSpec]:
    if not filters:
        return list(OFFICIAL_SCENARIOS)
    known = {item.scenario for item in OFFICIAL_SCENARIOS}
    unknown = sorted(set(filters) - known)
    if unknown:
        raise SystemExit(f"unknown official scenarios: {', '.join(unknown)}")
    filter_set = set(filters)
    return [item for item in OFFICIAL_SCENARIOS if item.scenario in filter_set]


def build_train_command(
    trainer_python: str,
    spec: ScenarioSpec,
    threshold: float,
    seed: int,
    subset_sizes: tuple[int, ...],
    epochs: tuple[int, ...],
    output_dir: Path,
    args: argparse.Namespace,
) -> list[str]:
    experiment_dir = resolve_experiment_dir(spec)
    return [
        trainer_python,
        str(PROJECT_ROOT / "training" / "train_graphsage_conflicts.py"),
        "--experiment-dir",
        str(experiment_dir),
        "--scenario",
        spec.scenario,
        "--split-by-rounds",
        "--epochs",
        ",".join(str(value) for value in epochs),
        "--subset-sizes",
        ",".join(str(value) for value in subset_sizes),
        "--threshold",
        str(threshold),
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
        "--selection-mode",
        args.selection_mode,
        "--seed",
        str(seed),
        "--output-dir",
        str(output_dir),
    ]


def protocol_manifest(
    output_root: Path,
    trainer_python: str,
    selected: list[ScenarioSpec],
    thresholds: tuple[float, ...],
    seeds: tuple[int, ...],
    subset_sizes: tuple[int, ...],
    epochs: tuple[int, ...],
    args: argparse.Namespace,
) -> dict:
    runs = []
    for threshold in thresholds:
        for seed in seeds:
            for spec in selected:
                run_output_dir = output_dir_for_run(output_root, threshold, seed, spec.scenario)
                runs.append(
                    {
                        "scenario": spec.scenario,
                        "domain": spec.domain,
                        "experiment_dir": str(resolve_experiment_dir(spec)),
                        "threshold": threshold,
                        "seed": seed,
                        "subset_sizes": list(subset_sizes),
                        "epochs": list(epochs),
                        "output_dir": str(run_output_dir),
                        "command": build_train_command(
                            trainer_python=trainer_python,
                            spec=spec,
                            threshold=threshold,
                            seed=seed,
                            subset_sizes=subset_sizes,
                            epochs=epochs,
                            output_dir=run_output_dir,
                            args=args,
                        ),
                    }
                )

    return {
        "schema": "greenran.graphsage_article00_protocol.v1",
        "generated_at": int(time.time()),
        "generated_at_iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "project_root": str(PROJECT_ROOT),
        "runs_root": str(RUNS_ROOT),
        "output_root": str(output_root),
        "trainer_python": trainer_python,
        "official_reference_docs": [
            str(PROJECT_ROOT / "docs" / "ARTICLE00_PROTOCOLO_EXPERIMENTAL.md"),
            str(PROJECT_ROOT / "docs" / "ARTICLE00_METODO_E_EXPERIMENTOS.md"),
        ],
        "official_target": {
            "target_epoch": args.target_epoch,
            "goal_f1": args.goal_f1,
            "official_subset_for_comparison": 450,
            "note": "Epoch 200 is the official success target. Later checkpoints are diagnostic only.",
        },
        "frozen_matrix": {
            "selected_runs": [asdict(item) for item in selected],
            "discarded_runs": [asdict(item) for item in DISCARDED_RUNS],
        },
        "protocol": {
            "seeds": list(seeds),
            "thresholds": list(thresholds),
            "subset_sizes": list(subset_sizes),
            "epochs": list(epochs),
            "hidden_dim": args.hidden_dim,
            "embed_dim": args.embed_dim,
            "dropout": args.dropout,
            "learning_rate": args.learning_rate,
            "weight_decay": args.weight_decay,
            "selection_mode": args.selection_mode,
            "split_strategy": "round_holdout",
        },
        "runs": runs,
    }


def ensure_output_root(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def run_already_complete(output_dir: Path, scenario: str, subset_sizes: tuple[int, ...]) -> bool:
    scenario_dir = output_dir / scenario
    for subset_size in subset_sizes:
        summary_path = scenario_dir / f"subset_{subset_size}" / "training_summary.json"
        if not summary_path.exists():
            return False
    return True


def run_command(command: list[str]) -> None:
    result = subprocess.run(command, cwd=PROJECT_ROOT, text=True)
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def main() -> int:
    args = parse_args()
    output_root = Path(args.output_root).resolve()
    trainer_python = resolve_trainer_python(args.trainer_python)
    seeds = parse_csv_ints(args.seeds)
    thresholds = parse_csv_floats(args.thresholds)
    subset_sizes = parse_csv_ints(args.subset_sizes)
    epochs = parse_csv_ints(args.epochs)
    if args.target_epoch not in epochs:
        raise SystemExit(
            f"target epoch {args.target_epoch} is not present in requested epochs {list(epochs)}"
        )

    selected = selected_scenarios(args.scenarios)
    ensure_output_root(output_root)

    manifest = protocol_manifest(
        output_root=output_root,
        trainer_python=trainer_python,
        selected=selected,
        thresholds=thresholds,
        seeds=seeds,
        subset_sizes=subset_sizes,
        epochs=epochs,
        args=args,
    )
    manifest_path = output_root / "protocol_manifest.json"
    with manifest_path.open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, ensure_ascii=False)

    print(f"Manifesto do protocolo: {manifest_path}")
    print(f"Runs planejados: {len(manifest['runs'])}")
    print(
        "Alvo oficial: subset 450 | epoch {epoch} | score {score:.3f}".format(
            epoch=args.target_epoch,
            score=args.goal_f1,
        )
    )

    for run in manifest["runs"]:
        print(
            "[plan] threshold={threshold} seed={seed} scenario={scenario}".format(
                threshold=run["threshold"],
                seed=run["seed"],
                scenario=run["scenario"],
            )
        )
        if args.plan_only:
            continue
        output_dir = Path(run["output_dir"])
        if run_already_complete(output_dir, run["scenario"], subset_sizes):
            print(
                "[skip] threshold={threshold} seed={seed} scenario={scenario} já concluído".format(
                    threshold=run["threshold"],
                    seed=run["seed"],
                    scenario=run["scenario"],
                )
            )
            continue
        run_command(run["command"])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
