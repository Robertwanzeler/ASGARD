from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import v2x_parallel_slots as slots


def test_canonical_slots_have_unique_ports_and_offsets():
    resolved = slots.resolve_slots("slot-a,slot-b")
    assert [item.slot_id for item in resolved] == ["slot-a", "slot-b"]
    assert len({port for item in resolved for port in (
        item.e2_term_port, item.e2_xapp_port, item.e2_local_port,
    )}) == 6
    assert [item.app_port_offset for item in resolved] == [0, 100]


def test_slot_resolution_rejects_duplicates_and_more_than_two():
    with pytest.raises(ValueError):
        slots.resolve_slots("slot-a,slot-a")
    with pytest.raises(ValueError):
        slots.resolve_slots(["slot-a", "slot-b", "slot-c"])


def test_disk_reservation_accounts_for_each_active_slot(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(slots, "free_gib", lambda path: 27.9)
    with pytest.raises(RuntimeError, match="2 slots"):
        slots.assert_disk_capacity(tmp_path, 2)
    monkeypatch.setattr(slots, "free_gib", lambda path: 28.1)
    slots.assert_disk_capacity(tmp_path, 2)


def test_lease_is_exclusive_and_reconciles_campaign_status(monkeypatch, tmp_path: Path):
    lease_root = tmp_path / "leases"
    monkeypatch.setattr(slots, "LEASE_DIR", lease_root)
    monkeypatch.setattr(slots, "assert_ports_free", lambda slot: None)
    slot = slots.resolve_slots("slot-a")[0]
    campaign = tmp_path / "campaign"
    with slots.SlotLease(slot, campaign) as lease:
        lease.publish_campaign_contract()
        assert json.loads((campaign / "slot_lease.json").read_text())["status"] == "acquired"
        with pytest.raises(RuntimeError, match="ocupado"):
            with slots.SlotLease(slot, tmp_path / "other"):
                pass
    assert json.loads((campaign / "slot_lease.json").read_text())["status"] == "released"


def test_serial_environment_remains_unchanged():
    slot = slots.resolve_slots("slot-a")[0]
    assert slot.as_environment()["GREENRAN_E2_TERM_PORT"] == "36421"
    assert slot.cgroup_relative_root == "slots/slot-a"
