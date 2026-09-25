import json
from pathlib import Path

import pytest

from scripts.run_tasam_v2x_energy_r5 import (
    PROFILE,
    _arm_command,
    _native_gate_ready,
    _parse_slots,
)


def test_engineering_pair_requires_both_canonical_slots():
    assert _parse_slots("slot-a,slot-b") == ("slot-a", "slot-b")
    with pytest.raises(Exception):
        _parse_slots("slot-a")
    with pytest.raises(Exception):
        _parse_slots("slot-b,slot-a")


def test_energy_arm_command_pins_profile_and_slot(tmp_path: Path):
    command = _arm_command(
        "rapp_only_actuating",
        tmp_path / "rapp",
        tmp_path / "checkpoint",
        tmp_path / "schedule.json",
        "schedule-id",
        tmp_path / "calibration.json",
        wall_time=9000,
        binary=tmp_path / "ns3",
        profile=PROFILE,
        execution_slot="slot-a",
    )
    assert "--profile" in command
    assert command[command.index("--profile") + 1] == PROFILE
    assert command[command.index("--execution-slot") + 1] == "slot-a"
    assert command[command.index("--decision-target") + 1] == "0"
    assert "--energy-enabled" in command
    assert "--disable-app-overrides" in command


def _write_native_gate_fixture(
    root: Path,
    *,
    mode: str,
    ack: bool = True,
    snapshot_end: float = 5.0,
    trace_time: float = 5.0,
    trace_rows: bool = True,
) -> None:
    (root / "xapp_metrics").mkdir(parents=True)
    (root / "xapp_intents").mkdir(parents=True)
    (root / "ns3_energy").mkdir(parents=True)
    (root / "xapp_metrics" / "extended_metrics.json").write_text(
        json.dumps({"sim_time_range": {"end": snapshot_end}}), encoding="utf-8"
    )
    audit = {
        "sequence": 7,
        "ack": ack,
        "applied": ack,
        "fallback": False,
        "mode": mode,
    }
    (root / "xapp_intents" / "tasam_control_audit.jsonl").write_text(
        json.dumps(audit) + "\n", encoding="utf-8"
    )
    (root / "ns3_energy" / "NativeControlContext.csv").write_text(
        "NativeControlSequence,DecisionId,ActionCorrelationId\n"
        + f"7,7,{('economic:' if mode.startswith('economic') else 'operational:rapp_live:')}test\n",
        encoding="utf-8",
    )
    rows = ["Time,EvidenceVersion,NativeControlSequence,CellId,ObservationKind"]
    if trace_rows:
        rows.extend(
            f"{trace_time},v6,7,{cell},{kind}"
            for cell in (2, 3, 4)
            for kind in ("power_readback", "state_snapshot")
        )
    (root / "ns3_energy" / "TasamControlObservations.csv").write_text(
        "\n".join(rows) + "\n", encoding="utf-8"
    )


def test_native_gate_requires_applied_non_fallback_audit(tmp_path: Path):
    rapp = tmp_path / "rapp"
    _write_native_gate_fixture(rapp, mode="operational:rapp_live")
    assert _native_gate_ready(rapp, require_economic=False)

    fallback = tmp_path / "fallback"
    _write_native_gate_fixture(fallback, mode="failsafe", ack=False)
    assert not _native_gate_ready(fallback, require_economic=False)


def test_native_gate_requires_economic_mode_for_asgard(tmp_path: Path):
    asgard = tmp_path / "asgard"
    _write_native_gate_fixture(asgard, mode="operational:rapp_live")
    assert not _native_gate_ready(asgard, require_economic=True)

    economic = tmp_path / "economic"
    _write_native_gate_fixture(economic, mode="economic_action_v3_per_du_sleep")
    assert _native_gate_ready(economic, require_economic=True)


def test_native_gate_uses_event_trace_over_lagging_snapshot(tmp_path: Path):
    """O snapshot estendido atrasa até ~1 s; o trace v6 por evento é autoritativo.

    Regressão do gate flaky: braços com a cadeia v6 completa eram derrubados
    porque o snapshot congelou em 4.8 s simulados no momento do deadline.
    """
    lagging = tmp_path / "lagging"
    _write_native_gate_fixture(
        lagging,
        mode="economic_action_v3_per_du_sleep",
        snapshot_end=4.8,
        trace_time=6.2,
    )
    assert _native_gate_ready(lagging, require_economic=True)

    # Sem linhas v6, o snapshot continua sendo a fonte do contrato de 5 s.
    empty_trace = tmp_path / "empty_trace"
    _write_native_gate_fixture(
        empty_trace,
        mode="economic_action_v3_per_du_sleep",
        snapshot_end=4.8,
        trace_rows=False,
    )
    assert not _native_gate_ready(empty_trace, require_economic=True)

    # Trace v6 abaixo do piso de 5 s também é respeitado (rigor mantido).
    short_trace = tmp_path / "short_trace"
    _write_native_gate_fixture(
        short_trace,
        mode="economic_action_v3_per_du_sleep",
        snapshot_end=4.8,
        trace_time=4.5,
    )
    assert not _native_gate_ready(short_trace, require_economic=True)
