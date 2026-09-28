import argparse
import json
from pathlib import Path

import pytest

from scripts.build_tasam_dynamic_floor_ledger import build_ledger
from src.tasam_dynamic_floor import SAFE_POWER_FLOOR_LEDGER_SCHEMA


PROFILE = "tasam_training_balanced_v6_1_v2x_gbr_deadline_mc_fallback_baseline_max"


def _physics_arm(root: Path, name: str, power: int, total_j: float) -> Path:
    arm = root / name
    energy = arm / "ns3_energy"
    energy.mkdir(parents=True)
    manifest = {
        "seed": 43,
        "profile": PROFILE,
        "sim_time_s": 120,
        "pairing_schedule_id": "seed43-schedule",
        "energy_model": {"kind": "native"},
        "build_provenance": {"binary_sha256": "d" * 64},
        "status": "finished",
    }
    (arm / "arm_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    header = "Time,NetEnergy,TxPowerPercent\n"
    for cell in (2, 3, 4):
        rows = [header, f"30,{total_j * 0.2},{power}\n", f"120,{total_j},{power}\n"]
        (energy / f"energyfilecell{cell}.csv").write_text("".join(rows), encoding="utf-8")
    observations = ["Time,CellId,TxPowerPercent,ObservationKind\n"]
    for cell in (2, 3, 4):
        observations.append(f"120,{cell},{power},power_readback\n")
    (energy / "TasamControlObservations.csv").write_text(
        "".join(observations), encoding="utf-8"
    )
    return arm


def _sleep_arm(root: Path) -> Path:
    arm = _physics_arm(root, "sleep", 25, 50_000)
    energy = arm / "ns3_energy"
    (energy / "TasamControlObservations.csv").write_text(
        "Time,CellId,TxPowerPercent,ObservationKind,SleepTransactionId\n"
        "120,2,0,power_readback,sleep:9:2\n",
        encoding="utf-8",
    )
    (energy / "TasamAssociationTrace.csv").write_text(
        "Time,CellId,Rnti,Imsi,AssociationEpoch,EvidenceVersion,CampaignId,"
        "SourceGeneration,TransactionId,NativeControlSequence,DecisionId,"
        "ActionCorrelationId,ObservationKind,AttachedUeCount,SleepTransactionId\n"
        "120,2,,,rrc-snapshot-2,v7,campaign,native,9,9,9,corr,cell_snapshot,0,sleep:9:2\n",
        encoding="utf-8",
    )
    return arm


def _arguments(tmp_path: Path) -> argparse.Namespace:
    baseline = _physics_arm(tmp_path, "p100", 100, 100_000)
    intermediate = _physics_arm(tmp_path, "p70", 70, 80_000)
    candidate = _physics_arm(tmp_path, "p45", 45, 60_000)
    low_power = _physics_arm(tmp_path, "p25", 25, 50_000)
    sleep_run = _sleep_arm(tmp_path)
    previous = tmp_path / "ledger-v1.json"
    previous.write_text(json.dumps({
        "schema": "greenran.tasam.v2x.safe_power_floor.v1",
        "status": "validated",
        "safe_floor_percent_by_cell": {"2": 60, "3": 60, "4": 60},
    }), encoding="utf-8")
    signature = tmp_path / "r26-signature.json"
    signature.write_text(json.dumps({
        "schema": "greenran.tasam.strict_pair.v1",
        "seed": 43,
        "profile": PROFILE,
        "experiment_contract": {"baseline_manifest": "r26/arm_manifest.json"},
        "sla_signature": {"baseline_violation_keys": []},
    }), encoding="utf-8")
    checkpoint = tmp_path / "r26-checkpoint"
    checkpoint.mkdir()
    (checkpoint / "tasam_marl_actors.pt").write_bytes(b"r26")
    return argparse.Namespace(
        baseline_run=baseline,
        intermediate_run=intermediate,
        candidate_run=candidate,
        low_power_run=low_power,
        sleep_run=sleep_run,
        previous_ledger=previous,
        initial_checkpoint=checkpoint,
        baseline_signature=signature,
        seed=43,
        profile=PROFILE,
        intermediate_power=70,
        candidate_power=45,
        low_power=25,
        warmup_s=30,
        duration_s=120,
    )


def test_ledger_requires_and_seals_the_complete_100_70_45_25_curve(tmp_path):
    ledger = build_ledger(_arguments(tmp_path))
    assert ledger["schema"] == SAFE_POWER_FLOOR_LEDGER_SCHEMA
    assert ledger["status"] == "candidate"
    assert ledger["physics_evidence"]["curve_power_percent"] == [100, 70, 45, 25]
    assert ledger["physics_evidence"]["energy_reduction_fraction"] == 0.5
    assert len(ledger["physics_evidence"]["intermediate_sha256"]) == 64
    assert ledger["sleep_evidence"]["status"] == "validated"


def test_ledger_rejects_a_mislabeled_intermediate_arm(tmp_path):
    args = _arguments(tmp_path)
    trace = args.intermediate_run / "ns3_energy" / "TasamControlObservations.csv"
    trace.write_text(trace.read_text(encoding="utf-8").replace(",70,", ",65,"), encoding="utf-8")
    with pytest.raises(SystemExit, match="potência física observada inválida"):
        build_ledger(args)
