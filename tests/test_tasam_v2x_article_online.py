from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.run_tasam_online_arm import (
    _validate_fixed_native_power_percent,
    build_environment,
    mode_contract,
)
from scripts.run_tasam_online_controlled import V2X_REPLAY_80_20_SCHEMA, build_v2x_replay_80_20
from scripts.run_tasam_vehicle_feasibility import _frozen_policy_evidence
from src.rapp_orchestrator import RappResourceOptimizer


def _row(*, phase: str, episode: int, seed: int, timestamp: int, evaluation: bool = False) -> dict:
    return {
        "replay_phase": phase,
        "replay_episode": episode,
        "replay_seed": seed,
        "replay_timestamp": timestamp,
        "timestamp": timestamp,
        "replay_partition": "evaluation" if evaluation else "training",
        "scenario_control_override": False,
        "reward_contract": "greenran.tasam.v2x.reward_adaptive.v1",
        "adaptive_reward": {
            "reward_contract": "greenran.tasam.v2x.reward_adaptive.v1",
            "reward": 0.5,
        },
        "next_metrics": {"packet_loss_percent": 0.1},
        "judge_feedback": {"feedback_status": "observed"},
        "collection_quality": {
            "valid_for_training": True,
            "collector_mode": "pdcp_real",
            "pdcp_real": True,
            "proxy_latency_sample_count": 0,
            "metric_alignment_valid": True,
            "sim_reset": False,
            "current_metric_skew_s": 0,
            "next_metric_skew_s": 0,
        },
    }


