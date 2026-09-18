from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path


ROOT = Path(__file__).parents[1]


def _module():
    path = ROOT / "scripts" / "run_tasam_online_economic_campaign.py"
    spec = importlib.util.spec_from_file_location("online_economic_observation", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_observation_summary_is_not_causal_and_records_economic_outcomes(tmp_path):
    module = _module()
    adaptation = tmp_path / "adaptation_online"
    adaptation.mkdir()
    db = adaptation / "rapp_data_lake.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "create table decisions_history ("
            "id integer, economic_application_status text, economic_transition_eligible integer, "
            "tasam_checkpoint_valid integer, tasam_fallback_used integer, live_power_w real, "
            "shadow_power_w real, energy_saving_fraction real, resource_saving_fraction real, "
            "tasam_online_reward real, total_budget_fraction real, armd_override_applied integer)"
        )
        conn.execute(
            "insert into decisions_history values "
            "(1, 'applied', 1, 1, 0, 100, 90, .1, .05, .08, .7, 0)"
        )
        conn.commit()
    (adaptation / "online_status.json").write_text(
        json.dumps({"updates_completed": 1, "candidate_promoted": True})
    )
    checkpoint = tmp_path / "checkpoint"
    checkpoint.mkdir()
    (checkpoint / "tasam_marl_checkpoint_meta.json").write_text(
        json.dumps({"parent_checkpoint": "/local/parent"})
    )
    calibration = tmp_path / "calibration.json"
    calibration.write_text(
        json.dumps({"calibration_version": "sim_native_v2_36cell", "status": "experimental_combined_model"})
    )
    summary = module._observation_summary(
        tmp_path / "campaign", adaptation, checkpoint, calibration, min_applied_actions=1
    )
    assert summary["schema"] == "greenran.tasam.online_observation.v1"
    assert summary["causal_comparison"] is False
    assert summary["applied_decisions"] == 1
    assert summary["average_energy_saving_fraction"] == 0.1
    assert summary["valid"] is True


def test_observation_summary_hydrates_applied_action_and_feedback_json(tmp_path):
    module = _module()
    adaptation = tmp_path / "adaptation_online"
    adaptation.mkdir()
    db = adaptation / "rapp_data_lake.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "create table decisions_history ("
            "id integer, timestamp integer, metric_snapshot_id integer, "
            "economic_action_json text, economic_application_status text, "
            "economic_transition_eligible integer, tasam_checkpoint_valid integer, "
            "tasam_fallback_used integer, tasam_evidence_valid integer, "
            "live_power_w real, shadow_power_w real, energy_saving_fraction real, "
            "resource_saving_fraction real)"
        )
        conn.execute(
            "create table judge_outcome_history (decision_id integer, feedback_json text)"
        )
        conn.execute(
            "create table extended_metrics (id integer, timestamp integer)"
        )
        conn.execute(
            "create table ue_metrics ("
            "timestamp integer, imsi integer, tx_pdus integer, rx_pdus integer, "
            "pdcp_provenance text, packet_loss_percent real)"
        )
        conn.execute(
            "insert into decisions_history values "
            "(1, 100, 7, ?, 'applied', 1, 1, 0, 1, 100, 80, .2, .1)",
            (json.dumps({
                "contract": "applied_action_v2",
                "applied": {
                    "power_percent": 50,
                    "total_allocation": .5,
                    "ran_allocation": .2,
                    "ai_allocation": .3,
                    "usable_budget": 1.0,
                },
            }),),
        )
        conn.execute(
            "insert into judge_outcome_history values (?, ?)",
            (1, json.dumps({"feedback": {
                "tasam_online_reward": .12,
                "tasam_energy_reward": .2,
                "tasam_allocation_reward": .1,
                "tasam_sla_penalty": 0.0,
                "applied_power_percent": 50,
                "applied_total_allocation": .5,
                "applied_ran_allocation": .2,
                "applied_ai_allocation": .3,
            }})),
        )
        conn.execute("insert into extended_metrics values (7, 100)")
        for imsi in range(1, 21):
            conn.execute(
                "insert into ue_metrics values (100, ?, 10, 9, 'pdcp_real', 10.0)",
                (imsi,),
            )
        conn.commit()
    (adaptation / "online_status.json").write_text(
        json.dumps({"updates_completed": 1, "candidate_promoted": True})
    )
    checkpoint = tmp_path / "checkpoint"
    checkpoint.mkdir()
    (checkpoint / "tasam_marl_checkpoint_meta.json").write_text(
        json.dumps({"parent_checkpoint": "/local/parent"})
    )
    calibration = tmp_path / "calibration.json"
    calibration.write_text(
        json.dumps({"calibration_version": "sim_native_v2_36cell", "status": "experimental_combined_model"})
    )

    summary = module._observation_summary(
        tmp_path / "campaign", adaptation, checkpoint, calibration, min_applied_actions=1
    )

    assert summary["average_applied_power_percent"] == 50.0
    assert summary["average_applied_total_allocation"] == 0.5
    assert summary["average_total_budget_fraction"] == 0.5
    assert summary["average_online_reward"] == 0.12
    assert summary["average_energy_reward"] == 0.2
    assert summary["average_allocation_reward"] == 0.1
    assert summary["camera_sla"] == 0.9
    assert summary["sensor_sla"] == 0.9
    assert summary["vehicle_sla"] == 0.9
