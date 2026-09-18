#!/usr/bin/env python3
"""Run the local 36-cell native ns-3 energy calibration sweep.

This launcher is fail-closed and sequential: it never reuses a non-empty
cell directory and never starts the causal treatment. The autonomous local
dispatcher supplies the required user-delegated cgroup-v2 enforcement.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COLLECTION = ROOT / "scripts" / "run_greenran_tasam_3du_collection.sh"
WALL = ROOT / "scripts" / "wall_clock_collection_supervisor.py"
STOP = ROOT / "scripts" / "stop_tasam_article_ns3_collection.sh"
INFRA = ROOT / "src" / "greenran_infra_monitor.py"
NS3_BIN_BASE = ROOT / "ns-O-RAN-flexric/mmwave-LENA-oran/build/scratch/ns3.42-Energy_saving_with_cell_utilization_scenario"
SEEDS = (45, 46, 47)
POWERS = (25, 50, 75, 100)
ACTIVE_CELLS = (1, 2, 3)


def _resolve_ns3_binary() -> Path:
    """Prefer the current configured build over a legacy unsuffixed binary."""
    candidates = (Path(f"{NS3_BIN_BASE}-default"), NS3_BIN_BASE, Path(f"{NS3_BIN_BASE}-debug"))
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.resolve()
    return candidates[0].resolve()


def _run(command: list[str], env: dict[str, str], log: Path, *, timeout: float | None = None) -> int:
    with log.open("a", encoding="utf-8") as handle:
        handle.write("\n$ " + " ".join(command) + "\n")
        completed = subprocess.run(command, cwd=ROOT, env=env, stdout=handle, stderr=subprocess.STDOUT, timeout=timeout, check=False)
    return int(completed.returncode)


def _validate_local(path: Path) -> None:
    root = ROOT.resolve()
    if path.resolve() != root and root not in path.resolve().parents:
        raise RuntimeError(f"artefato fora do projeto: {path}")
    if "/run/media/" in str(path.resolve()):
        raise RuntimeError(f"caminho externo proibido: {path}")


def _validate_native_energy(energy_dir: Path, power: int, active: int) -> None:
    files = sorted(energy_dir.glob("energyfilecell*.csv"))
    if len(files) != 3:
        raise RuntimeError(f"séries nativas incompletas: {len(files)}/3 em {energy_dir}")
    observed_active = 0
    required = {
        "Time",
        "NetEnergy",
        "TxPowerPercent",
        "ActiveCell",
    }
    for path in files:
        with path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        if len(rows) < 3:
            raise RuntimeError(f"série nativa sem três amostras: {path}")
        if any(not required.issubset(row) for row in rows):
            raise RuntimeError(f"colunas nativas ausentes: {path}")
        times = [float(row["Time"]) for row in rows]
        energies = [float(row["NetEnergy"]) for row in rows]
        powers = [float(row["TxPowerPercent"]) for row in rows]
        states = [float(row["ActiveCell"]) for row in rows]
        if any(not math.isfinite(value) for value in times + energies + powers + states):
            raise RuntimeError(f"amostra nativa não finita: {path}")
        if times[-1] <= times[0] or any(b <= a for a, b in zip(times, times[1:])):
            raise RuntimeError(f"timestamps nativos inválidos: {path}")
        if any(abs(value - power) > 1e-6 for value in powers):
            raise RuntimeError(f"potência solicitada não observada: {path}")
        if any(value not in (0.0, 1.0) for value in states):
            raise RuntimeError(f"estado ativo inválido: {path}")
        observed_active += int(states[-1] > 0.5)
    if observed_active != active:
        raise RuntimeError(
            f"contagem de células ativas inconsistente: observado={observed_active} esperado={active}"
        )


def run_cell(root: Path, seed: int, power: int, active: int, duration: float) -> dict:
    cell = root / f"seed_{seed:04d}" / f"power_{power:03d}_active_{active}" / "cells" / "cell"
    _validate_local(cell)
    if cell.exists() and any(cell.iterdir()):
        raise RuntimeError(f"célula não vazia; preservada sem sobrescrita: {cell}")
    cell.mkdir(parents=True, exist_ok=False)
    energy_dir = cell / "ns3_energy"
    env = os.environ.copy()
    env.update({
        "GREENRAN_PROJECT_DIR": str(ROOT),
        "GREENRAN_STATE_DIR": str(cell),
        "GREENRAN_FIXED_SCENARIO_CONFIG": str(ROOT / "config" / "greenran_fixed_scenario.json"),
        "GREENRAN_NS3_BIN": str(_resolve_ns3_binary()),
        "GREENRAN_NS3_CWD": str(ROOT / "ns-O-RAN-flexric/mmwave-LENA-oran"),
        "GREENRAN_NS3_UE_COUNT": "20",
        "GREENRAN_NS3_CAMERA_UE_COUNT": "3",
        "GREENRAN_NS3_VEHICLE_UE_COUNT": "5",
        "GREENRAN_NS3_MMWAVE_ENB_NODES": "3",
        "GREENRAN_NS3_ACTIVE_CELLS": str(active),
        "GREENRAN_NS3_RNG_RUN": str(seed),
        "GREENRAN_NS3_FIXED_POWER_PERCENT": str(power),
        "GREENRAN_NS3_ENABLE_ENERGY_CSV": "1",
        "GREENRAN_NS3_ENERGY_OUTPUT_DIR": str(energy_dir),
        "GREENRAN_NS3_SINGLE_RUN": "1",
        "GREENRAN_SIM_TIME": str(duration),
        "GREENRAN_COLLECTION_EVENT_PROFILE": "tasam_training_balanced_v3",
        "GREENRAN_RAN_PRESSURE_PROFILE": "tasam_training_balanced_v3",
        "GREENRAN_REQUIRE_REAL_PDCP": "1",
        "GREENRAN_REAL_ONLY": "1",
        "GREENRAN_START_RIC": "1",
        "GREENRAN_NS3_E2DU_ENABLED": "true",
        "GREENRAN_NS3_E2NR_ENABLED": "false",
        "GREENRAN_CGROUP_ENFORCE": "1",
        "GREENRAN_CGROUP_ALLOW_UNENFORCED": "0",
        "GREENRAN_TASAM_EXPORT_ENABLED": "0",
        "GREENRAN_TASAM_EXPORT_ALLOW_PROXY": "0",
        "GREENRAN_DB_SNAPSHOT_INTERVAL": "999999",
        "GREENRAN_NS3_ENABLE_POSITION_CSV": "0",
        "GREENRAN_LOCAL_ONLY": "1",
    })
    log = cell / "calibration_console.log"
    # The ns-3 scenario is intentionally short in simulated time, but its
    # real-time startup/teardown can take well over a minute on this host.
    # Keep the wall-clock budget comfortably above the observed runtime so
    # the third native-energy sample is not cut off before ns-3 exits.
    monitor = subprocess.Popen(
        [sys.executable, str(INFRA), "--run-dir", str(cell), "--duration-seconds", str(duration + 180), "--interval-seconds", "1"],
        cwd=ROOT, env=env, stdout=log.open("a", encoding="utf-8"), stderr=subprocess.STDOUT,
    )
    code = _run(["bash", str(COLLECTION)], env, log, timeout=180)
    if code != 0:
        raise RuntimeError(f"coleta falhou na célula {cell}: código {code}")
    wall_env = env.copy()
    wall_code = _run([sys.executable, str(WALL), "--state-dir", str(cell), "--duration-seconds", str(duration + 150), "--post-stop-grace-seconds", "1"], wall_env, log, timeout=duration + 210)
    wall_status = json.loads((cell / "wall_clock_status.json").read_text(encoding="utf-8")) if (cell / "wall_clock_status.json").is_file() else {}
    if wall_code != 0 or wall_status.get("phase") != "finished":
        raise RuntimeError(f"supervisor não finalizou na célula {cell}: code={wall_code}")
    _run(["bash", str(STOP), "--state-dir", str(cell)], env, log, timeout=30)
    try:
        monitor.wait(timeout=30)
    except subprocess.TimeoutExpired:
        monitor.kill()
        monitor.wait(timeout=5)
        raise RuntimeError(f"monitor cgroup não terminou na célula {cell}")
    if monitor.returncode != 0:
        raise RuntimeError(f"monitor cgroup incompleto na célula {cell}: código {monitor.returncode}")
    _validate_native_energy(energy_dir, power, active)
    payload = {
        "schema": "greenran.energy_calibration.cell.v1",
        "seed": seed,
        "power_percent": power,
        "active_cells": active,
        "duration_s": duration,
        "status": "finished",
        "pdcp_required": True,
        "cgroup_required": True,
        "energy_reference_source": "ns3_device_energy_model",
    }
    (cell / "calibration_manifest.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    infra = cell / "infrastructure_metrics.json"
    if not infra.is_file():
        raise RuntimeError(f"métricas cgroup ausentes na célula {cell}")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=ROOT / "runs" / "tasam_energy_calibration_sim_v5")
    parser.add_argument(
        "--duration-seconds",
        type=float,
        default=0.1,
        help="short native-energy window; the scenario emits three uniform samples",
    )
    parser.add_argument("--limit", type=int, default=36, help="use a smaller explicit limit only for smoke tests")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    root = args.output_root.resolve()
    _validate_local(root)
    binary = _resolve_ns3_binary()
    config = ROOT / "config/greenran_fixed_scenario.json"
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise SystemExit(f"pré-voo falhou: binário ns-3 ausente/não executável: {binary}")
    if not config.is_file():
        raise SystemExit(f"pré-voo falhou: configuração ausente: {config}")
    if os.environ.get("GREENRAN_CGROUP_ENFORCE", "1") != "1" or os.environ.get("GREENRAN_CGROUP_ALLOW_UNENFORCED", "0") != "0":
        raise SystemExit("pré-voo falhou: cgroup obrigatório não está em modo fail-closed")
    root.mkdir(parents=True, exist_ok=True)
    jobs = [(seed, power, active) for seed in SEEDS for power in POWERS for active in ACTIVE_CELLS]
    if args.limit < 1 or args.limit > len(jobs):
        parser.error("limit deve estar entre 1 e 36")
    if args.dry_run:
        print(json.dumps({"output_root": str(root), "jobs": jobs[:args.limit], "local_only": True}, ensure_ascii=False))
        return 0
    summary = []
    for seed, power, active in jobs[:args.limit]:
        summary.append(run_cell(root, seed, power, active, args.duration_seconds))
        (root / "sweep_progress.json").write_text(json.dumps({"completed": summary}, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"schema": "greenran.energy_calibration.sweep.v1", "completed": len(summary), "expected": len(jobs[:args.limit]), "output_root": str(root)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
