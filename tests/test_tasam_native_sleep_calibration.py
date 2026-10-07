from __future__ import annotations

from pathlib import Path

from src.tasam_native_sleep_calibration import (
    _pdcp_window_valid,
    choose_drain_plan,
)


def test_sleep_drain_requires_all_twenty_imsis_with_mc_destinations():
    snapshot = {
        2: {"imsis": set(range(1, 6)), "count": 5, "time_s": 30.0},
        3: {"imsis": set(range(1, 16)), "count": 15, "time_s": 30.0},
        4: {"imsis": set(range(6, 21)), "count": 15, "time_s": 30.0},
    }
    source, plan = choose_drain_plan(snapshot)
    assert source == 2
    assert {item["imsi"] for item in plan} == set(range(1, 6))


def test_sleep_pdcp_gate_accepts_native_uppercase_imsi_header(tmp_path: Path):
    trace = tmp_path / "VehiclePdcpPduTrace.csv"
    trace.write_text(
        "Time,IMSI,Event\n"
        + "".join(f"{10 + index / 10}, {imsi}, rx\n".replace(" ", "")
                    for index, imsi in enumerate(range(16, 21)))
    )
    assert _pdcp_window_valid(trace, after_s=10.0)
