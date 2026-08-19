#!/usr/bin/env python3
"""
Interactive experiment runner aligned with conflitos collection protocol.

What it does:
- guides 7 GreenRAN scenarios with 20 rounds each by default;
- records exact start/end timestamps for every round;
- exports a conflict dataset/graph for each round without mixing windows;
- learns the conflict matrix for each round using threshold 0.5;
- exports scenario-wide datasets and comparison subsets (50/150/450 rows).

What it does not do yet:
- GraphSAGE training with epochs. The current learner is heuristic/statistical,
  so we only align dataset sizes and threshold from conflitos. Epoch-based GNN
  training remains a later phase.
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from greenran_paths import (  # noqa: E402
    ARTICLE00_SCENARIO_CONTROL_PATH,
    RAPP_DB_PATH,
    STATE_DIR,
)

EXPORT_SCRIPT = PROJECT_ROOT / "scripts" / "export_conflict_dataset.py"
LEARN_SCRIPT = PROJECT_ROOT / "scripts" / "learn_conflict_matrix.py"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "runs" / "experimentos_conflitos"
SCENARIO_CONTROL_PATH = ARTICLE00_SCENARIO_CONTROL_PATH

DEFAULT_SUBSET_SIZES = (50, 150, 450)
DEFAULT_THRESHOLD = 0.5
DEFAULT_MIN_COUNT = 3
DEFAULT_ROUNDS = 20
DEFAULT_DURATION = 600
DEFAULT_PROGRESS_STEP = 60
DEFAULT_ROUND_GAP = 0
DEFAULT_SCENARIO_GAP = 0
TARGET_ROWS_EXEMPT_SCENARIOS = {"baseline_saude"}
GRACE_PERIOD_SEC = 10

SNAPSHOT_FILES = {
    "app1_monitoring_snapshot": STATE_DIR / "app1_vigilancia" / "monitoring_snapshot.json",
    "app2_monitoring_snapshot": STATE_DIR / "app2_monitoramento" / "monitoring_snapshot.json",
    "extended_metrics": STATE_DIR / "xapp_metrics" / "extended_metrics.json",
    "rapp_decisions": STATE_DIR / "rapp_decisions.jsonl",
    "scenario_control": SCENARIO_CONTROL_PATH,
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
    Scenario(
        slug="vehicle_warning",
        title="Vehicle warning",
        goal="forçar guarda veicular sem entrar em estado crítico total",
        instructions=(
            "mantenha App1 e App2 saudáveis",
            "leve o ego vehicle para latência >= 50ms ou packet loss >= 2%",
            "gere pelo menos 1 veículo em risco médio para treinar o estado de guarda",
        ),
    ),
    Scenario(
        slug="vehicle_critical",
        title="Vehicle critical",
        goal="forçar bloqueio por segurança veicular",
        instructions=(
            "mantenha App1 e App2 saudáveis",
            "leve o ego vehicle para latência >= 100ms ou packet loss >= 5%",
            "gere risco alto ou autonomia degradada no ego vehicle",
        ),
    ),
    Scenario(
        slug="vehicle_implicito",
        title="Vehicle implícito",
        goal="gerar conflito implícito com KPI global saudável e ego degradado",
        instructions=(
            "preserve CVaR/P95 global do sistema saudável",
            "degrade apenas o ego vehicle localmente",
            "o objetivo é ensinar conflito implícito específico do domínio veicular",
        ),
    ),
    Scenario(
        slug="vehicle_recovery",
        title="Vehicle recovery",
        goal="capturar recuperação do veículo após estado crítico",
        instructions=(
            "comece com ego vehicle crítico ou autonomia degradada",
            "recupere para estado saudável no meio da rodada",
            "use isso para treinar histerese e estabilização do domínio veicular",
        ),
    ),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run conflict-aligned GreenRAN conflict collection in guided rounds."
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
        help="comma-separated comparison dataset sizes",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD,
        help="learner threshold aligned to the conflict protocol",
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
    parser.add_argument(
        "--continue",
        dest="continue_experiment",
        action="store_true",
        help="continue from the last incomplete experiment instead of creating a new one",
    )
    parser.add_argument(
        "--target-rows-per-scenario",
        type=int,
        default=0,
        help="keep collecting until this number of conflict rows is reached for each scenario (except baseline_saude)",
    )
    parser.add_argument(
        "--max-rounds-per-scenario",
        type=int,
        default=0,
        help="hard cap for rounds per scenario when --target-rows-per-scenario is enabled; 0 means auto-derive",
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


def find_last_experiment(output_root: Path) -> Path | None:
    """Find the most recent experiment directory with collected data."""
    if not output_root.exists():
        return None
    experiments = sorted(
        [d for d in output_root.iterdir() if d.is_dir() and d.name.endswith("_conflict_protocol")],
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )
    
    for exp in experiments:
        completed = load_completed_scenarios(exp)
        total_rows = sum(c.get("rows", 0) for c in completed.values())
        if total_rows > 0:
            return exp
    
    return experiments[0] if experiments else None


def load_completed_scenarios(experiment_dir: Path) -> dict[str, dict]:
    """Load existing round data from an experiment to determine what was already completed."""
    completed = {}
    for scenario_dir in sorted(experiment_dir.iterdir()):
        if not scenario_dir.is_dir():
            continue
        if scenario_dir.name == "experiment_manifest.json" or scenario_dir.name.endswith(".json"):
            continue
        rounds_dir = scenario_dir / "rounds"
        if not rounds_dir.exists():
            continue
        
        rounds = sorted(rounds_dir.glob("round_*"))
        if not rounds:
            continue
        
        rows = 0
        confirmed = 0
        weak = 0
        round_count = 0
        last_round = None
        
        for r in rounds:
            summary_path = r / "round_summary.json"
            if summary_path.exists():
                with open(summary_path) as f:
                    s = json.load(f)
                rows += s.get("rows", 0)
                confirmed += s.get("confirmed_by_data", 0)
                weak += s.get("weak_or_low_support", 0)
                round_count += 1
                last_round = int(s.get("round", 0))
        
        completed[scenario_dir.name] = {
            "rows": rows,
            "confirmed": confirmed,
            "weak": weak,
            "rounds": round_count,
            "last_round": last_round,
        }
    
    return completed


def load_round_summaries_for_scenario(experiment_dir: Path, scenario_slug: str) -> list[dict]:
    """Load all persisted round_summary.json entries for a scenario."""
    scenario_dir = experiment_dir / scenario_slug
    rounds_dir = scenario_dir / "rounds"
    if not rounds_dir.exists():
        return []

    summaries: list[dict] = []
    for round_dir in sorted(rounds_dir.glob("round_*")):
        summary_path = round_dir / "round_summary.json"
        if not summary_path.exists():
            continue
        with summary_path.open() as handle:
            summaries.append(json.load(handle))
    return summaries


def is_scenario_complete(scenario_slug: str, data: dict, target_rows: int, max_rounds: int) -> bool:
    """Check if a scenario is complete based on rows and round count."""
    scenario_data = data.get(scenario_slug, {})
    rows = scenario_data.get("rows", 0)
    rounds = scenario_data.get("rounds", 0)
    
    rows_complete = target_rows > 0 and rows >= target_rows
    rounds_complete = rounds >= max_rounds
    
    return rows_complete or rounds_complete


def should_stop_scenario_collection(
    *,
    target_enabled: bool,
    cumulative_rows: int,
    target_rows: int,
    round_index: int,
    minimum_rounds: int,
    max_rounds: int,
) -> tuple[bool, str | None]:
    """Return whether a scenario should stop and, when applicable, why."""
    if target_enabled and cumulative_rows >= target_rows:
        return True, "target_reached"

    if not target_enabled and round_index >= minimum_rounds:
        return True, "minimum_rounds_done"

    if round_index >= max_rounds:
        if target_enabled:
            return True, "round_cap_before_target"
        return True, "round_cap"

    return False, None


def get_next_round_index(experiment_dir: Path, scenario_slug: str) -> int:
    """Get the next round index to continue from."""
    scenario_dir = experiment_dir / scenario_slug
    rounds_dir = scenario_dir / "rounds"
    if not rounds_dir.exists():
        return 1
    
    existing_rounds = sorted(rounds_dir.glob("round_*"))
    if not existing_rounds:
        return 1
    
    max_round = 0
    for r in existing_rounds:
        summary_path = r / "round_summary.json"
        if summary_path.exists():
            with open(summary_path) as f:
                s = json.load(f)
            round_num = s.get("round", 0)
            if round_num > max_round:
                max_round = round_num
    
    return max_round + 1


def is_rapp_alive() -> bool:
    """Check if rApp orchestrator is running and Data Lake is accessible."""
    result = subprocess.run(
        ["pgrep", "-f", "rapp_orchestrator.py"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return False
    db_path = Path(RAPP_DB_PATH)
    if not db_path.exists():
        return False
    return True


def check_rapp_health_and_abort(round_dir: Path | None = None) -> bool:
    """Check rApp health; if dead, write abort marker and return False."""
    if is_rapp_alive():
        return True
    print("  AVISO: rApp não está ativo! Abortando coleta de conflitos.")
    if round_dir:
        abort_marker = round_dir / "rapp_aborted.json"
        write_json(abort_marker, {
            "timestamp": int(time.time()),
            "reason": "rApp orchestrator não está em execução",
            "is_rapp_alive": False,
        })
    return False


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


def scenario_export_focus(scenario: Scenario) -> str:
    if scenario.slug.startswith("vehicle_"):
        return "vehicle"
    return "all"


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


ROUND_SUMMARY_FIELDNAMES = [
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
    "cumulative_rows",
    "dataset_path",
    "graph_path",
    "adjacency_path",
    "report_path",
    "export_focus",
]


def write_scenario_control(payload: dict) -> None:
    SCENARIO_CONTROL_PATH.parent.mkdir(parents=True, exist_ok=True)
    with SCENARIO_CONTROL_PATH.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)


def clear_scenario_control() -> None:
    if SCENARIO_CONTROL_PATH.exists():
        SCENARIO_CONTROL_PATH.unlink()


def scenario_capture_warmup_seconds(scenario: Scenario) -> int:
    # Some scenarios are sensitive to state carryover from the previous round.
    # Give the runtime a short settling window before opening the capture
    # interval so the dataset does not mix KPI regimes across domains.
    if scenario.slug in {
        "app1_latencia",
        "app2_degradado_leve",
        "app2_degradado_critico",
        "vehicle_critical",
        "vehicle_implicito",
        "vehicle_recovery",
    }:
        return 15
    return 0


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
        "throughput_source": "scenario_control",
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
    vehicle_healthy = {
        "enabled": True,
        "total_vehicles": 5,
        "high_risk_vehicles": 0,
        "medium_risk_vehicles": 0,
        "degraded_autonomy_vehicles": 0,
        "ego_latency_ms": 18.0,
        "traffic_latency_ms": 12.0,
        "ego_packet_loss_percent": 0.2,
        "traffic_packet_loss_percent": 0.1,
        "max_speed_mps": 7.0,
        "mode": "healthy",
    }

    if scenario.slug == "baseline_saude":
        return [{"offset_s": 0, "app1": app1_healthy, "app2": app2_healthy, "vehicle": vehicle_healthy}]

    if scenario.slug == "app1_throughput":
        return [
            {
                "offset_s": 0,
                "app1": {
                    **app1_healthy,
                    # Keep the throughput scenario in a single degraded regime
                    # so the dataset does not alternate between guard and
                    # blocked actions across rounds.
                    "throughput_mbps": 24.0,
                    "avg_throughput_mbps": 27.0,
                    "latency_ms": 22.0,
                },
                "app2": app2_healthy,
                "vehicle": vehicle_healthy,
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
                    # Keep throughput comfortably healthy so the scenario is
                    # dominated by camera latency rather than mixed SLA causes.
                    "throughput_mbps": 33.0,
                    "avg_throughput_mbps": 35.0,
                    "latency_ms": target_latency,
                    "critical_cameras": critical,
                },
                "app2": app2_healthy,
                "vehicle": vehicle_healthy,
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
                    # Stay below the packet-loss trigger so the scenario is
                    # dominated by connected-ratio degradation only.
                    "packet_loss_percent": 4.4,
                    "delivery_success_percent": 95.1,
                    "avg_latency_ms": 225.0,
                    "avg_rssi_dbm": -94.0,
                    "mode": "degraded_light",
                },
                "vehicle": vehicle_healthy,
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
                    # Keep the critical state centered on connected-ratio loss
                    # to avoid adding a concurrent guard path via packet loss.
                    "packet_loss_percent": 4.8,
                    "delivery_success_percent": 87.5,
                    "avg_latency_ms": 360.0,
                    "avg_rssi_dbm": -101.0,
                    "avg_battery_percent": 58.0,
                    "mode": "degraded_critical",
                },
                "vehicle": vehicle_healthy,
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
                "vehicle": vehicle_healthy,
            }
        ]

    if scenario.slug == "recuperacao":
        midpoint = max(1, duration // 2)
        return [
            {
                "offset_s": 0,
                # Mantem App1 saudável para que a recuperação multiapp seja
                # dominada pelo caminho App2, e não pelo SLA de câmera.
                "app1": app1_healthy,
                "app2": {
                    **app2_healthy,
                    "connected_sensors": 14,
                    "error_sensors": 3,
                    "low_battery_sensors": 1,
                    "packet_loss_percent": 11.8,
                    "delivery_success_percent": 87.5,
                    "avg_latency_ms": 360.0,
                    "avg_rssi_dbm": -101.0,
                    "avg_battery_percent": 58.0,
                    "mode": "recovery_start",
                },
                "vehicle": vehicle_healthy,
            },
            {"offset_s": midpoint, "app1": app1_healthy, "app2": app2_healthy, "vehicle": vehicle_healthy},
        ]

    if scenario.slug == "vehicle_warning":
        return [
            {
                "offset_s": 0,
                "app1": app1_healthy,
                "app2": app2_healthy,
                "vehicle": {
                    **vehicle_healthy,
                    "medium_risk_vehicles": 1,
                    "ego_latency_ms": 58.0 if round_index % 2 else 52.0,
                    "ego_packet_loss_percent": 2.6,
                    "mode": "vehicle_warning",
                },
            }
        ]

    if scenario.slug == "vehicle_critical":
        return [
            {
                "offset_s": 0,
                "app1": app1_healthy,
                "app2": app2_healthy,
                "vehicle": {
                    **vehicle_healthy,
                    "high_risk_vehicles": 1,
                    "degraded_autonomy_vehicles": 1,
                    "ego_latency_ms": 126.0,
                    "ego_packet_loss_percent": 5.8,
                    "mode": "vehicle_critical",
                },
            }
        ]

    if scenario.slug == "vehicle_implicito":
        return [
            {
                "offset_s": 0,
                "app1": {
                    **app1_healthy,
                    "throughput_mbps": 32.0,
                    "avg_throughput_mbps": 34.0,
                    "latency_ms": 18.0,
                },
                "app2": app2_healthy,
                "vehicle": {
                    **vehicle_healthy,
                    "medium_risk_vehicles": 1,
                    "ego_latency_ms": 68.0,
                    "ego_packet_loss_percent": 2.4,
                    "mode": "vehicle_implicit",
                },
            }
        ]

    if scenario.slug == "vehicle_recovery":
        midpoint = max(1, duration // 2)
        return [
            {
                "offset_s": 0,
                "app1": app1_healthy,
                "app2": app2_healthy,
                "vehicle": {
                    **vehicle_healthy,
                    "high_risk_vehicles": 1,
                    "degraded_autonomy_vehicles": 1,
                    "ego_latency_ms": 118.0,
                    "ego_packet_loss_percent": 5.2,
                    "mode": "vehicle_recovery_start",
                },
            },
            {
                "offset_s": midpoint,
                "app1": app1_healthy,
                "app2": app2_healthy,
                "vehicle": vehicle_healthy,
            },
        ]

    return [{"offset_s": 0, "app1": app1_healthy, "app2": app2_healthy, "vehicle": vehicle_healthy}]


def apply_control_stage(
    scenario: Scenario,
    round_index: int,
    total_rounds: int,
    duration: int,
    stage: dict,
) -> None:
    payload = {
        "schema": "greenran.scenario_control.v1",
        "generated_at": int(time.time()),
        "generated_at_iso": datetime.now().isoformat(timespec="seconds"),
        "scenario": scenario.slug,
        "round": round_index,
        "rounds_total": total_rounds,
        "duration_s": duration,
        "app1_camera_override": stage.get("app1", {}),
        "app2_sensor_override": stage.get("app2", {}),
        "vehicle_override": stage.get("vehicle", {}),
    }
    write_scenario_control(payload)


def scenario_manifest(scenario: Scenario) -> dict:
    return {
        "slug": scenario.slug,
        "title": scenario.title,
        "goal": scenario.goal,
        "instructions": list(scenario.instructions),
    }


def scenario_uses_target_rows(scenario: Scenario, target_rows: int) -> bool:
    return target_rows > 0 and scenario.slug not in TARGET_ROWS_EXEMPT_SCENARIOS


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
    cumulative_rows_before: int,
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
    warmup_s = scenario_capture_warmup_seconds(scenario) if auto_switch else 0

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
        if warmup_s > 0:
            print(f"  aguardando {warmup_s}s para estabilizar o cenário antes da captura")
            time.sleep(warmup_s)

    start_ts = int(time.time()) - GRACE_PERIOD_SEC

    write_json(
        round_dir / "round_window.json",
        {
            "scenario": scenario.slug,
            "round": round_index,
            "start_ts": start_ts,
            "start_iso": iso_from_ts(start_ts),
            "duration_s": duration,
            "capture_warmup_s": warmup_s,
        },
    )
    copy_snapshot_files(round_dir / "snapshots_before")

    countdown(duration, progress_step, transitions=transitions)
    end_ts = int(time.time())

    if not check_rapp_health_and_abort(round_dir):
        summary = {
            "scenario": scenario.slug,
            "round": round_index,
            "start_ts": start_ts,
            "end_ts": end_ts,
            "start_iso": iso_from_ts(start_ts),
            "end_iso": iso_from_ts(end_ts),
            "duration_s": max(0, end_ts - start_ts),
            "rows": 0,
            "nodes": 0,
            "edges": 0,
            "confirmed_by_data": 0,
            "weak_or_low_support": 0,
            "spurious_in_baseline": 0,
            "emergent_from_data": 0,
            "dataset_path": "",
            "graph_path": "",
            "adjacency_path": "",
            "report_path": "",
            "cumulative_rows": cumulative_rows_before,
            "export_focus": "all",
            "rapp_aborted": True,
        }
        write_json(round_dir / "round_summary.json", summary)
        print(f"  resumo rodada: rows=0 (rApp abortado)")
        return summary

    dataset_path = round_dir / "conflict_dataset.csv"
    graph_path = round_dir / "conflict_graph.json"
    adjacency_path = round_dir / "conflict_adjacency.json"
    report_path = round_dir / "conflict_report.json"
    export_focus = scenario_export_focus(scenario)

    export_command = [
        sys.executable,
        str(EXPORT_SCRIPT),
        "--since-ts",
        str(start_ts),
        "--until-exclusive-ts",
        str(end_ts),
        "--dataset",
        str(dataset_path),
        "--graph",
        str(graph_path),
    ]
    if export_focus != "all":
        export_command.extend(["--focus", export_focus])

    export_result = run_json_command(export_command)
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
        "cumulative_rows": cumulative_rows_before + int(export_result.get("rows", 0)),
        "export_focus": export_focus,
    }
    write_json(round_dir / "round_summary.json", summary)
    print(
        f"  resumo rodada: rows={summary['rows']} | "
        f"confirmed={summary['confirmed_by_data']} | weak={summary['weak_or_low_support']}"
    )
    if auto_switch:
        copy_snapshot_files(round_dir / "snapshots_after_control")
    return summary


def combine_round_datasets(round_summaries: list[dict], destination_csv: Path) -> int:
    destination_csv.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = None
    combined_rows = []
    for summary in round_summaries:
        dataset_path = Path(summary["dataset_path"])
        with dataset_path.open() as src:
            reader = csv.DictReader(src)
            if fieldnames is None:
                fieldnames = reader.fieldnames or []
            combined_rows.extend(reader)

    with destination_csv.open("w", newline="") as dst:
        writer = csv.DictWriter(dst, fieldnames=fieldnames or [])
        writer.writeheader()
        writer.writerows(combined_rows)

    return len(combined_rows)


def rebuild_graph_from_dataset(dataset_path: Path, graph_path: Path) -> dict:
    return run_json_command(
        [
            sys.executable,
            str(EXPORT_SCRIPT),
            "--dataset-input",
            str(dataset_path),
            "--graph",
            str(graph_path),
        ]
    )


def build_scenario_exports(
    scenario: Scenario,
    scenario_dir: Path,
    scenario_start_ts: int,
    scenario_end_ts: int,
    threshold: float,
    min_count: int,
    subset_sizes: tuple[int, ...],
    round_summaries: list[dict],
) -> dict:
    exports_dir = scenario_dir / "scenario_exports"
    exports_dir.mkdir(parents=True, exist_ok=True)

    full_dataset = exports_dir / "conflict_dataset_full.csv"
    full_graph = exports_dir / "conflict_graph_full.json"
    full_adjacency = exports_dir / "conflict_adjacency_full.json"
    full_report = exports_dir / "conflict_report_full.json"

    combined_rows = combine_round_datasets(round_summaries, full_dataset)
    export_result = rebuild_graph_from_dataset(full_dataset, full_graph)
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
            "rows": combined_rows,
            "dataset_path": str(full_dataset),
            "graph_path": str(full_graph),
            "adjacency_path": str(full_adjacency),
            "report_path": str(full_report),
            "export_focus": scenario_export_focus(scenario),
            "summary": read_json(full_report).get("summary", {}),
        },
        "conflict_alignment": {
            "dataset_sizes": list(subset_sizes),
            "threshold": threshold,
            "recommended_gnn_epochs": 600,
            "epochs_applied_in_current_runner": None,
            "note": (
                "dataset sizes and threshold are aligned with conflitos; "
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

    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    
    completed_scenarios = {}
    is_continuing = False

    if args.continue_experiment:
        last_experiment = find_last_experiment(output_root)
        if last_experiment and last_experiment.exists():
            print(f"[continuar] Usando experimento existente: {last_experiment}")
            experiment_dir = last_experiment
            completed_scenarios = load_completed_scenarios(experiment_dir)
            print(f"[continuar] Cenários encontrados: {list(completed_scenarios.keys())}")
            is_continuing = True
        else:
            print("[continuar] Nenhum experimento anterior encontrado, criando novo...")
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            experiment_dir = output_root / f"{timestamp}_conflict_protocol"
            experiment_dir.mkdir(parents=True, exist_ok=True)
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        experiment_dir = output_root / f"{timestamp}_conflict_protocol"
        experiment_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "schema": "greenran.conflict_protocol.v1",
        "generated_at": int(time.time()),
        "generated_at_iso": datetime.now().isoformat(timespec="seconds"),
        "config": {
            "rounds_per_scenario": args.rounds,
            "target_rows_per_scenario": args.target_rows_per_scenario,
            "max_rounds_per_scenario": args.max_rounds_per_scenario,
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
    print("Protocolo de conflitos:")
    print(f"- threshold: {args.threshold}")
    print(f"- subsets: {', '.join(str(size) for size in subset_sizes)}")
    print("- epochs GNN de referência: 600 (não aplicados neste runner atual)")
    print(f"- modo automático: {'sim' if args.auto else 'não'}")
    print(f"- troca automática de cenário: {'sim' if args.auto_switch else 'não'}")
    if args.target_rows_per_scenario > 0:
        print(f"- meta de linhas por cenário: {args.target_rows_per_scenario}")
        if args.max_rounds_per_scenario > 0:
            print(f"- limite máximo de rodadas por cenário: {args.max_rounds_per_scenario}")
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

            target_enabled = scenario_uses_target_rows(scenario, args.target_rows_per_scenario)
            max_rounds = args.max_rounds_per_scenario
            if max_rounds <= 0:
                max_rounds = max(args.rounds, args.rounds * 3 if target_enabled else args.rounds)

            if is_continuing and scenario.slug in completed_scenarios:
                scenario_data = completed_scenarios[scenario.slug]
                if is_scenario_complete(scenario.slug, completed_scenarios, args.target_rows_per_scenario, max_rounds):
                    persisted_round_summaries = load_round_summaries_for_scenario(experiment_dir, scenario.slug)
                    print(f"\n=== {scenario.title} ===")
                    print(f"  [continuar] Cenário já completo: {scenario_data['rounds']} rodadas, {scenario_data['rows']} rows")
                    if persisted_round_summaries:
                        write_csv(
                            scenario_dir / "round_summary.csv",
                            persisted_round_summaries,
                            fieldnames=ROUND_SUMMARY_FIELDNAMES,
                        )
                        experiment_summary_rows.extend(persisted_round_summaries)
                        scenario_report = build_scenario_exports(
                            scenario=scenario,
                            scenario_dir=scenario_dir,
                            scenario_start_ts=min(int(item.get("start_ts", 0)) for item in persisted_round_summaries),
                            scenario_end_ts=max(int(item.get("end_ts", 0)) for item in persisted_round_summaries),
                            threshold=args.threshold,
                            min_count=args.min_count,
                            subset_sizes=subset_sizes,
                            round_summaries=persisted_round_summaries,
                        )
                        write_json(scenario_dir / "scenario_report.json", scenario_report)
                        scenario_reports.append(scenario_report)
                        print(
                            f"  [continuar] Relatório consolidado regenerado: "
                            f"{scenario_report['full_export']['rows']} rows"
                        )
                    print(f"  Pulando para o próximo cenário...")
                    continue
                else:
                    round_index = get_next_round_index(experiment_dir, scenario.slug)
                    cumulative_rows = scenario_data.get("rows", 0)
                    persisted_round_summaries = load_round_summaries_for_scenario(experiment_dir, scenario.slug)
                    print(f"\n=== {scenario.title} ===")
                    print(f"  [continuar] Continuando da rodada {round_index} ({cumulative_rows} rows já coletados)")
            else:
                print(f"\n=== {scenario.title} ===")
                round_index = 1
                cumulative_rows = 0
                persisted_round_summaries = []
             
            print(f"Objetivo: {scenario.goal}")
            for instruction in scenario.instructions:
                print(f"- {instruction}")

            if args.auto:
                print(
                    f"Iniciando cenário '{scenario.slug}' automaticamente "
                    f"com mínimo de {args.rounds} rodadas."
                )
            else:
                scenario_action = prompt_action(
                    f"Preparar cenário '{scenario.slug}' e iniciar as {args.rounds} rodadas",
                    allow_skip=True,
                )
                if scenario_action == "skip":
                    continue

            round_summaries = list(persisted_round_summaries)
            scenario_start_ts = min((int(item.get("start_ts", 0)) for item in round_summaries), default=0)
            scenario_end_ts = max((int(item.get("end_ts", 0)) for item in round_summaries), default=0)

            while True:
                summary = run_round(
                    scenario=scenario,
                    round_index=round_index,
                    total_rounds=max_rounds if target_enabled else args.rounds,
                    scenario_dir=scenario_dir,
                    duration=args.duration,
                    progress_step=args.progress_step,
                    threshold=args.threshold,
                    min_count=args.min_count,
                    auto_mode=args.auto,
                    auto_switch=args.auto_switch,
                    cumulative_rows_before=cumulative_rows,
                )
                if summary is None:
                    round_index += 1
                    if not target_enabled and round_index > args.rounds:
                        break
                    if target_enabled and round_index > max_rounds:
                        break
                    continue

                if scenario_start_ts == 0:
                    scenario_start_ts = summary["start_ts"]
                scenario_end_ts = summary["end_ts"]
                cumulative_rows += int(summary["rows"])
                summary["cumulative_rows"] = cumulative_rows
                round_summaries.append(summary)
                experiment_summary_rows.append(summary)

                if target_enabled:
                    print(
                        f"  progresso {scenario.slug}: {cumulative_rows}/{args.target_rows_per_scenario} linhas"
                    )

                should_stop, stop_reason = should_stop_scenario_collection(
                    target_enabled=target_enabled,
                    cumulative_rows=cumulative_rows,
                    target_rows=args.target_rows_per_scenario,
                    round_index=round_index,
                    minimum_rounds=args.rounds,
                    max_rounds=max_rounds,
                )

                if should_stop:
                    if stop_reason == "round_cap_before_target":
                        print(
                            f"  meta de {args.target_rows_per_scenario} linhas não atingida em {scenario.slug}; "
                            f"parando no limite de {max_rounds} rodadas com {cumulative_rows} linhas"
                        )
                    break

                if args.auto and args.round_gap > 0:
                    auto_wait(
                        f"intervalo antes da próxima rodada de {scenario.slug}",
                        args.round_gap,
                        args.progress_step,
                    )
                round_index += 1

            write_csv(
                scenario_dir / "round_summary.csv",
                round_summaries,
                fieldnames=ROUND_SUMMARY_FIELDNAMES,
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
                    round_summaries=round_summaries,
                )
                write_json(scenario_dir / "scenario_report.json", scenario_report)
                scenario_reports.append(scenario_report)
                print(
                    f"cenário {scenario.slug}: rows_total="
                    f"{scenario_report['full_export']['rows']} | "
                    f"confirmed={scenario_report['full_export']['summary'].get('confirmed_by_data', 0)}"
                )
                if target_enabled:
                    print(
                        f"  fechamento {scenario.slug}: {scenario_report['full_export']['rows']}/"
                        f"{args.target_rows_per_scenario} linhas"
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
            "cumulative_rows",
            "dataset_path",
            "graph_path",
            "adjacency_path",
            "report_path",
            "export_focus",
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
