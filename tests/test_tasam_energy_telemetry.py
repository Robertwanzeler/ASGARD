from pathlib import Path

from scripts.run_tasam_online_arm import _energy_telemetry_evidence


HEADER = (
    "Time,NetEnergy,DiffEnergy,IdleSeconds,TxSeconds,DataSeconds,CtrlSeconds,"
    "TxPowerPercent,ActiveCell,PowerTransactionId,ModelTxPowerPercent,"
    "TasamTxPowerPercent,PowerLeaseFresh\n"
)


def _write_energy_files(root: Path, *, model_power: int = 75) -> None:
    energy = root / "ns3_energy"
    energy.mkdir()
    for cell in (2, 3, 4):
        (energy / f"energyfilecell{cell}.csv").write_text(
            HEADER
            + f"30,100,100,1,1,0,0,75,1,11,{model_power},75,1\n"
            + f"119.9,500,400,2,2,0,0,75,1,11,{model_power},75,1\n",
            encoding="utf-8",
        )


def test_energy_telemetry_accepts_matching_du_and_model_power(tmp_path: Path):
    _write_energy_files(tmp_path)

    evidence = _energy_telemetry_evidence(
        tmp_path, expected_power_percent=75, measurement_start_s=30.0
    )

    assert evidence["valid"] is True
    assert evidence["active_rows"] == 6
    assert evidence["mismatch_count"] == 0


def test_energy_telemetry_rejects_model_power_divergence(tmp_path: Path):
    _write_energy_files(tmp_path, model_power=100)

    evidence = _energy_telemetry_evidence(
        tmp_path, expected_power_percent=75, measurement_start_s=30.0
    )

    assert evidence["valid"] is False
    assert evidence["reason"] == "fixed_power_telemetry_mismatch"
    assert evidence["mismatch_count"] == 6


def test_energy_telemetry_rejects_expired_fixed_power(tmp_path: Path):
    _write_energy_files(tmp_path)
    for path in (tmp_path / "ns3_energy").glob("energyfilecell*.csv"):
        content = path.read_text(encoding="utf-8").replace(",75,1\n", ",75,0\n")
        path.write_text(content, encoding="utf-8")

    evidence = _energy_telemetry_evidence(
        tmp_path, expected_power_percent=75, measurement_start_s=30.0
    )

    assert evidence["valid"] is False
    assert evidence["reason"] == "fixed_power_telemetry_mismatch"
    assert evidence["stale_rows"] == 6


def test_energy_telemetry_accepts_default_safe_100_baseline_without_lease(
    tmp_path: Path,
):
    energy = tmp_path / "ns3_energy"
    energy.mkdir()
    for cell in (2, 3, 4):
        (energy / f"energyfilecell{cell}.csv").write_text(
            HEADER
            + "30,100,100,1,1,0,0,100,1,0,100,100,0\n"
            + "119.9,500,400,2,2,0,0,100,1,0,100,100,0\n",
            encoding="utf-8",
        )

    evidence = _energy_telemetry_evidence(
        tmp_path, expected_power_percent=100, measurement_start_s=30.0
    )

    assert evidence["valid"] is True
    assert evidence["active_rows"] == 0
    assert evidence["baseline_default_rows"] == 6
    assert evidence["expected_power_mismatch_count"] == 0
