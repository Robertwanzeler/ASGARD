import json
import math
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from greenran_control_bundle import (  # noqa: E402
    ACK_SCHEMA,
    ControlBundleClient,
    ControlBundleError,
    SCHEMA,
    power_percent_to_dbm,
    quantize_power_percent,
    validate_bundle,
)
from greenran_infra_budget import build_physical_budget  # noqa: E402


def _bundle():
    cells = {2: [], 3: [], 4: []}
    for imsi in range(1, 21):
        cell = 2 if imsi <= 6 else 3 if imsi <= 9 else 4
        cells[cell].append({
            "imsi": imsi,
            "min_dl_share_bp": 100,
            "min_ul_share_bp": 0,
            "surplus_weight_bp": 5000,
        })
    return {
        "schema": SCHEMA,
        "policy_id": "test-policy",
        "sequence": 7,
        "issued_at_ns": time.time_ns(),
        "ttl_ms": 5000,
        "mode": "combined",
        "cells": [
            {"cell_id": cell, "tx_power_percent": 63, "ue_policies": policies}
            for cell, policies in cells.items()
        ],
        "infra": build_physical_budget(0.75),
    }


def test_power_is_safe_quantized_and_linear_in_rf_domain():
    assert quantize_power_percent(63) == 65
    assert quantize_power_percent(0) == 25
    assert quantize_power_percent(120) == 100
    assert power_percent_to_dbm(100, 30) == 30
    assert power_percent_to_dbm(25, 30) == pytest.approx(30 + 10 * math.log10(0.25))


def test_bundle_requires_each_canonical_ue_exactly_once():
    valid = validate_bundle(_bundle())
    assert sum(len(cell["ue_policies"]) for cell in valid["cells"]) == 20
    assert all(cell["tx_power_percent"] == 65 for cell in valid["cells"])
    broken = _bundle()
    broken["cells"][0]["ue_policies"].pop()
    with pytest.raises(ControlBundleError, match="missing"):
        validate_bundle(broken)


def test_client_requires_applied_e2_ack(tmp_path, monkeypatch):
    received = {}

    class FakeSocket:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def settimeout(self, _):
            pass

        def connect(self, _):
            pass

        def sendall(self, payload):
            received.update(json.loads(payload))

        def recv(self, _):
            return json.dumps({
                "schema": ACK_SCHEMA,
                "policy_id": "test-policy",
                "sequence": 7,
                "ack": True,
                "applied": True,
                "e2_transactions": 23,
                "observed_confirmations": 23,
            }).encode()

    monkeypatch.setattr("greenran_control_bundle.socket.socket", lambda *_: FakeSocket())
    client = ControlBundleClient(
        socket_path=tmp_path / "tasam.sock",
        shadow_path=tmp_path / "bundle.json",
        ack_path=tmp_path / "ack.json",
    )
    ack = client.send(_bundle())
    assert ack["applied"] is True
    assert received["schema"] == SCHEMA


def _failsafe():
    from greenran_control_bundle import failsafe_bundle

    return failsafe_bundle(
        sequence=7,
        cell_ids=(2, 3, 4),
        reason='regression_test',
        sim_time_s=12.5,
    )


def _v6(monkeypatch):
    monkeypatch.setenv("GREENRAN_NATIVE_EVIDENCE_VERSION", "v6")


def test_failsafe_ack_sem_cell_results_e_aceito(tmp_path, monkeypatch):
    """r23: ack applied=True sem cell_results flipava BLOCKED silenciosamente.

    Failsafe é o estado seguro por definição: o ACK global basta.
    """
    _v6(monkeypatch)
    received = {}

    class FakeSocket:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def settimeout(self, _):
            pass

        def connect(self, _):
            pass

        def sendall(self, payload):
            received.update(json.loads(payload))

        def recv(self, _):
            return json.dumps({
                "schema": ACK_SCHEMA,
                "policy_id": received.get("policy_id"),
                "sequence": received.get("sequence"),
                "ack": True,
                "applied": True,
                "cell_results": [],
                "observed_confirmations": 8,
            }).encode()

    monkeypatch.setattr("greenran_control_bundle.socket.socket", lambda *_: FakeSocket())
    client = ControlBundleClient(
        socket_path=tmp_path / "tasam.sock",
        shadow_path=tmp_path / "bundle.json",
        ack_path=tmp_path / "ack.json",
    )
    ack = client.send(_failsafe())
    assert ack["applied"] is True
    assert ack["cell_ack_complete"] is True


def test_economico_sem_acks_por_celula_continua_rejeitado(tmp_path, monkeypatch):
    """Bundle econômico v6 sem cobertura 2/3/4 nos acks segue inválido."""
    _v6(monkeypatch)
    audited = []

    class FakeSocket:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def settimeout(self, _):
            pass

        def connect(self, _):
            pass

        def sendall(self, _):
            pass

        def recv(self, _):
            return json.dumps({
                "schema": ACK_SCHEMA,
                "policy_id": "test-policy",
                "sequence": 7,
                "ack": True,
                "applied": True,
                "cell_results": [
                    {"cell_id": 2, "scheduler_ack": True, "power_ack": True},
                ],
                "observed_confirmations": 8,
            }).encode()

    monkeypatch.setattr("greenran_control_bundle.socket.socket", lambda *_: FakeSocket())
    client = ControlBundleClient(
        socket_path=tmp_path / "tasam.sock",
        shadow_path=tmp_path / "bundle.json",
        ack_path=tmp_path / "ack.json",
        audit_path=tmp_path / "audit.jsonl",
    )
    original_audit = client._audit

    def spy(entry):
        audited.append(entry)
        original_audit(entry)

    client._audit = spy
    with pytest.raises(ControlBundleError):
        client.send(_bundle())
    assert audited and audited[-1].get("cell_ack_complete") is False
