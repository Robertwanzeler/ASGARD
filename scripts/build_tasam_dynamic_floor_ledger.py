#!/usr/bin/env python3
"""Seal the active 100/70/45/25 and handover/sleep calibration envelope."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from evaluate_tasam_strict_pair import causal_energy  # noqa: E402
from tasam_dynamic_floor import (  # noqa: E402
    SAFE_POWER_FLOOR_LEDGER_SCHEMA,
    load_baseline_signature,
    sha256_file,
)


def _json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"JSON inválido ou ausente: {path}") from exc
    if not isinstance(payload, dict):
        raise SystemExit(f"JSON deve ser objeto: {path}")
    return payload


def _evidence_sha256(run_dir: Path) -> str:
    digest = hashlib.sha256()
    paths = [run_dir / "arm_manifest.json"]
    paths.extend(sorted((run_dir / "ns3_energy").glob("energyfilecell*.csv")))
    paths.extend([
        run_dir / "ns3_energy" / "TasamControlObservations.csv",
        run_dir / "ns3_energy" / "TasamAssociationTrace.csv",
    ])
    for path in paths:
        if not path.is_file():
            continue
        digest.update(str(path.relative_to(run_dir)).encode("utf-8"))
        digest.update(bytes.fromhex(sha256_file(path)))
    return digest.hexdigest()


def _checkpoint_sha256(checkpoint: Path) -> str:
    digest = hashlib.sha256()
    files = sorted(
        path for path in checkpoint.rglob("*")
        if path.is_file() and path.name != "local_import_manifest.json"
    )
    if not files:
        raise SystemExit(f"checkpoint inicial vazio: {checkpoint}")
    for path in files:
        relative = path.relative_to(checkpoint).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(path.stat().st_size.to_bytes(8, "big"))
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def _assert_observed_power(run_dir: Path, expected_percent: int) -> None:
    trace = run_dir / "ns3_energy" / "TasamControlObservations.csv"
    try:
        with trace.open(newline="", encoding="utf-8") as handle:
            rows = [
                row for row in csv.DictReader(handle)
                if str(row.get("ObservationKind") or "") == "power_readback"
            ]
    except OSError as exc:
        raise SystemExit(f"trace física ausente: {trace}") from exc
    observed_cells = set()
    observed_power = set()
    for row in rows:
        try:
            observed_cells.add(int(row.get("CellId", -1)))
            observed_power.add(int(round(float(row.get("TxPowerPercent", -1)))))
        except (TypeError, ValueError):
            raise SystemExit(f"trace física malformada: {trace}") from None
    if observed_cells != {2, 3, 4} or observed_power != {int(expected_percent)}:
        raise SystemExit(
            f"potência física observada inválida em {run_dir}: "
            f"cells={sorted(observed_cells)}, power={sorted(observed_power)}"
        )


def _assert_validated_sleep(run_dir: Path) -> None:
    trace = run_dir / "ns3_energy" / "TasamControlObservations.csv"
    association = run_dir / "ns3_energy" / "TasamAssociationTrace.csv"
    try:
        with trace.open(newline="", encoding="utf-8") as handle:
            off_sleep_ids: dict[int, set[str]] = {}
            for row in csv.DictReader(handle):
                if str(row.get("ObservationKind") or "") != "power_readback":
                    continue
                raw_power = row.get("TxPowerPercent", 100)
                power = 100 if raw_power in (None, "") else float(raw_power)
                if int(power) == 0:
                    cell_id = int(row.get("CellId", -1))
                    sleep_id = str(row.get("SleepTransactionId") or "").strip()
                    if sleep_id:
                        off_sleep_ids.setdefault(cell_id, set()).add(sleep_id)
        with association.open(newline="", encoding="utf-8") as handle:
            empty = {
                (int(row.get("CellId", -1)), str(row.get("SleepTransactionId") or "").strip())
                for row in csv.DictReader(handle)
                if str(row.get("ObservationKind") or "") == "cell_snapshot"
                and int(row.get("AttachedUeCount", -1) or -1) == 0
                and str(row.get("SleepTransactionId") or "").strip()
            }
    except (OSError, TypeError, ValueError) as exc:
        raise SystemExit(f"evidência de sleep ausente ou inválida: {run_dir}") from exc
    if (
        len(off_sleep_ids) != 1
        or len(next(iter(off_sleep_ids.values()), set())) != 1
        or not empty
        or not any(
        (cell_id, sleep_id) in empty
        for cell_id, sleep_ids in off_sleep_ids.items()
        for sleep_id in sleep_ids
        )
    ):
        raise SystemExit(
            "calibração de sleep exige DU off, snapshot vazio e SleepTransactionId correlacionado"
        )


def build_ledger(args: argparse.Namespace) -> dict[str, Any]:
    baseline = args.baseline_run.resolve()
    intermediate = args.intermediate_run.resolve()
    candidate = args.candidate_run.resolve()
    low_power = args.low_power_run.resolve()
    sleep_run = args.sleep_run.resolve()
    baseline_manifest = _json(baseline / "arm_manifest.json")
    intermediate_manifest = _json(intermediate / "arm_manifest.json")
    candidate_manifest = _json(candidate / "arm_manifest.json")
    low_power_manifest = _json(low_power / "arm_manifest.json")
    sleep_manifest = _json(sleep_run / "arm_manifest.json")
    mismatches = []
    for field in ("seed", "profile", "sim_time_s", "pairing_schedule_id", "energy_model"):
        if not (
            baseline_manifest.get(field)
            == intermediate_manifest.get(field)
            == candidate_manifest.get(field)
            == low_power_manifest.get(field)
            == sleep_manifest.get(field)
        ):
            mismatches.append(field)
    baseline_binary = (baseline_manifest.get("build_provenance") or {}).get("binary_sha256")
    intermediate_binary = (intermediate_manifest.get("build_provenance") or {}).get("binary_sha256")
    candidate_binary = (candidate_manifest.get("build_provenance") or {}).get("binary_sha256")
    low_power_binary = (low_power_manifest.get("build_provenance") or {}).get("binary_sha256")
    sleep_binary = (sleep_manifest.get("build_provenance") or {}).get("binary_sha256")
    if not baseline_binary or not (
        baseline_binary == intermediate_binary == candidate_binary == low_power_binary == sleep_binary
    ):
        mismatches.append("binary_sha256")
    if int(baseline_manifest.get("seed", -1)) != args.seed:
        mismatches.append("expected_seed")
    if str(baseline_manifest.get("profile") or "") != args.profile:
        mismatches.append("expected_profile")
    if float(baseline_manifest.get("sim_time_s", 0.0) or 0.0) < args.duration_s:
        mismatches.append("baseline_duration")
    if float(intermediate_manifest.get("sim_time_s", 0.0) or 0.0) < args.duration_s:
        mismatches.append("intermediate_duration")
    if float(candidate_manifest.get("sim_time_s", 0.0) or 0.0) < args.duration_s:
        mismatches.append("candidate_duration")
    if float(low_power_manifest.get("sim_time_s", 0.0) or 0.0) < args.duration_s:
        mismatches.append("low_power_duration")
    if float(sleep_manifest.get("sim_time_s", 0.0) or 0.0) < args.duration_s:
        mismatches.append("sleep_duration")
    if any(
        manifest.get("status") != "finished"
        for manifest in (baseline_manifest, intermediate_manifest, candidate_manifest,
                         low_power_manifest, sleep_manifest)
    ):
        mismatches.append("arms_finished")
    if mismatches:
        raise SystemExit("runs físicos não equivalentes: " + ", ".join(sorted(set(mismatches))))

    _assert_observed_power(baseline, 100)
    _assert_observed_power(intermediate, args.intermediate_power)
    _assert_observed_power(candidate, args.candidate_power)
    _assert_observed_power(low_power, args.low_power)
    _assert_validated_sleep(sleep_run)
    baseline_energy = causal_energy(
        baseline, warmup_s=args.warmup_s, duration_s=args.duration_s
    )
    intermediate_energy = causal_energy(
        intermediate, warmup_s=args.warmup_s, duration_s=args.duration_s
    )
    candidate_energy = causal_energy(
        candidate, warmup_s=args.warmup_s, duration_s=args.duration_s
    )
    low_power_energy = causal_energy(
        low_power, warmup_s=args.warmup_s, duration_s=args.duration_s
    )
    if not all(
        evidence.get("valid")
        for evidence in (baseline_energy, intermediate_energy, candidate_energy, low_power_energy)
    ):
        raise SystemExit("energia causal incompleta nos runs físicos")
    baseline_j = float(baseline_energy["energy_j"])
    intermediate_j = float(intermediate_energy["energy_j"])
    candidate_j = float(candidate_energy["energy_j"])
    low_power_j = float(low_power_energy["energy_j"])
    if not baseline_j > intermediate_j > candidate_j > low_power_j:
        raise SystemExit(
            "curva física A/B/C não é estritamente monotônica: "
            f"p100={baseline_j}, p{args.intermediate_power}={intermediate_j}, "
            f"p{args.candidate_power}={candidate_j}, p{args.low_power}={low_power_j}"
        )
    reduction = (baseline_j - low_power_j) / baseline_j if baseline_j > 0 else -1.0
    if reduction <= 0.0:
        raise SystemExit(f"p{args.low_power} não apresentou redução física positiva")

    previous = _json(args.previous_ledger.resolve())
    if previous.get("schema") != "greenran.tasam.v2x.safe_power_floor.v1" or previous.get("status") != "validated":
        raise SystemExit("ledger r26 anterior não é safe_power_floor.v1 validado")
    previous_floor = previous.get("safe_floor_percent_by_cell") or {}
    if any(str(cell) not in previous_floor for cell in (2, 3, 4)):
        raise SystemExit("ledger r26 anterior não contém os três DUs")
    if any(int(float(previous_floor[str(cell)])) != 60 for cell in (2, 3, 4)):
        raise SystemExit("ledger r26 anterior não preserva o piso validado de 60%")
    load_baseline_signature(
        args.baseline_signature,
        expected_seed=args.seed,
        expected_profile=args.profile,
        strict_contract=True,
    )
    initial_checkpoint = args.initial_checkpoint.resolve()
    initial_checkpoint_sha256 = _checkpoint_sha256(initial_checkpoint)
    floor = {str(cell): int(args.low_power) for cell in (2, 3, 4)}
    return {
        "schema": SAFE_POWER_FLOOR_LEDGER_SCHEMA,
        "status": "candidate",
        "seed": args.seed,
        "profile": args.profile,
        "initial_floor_percent_by_cell": floor,
        "minimum_floor_percent_by_cell": floor,
        "previous_validated_floor_percent_by_cell": {
            str(cell): int(float(previous_floor[str(cell)])) for cell in (2, 3, 4)
        },
        "previous_validated_ledger": str(args.previous_ledger.resolve()),
        "previous_validated_ledger_sha256": sha256_file(args.previous_ledger),
        "initial_checkpoint": str(initial_checkpoint),
        "initial_checkpoint_sha256": initial_checkpoint_sha256,
        "baseline_signature": str(args.baseline_signature.resolve()),
        "baseline_signature_sha256": sha256_file(args.baseline_signature),
        "physics_evidence": {
            "status": "validated",
            "candidate_power_percent": int(args.low_power),
            "minimum_energy_reduction_fraction": 0.0,
            "energy_reduction_fraction": round(reduction, 8),
            "warmup_s": int(args.warmup_s),
            "duration_s": int(args.duration_s),
            "baseline_run": str(baseline),
            "intermediate_run": str(intermediate),
            "candidate_run": str(candidate),
            "low_power_run": str(low_power),
            "baseline_energy_j": baseline_j,
            "intermediate_energy_j": intermediate_j,
            "candidate_energy_j": candidate_j,
            "low_power_energy_j": low_power_j,
            "baseline_sha256": _evidence_sha256(baseline),
            "intermediate_sha256": _evidence_sha256(intermediate),
            "candidate_sha256": _evidence_sha256(candidate),
            "low_power_sha256": _evidence_sha256(low_power),
            "curve_power_percent": [100, int(args.intermediate_power), int(args.candidate_power), int(args.low_power)],
            "binary_sha256": baseline_binary,
            "energy_model": baseline_manifest.get("energy_model"),
            "pairing_schedule_id": baseline_manifest.get("pairing_schedule_id", ""),
        },
        "sleep_evidence": {
            "status": "validated",
            "sleep_run": str(sleep_run),
            "run_sha256": _evidence_sha256(sleep_run),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-run", type=Path, required=True)
    parser.add_argument("--intermediate-run", type=Path, required=True)
    parser.add_argument("--candidate-run", type=Path, required=True)
    parser.add_argument("--low-power-run", type=Path, required=True)
    parser.add_argument("--sleep-run", type=Path, required=True)
    parser.add_argument("--previous-ledger", type=Path, required=True)
    parser.add_argument("--initial-checkpoint", type=Path, required=True)
    parser.add_argument("--baseline-signature", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=43)
    parser.add_argument(
        "--profile",
        default="tasam_training_balanced_v6_1_v2x_gbr_deadline_mc_fallback_baseline_max",
    )
    parser.add_argument("--intermediate-power", type=int, default=70)
    parser.add_argument("--candidate-power", type=int, default=45)
    parser.add_argument("--low-power", type=int, default=25)
    parser.add_argument("--warmup-s", type=int, default=30)
    parser.add_argument("--duration-s", type=int, default=120)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit(f"output já existe: {args.output}")
    ledger = build_ledger(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(ledger, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
