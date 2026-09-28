from __future__ import annotations

import json

from src.rapp_orchestrator import RappResourceOptimizer
from src.rapp_data_lake import DataLake
from src.tasam_dynamic_floor import DYNAMIC_FLOOR_CONTRACT


def _runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("GREENRAN_TASAM_DYNAMIC_FLOOR_CONTRACT", DYNAMIC_FLOOR_CONTRACT)
    runtime = RappResourceOptimizer.__new__(RappResourceOptimizer)
    runtime._dynamic_floor_state_path = tmp_path / "dynamic_floor_state.json"
    runtime._dynamic_floor_state = {
        "contract": DYNAMIC_FLOOR_CONTRACT,
        "floor_percent_by_cell": {"2": 45, "3": 45, "4": 45},
        "projection_count": 0,
        "applied_projection_count": 0,
        "actor_influenced_decisions": 0,
    }
    return runtime


def test_runtime_preserves_asgard_power_above_floor(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path, monkeypatch)
    decision = {"selected_assistant": "ta_sam"}
    selected, failsafe = runtime._apply_energy_staircase(
        decision, {}, {}, {2: 40, 3: 70, 4: 45}
    )
    assert not failsafe
    assert selected == {2: 45, 3: 70, 4: 45}
    assert decision["dynamic_floor_projection"]["actor_preserved_cells"] == [3, 4]
    assert decision["dynamic_floor_projection"]["actor_influenced_cells"] == [3]

    runtime._record_dynamic_floor_application(
        decision,
        {
            "mode": "economic",
            "cells": [
                {"cell_id": 2, "tx_power_percent": 45},
                {"cell_id": 3, "tx_power_percent": 70},
                {"cell_id": 4, "tx_power_percent": 45},
            ],
        },
        accepted=True,
        failsafe=False,
    )
    assert runtime._dynamic_floor_state["actor_influenced_decisions"] == 1
    assert decision["dynamic_floor_actor_influenced_cells"] == [3]


def test_runtime_isolation_overrides_projection_and_is_persisted(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path, monkeypatch)
    decision = {
        "selected_assistant": "ta_sam",
        "snapshot_sequence_id": "pair:snapshot:000001",
        "tasam_control_sequence": 9,
        "economic_safety_isolation_reason": "critical_vehicle_sla",
    }
    runtime._apply_dynamic_floor(decision, {2: 45, 3: 45, 4: 45})
    bundle = {
        "mode": "failsafe",
        "reason": "critical_vehicle_sla",
        "cells": [
            {"cell_id": 2, "tx_power_percent": 100},
            {"cell_id": 3, "tx_power_percent": 100},
            {"cell_id": 4, "tx_power_percent": 100},
        ],
    }
    runtime._record_dynamic_floor_application(
        decision, bundle, accepted=True, failsafe=True
    )
    assert decision["dynamic_floor_applied"] is False
    assert decision["dynamic_floor_overridden_by_isolation"] is True
    state = json.loads(runtime._dynamic_floor_state_path.read_text())
    assert state["last_projection"]["safety_isolated"] is True
    assert state["last_projection"]["applied_power_percent_by_cell"] == {
        "2": 100, "3": 100, "4": 100,
    }


def test_runtime_records_asgard_resource_authority_even_at_zero_cap(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path, monkeypatch)
    decision = {
        "dynamic_floor_projected": True,
        "dynamic_floor_projection": {
            "requested_power_percent_by_cell": {"2": 25, "3": 25, "4": 25},
            "floor_percent_by_cell": {"2": 25, "3": 25, "4": 25},
            "selected_power_percent_by_cell": {"2": 25, "3": 25, "4": 25},
            "actor_influenced_cells": [],
        },
        "adaptive_resource_budget": {
            "applied_discretionary_dl_symbols_bp_by_cell": {"2": 0, "3": 3000, "4": 3000},
        },
    }
    bundle = {
        "mode": "economic_action_v3_per_du_sleep",
        "cells": [
            {"cell_id": 2, "tx_power_percent": 25, "max_discretionary_dl_symbols_bp": 0},
            {"cell_id": 3, "tx_power_percent": 25, "max_discretionary_dl_symbols_bp": 3000},
            {"cell_id": 4, "tx_power_percent": 25, "max_discretionary_dl_symbols_bp": 3000},
        ],
    }
    runtime._record_dynamic_floor_application(decision, bundle, accepted=True, failsafe=False)
    assert decision["dynamic_floor_resource_influenced_cells"] == [2, 3, 4]
    assert runtime._dynamic_floor_state["actor_influenced_decisions"] == 1
    assert runtime._dynamic_floor_state["resource_influenced_decisions"] == 1


