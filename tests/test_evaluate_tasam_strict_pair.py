import sqlite3
from pathlib import Path

from scripts.evaluate_tasam_strict_pair import evaluate_ue_windows, experiment_contract


def _database(path: Path, *, bad_imsi: int | None = None) -> None:
    conn = sqlite3.connect(path)
    conn.execute("""create table ue_metrics (
        id integer primary key, sim_time_s real, imsi integer,
        throughput_kbps real, latency_us real, latency_p95_us real,
        latency_max_us real, tx_pdus integer, rx_pdus integer,
        packet_loss_percent real, backlog_bytes integer,
        has_latency_samples integer, latency_is_proxy integer,
        pdcp_provenance text)""")
    row_id = 0
    for second in range(2, 4):
        for imsi in range(1, 21):
            row_id += 1
            throughput = 10 if imsi >= 4 else 25_000
            maximum = 10_000 if imsi >= 16 else 50_000
            if imsi == bad_imsi:
                maximum = 30_000
            conn.execute("insert into ue_metrics values (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (row_id, second + 0.1, imsi, throughput, maximum, maximum,
                          maximum, 100, 99, 1, 0, 1, 0, "pdcp_real"))
    conn.commit()
    conn.close()


def test_strict_ue_gate_accepts_every_real_ue_window(tmp_path):
    _database(tmp_path / "rapp_data_lake.db")
    report = evaluate_ue_windows(tmp_path, warmup_s=2, duration_s=4)
    assert report["valid"]
    assert report["observed_ue_windows"] == 40


def test_strict_ue_gate_rejects_one_vehicle_violation(tmp_path):
    _database(tmp_path / "rapp_data_lake.db", bad_imsi=16)
    report = evaluate_ue_windows(tmp_path, warmup_s=2, duration_s=4)
    assert not report["valid"]
    assert report["violation_count"] == 2


def test_strict_ue_gate_rejects_schema_without_real_pdcp_provenance(tmp_path):
    conn = sqlite3.connect(tmp_path / "rapp_data_lake.db")
    conn.execute("create table ue_metrics (id integer, sim_time_s real, imsi integer, "
                 "throughput_kbps real, latency_us real, tx_pdus integer, rx_pdus integer)")
    conn.execute("insert into ue_metrics values (1, 2.1, 1, 25000, 10000, 1, 1)")
    conn.commit()
    conn.close()
    report = evaluate_ue_windows(tmp_path, warmup_s=2, duration_s=3)
    assert not report["valid"]
    assert "non_real_or_missing_pdcp" in report["violations"][0]["reasons"]


def test_experiment_contract_requires_same_pair_and_frozen_checkpoint(tmp_path):
    baseline = tmp_path / "rapp_only"
    combined = tmp_path / "combined"
    baseline.mkdir()
    combined.mkdir()
    energy_model = {"kind": "calibrated_ru_mmwave_power_model", "physical_wattmeter": False}
    base_manifest = {
        "mode": "rapp_only", "seed": 47, "profile": "fixed", "sim_time_s": 600,
        "pairing_schedule_id": "pair-1", "status": "finished",
        "contract": {"tasam_enabled": False, "armd_mode": "off"},
        "energy_model": energy_model,
    }
    combined_manifest = {
        "mode": "combined", "seed": 47, "profile": "fixed", "sim_time_s": 600,
        "pairing_schedule_id": "pair-1", "status": "finished",
        "contract": {"tasam_enabled": True, "armd_mode": "assist",
                     "actuation_enabled": True, "frozen_checkpoint": True},
        "checkpoint_sha256_before": "abc", "checkpoint_sha256_after": "abc",
        "checkpoint_frozen_verified": True, "energy_model": energy_model,
    }
    (baseline / "arm_manifest.json").write_text(__import__("json").dumps(base_manifest))
    (combined / "arm_manifest.json").write_text(__import__("json").dumps(combined_manifest))
    assert experiment_contract(baseline, combined)["valid"]
    combined_manifest["checkpoint_sha256_after"] = "changed"
    (combined / "arm_manifest.json").write_text(__import__("json").dumps(combined_manifest))
    assert not experiment_contract(baseline, combined)["valid"]


def test_experiment_contract_requires_an_actuating_rapp_control_when_requested(tmp_path):
    baseline = tmp_path / "rapp_only"
    combined = tmp_path / "combined"
    baseline.mkdir()
    combined.mkdir()
    energy_model = {"kind": "native"}
    base_manifest = {
        "mode": "rapp_only", "seed": 47, "profile": "fixed", "sim_time_s": 331.5,
        "pairing_schedule_id": "pair-1", "status": "finished",
        "contract": {"tasam_enabled": False, "armd_mode": "off", "actuation_enabled": False},
        "energy_model": energy_model,
    }
    combined_manifest = {
        "mode": "combined", "seed": 47, "profile": "fixed", "sim_time_s": 331.5,
        "pairing_schedule_id": "pair-1", "status": "finished",
        "contract": {"tasam_enabled": True, "armd_mode": "assist", "actuation_enabled": True,
                     "frozen_checkpoint": True},
        "checkpoint_sha256_before": "abc", "checkpoint_sha256_after": "abc",
        "checkpoint_frozen_verified": True, "energy_model": energy_model,
    }
    (baseline / "arm_manifest.json").write_text(__import__("json").dumps(base_manifest))
    (combined / "arm_manifest.json").write_text(__import__("json").dumps(combined_manifest))
    report = experiment_contract(
        baseline, combined, duration_s=331.5, expected_baseline_mode="rapp_only_actuating"
    )
    assert not report["valid"]
    assert report["checks"]["expected_baseline_mode"] is False


