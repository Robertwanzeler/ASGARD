from __future__ import annotations

from pathlib import Path

from scripts.run_tasam_vehicle_feasibility_matrix import (
    _candidate_is_complete_and_valid,
    _provenance_compatible,
    _scheduler_warning,
)


def _result(*, valid: bool = True) -> dict:
    windows = []
    for index in range(30):
        windows.append({
            "index": index,
            "valid": valid,
            "vehicles": {
                str(imsi): {
                    "tx_pdus": 500,
                    "loss_percent": 0.2 if valid else 1.0,
                    "latency_p95_us": 5_000.0,
                }
                for imsi in range(16, 21)
            },
        })
    return {
        "interval_us": 4000,
        "exit_code": 0,
        "classification": "approved" if valid else "radio_infeasible",
        "evidence": {
            "valid": valid,
            "metric_contract": "per_pdu_cohort_v1",
            "evidence_source": "native_pdcp_pdu_tx_rx",
            "complete_windows": 30,
            "valid_windows": 30 if valid else 29,
            "scheduler_trace_valid": True,
            "link_trace_valid": True,
            "native_vehicle_bearer_valid": True,
            "simulation_completion": {"valid": True},
            "windows": windows,
        },
    }


def _manifest(seed: int, *, binary_hash: str = "same") -> dict:
    return {
        "profile": "tasam_training_balanced_v6_v2x_gbr_deadline_mc_fallback",
        "topology": {"ue_count": 20, "du_count": 3, "vehicle_imsis": [16, 17, 18, 19, 20]},
        "metric_contract": "per_pdu_cohort_v1",
        "link_metric_contract": "vehicle_link_state_v2",
        "connectivity_mode": "lte_anchored_mc",
        "scheduler_policy": "gbr_debt_rr_v1",
        "loss_grace_ms": 1000,
        "provenance": {
            "source_hashes": {"runner": "x"},
            "binary": {"sha256": binary_hash},
            "checkpoint": {"actors_sha256": "checkpoint"},
            "submodules": {"ns3-base": "revision"},
        },
        "selected": {
            "evidence": {"native_vehicle_bearer_manifest": {
                "qci": "GBR_V2X_MESSAGES", "priority": 25,
                "gbr_dl_bps": 6_400_000, "mbr_dl_bps": 8_000_000,
                "scheduler_gbr_priority": True, "connectivity_mode": "lte_anchored_mc",
            }},
        },
        "seed": seed,
    }


def test_seed47_reuse_requires_every_complete_vehicle_window():
    valid, reasons = _candidate_is_complete_and_valid(_result())
    assert valid is True
    assert reasons == []

    valid, reasons = _candidate_is_complete_and_valid(_result(valid=False))
    assert valid is False
    assert "candidate_not_approved:radio_infeasible" in reasons


def test_matrix_rejects_different_binary_or_source_provenance():
    manifests = {45: _manifest(45), 46: _manifest(46), 47: _manifest(47)}
    assert _provenance_compatible(manifests) == (True, [])

    manifests[46] = _manifest(46, binary_hash="other")
    valid, reasons = _provenance_compatible(manifests)
    assert valid is False
    assert reasons == ["provenance_mismatch_seed_46"]


def test_out_of_scope_scheduler_rows_are_warning_only(tmp_path: Path):
    trace = tmp_path / "ns3_energy" / "VehicleSchedulerTrace.csv"
    trace.parent.mkdir()
    trace.write_text(
        "Time,CellId,Rnti,Imsi\n"
        "0,4,2,16\n"
        "0,4,0,0\n",
        encoding="utf-8",
    )
    warning = _scheduler_warning(tmp_path)
    assert warning["status"] == "warning"
    assert warning["out_of_scope_rows"] == 1
    assert warning["out_of_scope_imsis"] == [0]
    assert warning["included_in_sla_or_gbr_evidence"] is False
