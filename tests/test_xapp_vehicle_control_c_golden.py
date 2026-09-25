"""Golden: o xApp-VehicleSafety C publica intent byte-idêntico ao Python.

Contrato: mesmo extended_metrics.json de entrada deve produzir o mesmo
arquivo de intenção (módulo TIMESTAMP), validando a port 1:1 da política
de SLA veicular para C (docs/APP3_VEICULAR_ARQUITETURA.md).
"""

from __future__ import annotations

import copy
import json
import os
import stat
import subprocess
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
C_BINARY = (
    PROJECT_ROOT
    / "flexric"
    / "build_e2ap_v1"
    / "examples"
    / "xApp"
    / "c"
    / "xapp_vehicle_control"
)

pytestmark = pytest.mark.skipif(
    not C_BINARY.exists(), reason="xapp_vehicle_control não construído"
)


def _base_document() -> dict:
    document: dict = {"timestamp": 0, "ue_metrics": {}}
    for imsi in ("16", "17", "18", "19", "20"):
        document["ue_metrics"][imsi] = {
            "device_type": "vehicle",
            "vehicle_id": f"veh-imsi-{imsi}",
            "vehicle_role": "traffic",
            "autonomy_state": "normal",
            "risk_state": "low",
            "latency_avg_us": 4200.0,
            "latency_us": 4200.0,
            "latency_is_proxy": False,
            "pdcp_provenance": "pdcp_real",
            "packet_loss_percent": 0.1,
            "tx_pdus": 8000,
            "rx_pdus": 9000,
            "sample_window_s": 30.0,
            "speed_mps": 8.0,
        }
    document["ue_metrics"]["1"] = {"device_type": "camera", "tx_pdus": 500}
    return document


def _patch_entries(document: dict, **fields) -> dict:
    patched = copy.deepcopy(document)
    for key, ue in patched["ue_metrics"].items():
        if ue.get("device_type") == "vehicle":
            ue.update(fields)
    return patched


def _scenarios() -> dict[str, dict]:
    base = _base_document()
    scenarios = {
        "healthy": base,
        "critical_loss": _patch_entries(base, packet_loss_percent=9.98),
        "critical_latency": _patch_entries(
            base, vehicle_role="ego", latency_avg_us=25000.0, latency_us=25000.0
        ),
        "warning_loss": _patch_entries(base, packet_loss_percent=0.7),
        "critical_risk": _patch_entries(base, risk_state="high"),
        "degraded_autonomy": _patch_entries(base, autonomy_state="manual"),
        "warmup": _patch_entries(base, tx_pdus=42),
    }
    idle = copy.deepcopy(base)
    for ue in idle["ue_metrics"].values():
        if ue.get("device_type") == "vehicle":
            ue["device_type"] = "sensor"
    scenarios["idle"] = idle
    return scenarios


def _run_c(metrics_path: Path, intent_path: Path, cycles: int = 1) -> None:
    env = os.environ.copy()
    env["GREENRAN_REQUIRE_REAL_PDCP"] = "1"
    subprocess.run(
        [
            str(C_BINARY),
            "--metrics-path",
            str(metrics_path),
            "--intent-path",
            str(intent_path),
            "--status-path",
            str(intent_path.parent / "status_c.json"),
            "--vehicle-imsis",
            "16,17,18,19,20",
            "--cycles",
            str(cycles),
            "--interval",
            "1",
        ],
        check=True,
        capture_output=True,
        env=env,
        cwd=str(PROJECT_ROOT),
        timeout=30,
    )


def _run_python(state_dir: Path) -> Path:
    env = os.environ.copy()
    env["GREENRAN_REQUIRE_REAL_PDCP"] = "1"
    env["GREENRAN_STATE_DIR"] = str(state_dir)
    env["PYTHONPATH"] = str(PROJECT_ROOT / "src")
    process = subprocess.Popen(
        ["python3", "xapp_vehicle_control.py", "--interval", "1"],
        cwd=str(PROJECT_ROOT / "src"),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    intent_path = state_dir / "xapp_intents" / "vehicle_control.txt"
    deadline = time.time() + 15
    try:
        while time.time() < deadline:
            if intent_path.exists():
                time.sleep(0.2)
                return intent_path
            time.sleep(0.1)
        raise AssertionError("Python xApp não publicou intent")
    finally:
        process.terminate()
        process.wait(timeout=10)


@pytest.mark.parametrize(
    "scenario", sorted(_scenarios()), ids=sorted(_scenarios())
)
def test_intent_c_identico_ao_python(tmp_path, scenario):
    scenario_dir = tmp_path / scenario
    metrics_dir = scenario_dir / "xapp_metrics"
    metrics_dir.mkdir(parents=True)
    document = _scenarios()[scenario]
    metrics_path = metrics_dir / "extended_metrics.json"
    metrics_path.write_text(json.dumps(document))

    intent_c = scenario_dir / "intent_c.txt"
    _run_c(metrics_path, intent_c)

    intent_python = _run_python(scenario_dir)

    def normalized(path: Path) -> list[str]:
        return [
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if not line.startswith("TIMESTAMP=")
        ]

    assert intent_c.exists(), "xApp C não publicou intent"
    assert normalized(intent_c) == normalized(intent_python), (
        f"intent divergiu no cenário {scenario}:\n"
        f"C:\n{intent_c.read_text()}\nPython:\n{intent_python.read_text()}"
    )