import json

from scripts.evaluate_tasam_strict_pair import causal_energy, e2_audit, evaluate_pair


def _energy_files(run_dir: Path, total_j: float, *, pct: int = 100) -> None:
    energy_dir = run_dir / "ns3_energy"
    energy_dir.mkdir(parents=True, exist_ok=True)
    header = ("Time,NetEnergy,DiffEnergy,IdleSeconds,TxSeconds,DataSeconds,"
              "CtrlSeconds,TxPowerPercent,ActiveCell")
    for cell in (2, 3, 4):
        rows = [header]
        for time_s, cumulative in ((30.0, total_j * 0.2), (60.0, total_j * 0.55),
                                   (90.0, total_j)):
            rows.append(f"{time_s},{cumulative},0,1,1,0,0,{pct},1")
        (energy_dir / f"energyfilecell{cell}.csv").write_text("\n".join(rows) + "\n")


def test_causal_energy_reads_ns3_energy_corpus(tmp_path):
    _energy_files(tmp_path, 90_000.0)
    report = causal_energy(tmp_path, warmup_s=30, duration_s=90)
    assert report["valid"]
    assert report["cell_count"] == 3
    assert report["energy_j"] == (90_000.0 - 18_000.0) * 3


def test_e2_audit_matches_power_readback_without_active_ues(tmp_path):
    intents = tmp_path / "xapp_intents"
    intents.mkdir()
    rows = [
        {"sequence": 5, "sim_time_s": 31.0, "ttl_ms": 2000, "ack": True,
         "applied": True, "observed_confirmations": 1,
         "requested_cells": [{"cell_id": 2, "tx_power_percent": 70, "expected_ues": 3},
                             {"cell_id": 3, "tx_power_percent": 70, "expected_ues": 3},
                             {"cell_id": 4, "tx_power_percent": 70, "expected_ues": 3}]},
        {"sequence": 6, "sim_time_s": 34.0, "ttl_ms": 2000, "ack": True,
         "applied": True, "observed_confirmations": 1,
         "requested_cells": [{"cell_id": 2, "tx_power_percent": 55, "expected_ues": 3}]},
    ]
    (intents / "tasam_control_audit.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n")
    energy_dir = tmp_path / "ns3_energy"
    energy_dir.mkdir()
    header = ("Time,CellId,SchedulerTransactionId,PowerTransactionId,ActiveUes,"
              "TxPowerPercent,TxPowerDbm,NominalTxPowerDbm,ObservationKind,"
              "PolicyActive,PolicyExpiryTime,SourceGeneration,AssociationEpoch,"
              "ActiveDlSymbols,ActiveDlSymbolCapacity,CampaignId,EvidenceVersion,"
              "CampaignGeneration,DecisionId,ActionCorrelationId,"
              "NativeControlSequence,NativeAllocationSource")
    lines = [header]
    for cell in (2, 3, 4):
        lines.append(f"31.5,{cell},0,5,0,70,28,30,power_readback,0,33.0,"
                     "gen,rrc-epoch-0,0,0,camp,v6,gen,1,corr,1,src")
    (energy_dir / "TasamControlObservations.csv").write_text("\n".join(lines) + "\n")
    report = e2_audit(tmp_path, warmup_s=30)
    assert report["valid"]
    assert report["required_transactions"] == 1
    assert report["unobservable_final"] == [6]
    assert report["scheduler_phy_confirmed"] == 1


def _sla_database(path: Path, *, extra_imsi16_windows: tuple[int, ...] = ()) -> None:
    conn = sqlite3.connect(path)
    conn.execute("""create table ue_metrics (
        id integer primary key, sim_time_s real, imsi integer,
        throughput_kbps real, latency_us real, latency_p95_us real,
        latency_max_us real, tx_pdus integer, rx_pdus integer,
        packet_loss_percent real, backlog_bytes integer,
        has_latency_samples integer, latency_is_proxy integer,
        pdcp_provenance text)""")
    row_id = 0
    for second in range(30, 90):
        for imsi in range(1, 21):
            row_id += 1
            loss = 2.0 if (imsi == 16 and second in extra_imsi16_windows) else 0.5
            conn.execute("insert into ue_metrics values (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (row_id, second + 0.1, imsi, 30_000, 10_000, 10_000,
                          10_000, 100, 99, loss, 0, 1, 0, "pdcp_real"))
    conn.commit()
    conn.close()


