#!/usr/bin/env python3
"""Benchmark the local GreenRAN ns-3 runtime before economic adaptation.

This deliberately runs the simulator without the RIC/actuator.  It measures
whether the fixed 20-UE/3-DU topology can advance 600 simulated seconds at
the minimum operational rate.  The result is a performance prerequisite, not
experimental evidence and is never used as a causal comparison arm.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BINARY = ROOT / "ns-O-RAN-flexric/mmwave-LENA-oran/build/scratch/ns3.42-Energy_saving_with_cell_utilization_scenario-default"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--binary", type=Path, default=DEFAULT_BINARY)
    parser.add_argument("--sim-time", type=float, default=600.0)
    parser.add_argument("--min-rtf", type=float, default=0.10)
    parser.add_argument("--wall-timeout", type=float, default=6000.0)
    parser.add_argument("--seed", type=int, default=47)
    args = parser.parse_args()

    output = args.output_dir.resolve()
    if output.exists() and any(output.iterdir()):
        raise SystemExit(f"benchmark output is not empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    if not args.binary.is_file() or not os.access(args.binary, os.X_OK):
        raise SystemExit(f"ns-3 binary is not executable: {args.binary}")

    log_path = output / "benchmark.log"
    energy_dir = output / "ns3_energy"
    energy_dir.mkdir()
    command = [
        str(args.binary.resolve()),
        "--e2TermIp=127.0.0.1",
        f"--simTime={args.sim_time:g}",
        "--ranPressureProfile=tasam_training_balanced_v3",
        "--enableTraces=true",
        "--enableEnergyCsvDump=1",
        f"--energyOutputDir={energy_dir}",
        f"--RngRun={args.seed}",
        "--fixedTxPowerPercent=100",
        "--activeCells=3",
        "--ueCount=20",
        "--cameraUeCount=3",
        "--vehicleUeCount=5",
        "--mmWaveEnbNodes=3",
        "--useMcUeDevices=true",
        "--e2lteEnabled=false",
        "--e2nrEnabled=false",
        "--e2du=true",
        "--e2cuUp=false",
        "--e2cuCp=false",
        "--e2ControlEnabled=false",
        "--enableE2FileLogging=false",
    ]
    env = dict(os.environ)
    env.update({
        "GREENRAN_NS3_ENERGY_OUTPUT_DIR": str(energy_dir),
        "GREENRAN_CAMPAIGN_ID": output.name,
    })
    started = time.monotonic()
    return_code = None
    timed_out = False
    with log_path.open("w", encoding="utf-8") as log:
        try:
            completed = subprocess.run(
                command,
                cwd=ROOT / "ns-O-RAN-flexric/mmwave-LENA-oran",
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=float(args.wall_timeout),
                check=False,
            )
            return_code = completed.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            return_code = 124
    elapsed = max(0.001, time.monotonic() - started)
    rtf = float(args.sim_time) / elapsed
    payload = {
        "schema": "greenran.simulation_performance_benchmark.v1",
        "binary": str(args.binary.resolve()),
        "binary_sha256": _sha256(args.binary),
        "seed": args.seed,
        "ue_count": 20,
        "du_count": 3,
        "sim_time_s": args.sim_time,
        "wall_time_s": elapsed,
        "rtf": rtf,
        "min_rtf": args.min_rtf,
        "return_code": return_code,
        "timed_out": timed_out,
        "valid": bool(return_code == 0 and not timed_out and rtf >= args.min_rtf),
        "reason": (
            "ok" if return_code == 0 and not timed_out and rtf >= args.min_rtf
            else "simulation_performance_infeasible"
        ),
        "command": command,
        "log": str(log_path),
    }
    (output / "benchmark_manifest.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if payload["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
