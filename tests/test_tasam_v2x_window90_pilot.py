from __future__ import annotations

import ast
import csv
import inspect
import json
from pathlib import Path

from scripts.run_tasam_online_arm import build_environment, mode_contract
from scripts.run_tasam_online_controlled import (
    V2X_REPLAY_WINDOW90_SCHEMA,
    build_v2x_replay_window90,
    run_update,
)
from scripts.run_tasam_v2x_window90_pilot import STAGES, select_stage_transitions
from src.greenran_v2x_window90 import validate_online_transition


def _write_rows(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _row(stage: str, decision_id: int) -> dict:
    return {
        "decision_stage_name": stage,
        "scenario_stage": stage,
        "scenario_control_override": False,
        "timestamp": decision_id,
        "decision_id": decision_id,
        "next_metrics": {"collector_mode": "pdcp_real"},
        "metrics": {"latency_p95_per_ue_us": 5000, "global_packet_loss_rate": 0.001},
        "collection_quality": {
            "collector_mode": "pdcp_real", "pdcp_real": True,
            "proxy_latency_sample_count": 0, "metric_alignment_valid": True,
            "sim_reset": False,
        },
        "decision": {
            "id": decision_id,
            "action_correlation_id": f"corr-{decision_id}",
            "e2_ack_complete": True,
            "native_readback_observed": True,
        },
    }


def _native_files(arm: Path, count: int) -> None:
    (arm / "ns3_energy").mkdir(parents=True)
    (arm / "ns3_traces").mkdir(parents=True)
    (arm / "ns3_traces" / "DlPdcpStats.txt").write_text("native\n", encoding="utf-8")
    with (arm / "ns3_energy" / "NativeControlContext.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["DecisionId"])
        writer.writeheader()
        for i in range(1, count + 1):
            writer.writerow({"DecisionId": i})
    with (arm / "ns3_energy" / "TasamControlObservations.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["DecisionId", "ObservationKind", "ActionCorrelationId"])
        writer.writeheader()
        for i in range(1, count + 1):
            writer.writerow({"DecisionId": i, "ObservationKind": "power_readback", "ActionCorrelationId": f"corr-{i}"})


def test_window90_selects_exactly_ten_per_stage(tmp_path: Path):
    arm = tmp_path / "arm"
    rows = [_row(stage, index) for index, stage in enumerate(STAGES * 10, start=1)]
    raw = tmp_path / "raw.jsonl"
    _write_rows(raw, rows)
    _native_files(arm, len(rows))

    selected, report = select_stage_transitions(arm, raw, "baseline_rapp_only")

    assert report["complete"] is True
    assert len(selected) == 90
    assert all(report["per_stage"][stage] == 10 for stage in STAGES)
    assert len({row["tasam_experience_id"] for row in selected}) == 90


def test_window90_replay_is_exact_72_18(tmp_path: Path):
    historical = tmp_path / "historical.jsonl"
    recent = tmp_path / "recent.jsonl"
    output = tmp_path / "replay.jsonl"
    def make(phase: str, start: int, total: int):
        return [{
            "replay_phase": phase, "replay_episode": "e", "replay_seed": 43,
            "replay_timestamp": start + i, "timestamp": start + i,
            "scenario_control_override": False,
            "reward_contract": "greenran.tasam.v2x.reward_adaptive.v1",
            "adaptive_reward": {"reward_contract": "greenran.tasam.v2x.reward_adaptive.v1"},
            "next_metrics": {}, "judge_feedback": {},
            "collection_quality": {"valid_for_training": True, "collector_mode": "pdcp_real", "pdcp_real": True, "proxy_latency_sample_count": 0, "metric_alignment_valid": True, "sim_reset": False, "current_metric_skew_s": 0, "next_metric_skew_s": 0},
        } for i in range(total)]
    _write_rows(historical, make("baseline", 0, 90))
    _write_rows(recent, make("asgard", 1000, 18))

    report = build_v2x_replay_window90(historical, recent, output, seed=43)

    assert report["schema"] == V2X_REPLAY_WINDOW90_SCHEMA
    assert report["historical_used"] == 72
    assert report["recent_used"] == 18
    assert report["total"] == 90


def test_window90_asgard_is_full_rollout_and_non_promotable(tmp_path: Path):
    contract = mode_contract("asgard_v2x_window90_online")
    env = build_environment(
        "asgard_v2x_window90_online", tmp_path / "arm", 43,
        "tasam_training_balanced_v6_v2x_gbr_deadline_mc_fallback", 120,
        native_fidelity=True, max_rollout_fraction=1.0,
    )
    assert contract["replay_contract"] == V2X_REPLAY_WINDOW90_SCHEMA
    assert contract["pilot_rollout_100"] is True
    assert env["GREENRAN_TASAM_ONLINE_ROLLOUT_FRACTION"] == "1.0"
    assert env["GREENRAN_TASAM_PILOT_FULL_ROLLOUT"] == "1"


def test_window90_asgard_requires_real_only_environment(tmp_path: Path):
    env = build_environment(
        "asgard_v2x_window90_online", tmp_path / "arm", 43,
        "tasam_training_balanced_v6_v2x_gbr_deadline_mc_fallback", 120,
        native_fidelity=True, max_rollout_fraction=1.0,
        disable_app_overrides=True,
    )
    assert env["GREENRAN_COLLECTION_DISABLE_APP_OVERRIDES"] == "1"


def test_window90_live_transition_fails_closed_until_judge_is_observed():
    row = _row("allowed_bootstrap", 1)
    row.update({
        "judge_feedback": {"outcome_observed": True, "tasam_reward_source": "observed_real_metrics"},
        "judge_feedback_observed": True,
        "action_correlation_valid": True,
        "reward_contract": "greenran.tasam.v2x.reward_adaptive.v1",
        "adaptive_reward": {"reward_contract": "greenran.tasam.v2x.reward_adaptive.v1"},
    })
    ok, reasons = validate_online_transition(row)
    assert not ok
    assert "collection_quality_invalid" in reasons
    assert "next_pdcp_metrics_missing" not in reasons

    row["collection_quality"]["valid_for_training"] = True
    row["next_metrics"] = {"pdcp": "real"}
    ok, reasons = validate_online_transition(row)
    assert ok, reasons

    row["scenario_control_override"] = True
    ok, reasons = validate_online_transition(row)
    assert not ok
    assert "scenario_control_override" in reasons


def test_window90_successful_training_reaches_candidate_annotation_not_failure_return():
    """Keep a successful V2X learner invocation out of the failure branch.

    A prior indentation error placed the V2X failure return after the
    ``try/except`` block, which made every successful candidate crash while
    formatting an exception that did not exist.  This structural regression
    test keeps the error return scoped to the exception handler and the V2X
    annotation on the success path.
    """
    function = ast.parse(inspect.getsource(run_update)).body[0]
    assert isinstance(function, ast.FunctionDef)
    training_handler = next(
        handler
        for node in ast.walk(function)
        if isinstance(node, ast.Try)
        for handler in node.handlers
        if any(
            isinstance(child, ast.Return)
            and isinstance(child.value, ast.Dict)
            and any(
                isinstance(key, ast.Constant)
                and key.value == "status"
                and isinstance(value, ast.Constant)
                and value.value == "training_failed"
                for key, value in zip(child.value.keys, child.value.values)
            )
            for child in ast.walk(handler)
        )
    )
    assert any(
        isinstance(node, ast.Return)
        and isinstance(node.value, ast.Dict)
        for node in ast.walk(training_handler)
    )
    assert any(
        isinstance(node, ast.If)
        and isinstance(node.test, ast.Name)
        and node.test.id == "v2x_replay"
        and any(
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == "_annotate_v2x_candidate"
            for call in ast.walk(node)
        )
        for node in function.body
    )
