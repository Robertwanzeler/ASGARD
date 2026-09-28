from pathlib import Path

import pytest

from scripts.run_tasam_v2x_dynamic_floor_proof import _assert_full_power_baseline


def _trace(root: Path, powers: dict[int, int]) -> None:
    energy = root / "ns3_energy"
    energy.mkdir(parents=True)
    rows = ["Time,CellId,TxPowerPercent,ObservationKind\n"]
    for time_s in (30.0, 60.0, 119.9):
        for cell in (2, 3, 4):
            rows.append(f"{time_s},{cell},{powers[cell]},power_readback\n")
    (energy / "TasamControlObservations.csv").write_text(
        "".join(rows), encoding="utf-8"
    )


def test_full_power_baseline_requires_all_three_dus_through_120_seconds(tmp_path):
    _trace(tmp_path, {2: 100, 3: 100, 4: 100})
    _assert_full_power_baseline(tmp_path)


def test_full_power_baseline_rejects_a_lower_power_readback(tmp_path):
    _trace(tmp_path, {2: 100, 3: 70, 4: 100})
    with pytest.raises(SystemExit, match="não prova 100%"):
        _assert_full_power_baseline(tmp_path)