def test_runtime_sla_keys_match_strict_pair_shape(tmp_path):
    lake = DataLake(str(tmp_path / "runtime.db"))
    try:
        metric_id = lake.record_extended_metric(timestamp=123, sim_time_s=50.1)
        rows = []
        for imsi in range(1, 21):
            rows.append({
                "imsi": imsi,
                "device_type": (
                    "camera" if imsi <= 3 else "sensor" if imsi <= 15 else "vehicle"
                ),
                "throughput_kbps": 30_000 if imsi <= 3 else 1_000,
                "latency_us": 5_000,
                "latency_p95_us": 5_000,
                "latency_max_us": 6_000,
                "tx_pdus": 100,
                "rx_pdus": 98 if imsi == 16 else 100,
                "packet_loss_percent": 2.0 if imsi == 16 else 0.0,
                "backlog_bytes": 0,
                "has_latency_samples": True,
                "latency_is_proxy": False,
                "pdcp_provenance": "pdcp_real",
                "sim_time_s": 50.1,
            })
        lake.record_ue_metrics(timestamp=123, ue_metrics_list=rows, sim_time_s=50.1)
        evidence = lake.sla_violation_keys(metric_id)
        assert evidence["valid"] is True
        assert evidence["violation_keys"] == [(50, 16, "vehicle_loss")]
    finally:
        lake.close()


def test_sleep_runs_drain_then_commits_only_after_empty_10_second_window(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path, monkeypatch)
    source_present = {
        2: {1, 2},
        3: set(range(1, 11)),
        4: set(range(11, 21)),
    }
    decision = {
        "tasam_control_sequence": 9,
        "dynamic_floor_projection": {"requested_power_percent_by_cell": {"2": 0, "3": 50, "4": 50}},
    }
    draining, transition, failsafe = runtime._adaptive_sleep_transition(
        decision, {2: 25, 3: 50, 4: 50}, source_present,
        sla_valid=True, pdcp_mature=True, sim_time_s=1.0,
    )
    assert not failsafe and transition["phase"] == "drain"
    assert draining == {2: 25, 3: 100, 4: 100}

    empty_source = {2: set(), 3: set(range(1, 11)), 4: set(range(11, 21))}
    _, transition, _ = runtime._adaptive_sleep_transition(
        decision, draining, empty_source, sla_valid=True, pdcp_mature=True, sim_time_s=2.0,
    )
    assert transition["phase"] == "drain"
    committed, transition, failsafe = runtime._adaptive_sleep_transition(
        decision, draining, empty_source, sla_valid=True, pdcp_mature=True, sim_time_s=12.0,
    )
    assert not failsafe and transition["phase"] == "commit"
    assert committed[2] == 0
    still_committed, transition, failsafe = runtime._adaptive_sleep_transition(
        decision, committed, empty_source, sla_valid=True, pdcp_mature=True, sim_time_s=13.0,
    )
    assert not failsafe and transition["phase"] == "commit" and still_committed[2] == 0
    awake, transition, failsafe = runtime._adaptive_sleep_transition(
        decision, committed, empty_source, sla_valid=True, pdcp_mature=False, sim_time_s=14.0,
    )
    assert failsafe and transition["phase"] == "wake" and set(awake.values()) == {100}


