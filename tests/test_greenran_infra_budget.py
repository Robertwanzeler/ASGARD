from pathlib import Path

import pytest

from src.greenran_infra_budget import (
    CgroupV2Controller,
    InfraBudgetError,
    ResourceAccumulator,
    build_physical_budget,
    validate_physical_budget,
)
from src.greenran_infra_monitor import InfrastructureMonitor


def _fake_group(root: Path, name: str) -> Path:
    group = root / name
    group.mkdir(parents=True)
    for filename in ("cpu.max", "memory.high", "memory.max", "io.weight", "cgroup.procs"):
        (group / filename).write_text("unchanged")
    return group


def test_budget_maps_rai_to_physical_limits_and_preserves_safety_channel():
    full = build_physical_budget(1.0)
    reduced = validate_physical_budget(build_physical_budget(0.5))
    assert reduced["groups"]["tasam"]["cpu_quota_us"] < full["groups"]["tasam"]["cpu_quota_us"]
    assert reduced["groups"]["simulator"] == full["groups"]["simulator"]
    assert reduced["telemetry"]["safety_period_ms"] == 100
    separated = build_physical_budget(1.0, compute_fraction=0.5, io_fraction=0.25)
    assert separated["groups"]["tasam"]["cpu_quota_us"] == 50_000
    assert separated["groups"]["tasam"]["io_weight"] == 25


def test_cgroup_apply_writes_cpu_memory_high_io_and_never_memory_max(tmp_path):
    budget = build_physical_budget(0.5)
    for name in budget["groups"]:
        _fake_group(tmp_path, name)
    controller = CgroupV2Controller(tmp_path)
    controller.apply(budget, {"tasam": 42})
    assert (tmp_path / "tasam" / "cpu.max").read_text() == "50000 100000"
    assert (tmp_path / "tasam" / "cgroup.procs").read_text() == "42"
    assert (tmp_path / "tasam" / "memory.max").read_text() == "unchanged"


def test_cgroup_accepts_privileged_predelegated_root(tmp_path):
    parent = tmp_path / "cgroup"
    root = parent / "greenran"
    root.mkdir(parents=True)
    (parent / "cgroup.controllers").write_text("cpu memory io pids")
    (root / "cgroup.controllers").write_text("cpu memory io")
    (root / "cgroup.subtree_control").write_text("cpu memory io")

    controller = CgroupV2Controller(root)
    controller.apply(build_physical_budget(1.0))

    assert (root / "simulator" / "cpu.max").read_text() == "200000 100000"


def test_invalid_safety_period_is_rejected():
    budget = build_physical_budget(1.0)
    budget["telemetry"]["safety_period_ms"] = 200
    with pytest.raises(InfraBudgetError):
        validate_physical_budget(budget)


def test_resource_accumulator_integrates_byte_seconds():
    accumulator = ResourceAccumulator()
    accumulator.observe({"timestamp_ns": 0, "memory_current_bytes": 100,
                         "memory_peak_bytes": 100})
    result = accumulator.observe({"timestamp_ns": 2_000_000_000,
                                  "memory_current_bytes": 300,
                                  "memory_peak_bytes": 350})
    assert result["memory_byte_seconds"] == 400
    assert result["observed_memory_peak_bytes"] == 350


def test_monitor_emits_complete_strict_metrics(tmp_path):
    cgroup_root = tmp_path / "cgroups"
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    for name in build_physical_budget(1.0)["groups"]:
        group = cgroup_root / name
        group.mkdir(parents=True)
        (group / "cpu.stat").write_text("usage_usec 10\n")
        (group / "memory.current").write_text("100")
        (group / "memory.peak").write_text("120")
        (group / "io.stat").write_text("8:0 rbytes=20 wbytes=30\n")
    monitor = InfrastructureMonitor(run_dir, cgroup_root=cgroup_root)
    monitor.sample()
    report = monitor.report()
    assert report["complete"] is True
    assert report["totals"]["memory_peak_bytes"] == 5 * 120
    assert set(report["groups"]) == set(build_physical_budget(1.0)["groups"])
