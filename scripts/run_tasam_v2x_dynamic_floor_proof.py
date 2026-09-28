#!/usr/bin/env python3
"""Run the 120 s seed-43 adaptive-ASGARD proof against a finished 100% arm."""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARM = ROOT / "scripts" / "run_tasam_online_arm.py"
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from evaluate_tasam_strict_pair import evaluate_pair  # noqa: E402
from run_tasam_online_arm import checkpoint_fingerprint, file_sha256  # noqa: E402
from tasam_dynamic_floor import load_dynamic_floor_ledger  # noqa: E402

MODE = "asgard_v2x_window90_energy_dynamic"
PROFILE = "tasam_training_balanced_v6_1_v2x_gbr_deadline_mc_fallback_baseline_max"


def _json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"JSON inválido ou ausente: {path}") from exc
    return value if isinstance(value, dict) else {}


def _assert_full_power_baseline(run_dir: Path) -> None:
    trace = run_dir / "ns3_energy" / "TasamControlObservations.csv"
    try:
        with trace.open(newline="", encoding="utf-8") as handle:
            rows = [
                row for row in csv.DictReader(handle)
                if str(row.get("ObservationKind") or "") == "power_readback"
                and 30.0 <= float(row.get("Time", -1)) <= 120.0
            ]
    except (OSError, TypeError, ValueError) as exc:
        raise SystemExit(f"readback do braço 100% inválido: {trace}") from exc
    by_cell = {cell: [] for cell in (2, 3, 4)}
    for row in rows:
        try:
            cell = int(row.get("CellId", -1))
            power = int(round(float(row.get("TxPowerPercent", -1))))
            sim_time = float(row.get("Time", -1))
        except (TypeError, ValueError):
            raise SystemExit(f"readback do braço 100% malformado: {trace}") from None
        if cell in by_cell:
            by_cell[cell].append((sim_time, power))
    if any(
        not samples
        or max(time_s for time_s, _ in samples) < 119.0
        or {power for _, power in samples} != {100}
        for samples in by_cell.values()
    ):
        raise SystemExit("baseline energético não prova 100% nos três DUs por 120 s")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-run", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--experience-bank", type=Path, required=True)
    parser.add_argument("--recent-experience-bank", type=Path, required=True)
    parser.add_argument("--dynamic-floor-ledger", type=Path, required=True)
    parser.add_argument("--baseline-signature", type=Path, required=True)
    parser.add_argument("--pairing-schedule-file", type=Path, required=True)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument(
        "--energy-calibration", type=Path,
        default=ROOT / "config/energy_calibration_sim_v3_sleep.json",
    )
    parser.add_argument("--wall-time", type=float, default=9000.0)
    parser.add_argument("--minimum-rtf", type=float, default=0.016)
    args = parser.parse_args()

    baseline = args.baseline_run.resolve()
    run_dir = args.run_dir.resolve()
    if run_dir.exists():
        raise SystemExit(f"run-dir já existe e não pode ser reutilizado: {run_dir}")
    baseline_manifest = _json(baseline / "arm_manifest.json")
    if baseline_manifest.get("status") != "finished":
        raise SystemExit("braço 100% ainda não terminou com status finished")
    if baseline_manifest.get("mode") != "fixed_100_native":
        raise SystemExit("baseline deve ser o braço E2 fixed_100_native")
    if int(baseline_manifest.get("seed", -1)) != 43:
        raise SystemExit("baseline deve usar seed 43")
    if float(baseline_manifest.get("sim_time_s", 0.0) or 0.0) < 120.0:
        raise SystemExit("baseline não cobre 120 s")
    if baseline_manifest.get("profile") != PROFILE:
        raise SystemExit("baseline usa perfil diferente do contrato seed-43")
    _assert_full_power_baseline(baseline)
    schedule_id = str(baseline_manifest.get("pairing_schedule_id") or "")
    if not schedule_id:
        raise SystemExit("baseline não possui pairing_schedule_id")
    schedule = _json(args.pairing_schedule_file.resolve())
    if (
        schedule.get("schema") != "greenran.tasam_pairing_schedule.v1"
        or schedule.get("schedule_id") != schedule_id
        or int(schedule.get("seed", -1)) != 43
        or schedule.get("profile") != PROFILE
    ):
        raise SystemExit("pairing schedule não corresponde ao braço 100%")
    ledger = load_dynamic_floor_ledger(
        args.dynamic_floor_ledger.resolve(),
        expected_seed=43,
        expected_profile=PROFILE,
        baseline_signature_path=args.baseline_signature.resolve(),
    )
    physics = ledger.get("physics_evidence") or {}
    if Path(str(physics.get("baseline_run") or "")).resolve() != baseline:
        raise SystemExit("baseline da prova deve ser exatamente o braço A=100% do ledger")
    if str(physics.get("pairing_schedule_id") or "") != schedule_id:
        raise SystemExit("schedule do ledger físico difere do braço 100%")
    if physics.get("energy_model") != baseline_manifest.get("energy_model"):
        raise SystemExit("modelo energético do ledger difere do braço 100%")
    baseline_binary = str(
        (baseline_manifest.get("build_provenance") or {}).get("binary_sha256") or ""
    )
    selected_binary = file_sha256(args.binary.resolve())
    if not baseline_binary or not (
        baseline_binary == str(physics.get("binary_sha256") or "") == selected_binary
    ):
        raise SystemExit("binário da prova difere do braço A/B/C")
    initial_checkpoint_hash = checkpoint_fingerprint(args.checkpoint.resolve())
    if initial_checkpoint_hash != str(ledger.get("initial_checkpoint_sha256") or ""):
        raise SystemExit("checkpoint inicial não corresponde ao checkpoint r26 selado")

    command = [
        sys.executable, str(ARM),
        "--mode", MODE,
        "--run-dir", str(run_dir),
        "--seed", "43",
        "--profile", PROFILE,
        "--wall-time", str(args.wall_time),
        "--sim-time", "120",
        "--decision-target", "0",
        "--native-fidelity",
        "--performance-min-rtf", str(args.minimum_rtf),
        "--binary", str(args.binary.resolve()),
        "--checkpoint", str(args.checkpoint.resolve()),
        "--experience-bank", str(args.experience_bank.resolve()),
        "--recent-experience-bank", str(args.recent_experience_bank.resolve()),
        "--replay-rows", "90",
        "--min-new-snapshots", "18",
        "--min-trainable-transitions", "18",
        "--epochs-per-update", "2",
        "--controller-poll-seconds", "10",
        "--max-rollout-fraction", "1.0",
        "--economic-update-min-transitions", "0",
        "--energy-calibration", str(args.energy_calibration.resolve()),
        "--pairing-schedule-id", schedule_id,
        "--pairing-schedule-file", str(args.pairing_schedule_file.resolve()),
        "--dynamic-floor-ledger", str(args.dynamic_floor_ledger.resolve()),
        "--baseline-signature", str(args.baseline_signature.resolve()),
        "--disable-app-overrides",
        "--energy-enabled",
        "--min-free-gib", "20",
        "--artifact-min-free-gib", "20",
        "--artifact-budget-gib", "4",
    ]
    log_path = run_dir.parent / f"{run_dir.name}.launcher.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log:
        result = subprocess.run(
            command,
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=False,
        )
    if result.returncode != 0:
        raise SystemExit(
            f"braço dynamic falhou com código {result.returncode}; veja {log_path}"
        )

    report = evaluate_pair(
        baseline,
        run_dir,
        warmup_s=30,
        duration_s=120,
        expected_seed=43,
        expected_profile=PROFILE,
        expected_baseline_mode="fixed_100_native",
        minimum_energy_saving_fraction=0.36,
    )
    report_path = run_dir.parent / f"{run_dir.name}.strict_pair.json"
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report.get("passed") else 2


if __name__ == "__main__":
    raise SystemExit(main())
