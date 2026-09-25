from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from scripts import run_tasam_v2x_micro_baseline as micro
from src.rapp_xapp_manager import build_xapp_command


def _args(**overrides):
    values = {
        "profile": micro.PROFILE,
        "mode": micro.MODE,
        "seed": 47,
        "sim_time": 120.0,
        "decision_target": 0,
        "performance_min_rtf": 0.016,
        "energy_enabled": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_fixed_micro_contract_is_strict():
    micro.validate_fixed_args(_args())
    with pytest.raises(SystemExit):
        micro.validate_fixed_args(_args(sim_time=60.0))
    with pytest.raises(SystemExit):
        micro.validate_fixed_args(_args(decision_target=1))
    with pytest.raises(SystemExit):
        micro.validate_fixed_args(_args(performance_min_rtf=0.1))


def test_stage_evidence_requires_all_stages_and_pdcp_correlation(tmp_path: Path):
    lines = []
    for index, stage in enumerate(micro.EXPECTED_STAGES):
        lines.append(json.dumps({
            "collection_event_stage_name": stage,
            "snapshot_sequence_id": f"pdcp:{index + 1}",
        }))
    (tmp_path / "rapp_decisions.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    result = micro.stage_evidence(tmp_path)
    assert result["complete_order"] is True
    assert result["correlated_decisions"] == len(micro.EXPECTED_STAGES)


def test_stage_evidence_rejects_missing_stage(tmp_path: Path):
    (tmp_path / "rapp_decisions.jsonl").write_text(
        json.dumps({"collection_event_stage_name": micro.EXPECTED_STAGES[0], "snapshot_sequence_id": "pdcp:1"}) + "\n",
        encoding="utf-8",
    )
    assert micro.stage_evidence(tmp_path)["complete_order"] is False


def test_micro_report_is_never_promotable(tmp_path: Path, monkeypatch):
    arm = tmp_path / "arm"
    arm.mkdir()
    monkeypatch.setattr(micro, "_decision_records", lambda _: [])
    report = micro.build_report(tmp_path, 0)
    assert report["scientific_decision"] == "not_promotable"
    assert report["promotion_eligible"] is False
    assert report["baseline_eligible"] is False


def test_pdcp_requires_all_vehicle_imsis(tmp_path: Path):
    trace = tmp_path / "ns3_traces"
    trace.mkdir()
    (trace / "DlPdcpStats.txt").write_text(
        "\n".join(f"0 1 1 {imsi} 1 3 10 8000 10 8000 0.001" for imsi in micro.VEHICLE_IMSIS) + "\n",
        encoding="utf-8",
    )
    evidence = micro.pdcp_evidence(tmp_path)
    assert evidence["collector_mode"] == "pdcp_real"
    assert evidence["complete_vehicle_coverage"] is True


def test_tasam_actuator_command_receives_isolated_xapp_port():
    command = build_xapp_command(
        "tasam_actuator", "/bin/actuator", "/etc/flexric.conf", "/opt/flexric", "36422"
    )
    assert command == [
        "/bin/actuator", "-c", "/etc/flexric.conf", "-p", "/opt/flexric/", "-x", "36422"
    ]


def test_subscription_xapps_keep_their_xapp_port():
    command = build_xapp_command(
        "slicer", "/bin/slicer", "/etc/flexric.conf", "/opt/flexric", "36422"
    )
    assert command[-2:] == ["-x", "36422"]
