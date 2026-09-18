"""Tests for the root-once, user-runnable GreenRAN cgroup setup."""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest

import src.greenran_infra_budget as infra
from src.greenran_infra_budget import GROUPS, cgroup_delegation_status


ROOT = Path(__file__).resolve().parents[1]


def _delegated_tree(tmp_path: Path) -> Path:
    root = tmp_path / "greenran"
    root.mkdir()
    (root.parent / "cgroup.procs").write_text("")
    (root / "cgroup.procs").write_text("")
    (root / "cgroup.controllers").write_text("cpu memory io pids")
    (root / "cgroup.subtree_control").write_text("cpu memory io")
    for name in GROUPS:
        group = root / name
        group.mkdir()
        for filename in ("cpu.max", "memory.high", "io.weight", "cgroup.procs"):
            (group / filename).write_text("")
        (group / "cpu.stat").write_text("usage_usec 0\n")
        (group / "memory.current").write_text("0\n")
        (group / "memory.peak").write_text("0\n")
        (group / "io.stat").write_text("")
    return root


def _load_campaign_launcher():
    path = ROOT / "scripts" / "run_tasam_online_economic_campaign.py"
    spec = importlib.util.spec_from_file_location("online_economic_campaign_under_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_delegation_status_accepts_complete_user_writable_tree(tmp_path):
    status = cgroup_delegation_status(_delegated_tree(tmp_path))
    assert status["valid"] is True
    assert status["failures"] == []
    assert set(status["groups"]) == set(GROUPS)


def test_delegation_status_rejects_missing_leaf_write_permission(tmp_path):
    root = _delegated_tree(tmp_path)
    (root / "tasam" / "io.weight").unlink()
    status = cgroup_delegation_status(root)
    assert status["valid"] is False
    assert "tasam/io.weight não é gravável" in status["failures"]


def test_delegation_status_rejects_non_writable_common_ancestor(tmp_path):
    root = _delegated_tree(tmp_path)
    (root.parent / "cgroup.procs").chmod(0o444)
    status = cgroup_delegation_status(root)
    assert status["valid"] is False
    assert "cgroup.procs do ancestral comum não é gravável" in " ".join(status["failures"])


def test_runtime_pointer_selects_bootstrapped_user_tree(tmp_path, monkeypatch):
    root = _delegated_tree(tmp_path)
    pointer = tmp_path / "greenran-cgroup-root"
    pointer.write_text(str(root))
    monkeypatch.delenv("GREENRAN_CGROUP_ROOT", raising=False)
    monkeypatch.setattr(infra, "CGROUP_ROOT_FILE", pointer)
    assert infra.resolve_cgroup_root() == root


def test_campaign_rejects_missing_delegation_before_creating_directory(tmp_path):
    launcher = _load_campaign_launcher()
    campaign = tmp_path / "must_not_exist"
    with patch.object(
        launcher,
        "assert_cgroup_delegation",
        side_effect=launcher.InfraBudgetError("delegação ausente"),
    ):
        with pytest.raises(SystemExit, match="delegação ausente"):
            launcher.run(argparse.Namespace(campaign_dir=campaign))
    assert not campaign.exists()
