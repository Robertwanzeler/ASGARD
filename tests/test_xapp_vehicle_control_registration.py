"""Registro do xApp-VehicleSafety nativo atrás de GREENRAN_VEHICLE_XAPP."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

from src.rapp_xapp_manager import XAppManager


@pytest.fixture()
def manager(tmp_path):
    instance = XAppManager()
    instance.flexric_build = str(tmp_path / "build")
    return instance


def _make_c_binary(manager, executable=True):
    target = (
        Path(manager.flexric_build)
        / "examples"
        / "xApp"
        / "c"
        / "xapp_vehicle_control"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("#!/usr/bin/env python3\nimport time\ntime.sleep(30)\n")
    if executable:
        target.chmod(target.stat().st_mode | stat.S_IEXEC)
    return target


def test_default_mode_usa_python(manager):
    assert manager._vehicle_safety_c_command() is None


def test_outros_valores_usam_python(manager, monkeypatch):
    monkeypatch.setenv("GREENRAN_VEHICLE_XAPP", "PYTHON")
    assert manager._vehicle_safety_c_command() is None


def test_modo_c_sem_binario_cai_no_python(manager, monkeypatch, capsys):
    monkeypatch.setenv("GREENRAN_VEHICLE_XAPP", "c")
    assert manager._vehicle_safety_c_command() is None
    assert "fallback Python" in capsys.readouterr().out


def test_modo_c_monta_comando_com_e2(manager, monkeypatch):
    monkeypatch.setenv("GREENRAN_VEHICLE_XAPP", "c")
    monkeypatch.setenv("GREENRAN_E2_XAPP_PORT", "36430")
    binary = _make_c_binary(manager)
    command = manager._vehicle_safety_c_command()
    assert command is not None
    assert command[0] == str(binary)
    assert "--metrics-path" in command
    assert "--intent-path" in command
    assert "--status-path" in command
    imsis = command[command.index("--vehicle-imsis") + 1].split(",")
    assert imsis == ["16", "17", "18", "19", "20"]
    assert command[command.index("--interval") + 1] == "2"
    e2 = command[command.index("-x") + 1]
    assert e2 == "36430"
    assert command[command.index("-p") + 1].endswith("/")


def test_start_veiculo_usa_binario_c(manager, monkeypatch, tmp_path):
    monkeypatch.setenv("GREENRAN_VEHICLE_XAPP", "c")
    binary = _make_c_binary(manager)
    started = {}

    real_popen = subprocess.Popen

    def fake_popen(command, **kwargs):
        started["command"] = command
        return real_popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            preexec_fn=os.setsid,
        )

    monkeypatch.setattr("src.rapp_xapp_manager.subprocess.Popen", fake_popen)
    assert manager.start("vehicle_control") is True
    assert started["command"][0] == str(binary)
    assert manager.is_running("vehicle_control")
    manager.stop("vehicle_control")
    assert not manager.is_running("vehicle_control")