def _e2_confirmed(run_dir: Path) -> None:
    intents = run_dir / "xapp_intents"
    intents.mkdir(parents=True, exist_ok=True)
    row = {"sequence": 1, "sim_time_s": 31.0, "ttl_ms": 2000, "ack": True,
           "applied": True, "observed_confirmations": 1,
           "requested_cells": [{"cell_id": 2, "tx_power_percent": 70, "expected_ues": 0}]}
    (intents / "tasam_control_audit.jsonl").write_text(json.dumps(row) + "\n")
    energy_dir = run_dir / "ns3_energy"
    energy_dir.mkdir(parents=True, exist_ok=True)
    header = ("Time,CellId,SchedulerTransactionId,PowerTransactionId,ActiveUes,"
              "TxPowerPercent,TxPowerDbm,NominalTxPowerDbm,ObservationKind,"
              "PolicyActive,PolicyExpiryTime,SourceGeneration,AssociationEpoch,"
              "ActiveDlSymbols,ActiveDlSymbolCapacity,CampaignId,EvidenceVersion,"
              "CampaignGeneration,DecisionId,ActionCorrelationId,"
              "NativeControlSequence,NativeAllocationSource")
    (energy_dir / "TasamControlObservations.csv").write_text(
        header + "\n" + "31.5,2,0,1,0,70,28,30,power_readback,0,33.0,"
        "gen,rrc-epoch-0,0,0,camp,v6,gen,1,corr,1,src\n")


def _pair(tmp_path: Path, *, combined_mode: str) -> tuple[Path, Path]:
    baseline = tmp_path / "rapp"
    combined = tmp_path / "asgard"
    baseline.mkdir()
    combined.mkdir()
    energy_model = {"kind": "native_mmwave"}
    base_manifest = {
        "mode": "rapp_only_actuating", "seed": 43, "profile": "p1",
        "sim_time_s": 120, "pairing_schedule_id": "pair-1", "status": "finished",
        "contract": {"tasam_enabled": False, "armd_mode": "off",
                     "actuation_enabled": True},
        "energy_model": energy_model,
    }
    combined_manifest = {
        "mode": combined_mode, "seed": 43, "profile": "p1",
        "sim_time_s": 120, "pairing_schedule_id": "pair-1", "status": "finished",
        "contract": {"tasam_enabled": True, "armd_mode": "assist",
                     "actuation_enabled": True, "frozen_checkpoint": True},
        "checkpoint_sha256_before": "abc", "checkpoint_sha256_after": "abc",
        "checkpoint_frozen_verified": True, "energy_model": energy_model,
    }
    (baseline / "arm_manifest.json").write_text(json.dumps(base_manifest))
    (combined / "arm_manifest.json").write_text(json.dumps(combined_manifest))
    for arm, cpu in ((baseline, 1_000_000.0), (combined, 1_010_000.0)):
        (arm / "infrastructure_metrics.json").write_text(json.dumps({
            "schema": "greenran.infrastructure.metrics.v1", "complete": True,
            "totals": {"artifact_bytes": 1.0, "cpu_usage_usec": cpu,
                       "io_bytes": 1.0, "management_tx_bytes": 1.0,
                       "memory_byte_seconds": 1.0, "memory_peak_bytes": 1.0}}))
        _sla_database(arm / "rapp_data_lake.db",
                      extra_imsi16_windows=(88, 89))
        _e2_confirmed(arm)
    _energy_files(baseline, 212_865.0)
    _energy_files(combined, 130_000.0, pct=70)
    return baseline, combined


def test_evaluate_pair_accepts_scenario_signature_violations(tmp_path):
    baseline, combined = _pair(tmp_path, combined_mode="asgard_v2x_window90_energy_frozen")
    report = evaluate_pair(baseline, combined, warmup_s=30, duration_s=90,
                           expected_seed=43, expected_baseline_mode="rapp_only_actuating")
    assert report["criteria"]["sla_differential_non_worse"]
    assert report["sla_signature"]["attributable_violation_keys"] == []
    assert report["criteria"]["energy_strictly_lower"]
    assert report["passed"]


def test_evaluate_pair_flags_controller_attributable_violations(tmp_path):
    baseline, combined = _pair(tmp_path, combined_mode="asgard_v2x_window90_energy_frozen")
    conn = sqlite3.connect(combined / "rapp_data_lake.db")
    conn.execute("update ue_metrics set packet_loss_percent = 2.0 "
                 "where imsi = 16 and cast(sim_time_s as int) = 50")
    conn.commit()
    conn.close()
    report = evaluate_pair(baseline, combined, warmup_s=30, duration_s=90,
                           expected_seed=43, expected_baseline_mode="rapp_only_actuating")
    assert not report["criteria"]["sla_differential_non_worse"]
    assert (50, 16, "vehicle_loss") in {
        (window, imsi, reason)
        for window, imsi, reason in report["sla_signature"]["attributable_violation_keys"]
    }
    assert not report["passed"]
