from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from scripts.greenran_v2x_binary_freshness import (
    assert_v2x_binary_fresh,
    stale_v2x_binary_sources,
)
from scripts.run_tasam_vehicle_feasibility import (
    _candidate_metrics,
    _early_vehicle_sla_violation,
    _native_vehicle_bearer_evidence,
    _per_pdu_cohort_bins,
    _candidate_classification,
    _simulation_completion,
    _validate_scheduler_trace,
    _validate_link_traces,
    _lowest_valid_candidate,
)


def test_strict_v2x_refuses_binary_older_than_evidence_sources(tmp_path: Path):
    binary = tmp_path / "scenario"
    scenario = tmp_path / "scenario.cc"
    scheduler = tmp_path / "scheduler.cc"
    binary.write_text("old executable", encoding="utf-8")
    scenario.write_text("new source", encoding="utf-8")
    scheduler.write_text("old source", encoding="utf-8")
    os.utime(binary, ns=(1_000_000_000, 1_000_000_000))
    os.utime(scenario, ns=(2_000_000_000, 2_000_000_000))
    os.utime(scheduler, ns=(500_000_000, 500_000_000))

    assert stale_v2x_binary_sources(binary, sources=(scenario, scheduler)) == (scenario.resolve(),)
    with pytest.raises(SystemExit, match="binário ns-3 desatualizado"):
        assert_v2x_binary_fresh(binary, sources=(scenario, scheduler))


def test_strict_v2x_accepts_binary_at_or_after_evidence_sources(tmp_path: Path):
    binary = tmp_path / "scenario"
    source = tmp_path / "scenario.cc"
    binary.write_text("fresh executable", encoding="utf-8")
    source.write_text("source", encoding="utf-8")
    os.utime(source, ns=(1_000_000_000, 1_000_000_000))
    os.utime(binary, ns=(1_000_000_000, 1_000_000_000))

    assert stale_v2x_binary_sources(binary, sources=(source,)) == ()
    assert_v2x_binary_fresh(binary, sources=(source,))


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


def _write_pdu_trace(candidate: Path, *, boundary_delay_ms: float = 5.0, duplicate: bool = False) -> None:
    trace = candidate / "ns3_traces" / "VehiclePdcpPduTrace.csv"
    rows = ["Event,EventId,TxTimeNs,RxTimeNs,CellId,IMSI,RNTI,LCID,PacketSize,DelayNs,CorrelationStatus"]
    event_id = 0
    # 500 PDUs per vehicle in the 30--40s cohort.  A later event supplies
    # explicit evidence that the one-second drain was reached.
    for imsi in range(16, 21):
        for sample in range(500):
            event_id += 1
            tx_ns = 30_000_000_000 + sample * 10_000_000
            delay_ns = int(boundary_delay_ms * 1_000_000)
            rows.append(f"TX,{event_id},{tx_ns},,4,{imsi},{imsi - 14},3,100,{0},tx")
            rows.append(f"RX,{event_id},{tx_ns},{tx_ns + delay_ns},4,{imsi},{imsi - 14},3,100,{delay_ns},matched")
    # Deliberately not part of the cohort; proves the drain horizon elapsed.
    rows.append("TX,999999,41000000000,,4,16,2,3,100,0,tx")
    if duplicate:
        rows.append("RX,1,30000000000,30005000000,4,16,2,3,100,5000000,matched")
    trace.write_text("\n".join(rows) + "\n")


def test_per_pdu_contract_counts_window_boundary_after_drain(tmp_path):
    candidate = _write_candidate(tmp_path)
    _write_pdu_trace(candidate)
    result = _candidate_metrics(candidate, 30.0, 10.0, 1, 500, per_pdu=True, loss_grace_seconds=1.0)
    assert result["valid"] is True
    assert result["windows"][0]["vehicles"]["16"]["loss_percent"] == 0.0
    assert result["windows"][0]["vehicles"]["16"]["latency_p95_us"] == 5000.0


def test_per_pdu_contract_rejects_duplicate_rx_identifier(tmp_path):
    candidate = _write_candidate(tmp_path)
    _write_pdu_trace(candidate, duplicate=True)
    bins, error = _per_pdu_cohort_bins(candidate, 30.0, 10.0, 1, 500, 1.0)
    assert bins == []
    assert error == "vehicle_pdcp_unmatched_or_duplicate_rx"


