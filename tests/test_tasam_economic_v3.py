import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from greenran_control_bundle import ControlBundleError, V3_SCHEMA, validate_bundle
from greenran_infra_budget import build_physical_budget
from tasam_economic_v3 import (
    CONTRACT,
    DUSleepCoordinator,
    ENERGY_STAIRCASE_CONTRACT,
    EconomicActionV3Error,
    build_energy_staircase,
    normalize_power_by_cell,
    project_total_budget_fraction,
    quantize_power_percent_v3,
    realized_economic_reward,
    staircase_candidate,
)


def _bundle(power=None, sleep=None):
    power = power or {2: 100, 3: 75, 4: 50}
    cells = {2: [], 3: [], 4: []}
    for imsi in range(1, 21):
        cell = 2 if imsi <= 6 else 3 if imsi <= 9 else 4
        cells[cell].append({"imsi": imsi, "min_dl_share_bp": 100, "min_ul_share_bp": 0})
    payload = {
        "schema": V3_SCHEMA,
        "economic_action_contract": CONTRACT,
        "policy_id": "v10-test",
        "sequence": 1,
        "issued_at_ns": time.time_ns(),
        "ttl_ms": 5000,
        "mode": CONTRACT,
        "power_percent_by_cell": {str(k): v for k, v in power.items()},
        "cells": [
            {"cell_id": cell, "tx_power_percent": power[cell], "ue_policies": rows}
            for cell, rows in cells.items()
        ],
        "infra": build_physical_budget(1.0),
    }
    if sleep:
        payload["du_sleep"] = sleep
    return payload


def test_v3_accepts_independent_du_power_and_zero_only_for_sleep():
    normalized = validate_bundle(_bundle())
    assert normalized["power_percent_by_cell"] == {"2": 100, "3": 75, "4": 50}
    assert quantize_power_percent_v3(0) == 0
    with pytest.raises(EconomicActionV3Error):
        quantize_power_percent_v3(20)


def test_v3_allows_only_explicit_native_mc_overlap():
    bundle = _bundle()
    bundle["association_mode"] = "native_rrc_mc_overlap"
    bundle["cells"][1]["ue_policies"].append(dict(bundle["cells"][0]["ue_policies"][0]))
    assert validate_bundle(bundle)["association_mode"] == "native_rrc_mc_overlap"
    bundle.pop("association_mode")
    with pytest.raises(ControlBundleError, match="duplicate IMSI"):
        validate_bundle(bundle)


def test_v3_rejects_two_sleeping_dus_and_unconfirmed_sleep():
    with pytest.raises(ControlBundleError, match="no more than one"):
        validate_bundle(_bundle({2: 0, 3: 0, 4: 100}))
    with pytest.raises(ControlBundleError, match="confirmed handover"):
        validate_bundle(_bundle({2: 0, 3: 100, 4: 100}))
    normalized = validate_bundle(_bundle(
        {2: 0, 3: 100, 4: 100},
        {"source_cell_id": 2, "handover_confirmed": True,
         "pdcp_window_valid": True, "association_valid": True,
         "pdcp_window_s": 10.0},
    ))
    assert normalized["cells"][0]["tx_power_percent"] == 0


def test_v3_budget_uses_capacity_but_never_breaks_sla_floor():
    assert project_total_budget_fraction(0.2, 0.4)[0] == 0.4
    assert project_total_budget_fraction(1.2, 0.4)[0] == 1.0
    assert project_total_budget_fraction(0.5, 1.1)[0] is None


def test_realized_reward_penalizes_excess_and_shortfall():
    positive = realized_economic_reward(0.2, 0.1)
    excess = realized_economic_reward(-0.2, -0.1)
    critical = realized_economic_reward(0.2, 0.2, hard_safety_penalty=1.0)
    assert positive["reward"] > 0
    assert excess["reward"] < 0
    assert critical["reward"] == -1.0


def test_sleep_requires_handover_and_one_full_pdcp_window():
    coordinator = DUSleepCoordinator()
    coordinator.request_sleep(2, [1, 2], [3, 4])
    assert not coordinator.confirm_pdcp_window(duration_s=10, sla_valid=True, all_ues_present=True)
    assert coordinator.confirm_handover(association_valid=True, remaining_ues=[])
    assert not coordinator.confirm_pdcp_window(duration_s=9.9, sla_valid=True, all_ues_present=True)
    assert coordinator.confirm_pdcp_window(duration_s=10, sla_valid=True, all_ues_present=True)
    event = coordinator.commit_sleep()
    assert event["cell_id"] == 2
    with pytest.raises(EconomicActionV3Error):
        coordinator.request_sleep(3, [3], [4])


def test_energy_staircase_is_quantized_and_bounded_to_floor_times_115():
    ladder = build_energy_staircase({2: 41, 3: 50, 4: 80})
    assert ladder[2] == (45, 50)
    assert ladder[3] == (50, 55, 60)
    assert ladder[4] == (80, 85, 90, 95)
    assert all(25 <= value <= 100 for values in ladder.values() for value in values)


def test_energy_staircase_starts_high_then_descends_after_three_healthy_decisions():
    floors = {2: 40, 3: 50, 4: 60}
    requested = {2: 25, 3: 25, 4: 25}
    selected, state = staircase_candidate(
        floors, requested, state={"contract": ENERGY_STAIRCASE_CONTRACT}
    )
    assert selected == {2: 50, 3: 60, 4: 70}
    state["last_observation_healthy"] = True
    for _ in range(2):
        selected, state = staircase_candidate(
            floors, requested, state=state, healthy=True, healthy_required=3
        )
    assert selected == {2: 50, 3: 60, 4: 70}
    selected, state = staircase_candidate(
        floors, requested, state=state, healthy=True, healthy_required=3
    )
    assert selected == {2: 45, 3: 55, 4: 65}


def test_energy_staircase_rejects_missing_floor_and_sleep_is_explicit():
    with pytest.raises(EconomicActionV3Error):
        build_energy_staircase(None)
    ladder = build_energy_staircase({2: 25, 3: 25, 4: 25}, allow_sleep=True)
    assert ladder[2][0] == 0
    selected, _ = staircase_candidate(
        {2: 25, 3: 25, 4: 25}, {2: 0, 3: 100, 4: 100},
        allow_sleep=True,
    )
    assert selected[2] == 0
