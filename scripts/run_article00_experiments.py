#!/usr/bin/env python3
"""
Interactive experiment runner aligned with artigo00 collection protocol.

What it does:
- guides 7 GreenRAN scenarios with 20 rounds each by default;
- records exact start/end timestamps for every round;
- exports a conflict dataset/graph for each round without mixing windows;
- learns the conflict matrix for each round using threshold 0.5;
- exports scenario-wide datasets and article-style subsets (50/150/450 rows).

What it does not do yet:
- GraphSAGE training with epochs. The current learner is heuristic/statistical,
  so we only align dataset sizes and threshold from artigo00. Epoch-based GNN
  training remains a later phase.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPORT_SCRIPT = PROJECT_ROOT / "scripts" / "export_conflict_dataset.py"
LEARN_SCRIPT = PROJECT_ROOT / "scripts" / "learn_conflict_matrix.py"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "runs" / "article00_experiments"
SCENARIO_CONTROL_PATH = Path("/tmp/article00_scenario_control.json")

DEFAULT_SUBSET_SIZES = (50, 150, 450)
DEFAULT_THRESHOLD = 0.5
DEFAULT_MIN_COUNT = 3
DEFAULT_ROUNDS = 20
DEFAULT_DURATION = 600
DEFAULT_PROGRESS_STEP = 60
DEFAULT_ROUND_GAP = 0
DEFAULT_SCENARIO_GAP = 0

SNAPSHOT_FILES = {
    "app1_monitoring_snapshot": Path("/tmp/app1_vigilancia/monitoring_snapshot.json"),
    "app2_monitoring_snapshot": Path("/tmp/app2_monitoramento/monitoring_snapshot.json"),
    "extended_metrics": Path("/tmp/xapp_metrics/extended_metrics.json"),
    "rapp_decisions": Path("/tmp/rapp_decisions.jsonl"),
    "article00_scenario_control": SCENARIO_CONTROL_PATH,
}


@dataclass(frozen=True)
class Scenario:
    slug: str
    title: str
    goal: str
    instructions: tuple[str, ...]


SCENARIOS = (
    Scenario(
        slug="baseline_saude",
        title="Baseline saudável",
        goal="estabelecer referência com App1 e App2 sem conflito prioritário",
        instructions=(
            "mantenha as 3 câmeras acima de 30 Mbps e latência baixa",
            "mantenha App2 próximo de 17/17 sensores, packet loss abaixo de 4%",
            "use este cenário para medir comportamento normal de ML/DRL/economia",
        ),
    ),
    Scenario(
        slug="app1_throughput",
        title="App1 degradado por throughput",
        goal="forçar conflito de throughput mínimo por câmera",
        instructions=(
            "leve pelo menos 1 câmera para a faixa 25-30 Mbps",
            "em parte das rodadas, derrube 1 câmera abaixo de 25 Mbps",
            "preserve CVaR global saudável quando possível para evidenciar conflito implícito",
        ),
    ),
    Scenario(
        slug="app1_latencia",
        title="App1 degradado por latência",
        goal="forçar guarda e bloqueio por latência da câmera",
        instructions=(
            "leve câmera para a faixa 60-80 ms em algumas rodadas",
            "leve câmera acima de 80 ms em outras rodadas",
            "mantenha App2 saudável para isolar o efeito em App1",
        ),
    ),
    Scenario(
        slug="app2_degradado_leve",
        title="App2 degradado leve",
        goal="capturar guarda em mMTC sem chegar ao crítico em todas as rodadas",
        instructions=(
            "faça oscilar App2 entre 16/17 e 17/17 sensores",
            "mantenha packet loss entre 4% e 6%",
            "mantenha delivery próximo de 95%",
        ),
    ),
    Scenario(
        slug="app2_degradado_critico",
        title="App2 degradado crítico",
        goal="capturar bloqueio de mMTC",
        instructions=(
            "leve App2 para 15/17 ou menos sensores conectados",
            "ou packet loss acima de 10%",
            "ou delivery abaixo de 90%",
        ),
    ),
    Scenario(
        slug="conflito_implicito",
        title="Conflito implícito",
        goal="mostrar KPI global saudável mascarando KPI local degradado",
        instructions=(
            "mantenha CVaR/P95 global saudável",
            "degrade App1 ou App2 localmente",
            "o objetivo é gerar conflito implícito claro para o grafo",
        ),
    ),
    Scenario(
        slug="recuperacao",
        title="Recuperação e histerese",
        goal="observar retorno gradual do sistema após estado ruim",
        instructions=(
            "comece a rodada com App1 ou App2 degradado",
            "depois recupere para estado saudável durante a janela",
            "isso ajuda a medir persistência, guarda e estabilização",
        ),
    ),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run artigo00-aligned GreenRAN conflict collection in guided rounds."
    )
    parser.add_argument("--rounds", type=int, default=DEFAULT_ROUNDS, help="rounds per scenario")
    parser.add_argument("--duration", type=int, default=DEFAULT_DURATION, help="seconds per round")
    parser.add_argument(
        "--progress-step",
        type=int,
        default=DEFAULT_PROGRESS_STEP,
        help="countdown print step in seconds",
    )
    parser.add_argument(
        "--output-root",
        default=str(DEFAULT_OUTPUT_ROOT),
        help="directory where experiment outputs will be stored",
    )
    parser.add_argument(
        "--scenario",
        action="append",
        dest="scenarios",
        default=[],
        help="run only the given scenario slug; may be passed multiple times",
    )
    parser.add_argument(
        "--subset-sizes",
        default="50,150,450",
        help="comma-separated article-style dataset sizes",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD,
        help="learner threshold aligned to artigo00",
    )
    parser.add_argument(
        "--min-count",
        type=int,
        default=DEFAULT_MIN_COUNT,
        help="minimum support for strong learned edges",
    )
    parser.add_argument(
        "--auto",
        action="store_true",
        help="run all configured rounds without prompting for Enter between scenarios/rounds",
    )
    parser.add_argument(
        "--round-gap",
        type=int,
        default=DEFAULT_ROUND_GAP,
        help="seconds to wait automatically between rounds in --auto mode",
    )
    parser.add_argument(
        "--scenario-gap",
        type=int,
        default=DEFAULT_SCENARIO_GAP,
        help="seconds to wait automatically between scenarios in --auto mode",
    )
    parser.add_argument(
        "--auto-switch",
        action="store_true",
        help="apply automatic App1/App2 control profiles so scenario labels also change the live conditions",
    )
    return parser.parse_args()


def selected_scenarios(slugs: list[str]) -> list[Scenario]:
    if not slugs:
        return list(SCENARIOS)

    scenario_map = {scenario.slug: scenario for scenario in SCENARIOS}
    selected = []
    for slug in slugs:
        if slug not in scenario_map:
            raise SystemExit(
                f"unknown scenario '{slug}'. Available: {', '.join(sorted(scenario_map))}"
            )
        selected.append(scenario_map[slug])
    return selected


def prompt_action(prompt: str, allow_skip: bool = True) -> str:
    suffix = " [Enter=continuar"
    if allow_skip:
        suffix += ", skip=pular"
    suffix += ", q=sair]: "

    response = input(f"{prompt}{suffix}").strip().lower()
    if response in {"q", "quit", "exit"}:
        raise KeyboardInterrupt()
    if allow_skip and response in {"skip", "s"}:
        return "skip"
    return "continue"


def countdown(
    total_seconds: int,
    step_seconds: int,
    transitions: list[tuple[int, Callable[[], None], str]] | None = None,
) -> None:
    if total_seconds <= 0:
        return

    transitions = sorted(transitions or [], key=lambda item: item[0])
    next_transition = 0
    last_progress_remaining = None
    start = time.monotonic()
    print(f"  coleta em andamento por {total_seconds}s")

    while True:
        elapsed = int(time.monotonic() - start)
        while next_transition < len(transitions) and elapsed >= transitions[next_transition][0]:
            _, callback, label = transitions[next_transition]
            callback()
            if label:
                print(f"  transição automática: {label}")
            next_transition += 1

        remaining = max(0, total_seconds - elapsed)
        if (
            step_seconds > 0
            and remaining > 0
            and remaining != total_seconds
            and remaining % step_seconds == 0
            and remaining != last_progress_remaining
        ):
            print(f"  faltam {remaining}s")
            last_progress_remaining = remaining

        if remaining <= 0:
            break
        time.sleep(1)

    print("  rodada encerrada")


def auto_wait(label: str, total_seconds: int, step_seconds: int) -> None:
    if total_seconds <= 0:
        return

    print(f"  {label}: aguardando {total_seconds}s")
    countdown(total_seconds, step_seconds)


def iso_from_ts(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp).isoformat(timespec="seconds")


def run_json_command(command: list[str]) -> dict:
    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    stdout = completed.stdout.strip()
    if not stdout:
        return {}
    return json.loads(stdout)


def copy_snapshot_files(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for label, source in SNAPSHOT_FILES.items():
        if source.exists():
            shutil.copy2(source, destination / source.name)


def subset_csv(source_csv: Path, destination_csv: Path, max_rows: int) -> int:
    with source_csv.open() as src:
        reader = csv.DictReader(src)
        rows = list(reader)
        fieldnames = reader.fieldnames or []

    subset = rows[:max_rows]
    with destination_csv.open("w", newline="") as dst:
        writer = csv.DictWriter(dst, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(subset)

    return len(subset)


def read_json(path: Path) -> dict:
    with path.open() as f:
        return json.load(f)


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)


def write_csv(path: Path, rows: list[dict], fieldnames: Iterable[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(fieldnames))
        writer.writeheader()
        writer.writerows(rows)


def write_scenario_control(payload: dict) -> None:
    SCENARIO_CONTROL_PATH.parent.mkdir(parents=True, exist_ok=True)
    with SCENARIO_CONTROL_PATH.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)


def clear_scenario_control() -> None:
    if SCENARIO_CONTROL_PATH.exists():
        SCENARIO_CONTROL_PATH.unlink()


def build_round_control_profile(scenario: Scenario, round_index: int, duration: int) -> list[dict]:
    app1_healthy = {
        "enabled": True,
        "active_cameras": 3,
        "observed_cameras": 3,
        "critical_cameras": 0,
        "throughput_ready": True,
        "throughput_mbps": 32.0,
        "avg_throughput_mbps": 34.0,
        "latency_ms": 18.0,
        "throughput_source": "article00_control",
    }
    app2_healthy = {
        "enabled": True,
        "total_sensors": 17,
        "connected_sensors": 17,
        "error_sensors": 0,
        "low_battery_sensors": 0,
        "packet_loss_percent": 3.2,
        "delivery_success_percent": 97.2,
        "avg_latency_ms": 185.0,
        "avg_rssi_dbm": -89.0,
        "avg_battery_percent": 76.0,
        "avg_power_mw": 182.0,
        "network_utilization_percent": 62.0,
        "mode": "healthy",
    }

    if scenario.slug == "baseline_saude":
        return [{"offset_s": 0, "app1": app1_healthy, "app2": app2_healthy}]

    if scenario.slug == "app1_throughput":
        target_tp = 24.0 if round_index % 2 else 27.5
        return [
            {
                "offset_s": 0,
                "app1": {
                    **app1_healthy,
                    "throughput_mbps": target_tp,
                    "avg_throughput_mbps": round(target_tp + 3.0, 1),
                    "latency_ms": 22.0,
                },
                "app2": app2_healthy,
            }
        ]

    if scenario.slug == "app1_latencia":
        target_latency = 92.0 if round_index % 2 == 0 else 72.0
        critical = 1 if target_latency >= 80.0 else 0
        return [
            {
                "offset_s": 0,
                "app1": {
                    **app1_healthy,
                    "throughput_mbps": 31.0,
                    "avg_throughput_mbps": 33.0,
                    "latency_ms": target_latency,
                    "critical_cameras": critical,
                },
                "app2": app2_healthy,
            }
        ]

    if scenario.slug == "app2_degradado_leve":
        return [
            {
                "offset_s": 0,
                "app1": app1_healthy,
                "app2": {
                    **app2_healthy,
                    "connected_sensors": 16,
                    "error_sensors": 1,
                    "packet_loss_percent": 5.4,
                    "delivery_success_percent": 95.1,
                    "avg_latency_ms": 225.0,
                    "avg_rssi_dbm": -94.0,
                    "mode": "degraded_light",
                },
            }
        ]

    if scenario.slug == "app2_degradado_critico":
        return [
            {
                "offset_s": 0,
                "app1": app1_healthy,
                "app2": {
                    **app2_healthy,
                    "connected_sensors": 14,
                    "error_sensors": 3,
                    "low_battery_sensors": 2,
                    "packet_loss_percent": 11.8,
                    "delivery_success_percent": 87.5,
                    "avg_latency_ms": 360.0,
                    "avg_rssi_dbm": -101.0,
                    "avg_battery_percent": 58.0,
                    "mode": "degraded_critical",
                },
            }
        ]

    if scenario.slug == "conflito_implicito":
        return [
            {
                "offset_s": 0,
                "app1": {
                    **app1_healthy,
                    "throughput_mbps": 23.5,
                    "avg_throughput_mbps": 27.0,
                    "latency_ms": 24.0,
                },
                "app2": app2_healthy,
            }
        ]

    if scenario.slug == "recuperacao":
        midpoint = max(1, duration // 2)
        return [
            {
                "offset_s": 0,
                "app1": {
                    **app1_healthy,
                    "throughput_mbps": 22.5,
                    "avg_throughput_mbps": 25.0,
                    "latency_ms": 85.0,
                    "critical_cameras": 1,
                },
                "app2": {
                    **app2_healthy,
                    "connected_sensors": 15,
                    "error_sensors": 2,
                    "packet_loss_percent": 8.6,
                    "delivery_success_percent": 91.5,
                    "avg_latency_ms": 280.0,
                    "mode": "recovery_start",
                },
            },
            {"offset_s": midpoint, "app1": app1_healthy, "app2": app2_healthy},
        ]

    return [{"offset_s": 0, "app1": app1_healthy, "app2": app2_healthy}]


def apply_control_stage(
    scenario: Scenario,
    round_index: int,
    total_rounds: int,
    duration: int,
    stage: dict,
) -> None:
    payload = {
        "schema": "greenran.article00_scenario_control.v1",
        "generated_at": int(time.time()),
        "generated_at_iso": datetime.now().isoformat(timespec="seconds"),
        "scenario": scenario.slug,
        "round": round_index,
        "rounds_total": total_rounds,
        "duration_s": duration,
        "app1_camera_override": stage.get("app1", {}),
        "app2_sensor_override": stage.get("app2", {}),
    }
    write_scenario_control(payload)


def scenario_manifest(scenario: Scenario) -> dict:
    return {
        "slug": scenario.slug,
        "title": scenario.title,
        "goal": scenario.goal,
        "instructions": list(scenario.instructions),
    }


def run_round(
    scenario: Scenario,
    round_index: int,
    total_rounds: int,
    scenario_dir: Path,
    duration: int,
    progress_step: int,
    threshold: float,
    min_count: int,
    auto_mode: bool,
    auto_switch: bool,
) -> dict | None:
    round_label = f"round_{round_index:02d}"
    round_dir = scenario_dir / "rounds" / round_label
    if auto_mode:
        print(
            f"\n{scenario.title} | rodada {round_index}/{total_rounds}. "
            "Modo automático ativo, iniciando coleta sem prompt."
        )
    else:
        action = prompt_action(
            f"\n{scenario.title} | rodada {round_index}/{total_rounds}. "
            "Prepare o cenário e inicie quando estiver pronto",
            allow_skip=True,
        )
        if action == "skip":
            return None

    round_dir.mkdir(parents=True, exist_ok=True)
    control_profile = build_round_control_profile(scenario, round_index, duration) if auto_switch else []
    transitions: list[tuple[int, Callable[[], None], str]] = []
    if auto_switch and control_profile:
        initial_stage = control_profile[0]
        apply_control_stage(scenario, round_index, total_rounds, duration, initial_stage)
        for stage in control_profile[1:]:
            offset_s = max(0, int(stage.get("offset_s", 0) or 0))
            label = f"{scenario.slug} offset={offset_s}s"
            transitions.append(
                (
                    offset_s,
                    lambda stage=stage: apply_control_stage(
                        scenario,
                        round_index,
                        total_rounds,
                        duration,
                        stage,
                    ),
                    label,
                )
            )
        print(f"  controle automático aplicado para {scenario.slug}")

    start_ts = int(time.time())
    write_json(
        round_dir / "round_window.json",
        {
            "scenario": scenario.slug,
            "round": round_index,
            "start_ts": start_ts,
            "start_iso": iso_from_ts(start_ts),
            "duration_s": duration,
        },
    )
    copy_snapshot_files(round_dir / "snapshots_before")

    countdown(duration, progress_step, transitions=transitions)
    end_ts = int(time.time())

    dataset_path = round_dir / "conflict_dataset.csv"
    graph_path = round_dir / "conflict_graph.json"
    adjacency_path = round_dir / "conflict_adjacency.json"
    report_path = round_dir / "conflict_report.json"

    export_result = run_json_command(
        [
            sys.executable,
            str(EXPORT_SCRIPT),
            "--since-ts",
            str(start_ts),
            "--until-ts",
            str(end_ts),
            "--dataset",
            str(dataset_path),
            "--graph",
            str(graph_path),
        ]
    )
    learn_result = run_json_command(
        [
            sys.executable,
            str(LEARN_SCRIPT),
            "--dataset",
            str(dataset_path),
            "--graph",
            str(graph_path),
            "--adjacency",
            str(adjacency_path),
            "--report",
            str(report_path),
            "--threshold",
            str(threshold),
            "--min-count",
            str(min_count),
        ]
    )

    copy_snapshot_files(round_dir / "snapshots_after")

    report = read_json(report_path)
    summary = {
        "scenario": scenario.slug,
        "round": round_index,
        "start_ts": start_ts,
        "end_ts": end_ts,
        "start_iso": iso_from_ts(start_ts),
        "end_iso": iso_from_ts(end_ts),
        "duration_s": max(0, end_ts - start_ts),
        "rows": int(export_result.get("rows", 0)),
        "nodes": int(export_result.get("nodes", 0)),
        "edges": int(export_result.get("edges", 0)),
        "confirmed_by_data": int(report.get("summary", {}).get("confirmed_by_data", 0)),
        "weak_or_low_support": int(report.get("summary", {}).get("weak_or_low_support", 0)),
        "spurious_in_baseline": int(report.get("summary", {}).get("spurious_in_baseline", 0)),
        "emergent_from_data": int(report.get("summary", {}).get("emergent_from_data", 0)),
        "dataset_path": str(dataset_path),
        "graph_path": str(graph_path),
        "adjacency_path": str(adjacency_path),
        "report_path": str(report_path),
    }
    write_json(round_dir / "round_summary.json", summary)
    print(
        f"  resumo rodada: rows={summary['rows']} | "
        f"confirmed={summary['confirmed_by_data']} | weak={summary['weak_or_low_support']}"
    )
    if auto_switch:
        copy_snapshot_files(round_dir / "snapshots_after_control")
    return summary


def build_scenario_exports(
    scenario: Scenario,
    scenario_dir: Path,
    scenario_start_ts: int,
    scenario_end_ts: int,
    threshold: float,
    min_count: int,
    subset_sizes: tuple[int, ...],
) -> dict:
    exports_dir = scenario_dir / "scenario_exports"
    exports_dir.mkdir(parents=True, exist_ok=True)

    full_dataset = exports_dir / "conflict_dataset_full.csv"
    full_graph = exports_dir / "conflict_graph_full.json"
    full_adjacency = exports_dir / "conflict_adjacency_full.json"
    full_report = exports_dir / "conflict_report_full.json"

    export_result = run_json_command(
        [
            sys.executable,
            str(EXPORT_SCRIPT),
            "--since-ts",
            str(scenario_start_ts),
            "--until-ts",
            str(scenario_end_ts),
            "--dataset",
            str(full_dataset),
            "--graph",
            str(full_graph),
        ]
    )
    run_json_command(
        [
            sys.executable,
            str(LEARN_SCRIPT),
            "--dataset",
            str(full_dataset),
            "--graph",
            str(full_graph),
            "--adjacency",
            str(full_adjacency),
            "--report",
            str(full_report),
            "--threshold",
            str(threshold),
            "--min-count",
            str(min_count),
        ]
    )

    subsets = []
    for size in subset_sizes:
        subset_dir = exports_dir / f"subset_{size}"
        subset_dir.mkdir(parents=True, exist_ok=True)
        subset_dataset = subset_dir / f"conflict_dataset_{size}.csv"
        subset_adjacency = subset_dir / f"conflict_adjacency_{size}.json"
        subset_report = subset_dir / f"conflict_report_{size}.json"
        actual_rows = subset_csv(full_dataset, subset_dataset, size)
        if actual_rows == 0:
            continue

        run_json_command(
            [
                sys.executable,
                str(LEARN_SCRIPT),
                "--dataset",
                str(subset_dataset),
                "--graph",
                str(full_graph),
                "--adjacency",
                str(subset_adjacency),
                "--report",
                str(subset_report),
                "--threshold",
                str(threshold),
                "--min-count",
                str(min_count),
            ]
        )

        subset_summary = read_json(subset_report).get("summary", {})
        subsets.append(
            {
                "requested_rows": size,
                "actual_rows": actual_rows,
                "dataset_path": str(subset_dataset),
                "adjacency_path": str(subset_adjacency),
                "report_path": str(subset_report),
                "summary": subset_summary,
            }
        )

    return {
        "scenario": scenario.slug,
        "start_ts": scenario_start_ts,
        "end_ts": scenario_end_ts,
        "start_iso": iso_from_ts(scenario_start_ts),
        "end_iso": iso_from_ts(scenario_end_ts),
        "full_export": {
            "rows": int(export_result.get("rows", 0)),
            "dataset_path": str(full_dataset),
            "graph_path": str(full_graph),
            "adjacency_path": str(full_adjacency),
            "report_path": str(full_report),
            "summary": read_json(full_report).get("summary", {}),
        },
        "article00_alignment": {
            "dataset_sizes": list(subset_sizes),
            "threshold": threshold,
            "recommended_gnn_epochs": 600,
            "epochs_applied_in_current_runner": None,
            "note": (
                "dataset sizes and threshold are aligned with artigo00; "
                "GraphSAGE epoch training is not implemented in the current heuristic learner"
            ),
        },
        "subsets": subsets,
    }


def main() -> int:
    args = parse_args()
    scenarios = selected_scenarios(args.scenarios)
    subset_sizes = tuple(
        sorted({int(piece.strip()) for piece in args.subset_sizes.split(",") if piece.strip()})
    )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    experiment_dir = Path(args.output_root) / f"{timestamp}_artigo00_protocol"
    experiment_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "schema": "greenran.artigo00_protocol.v1",
        "generated_at": int(time.time()),
        "generated_at_iso": datetime.now().isoformat(timespec="seconds"),
        "config": {
            "rounds_per_scenario": args.rounds,
            "round_duration_s": args.duration,
            "progress_step_s": args.progress_step,
            "auto_mode": args.auto,
            "auto_switch": args.auto_switch,
            "round_gap_s": args.round_gap,
            "scenario_gap_s": args.scenario_gap,
            "subset_sizes": list(subset_sizes),
            "threshold": args.threshold,
            "min_count": args.min_count,
        },
        "scenarios": [scenario_manifest(scenario) for scenario in scenarios],
    }
    write_json(experiment_dir / "experiment_manifest.json", manifest)

    print(f"Saída do experimento: {experiment_dir}")
    print("Protocolo artigo00:")
    print(f"- threshold: {args.threshold}")
    print(f"- subsets: {', '.join(str(size) for size in subset_sizes)}")
    print("- epochs GNN recomendados no artigo: 600 (não aplicados neste runner atual)")
    print(f"- modo automático: {'sim' if args.auto else 'não'}")
    print(f"- troca automática de cenário: {'sim' if args.auto_switch else 'não'}")
    if args.auto:
        print(f"- intervalo automático entre rodadas: {args.round_gap}s")
        print(f"- intervalo automático entre cenários: {args.scenario_gap}s")

    experiment_summary_rows: list[dict] = []
    scenario_reports: list[dict] = []
    clear_scenario_control()

    try:
        for scenario in scenarios:
            scenario_dir = experiment_dir / scenario.slug
            scenario_dir.mkdir(parents=True, exist_ok=True)
            write_json(scenario_dir / "scenario_manifest.json", scenario_manifest(scenario))

            print(f"\n=== {scenario.title} ===")
            print(f"Objetivo: {scenario.goal}")
            for instruction in scenario.instructions:
                print(f"- {instruction}")

            if args.auto:
                print(
                    f"Iniciando cenário '{scenario.slug}' automaticamente "
                    f"com {args.rounds} rodadas."
                )
            else:
                scenario_action = prompt_action(
                    f"Preparar cenário '{scenario.slug}' e iniciar as {args.rounds} rodadas",
                    allow_skip=True,
                )
                if scenario_action == "skip":
                    continue

            round_summaries = []
            scenario_start_ts = 0
            scenario_end_ts = 0

            for round_index in range(1, args.rounds + 1):
                summary = run_round(
                    scenario=scenario,
                    round_index=round_index,
                    total_rounds=args.rounds,
                    scenario_dir=scenario_dir,
                    duration=args.duration,
                    progress_step=args.progress_step,
                    threshold=args.threshold,
                    min_count=args.min_count,
                    auto_mode=args.auto,
                    auto_switch=args.auto_switch,
                )
                if summary is None:
                    continue

                if scenario_start_ts == 0:
                    scenario_start_ts = summary["start_ts"]
                scenario_end_ts = summary["end_ts"]
                round_summaries.append(summary)
                experiment_summary_rows.append(summary)

                if args.auto and args.round_gap > 0 and round_index < args.rounds:
                    auto_wait(
                        f"intervalo antes da próxima rodada de {scenario.slug}",
                        args.round_gap,
                        args.progress_step,
                    )

            write_csv(
                scenario_dir / "round_summary.csv",
                round_summaries,
                fieldnames=[
                    "scenario",
                    "round",
                    "start_ts",
                    "end_ts",
                    "start_iso",
                    "end_iso",
                    "duration_s",
                    "rows",
                    "nodes",
                    "edges",
                    "confirmed_by_data",
                    "weak_or_low_support",
                    "spurious_in_baseline",
                    "emergent_from_data",
                    "dataset_path",
                    "graph_path",
                    "adjacency_path",
                    "report_path",
                ],
            )

            if round_summaries:
                scenario_report = build_scenario_exports(
                    scenario=scenario,
                    scenario_dir=scenario_dir,
                    scenario_start_ts=scenario_start_ts,
                    scenario_end_ts=scenario_end_ts,
                    threshold=args.threshold,
                    min_count=args.min_count,
                    subset_sizes=subset_sizes,
                )
                write_json(scenario_dir / "scenario_report.json", scenario_report)
                scenario_reports.append(scenario_report)
                print(
                    f"cenário {scenario.slug}: rows_total="
                    f"{scenario_report['full_export']['rows']} | "
                    f"confirmed={scenario_report['full_export']['summary'].get('confirmed_by_data', 0)}"
                )

            if args.auto and args.scenario_gap > 0:
                if args.auto_switch:
                    clear_scenario_control()
                auto_wait(
                    f"transição para o próximo cenário após {scenario.slug}",
                    args.scenario_gap,
                    args.progress_step,
                )
            elif args.auto:
                print(f"cenário {scenario.slug} concluído, seguindo automaticamente")

    except KeyboardInterrupt:
        print("\nExecução interrompida pelo usuário.")
    finally:
        clear_scenario_control()

    write_csv(
        experiment_dir / "experiment_round_summary.csv",
        experiment_summary_rows,
        fieldnames=[
            "scenario",
            "round",
            "start_ts",
            "end_ts",
            "start_iso",
            "end_iso",
            "duration_s",
            "rows",
            "nodes",
            "edges",
            "confirmed_by_data",
            "weak_or_low_support",
            "spurious_in_baseline",
            "emergent_from_data",
            "dataset_path",
            "graph_path",
            "adjacency_path",
            "report_path",
        ],
    )
    write_json(
        experiment_dir / "experiment_report.json",
        {
            "generated_at": int(time.time()),
            "generated_at_iso": datetime.now().isoformat(timespec="seconds"),
            "experiment_dir": str(experiment_dir),
            "scenario_reports": scenario_reports,
            "total_rounds_recorded": len(experiment_summary_rows),
        },
    )

    print(f"\nExperimento preparado em: {experiment_dir}")
    print("Arquivos principais:")
    print(f"- {experiment_dir / 'experiment_manifest.json'}")
    print(f"- {experiment_dir / 'experiment_round_summary.csv'}")
    print(f"- {experiment_dir / 'experiment_report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