def _write_scheduler_trace(candidate: Path, body: str) -> None:
    path = candidate / "ns3_energy" / "VehicleSchedulerTrace.csv"
    path.write_text(
        "Time,CellId,Rnti,Imsi,Cqi,Mcs,RlcQueueBytes,GbrDlBps,GbrCreditBytes,"
        "RequestedSymbols,GrantedSymbols,GrantedTbBytes,HarqNacks,HarqMaxRetxDrops,"
        "HarqRetxSymbols,DeficitReason\n" + body,
        encoding="utf-8",
    )


def test_scheduler_trace_rejects_control_byte_and_missing_vehicle(tmp_path):
    candidate = _write_candidate(tmp_path)
    _write_scheduler_trace(candidate, "0,4,2,16,\r,20,1,6400000,1,1,1,1,0,0,0,ok\n")
    valid, reason = _validate_scheduler_trace(candidate)
    assert valid is False
    assert reason == "vehicle_scheduler_trace_control_byte"


def test_scheduler_trace_rejects_missing_column(tmp_path):
    candidate = _write_candidate(tmp_path)
    path = candidate / "ns3_energy" / "VehicleSchedulerTrace.csv"
    path.write_text("Time,CellId\n0,4\n", encoding="utf-8")
    valid, reason = _validate_scheduler_trace(candidate)
    assert valid is False
    assert reason == "vehicle_scheduler_trace_header_invalid"


def test_scheduler_trace_rejects_missing_vehicle_imsi(tmp_path):
    candidate = _write_candidate(tmp_path)
    rows = "".join(
        f"0,4,{imsi - 14},{imsi},10,20,1,6400000,1,1,1,1,0,0,0,ok\n"
        for imsi in range(16, 20)
    )
    _write_scheduler_trace(candidate, rows)
    valid, reason = _validate_scheduler_trace(candidate)
    assert valid is False
    assert reason == "vehicle_scheduler_trace_vehicle_coverage_incomplete"


def test_link_state_contract_accepts_one_real_state_per_vehicle_sample(tmp_path):
    candidate = _write_candidate(tmp_path)
    traces = candidate / "ns3_traces"
    traces.joinpath("VehicleCellSinrTrace.csv").write_text(
        "Time,Imsi,CellId,SinrDb\n" + "".join(
            f"0.1,{imsi},4,-2.0\n" for imsi in range(16, 21)
        ), encoding="utf-8"
    )
    traces.joinpath("VehicleLinkTrace.csv").write_text(
        "Time,Imsi,Rnti,Connectivity,ServingCellId,ServingSinrDb,BestMmWaveCellId,"
        "BestMmWaveSinrDb,SinrDeltaDb,OutageThresholdDb,Outage,Cqi,Mcs,HarqNackStreak,"
        "HarqMaxRetxDrops,Event,Reason\n" + "".join(
            f"0.1,{imsi},{imsi - 14},mmwave,4,-2.0,4,-2.0,0,-5.0,false,-1,-1,-1,-1,measurement,test\n"
            for imsi in range(16, 21)
        ), encoding="utf-8"
    )
    assert _validate_link_traces(candidate) == (True, "ok")


def test_link_state_contract_reads_current_ns3_energy_bundle(tmp_path):
    candidate = tmp_path / "candidate"
    traces = candidate / "ns3_energy"
    traces.mkdir(parents=True)
    traces.joinpath("VehicleCellSinrTrace.csv").write_text(
        "Time,Imsi,CellId,SinrDb\n" + "".join(
            f"0.1,{imsi},4,-2.0\n" for imsi in range(16, 21)
        ), encoding="utf-8"
    )
    traces.joinpath("VehicleLinkTrace.csv").write_text(
        "Time,Imsi,Rnti,Connectivity,ServingCellId,ServingSinrDb,BestMmWaveCellId,"
        "BestMmWaveSinrDb,SinrDeltaDb,OutageThresholdDb,Outage,Cqi,Mcs,HarqNackStreak,"
        "HarqMaxRetxDrops,Event,Reason\n" + "".join(
            f"0.1,{imsi},{imsi - 14},mmwave,4,-2.0,4,-2.0,0,-5.0,false,-1,-1,-1,-1,measurement,test\n"
            for imsi in range(16, 21)
        ), encoding="utf-8"
    )
    assert _validate_link_traces(candidate) == (True, "ok")


