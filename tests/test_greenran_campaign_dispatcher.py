"""Contract tests for autonomous, local-only GreenRAN campaign jobs."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

import src.greenran_campaign_jobs as jobs
from src.greenran_campaign_jobs import (
    JOB_SCHEMA,
    JobSpec,
    JobValidationError,
    atomic_json_write,
    normalize_job,
    prepare_queue,
)


ROOT = Path(__file__).resolve().parents[1]


def _checkpoint(tmp_path: Path, *, economic: bool = True) -> Path:
    path = tmp_path / "checkpoint"
    path.mkdir()
    metadata = {
        "du_count": 3,
        "du_state_dim": 13,
        "global_state_dim": 13,
        "joint_action_dim": 12,
        "uses_global_energy_infra_actor": True,
        "allocation_head_output_dim": 3 if economic else 2,
        "allocation_head_outputs": ["ran_share", "ai_share", "total_budget_fraction"] if economic else ["ran_share", "ai_share"],
    }
    (path / "tasam_marl_checkpoint_meta.json").write_text(json.dumps(metadata))
    (path / "tasam_marl_actors.pt").write_bytes(b"test")
    return path


def _payload(tmp_path: Path, kind: str = "online_economic") -> dict:
    checkpoint = _checkpoint(tmp_path)
    calibration = tmp_path / "calibration.json"
    calibration.write_text(json.dumps({"schema": "greenran.energy_calibration.v2"}))
    vehicle_manifest = tmp_path / "vehicle_profile.json"
    vehicle_manifest.write_text(json.dumps({
        "schema": "greenran.autonomous_vehicle_feasibility.v1",
        "profile": "tasam_training_balanced_v3",
        "status": "passed",
        "selected_interval_us": 8000,
    }))
    # Use project-relative paths only after monkeypatching the module root in
    # the individual test; this shape documents the public job contract.
    return {
        "schema": JOB_SCHEMA,
        "job_id": "online-economic-test-001",
        "kind": kind,
        "campaign_dir": str(tmp_path / "campaign"),
        "checkpoint": str(checkpoint),
        "calibration": str(calibration),
        "seed": 47,
        "profile": "tasam_training_balanced_v3",
        "vehicle_profile_manifest": str(vehicle_manifest),
    }


def _load_dispatcher():
    path = ROOT / "scripts" / "greenran_campaign_dispatcher.py"
    spec = importlib.util.spec_from_file_location("greenran_dispatcher_under_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_online_job_rejects_path_outside_workspace(tmp_path):
    payload = _payload(tmp_path)
    with pytest.raises(JobValidationError, match="fora do projeto"):
        normalize_job(payload)


def test_invalid_kind_is_rejected_before_execution():
    with pytest.raises(JobValidationError, match="kind não permitido"):
        normalize_job({"schema": JOB_SCHEMA, "job_id": "invalid-job-001", "kind": "shell"})


def test_online_job_normalizes_to_fixed_python_command(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    runs.mkdir()
    monkeypatch.setattr(jobs, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(jobs, "RUNS_ROOT", runs)
    payload = _payload(tmp_path)
    payload["campaign_dir"] = str(runs / "new_campaign")
    spec = jobs.normalize_job(payload)
    assert spec.kind == "online_economic"
    assert spec.command[0] == sys.executable
    assert "run_tasam_online_economic_campaign.py" in spec.command[1]
    assert spec.command[spec.command.index("--wall-time") + 1] == "43200"
    assert "--native-fidelity" in spec.command
    assert "0.016" in spec.command
    assert spec.command[spec.command.index("--startup-min-free-gib") + 1] == "15.8"
    assert spec.command[spec.command.index("--min-free-gib") + 1] == "10.0"
    assert "--require-vehicle-feasibility" in spec.command
    assert spec.command[spec.command.index("--vehicle-profile-manifest") + 1] == payload["vehicle_profile_manifest"]
    assert "/run/media/" not in " ".join(spec.command)


def test_online_job_rejects_missing_or_failed_vehicle_baseline(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    runs.mkdir()
    monkeypatch.setattr(jobs, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(jobs, "RUNS_ROOT", runs)
    payload = _payload(tmp_path)
    payload["campaign_dir"] = str(runs / "missing-vehicle-baseline")
    payload.pop("vehicle_profile_manifest")
    with pytest.raises(JobValidationError, match="vehicle_profile_manifest ausente"):
        jobs.normalize_job(payload)

    manifest = tmp_path / "failed_vehicle_profile.json"
    manifest.write_text(json.dumps({
        "schema": "greenran.autonomous_vehicle_feasibility.v1",
        "profile": "tasam_training_balanced_v3",
        "status": "baseline_infeasible",
        "selected_interval_us": None,
    }))
    payload["vehicle_profile_manifest"] = str(manifest)
    with pytest.raises(JobValidationError, match="não foi aprovado"):
        jobs.normalize_job(payload)


def test_actuation_smoke_uses_host_preflight_limits_and_300_decisions(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    runs.mkdir()
    monkeypatch.setattr(jobs, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(jobs, "RUNS_ROOT", runs)
    payload = _payload(tmp_path, kind="actuation_smoke")
    payload["campaign_dir"] = str(runs / "actuation-smoke")
    spec = jobs.normalize_job(payload)
    assert spec.command[spec.command.index("--decision-target") + 1] == "300"
    assert spec.command[spec.command.index("--wall-time") + 1] == "900"
    assert spec.command[spec.command.index("--min-free-gib") + 1] == "15.8"
    assert spec.command[spec.command.index("--artifact-min-free-gib") + 1] == "10.0"
    assert spec.command[spec.command.index("--artifact-budget-gib") + 1] == "2.0"


def test_online_observe_only_is_forwarded_only_for_online_job(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    runs.mkdir()
    monkeypatch.setattr(jobs, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(jobs, "RUNS_ROOT", runs)
    payload = _payload(tmp_path)
    payload["campaign_dir"] = str(runs / "observation")
    payload["observe_only"] = True
    spec = jobs.normalize_job(payload)
    assert "--observe-only" in spec.command
    payload["kind"] = "shadow"
    payload["campaign_dir"] = str(runs / "shadow-observation")
    with pytest.raises(JobValidationError, match="observe_only só é permitido"):
        jobs.normalize_job(payload)
    payload["observe_only"] = False
    spec = jobs.normalize_job(payload)
    assert "--observe-only" not in spec.command


def test_online_warm_start_forwards_parent_without_reusing_evidence(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    runs.mkdir()
    monkeypatch.setattr(jobs, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(jobs, "RUNS_ROOT", runs)
    payload = _payload(tmp_path)
    payload["campaign_dir"] = str(runs / "warm-start")
    payload["warm_start"] = True
    payload["warm_start_parent"] = payload["checkpoint"]
    payload["min_free_gib"] = 15.8
    spec = jobs.normalize_job(payload)
    assert "--warm-start" in spec.command
    assert "--warm-start-parent" in spec.command


def test_frozen_asgard_job_is_a_separate_fail_closed_kind(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    runs.mkdir()
    adaptation = runs / "adaptation"
    adaptation.mkdir()
    monkeypatch.setattr(jobs, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(jobs, "RUNS_ROOT", runs)
    payload = _payload(tmp_path, kind="asgard_frozen_evaluation")
    payload["campaign_dir"] = str(runs / "frozen")
    payload["adaptation_dir"] = str(adaptation)
    (adaptation / "online_state.json").write_text(json.dumps({
        "active_checkpoint_promoted": True,
        "promotion_count": 1,
        "last_promoted_checkpoint": str(Path(payload["checkpoint"]).resolve()),
    }))
    spec = jobs.normalize_job(payload)
    assert spec.kind == "asgard_frozen_evaluation"
    assert "run_tasam_asgard_frozen_evaluation.py" in spec.command[1]


def test_shadow_job_requires_and_passes_a_calibration(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    runs.mkdir()
    monkeypatch.setattr(jobs, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(jobs, "RUNS_ROOT", runs)
    payload = _payload(tmp_path, kind="shadow")
    payload["campaign_dir"] = str(runs / "shadow")
    payload.pop("calibration")
    with pytest.raises(JobValidationError, match="calibration ausente"):
        jobs.normalize_job(payload)
    calibration = tmp_path / "calibration.json"
    payload["calibration"] = str(calibration)
    spec = jobs.normalize_job(payload)
    assert spec.command[spec.command.index("--energy-calibration") + 1] == str(calibration)


def test_job_rejects_nonempty_campaign_directory(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    runs.mkdir()
    monkeypatch.setattr(jobs, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(jobs, "RUNS_ROOT", runs)
    payload = _payload(tmp_path)
    campaign = runs / "preserved"
    campaign.mkdir()
    (campaign / "manifest.json").write_text("{}")
    payload["campaign_dir"] = str(campaign)
    with pytest.raises(JobValidationError, match="já contém artefatos"):
        jobs.normalize_job(payload)


def test_dispatcher_marks_success_and_serializes_command(tmp_path):
    dispatcher = _load_dispatcher()
    queue = tmp_path / "queue"
    layout = prepare_queue(queue)
    payload = {"schema": JOB_SCHEMA, "job_id": "smoke-test-001", "kind": "smoke"}
    atomic_json_write(layout["queued"] / "smoke-test-001.json", payload)
    spec = JobSpec(
        job_id="smoke-test-001", kind="smoke", payload=payload,
        command=[sys.executable, "-c", "raise SystemExit(0)"], campaign_path=tmp_path / "campaign",
    )
    with patch.object(dispatcher, "normalize_job", return_value=spec), \
         patch.object(dispatcher, "_host_cgroup_probe", return_value={"valid": True}), \
         patch.object(dispatcher, "assert_cgroup_delegation"), \
         patch.object(dispatcher, "_active_cgroup_pids", side_effect=[{}, {}, {}]):
        assert dispatcher.run_one(queue) is True
    status = json.loads((layout["status"] / "smoke-test-001.json").read_text())
    assert status["state"] == "finished"
    assert status["command"] == spec.command
    assert (layout["finished"] / "smoke-test-001.json").is_file()
    assert (layout["status"] / "smoke-test-001.cgroup_host_probe.json").is_file()


def test_dispatcher_rejects_and_records_host_cgroup_probe(tmp_path):
    dispatcher = _load_dispatcher()
    queue = tmp_path / "queue"
    layout = prepare_queue(queue)
    payload = {"schema": JOB_SCHEMA, "job_id": "smoke-test-004", "kind": "smoke"}
    atomic_json_write(layout["queued"] / "smoke-test-004.json", payload)
    spec = JobSpec(
        job_id="smoke-test-004", kind="smoke", payload=payload,
        command=[sys.executable, "-c", "raise SystemExit(0)"],
        campaign_path=tmp_path / "campaign",
    )
    probe = {"valid": False, "reason": "delegation_invalid"}
    with patch.object(dispatcher, "normalize_job", return_value=spec), \
         patch.object(dispatcher, "_host_cgroup_probe", return_value=probe):
        assert dispatcher.run_one(queue) is True
    status = json.loads((layout["status"] / "smoke-test-004.json").read_text())
    assert status["state"] == "rejected"
    assert status["cgroup_host_probe"] == probe
    assert (layout["status"] / "smoke-test-004.cgroup_host_probe.json").is_file()
    assert (layout["failed"] / "smoke-test-004.json").is_file()


def test_dispatcher_rejects_existing_cgroup_processes(tmp_path):
    dispatcher = _load_dispatcher()
    queue = tmp_path / "queue"
    layout = prepare_queue(queue)
    payload = {"schema": JOB_SCHEMA, "job_id": "smoke-test-002", "kind": "smoke"}
    atomic_json_write(layout["queued"] / "smoke-test-002.json", payload)
    spec = JobSpec("smoke-test-002", "smoke", payload, [sys.executable, "-c", "raise SystemExit(0)"], tmp_path / "campaign")
    with patch.object(dispatcher, "normalize_job", return_value=spec), \
         patch.object(dispatcher, "_host_cgroup_probe", return_value={"valid": True}), \
         patch.object(dispatcher, "assert_cgroup_delegation"), \
         patch.object(dispatcher, "_active_cgroup_pids", return_value={"collectors": [99]}):
        assert dispatcher.run_one(queue) is True
    status = json.loads((layout["status"] / "smoke-test-002.json").read_text())
    assert status["state"] == "rejected"
    assert "processos ativos" in status["reason"]


def test_dispatcher_records_cgroup_failure_after_claim(tmp_path):
    dispatcher = _load_dispatcher()
    queue = tmp_path / "queue"
    layout = prepare_queue(queue)
    payload = {"schema": JOB_SCHEMA, "job_id": "smoke-test-003", "kind": "smoke"}
    atomic_json_write(layout["queued"] / "smoke-test-003.json", payload)
    spec = JobSpec("smoke-test-003", "smoke", payload, [sys.executable, "-c", "raise SystemExit(0)"], tmp_path / "campaign")
    with patch.object(dispatcher, "normalize_job", return_value=spec), \
         patch.object(dispatcher, "_host_cgroup_probe", return_value={"valid": True}), \
         patch.object(dispatcher, "assert_cgroup_delegation", side_effect=dispatcher.InfraBudgetError("cgroup perdido")):
        assert dispatcher.run_one(queue) is True
    status = json.loads((layout["status"] / "smoke-test-003.json").read_text())
    assert status["state"] == "rejected"
    assert "cgroup perdido" in status["reason"]


def test_dispatcher_drains_only_verified_job_process_group(monkeypatch, tmp_path):
    dispatcher = _load_dispatcher()
    active = {"collectors": [101, 202, 303]}
    samples = [active, {}, {}]
    signalled: list[tuple[int, int]] = []

    monkeypatch.setattr(dispatcher, "_active_cgroup_pids", lambda: samples.pop(0) if samples else {})
    process_groups = {101: 700, 202: 900, 303: 901}
    monkeypatch.setattr(dispatcher.os, "getpgid", lambda pid: process_groups[pid])
    monkeypatch.setattr(dispatcher.os, "kill", lambda pid, sig: signalled.append((pid, sig)))

    campaign = tmp_path / "campaign"
    campaign.mkdir()
    (campaign / "csv_metrics.pid").write_text("900\n")
    result = dispatcher._cleanup_finished_job(campaign, 700, grace_seconds=0.05)

    assert result["verified_process_groups"] == [700, 900]
    assert result["verified_job_pids"] == {"collectors": [101, 202]}
    assert result["terminated_pids"] == [101, 202]
    assert result["remaining_active_cgroup_pids"] == {}
    assert signalled == [(101, dispatcher.signal.SIGTERM), (202, dispatcher.signal.SIGTERM)]


def test_dispatcher_recovers_running_job_after_restart(tmp_path, monkeypatch):
    dispatcher = _load_dispatcher()
    queue = tmp_path / "queue"
    layout = prepare_queue(queue)
    campaign = tmp_path / "runs" / "recovery-campaign"
    campaign.mkdir(parents=True)
    payload = {
        "schema": JOB_SCHEMA,
        "job_id": "recovery-test-001",
        "kind": "smoke",
        "campaign_dir": str(campaign),
    }
    atomic_json_write(layout["running"] / "recovery-test-001.json", payload)
    monkeypatch.setattr(dispatcher, "ROOT", tmp_path)
    monkeypatch.setattr(dispatcher, "DEFAULT_QUEUE", queue)
    monkeypatch.setattr(dispatcher, "_cleanup_finished_job", lambda path, _pgid: {"campaign": str(path)})

    dispatcher._recover_interrupted_jobs(layout)

    status = json.loads((layout["status"] / "recovery-test-001.json").read_text())
    assert status["state"] == "failed"
    assert status["reason"] == "dispatcher_restarted_or_crashed_before_job_reaped"
    assert (layout["failed"] / "recovery-test-001.json").is_file()
