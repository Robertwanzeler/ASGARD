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