def test_native_empty_du_snapshot_is_imported_with_sleep_identity(tmp_path):
    trace = tmp_path / "TasamAssociationTrace.csv"
    trace.write_text(
        "Time,CellId,Rnti,Imsi,AssociationEpoch,EvidenceVersion,CampaignId,SourceGeneration,"
        "TransactionId,NativeControlSequence,DecisionId,ActionCorrelationId,ObservationKind,"
        "AttachedUeCount,SleepTransactionId\n"
        "12,2,,,rrc-snapshot-3,v7,campaign,native,9,9,3,corr,cell_snapshot,0,sleep:9:2\n",
        encoding="utf-8",
    )
    lake = DataLake(str(tmp_path / "snapshot.db"))
    try:
        assert lake.ingest_native_association_observations(trace) == 1
        row = lake.conn.execute(
            "SELECT cell_id, attached_ue_count, sleep_transaction_id "
            "FROM tasam_native_association_snapshots"
        ).fetchone()
        assert tuple(row) == (2, 0, "sleep:9:2")
    finally:
        lake.close()


def test_empty_association_snapshot_requires_the_active_sleep_transaction(tmp_path, monkeypatch):
    trace = tmp_path / "TasamAssociationTrace.csv"
    rows = [
        "Time,CellId,Rnti,Imsi,AssociationEpoch,EvidenceVersion,CampaignId,SourceGeneration,"
        "TransactionId,NativeControlSequence,DecisionId,ActionCorrelationId,ObservationKind,"
        "AttachedUeCount,SleepTransactionId\n",
        "12,2,,,rrc-snapshot-1,v7,campaign,native,9,9,3,corr,cell_snapshot,0,sleep:9:2\n",
    ]
    for imsi in range(1, 21):
        cell = 3 if imsi <= 10 else 4
        rows.append(
            f"12,{cell},{imsi},{imsi},rrc-epoch-1,v7,campaign,native,9,9,3,corr,"
            "ue_association,1,sleep:9:2\n"
        )
    trace.write_text("".join(rows), encoding="utf-8")
    monkeypatch.setenv("GREENRAN_NS3_ENERGY_OUTPUT_DIR", str(tmp_path))
    runtime = RappResourceOptimizer.__new__(RappResourceOptimizer)
    runtime.data_lake = DataLake(str(tmp_path / "association.db"))
    try:
        cells, evidence = runtime._native_association_cells(
            12.0, allow_empty_cell=2, allow_empty_sleep_transaction="sleep:9:2"
        )
        assert cells[2] == set() and set(cells[3]) == set(range(1, 11))
        assert evidence[2]["imsis"] == []
        cells, reason = runtime._native_association_cells(
            12.0, allow_empty_cell=2, allow_empty_sleep_transaction="wrong"
        )
        assert cells == {}
        assert reason == "association_empty_du_without_matching_sleep_transaction"
    finally:
        runtime.data_lake.close()


def test_native_scheduler_budget_observation_is_persisted(tmp_path):
    trace = tmp_path / "TasamControlObservations.csv"
    trace.write_text(
        "Time,CellId,SchedulerTransactionId,PowerTransactionId,ActiveUes,TxPowerPercent,"
        "TxPowerDbm,NominalTxPowerDbm,ObservationKind,PolicyActive,PolicyExpiryTime,"
        "SourceGeneration,AssociationEpoch,ActiveDlSymbols,ActiveDlSymbolCapacity,"
        "RequestedDiscretionaryDlSymbolsBp,AppliedDiscretionaryDlSymbolsBp,"
        "MandatoryDlSymbols,DiscretionaryDlSymbols,WithheldDlSymbols,SleepTransactionId,"
        "CampaignId,EvidenceVersion,CampaignGeneration,DecisionId,ActionCorrelationId,"
        "NativeControlSequence,NativeAllocationSource\n"
        "12,2,9,9,6,25,23,30,state_snapshot,1,17,native,rrc-1,11,14,3000,3000,8,3,5,"
        "sleep:9:2,campaign,v7,native,3,corr,9,tasam_native_aggregate_v1\n",
        encoding="utf-8",
    )
    lake = DataLake(str(tmp_path / "control.db"))
    try:
        assert lake.ingest_native_control_observations(trace) == 1
        row = lake.conn.execute(
            "SELECT requested_discretionary_dl_symbols_bp, applied_discretionary_dl_symbols_bp, "
            "mandatory_dl_symbols, discretionary_dl_symbols, withheld_dl_symbols, sleep_transaction_id "
            "FROM tasam_control_observations"
        ).fetchone()
        assert tuple(row) == (3000, 3000, 8, 3, 5, "sleep:9:2")
    finally:
        lake.close()