def test_link_state_contract_rejects_inconsistent_outage(tmp_path):
    candidate = tmp_path / "candidate"
    (candidate / "ns3_traces").mkdir(parents=True)
    traces = candidate / "ns3_traces"
    traces.joinpath("VehicleCellSinrTrace.csv").write_text(
        "Time,Imsi,CellId,SinrDb\n" + "".join(f"0.1,{imsi},4,-10.0\n" for imsi in range(16, 21)),
        encoding="utf-8"
    )
    traces.joinpath("VehicleLinkTrace.csv").write_text(
        "Time,Imsi,Rnti,Connectivity,ServingCellId,ServingSinrDb,BestMmWaveCellId,"
        "BestMmWaveSinrDb,SinrDeltaDb,OutageThresholdDb,Outage,Cqi,Mcs,HarqNackStreak,"
        "HarqMaxRetxDrops,Event,Reason\n" + "".join(
            f"0.1,{imsi},{imsi - 14},mmwave,4,-10.0,4,-10.0,0,-5.0,false,-1,-1,-1,-1,measurement,test\n"
            for imsi in range(16, 21)
        ), encoding="utf-8"
    )
    assert _validate_link_traces(candidate) == (False, "vehicle_link_state_outage_inconsistent")


def test_incomplete_scientific_execution_is_metric_invalid(tmp_path):
    candidate = _write_candidate(tmp_path)
    (candidate / "arm_manifest.json").write_text(json.dumps({
        "simulation_performance": {"sim_time_observed_s": 9.9},
    }))
    evidence = {
        "metric_contract": "per_pdu_cohort_v1",
        "valid": False,
        "reason": "insufficient_complete_windows",
        "complete_windows": 0,
        "required_scored_windows": 1,
    }
    assert _simulation_completion(candidate, 44.5)["reason"] == "simulation_ended_before_required_time"
    assert _candidate_classification(candidate, evidence) == "metric_invalid"


def test_simulation_completion_uses_native_pdu_clock_when_metrics_publisher_is_absent(tmp_path):
    candidate = tmp_path / "candidate"
    (candidate / "ns3_energy").mkdir(parents=True)
    (candidate / "arm_manifest.json").write_text(json.dumps({
        "simulation_performance": {"sim_time_observed_s": 0.0},
    }))
    (candidate / "ns3_energy" / "VehiclePdcpPduTrace.csv").write_text(
        "Event,EventId,TxTimeNs,RxTimeNs,CellId,IMSI,RNTI,LCID,PacketSize,DelayNs,CorrelationStatus\n"
        "TX,1,16000000000,,4,16,2,3,100,0,tx\n"
        "RX,1,16000000000,16400000000,4,16,2,3,100,4000000,matched\n",
        encoding="utf-8",
    )
    result = _simulation_completion(candidate, 16.5)
    assert result["valid"] is True
    assert result["observed_sim_time_source"] == "native_vehicle_pdcp_pdu_trace"


def test_matrix_selection_uses_lowest_valid_interval_after_all_results_exist():
    results = [
        {"interval_us": 4000, "exit_code": 0, "evidence": {"valid": False}},
        {"interval_us": 8000, "exit_code": 0, "evidence": {"valid": True}},
        {"interval_us": 6000, "exit_code": 0, "evidence": {"valid": True}},
        {"interval_us": 12000, "exit_code": 3, "evidence": {"valid": True}},
    ]
    assert _lowest_valid_candidate(results)["interval_us"] == 6000


def test_matrix_selection_accepts_supervisor_sigterm_only_after_native_completion(tmp_path):
    run_dir = tmp_path / "candidate"
    run_dir.mkdir()
    (run_dir / "ns3.log").write_text(
        "[NS3_SUPERVISOR] ns3 exited code 0 at test\n", encoding="utf-8"
    )
    result = {
        "interval_us": 4000,
        "exit_code": 143,
        "command": ["python", "arm.py", "--run-dir", str(run_dir)],
        "evidence": {
            "valid": True,
            "simulation_completion": {"valid": True},
        },
    }
    assert _lowest_valid_candidate([result]) == result


def test_non_vehicle_scheduler_deficits_do_not_classify_vehicle_sla(tmp_path):
    candidate = _write_candidate(tmp_path)
    _write_scheduler_trace(
        candidate,
        "".join(
            f"{index},4,0,0,0,0,0,0,0,0,0,0,0,0,0,scheduler_capacity_shortfall\n"
            for index in range(8)
        ),
    )
    assert _candidate_classification(candidate, {
        "metric_contract": "per_pdu_cohort_v1", "valid": True,
    }) == "approved"
