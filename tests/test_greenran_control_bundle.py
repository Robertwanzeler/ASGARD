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
