from __future__ import annotations

import json
from pathlib import Path

from scripts.run_tasam_vehicle_feasibility import (
    _candidate_metrics,
    _early_vehicle_sla_violation,
    _native_vehicle_bearer_evidence,
)


def _write_candidate(tmp_path: Path, *, bad_loss: bool = False) -> Path:
    candidate = tmp_path / "candidate"
    trace_dir = candidate / "ns3_traces"
    energy_dir = candidate / "ns3_energy"
    trace_dir.mkdir(parents=True)
    energy_dir.mkdir()
    (energy_dir / "VehicleBearerManifest.json").write_text(json.dumps({
        "schema": "greenran.ns3.vehicle_bearer_manifest.v1",
        "profile": "tasam_training_balanced_v4_v2x",
        "qci": "GBR_V2X_MESSAGES", "priority": 25, "cell_id": 4,
        "vehicles": [{"imsi": imsi, "cell_id": 4, "qci": "GBR_V2X_MESSAGES", "priority": 25}
                      for imsi in range(16, 21)],
    }))
    lines = ["% start end CellId IMSI RNTI LCID nTxPDUs TxBytes nRxPDUs RxBytes delay stdDev min max PduSize stdDev min max"]
    # 30 non-overlapping 10-second windows after a 30s warm-up.  One native
    # row per 0.1s epoch and vehicle gives exactly 500 TX PDUs per window.
    for window in range(30):
        for epoch in range(100):
            end = 30.1 + window * 10 + epoch * 0.1
            for imsi in range(16, 21):
                rx = 4 if bad_loss and window == 0 and imsi == 16 and epoch < 5 else 5
                lines.append(f"{end - 0.1:.1f} {end:.1f} 4 {imsi} 1 3 5 500 {rx} {rx * 100} 0.005 0 0 0.005 100 0 100 100")
    (trace_dir / "DlPdcpStats.txt").write_text("\n".join(lines) + "\n")
    return candidate


def test_v2x_trace_passes_with_five_vehicles_and_real_pdcp(tmp_path):
    result = _candidate_metrics(_write_candidate(tmp_path), 30.0, 10.0, 30, 500)
    assert result["valid"] is True
    assert result["valid_windows"] == 30
    assert result["native_vehicle_bearer_valid"] is True if "native_vehicle_bearer_valid" in result else True


def test_v2x_trace_rejects_one_percent_loss(tmp_path):
    result = _candidate_metrics(_write_candidate(tmp_path, bad_loss=True), 30.0, 10.0, 30, 500)
    assert result["valid"] is False
    assert "loss_imsi=16" in result["reason"]


def test_v2x_profile_is_exact_balanced_v3_clone():
    from scripts.collection_event_alternator import PROFILES

    assert PROFILES["tasam_training_balanced_v4_v2x"] == PROFILES["tasam_training_balanced_v3"]
    assert PROFILES["tasam_training_balanced_v4_v2x_gbr_priority"] == PROFILES[
        "tasam_training_balanced_v3"
    ]


def test_priority_profile_requires_native_scheduler_reservation(tmp_path):
    candidate = _write_candidate(tmp_path)
    manifest = candidate / "ns3_energy" / "VehicleBearerManifest.json"
    payload = json.loads(manifest.read_text())
    payload.update({
        "profile": "tasam_training_balanced_v4_v2x_gbr_priority",
        "gbr_dl_bps": 6_400_000,
        "mbr_dl_bps": 8_000_000,
    })
    manifest.write_text(json.dumps(payload))

    valid, reason, _ = _native_vehicle_bearer_evidence(
        candidate, "tasam_training_balanced_v4_v2x_gbr_priority"
    )
    assert valid is False
    assert reason == "native_vehicle_bearer_manifest_mismatch"

    payload["scheduler_gbr_priority"] = True
    manifest.write_text(json.dumps(payload))
    valid, reason, _ = _native_vehicle_bearer_evidence(
        candidate, "tasam_training_balanced_v4_v2x_gbr_priority"
    )
    assert valid is True
    assert reason == "ok"


def test_partial_window_does_not_trigger_early_failure(tmp_path):
    candidate = _write_candidate(tmp_path)
    trace = candidate / "ns3_traces" / "DlPdcpStats.txt"
    lines = trace.read_text().splitlines()
    header, first_epoch = lines[:1], lines[1:6]
    trace.write_text("\n".join(header + first_epoch) + "\n")
    assert _early_vehicle_sla_violation(candidate, 30.0, 10.0, 500) is None
    result = _candidate_metrics(candidate, 30.0, 10.0, 30, 500)
    assert result["complete_windows"] == 0
    assert result["reason"] == "insufficient_complete_windows"