def _write(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_v2x_replay_uses_exact_480_120_without_duplicates(tmp_path: Path):
    historical = tmp_path / "historical.jsonl"
    recent = tmp_path / "recent.jsonl"
    output = tmp_path / "replay.jsonl"
    _write(historical, [_row(phase="seed43", episode=1, seed=43, timestamp=index) for index in range(500)])
    _write(recent, [_row(phase="seed44", episode=1, seed=44, timestamp=index) for index in range(150)])

    report = build_v2x_replay_80_20(historical, recent, output, seed=9, max_rows=600)

    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert report["schema"] == V2X_REPLAY_80_20_SCHEMA
    assert report["status"] == "ready"
    assert report["historical_used"] == 480
    assert report["recent_used"] == 120
    assert len(rows) == 600
    assert sum(row["replay_bucket"] == "historical" for row in rows) == 480
    assert sum(row["replay_bucket"] == "recent" for row in rows) == 120
    assert len({(row["replay_phase"], row["replay_episode"], row["replay_seed"], row["replay_timestamp"]) for row in rows}) == 600


def test_v2x_replay_waits_and_excludes_official_evaluation(tmp_path: Path):
    historical = tmp_path / "historical.jsonl"
    recent = tmp_path / "recent.jsonl"
    output = tmp_path / "must_not_exist.jsonl"
    _write(historical, [_row(phase="seed43", episode=1, seed=43, timestamp=index) for index in range(480)])
    _write(recent, [_row(phase="evaluation47", episode=1, seed=47, timestamp=index, evaluation=True) for index in range(200)])

    report = build_v2x_replay_80_20(historical, recent, output, seed=9)

    assert report["status"] == "waiting_replay_quota"
    assert report["quota_unmet_groups"] == ["recent"]
    assert report["recent_rejected"]["evaluation_seed_excluded"] == 200
    assert not output.exists()


def test_article_arm_contracts_separate_online_training_and_frozen_evaluation():
    baseline_online = mode_contract("sac_l2_online")
    baseline_frozen = mode_contract("sac_l2_frozen")
    treatment_online = mode_contract("tasam_v2x_online")
    treatment_frozen = mode_contract("tasam_v2x_frozen")

    assert baseline_online["sam_mode"] == "l2"
    assert baseline_online["l2_weight"] == pytest.approx(1e-4)
    assert baseline_online["armd_mode"] == "off"
    assert baseline_online["controller_enabled"] is True
    assert treatment_online["sam_mode"] == "tasam_selective"
    assert treatment_online["armd_mode"] == "assist"
    assert treatment_online["controller_enabled"] is True
    for contract in (baseline_frozen, treatment_frozen):
        assert contract["frozen_checkpoint"] is True
        assert contract["controller_enabled"] is False
        assert contract["actuation_enabled"] is True
        assert contract["replay_contract"] == V2X_REPLAY_80_20_SCHEMA


def test_article_online_environment_enables_only_the_declared_method(tmp_path: Path):
    baseline = build_environment("sac_l2_online", tmp_path / "baseline", 43, "tasam_training_balanced_v6_v2x_gbr_deadline_mc_fallback", 60)
    treatment = build_environment("tasam_v2x_online", tmp_path / "treatment", 43, "tasam_training_balanced_v6_v2x_gbr_deadline_mc_fallback", 60)
    frozen = build_environment("tasam_v2x_frozen", tmp_path / "frozen", 45, "tasam_training_balanced_v6_v2x_gbr_deadline_mc_fallback", 60)

    assert baseline["GREENRAN_ARMD_MODE"] == "off"
    assert baseline["GREENRAN_TASAM_ONLINE_SAM_MODE"] == "l2"
    assert baseline["GREENRAN_TASAM_ONLINE_L2_WEIGHT"] == "0.0001"
    assert treatment["GREENRAN_ARMD_MODE"] == "assist"
    assert treatment["GREENRAN_TASAM_ONLINE_SAM_MODE"] == "tasam_selective"
    assert "GREENRAN_TASAM_ECONOMIC_HEAD_ENABLED" not in treatment
    assert frozen["GREENRAN_ML_RETRAIN_ENABLED"] == "false"
    assert frozen["GREENRAN_ONLINE_UPDATE_OWNER"] == "frozen_checkpoint_evaluation"


def test_baseline_max_environment_forces_mc_lte_and_versioned_budget(tmp_path: Path):
    env = build_environment(
        "rapp_only_actuating",
        tmp_path / "baseline-max",
        43,
        "tasam_training_balanced_v6_1_v2x_gbr_deadline_mc_fallback_baseline_max",
        9000,
        sim_time=120,
        native_fidelity=True,
        disable_app_overrides=True,
    )
    assert env["GREENRAN_INFRA_RESOURCE_PROFILE"] == "baseline_max_v1"
    assert env["GREENRAN_CGROUP_BACKEND"] == "systemd_user_scope_v1"
    assert env["GREENRAN_CGROUP_SCOPE_SIMULATOR_CPU_QUOTA_US"] == "1200000"
    assert env["GREENRAN_CGROUP_SCOPE_SIMULATOR_MEMORY_HIGH_BYTES"] == str(8 * 1024**3)
    assert env["GREENRAN_NS3_USE_MC_UE_DEVICES"] == "true"
    assert env["GREENRAN_NS3_E2LTE_ENABLED"] == "true"
    assert env["GREENRAN_NS3_E2NR_ENABLED"] == "false"
    assert env["GREENRAN_V2X_FALLBACK_POLICY"].endswith("_v2")


def test_energy_v3_modes_require_v6_native_evidence_and_full_economic_contract(tmp_path: Path):
    online = mode_contract("asgard_v2x_window90_energy_online")
    frozen = mode_contract("asgard_v2x_window90_energy_frozen")
    assert online["economic_action_contract"] == "economic_action_v3_per_du_sleep"
    assert frozen["economic_action_contract"] == "economic_action_v3_per_du_sleep"
    assert online["controller_enabled"] is True
    assert frozen["controller_enabled"] is False
    env = build_environment(
        "asgard_v2x_window90_energy_online", tmp_path / "energy", 43,
        "tasam_training_balanced_v6_v2x_gbr_deadline_mc_fallback", 9000,
        sim_time=120, native_fidelity=True, energy_enabled=True,
        disable_app_overrides=True,
    )
    assert env["GREENRAN_NATIVE_EVIDENCE_VERSION"] == "v6"
    assert env["GREENRAN_TASAM_ECONOMIC_ACTION_CONTRACT"] == "economic_action_v3_per_du_sleep"
    assert env["GREENRAN_TASAM_REWARD_ENERGY_ENABLED"] == "1"
    assert env["GREENRAN_TASAM_RESOURCE_FLOOR_POLICY"] == "floor_to_115_percent_v1"
    rapp_env = build_environment(
        "rapp_only_actuating", tmp_path / "rapp-energy", 43,
        "tasam_training_balanced_v6_v2x_gbr_deadline_mc_fallback", 9000,
        sim_time=120, native_fidelity=True, energy_enabled=True,
        disable_app_overrides=True,
    )
    assert rapp_env["GREENRAN_NATIVE_EVIDENCE_VERSION"] == "v6"
    assert rapp_env["GREENRAN_TASAM_ECONOMIC_HEAD_ENABLED"] == "0"
    assert rapp_env["GREENRAN_TASAM_ECONOMIC_ACTION_CONTRACT"] == "economic_action_v3_per_du_sleep"


def test_dynamic_energy_mode_keeps_online_asgard_and_disables_legacy_staircase(tmp_path: Path):
    ledger = tmp_path / "ledger-v2.json"
    signature = tmp_path / "r26-signature.json"
    ledger.write_text("{}")
    signature.write_text("{}")
    contract = mode_contract("asgard_v2x_window90_energy_dynamic")
    assert contract["controller_enabled"] is True
    assert contract["frozen_checkpoint"] is False
    assert contract["dynamic_floor_contract"] == "greenran.tasam.adaptive_energy_envelope.v2"
    env = build_environment(
        "asgard_v2x_window90_energy_dynamic",
        tmp_path / "dynamic",
        43,
        "tasam_training_balanced_v6_1_v2x_gbr_deadline_mc_fallback_baseline_max",
        9000,
        sim_time=120,
        native_fidelity=True,
        energy_enabled=True,
        energy_staircase=True,
        dynamic_floor_ledger=ledger,
        baseline_signature=signature,
        disable_app_overrides=True,
    )
    assert env["GREENRAN_TASAM_DYNAMIC_FLOOR_CONTRACT"] == "greenran.tasam.adaptive_energy_envelope.v2"
    assert "GREENRAN_TASAM_ENERGY_STAIRCASE_CONTRACT" not in env
    assert env["GREENRAN_TASAM_ALLOW_DU_SLEEP"] == "1"
    assert env["GREENRAN_TASAM_RESOURCE_FLOOR_POLICY"] == "adaptive_energy_envelope_v2"
    assert env["GREENRAN_ML_RETRAIN_ENABLED"] == "true"


def test_fixed_native_power_percent_flag_propagates_exact_percent(tmp_path: Path):
    run_dir = tmp_path / "fixed-power"
    for percent in (45, 70, 25, 100):
        env = build_environment(
            "fixed_100_native", run_dir / str(percent), 43,
            "tasam_training_balanced_v6_1_v2x_gbr_deadline_mc_fallback_baseline_max",
            9000, sim_time=120, native_fidelity=True, energy_enabled=True,
            fixed_native_power_percent=percent,
        )
        assert env["GREENRAN_TASAM_FIXED_NATIVE_POWER_PERCENT"] == str(percent)
        assert env["GREENRAN_TASAM_ECONOMIC_ACTION_CONTRACT"] == "economic_action_v3_per_du_sleep"
    default_env = build_environment(
        "fixed_100_native", run_dir / "default", 43,
        "tasam_training_balanced_v6_1_v2x_gbr_deadline_mc_fallback_baseline_max",
        9000, sim_time=120, native_fidelity=True, energy_enabled=True,
    )
    assert default_env["GREENRAN_TASAM_FIXED_NATIVE_POWER_PERCENT"] == "100"


def test_fixed_native_power_percent_rejects_off_grid_or_misplaced_values():
    for invalid in (20, 47, 105, 0, -25):
        with pytest.raises(SystemExit, match="passos de 5"):
            _validate_fixed_native_power_percent(invalid, "fixed_100_native")
    with pytest.raises(SystemExit, match="fixed_100_native"):
        _validate_fixed_native_power_percent(45, "asgard_v2x_window90_energy_dynamic")
    with pytest.raises(SystemExit, match="fixed_100_native"):
        _validate_fixed_native_power_percent(70, "rapp_only_actuating")
    assert _validate_fixed_native_power_percent(45, "fixed_100_native") == 45
    assert _validate_fixed_native_power_percent("70", "fixed_100_native") == 70


def test_rapp_live_fixed_power_honors_five_percent_grid(monkeypatch):
    def _fixed(percent: str):
        monkeypatch.setenv("GREENRAN_TASAM_FIXED_NATIVE_POWER_PERCENT", percent)
        return RappResourceOptimizer._rapp_live_power_percent(object(), {})

    assert _fixed("45") == 45.0
    assert _fixed("70") == 70.0
    assert _fixed("25") == 25.0
    assert _fixed("100") == 100.0
    assert _fixed("47") == 45.0
    assert _fixed("110") == 100.0
    assert _fixed("20") == 25.0
    assert _fixed("abc") == 100.0
    monkeypatch.delenv("GREENRAN_TASAM_FIXED_NATIVE_POWER_PERCENT", raising=False)
    fallback = RappResourceOptimizer._rapp_live_power_percent(RappResourceOptimizer, {})
    assert fallback == 100.0


def test_energy_staircase_is_opt_in_and_only_enabled_for_the_tasam_arm(tmp_path: Path):
    env = build_environment(
        "asgard_v2x_window90_energy_online", tmp_path / "staircase", 43,
        "tasam_training_balanced_v6_1_v2x_gbr_deadline_mc_fallback_baseline_max", 9000,
        sim_time=12, native_fidelity=True, energy_enabled=True,
        energy_staircase=True, disable_app_overrides=True,
    )
    assert env["GREENRAN_TASAM_ENERGY_STAIRCASE_CONTRACT"] == "greenran.tasam.v2x.energy_staircase.v1"
    assert env["GREENRAN_TASAM_ALLOW_DU_SLEEP"] == "1"


def test_v61_baseline_max_profile_uses_v6_for_rapp_energy(tmp_path: Path):
    env = build_environment(
        "rapp_only_actuating", tmp_path / "rapp-v61", 43,
        "tasam_training_balanced_v6_1_v2x_gbr_deadline_mc_fallback_baseline_max", 9000,
        sim_time=12, native_fidelity=True, energy_enabled=True,
        disable_app_overrides=True,
    )
    assert env["GREENRAN_NATIVE_EVIDENCE_VERSION"] == "v6"
    assert env["GREENRAN_NS3_NATIVE_AGGREGATED_EVIDENCE"] == "1"
    assert env["GREENRAN_TASAM_ECONOMIC_ACTION_CONTRACT"] == "economic_action_v3_per_du_sleep"


def test_frozen_policy_evidence_rejects_mutable_or_unacknowledged_arm(tmp_path: Path):
    manifest = {
        "mode": "sac_l2_frozen",
        "contract": {"frozen_checkpoint": True, "controller_enabled": False, "actuation_enabled": True},
        "e2_control_enabled": True,
        "checkpoint_frozen_verified": True,
        "feedback_integrity_valid": True,
    }
    (tmp_path / "arm_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    valid, reason, _ = _frozen_policy_evidence(tmp_path, "sac_l2_frozen")
    assert valid is True
    assert reason == "ok"

    manifest["feedback_integrity_valid"] = False
    (tmp_path / "arm_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    valid, reason, _ = _frozen_policy_evidence(tmp_path, "sac_l2_frozen")
    assert valid is False
    assert reason == "evaluation_e2_ack_or_feedback_incomplete"
