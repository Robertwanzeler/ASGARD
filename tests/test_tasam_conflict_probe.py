from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "conflict_probe", ROOT / "scripts" / "run_tasam_v2x_conflict_probe.py"
)
assert SPEC and SPEC.loader
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


def _decision(stage: str, cycle: int = 1, *, conflict: str = "", classification: str = "safe_conservative", reduced: bool = False):
    if reduced:
        classification = "safe_efficient"
    return {
        "decision_id": f"{cycle}-{stage}-{conflict}",
        "cycle": cycle,
        "stage": stage,
        "expected_domain": probe._expected_domain(stage),
        "observed_conflict_domain": conflict,
        "native_conflict_observed": True,
        "classification": classification,
        "e2_complete": True,
        "pdcp_complete": True,
        "power_reduced": reduced,
        "energy_native": reduced,
        "power_w": 400.0 if reduced else 430.0,
        "reference_power_w": 430.0,
        "sim_time_s": float(len(stage) + cycle),
    }


def _complete_cycle(cycle: int = 1):
    rows = []
    for stage in probe.STAGES:
        domain = {"camera_conditional": "camera", "camera_blocked": "camera",
                  "vehicle_conditional": "vehicle", "vehicle_blocked": "vehicle",
                  "app2_conditional": "app2", "app2_blocked": "app2"}.get(stage, "")
        for index in range(3):
            rows.append(_decision(stage, cycle, conflict=domain, reduced=stage == "allowed_stable" and index == 0))
    return rows


def test_coverage_requires_three_native_rows_and_domain_conflict():
    report = probe.coverage_report(_complete_cycle())
    assert report["complete"] is True
    assert report["complete_cycles"] == ["1"]

    rows = _complete_cycle()
    rows = [row for row in rows if not (row["stage"] == "vehicle_blocked" and row["observed_conflict_domain"] == "vehicle")]
    report = probe.coverage_report(rows)
    assert report["complete"] is False


def test_incomplete_coverage_blocks_projection():
    rows = _complete_cycle()
    coverage = probe.coverage_report(rows[:-1])
    projection = probe.energy_projection(rows[:-1], coverage)
    assert coverage["complete"] is False
    assert "projection" not in projection
    assert projection["projection_blocked_reason"]


def test_projection_uses_only_safe_efficient_rows():
    rows = _complete_cycle()
    coverage = probe.coverage_report(rows)
    projection = probe.energy_projection(rows, coverage)
    assert projection["projection"]["complete_cycle_count"] == 1
    assert projection["projection"]["wh_saved_per_sim_second_median"] > 0


def test_missing_native_evidence_never_is_efficient():
    row = {
        "economic_action_contract": probe.ECONOMIC_CONTRACT,
        "scenario_control_override": False,
        "economic_action": {
            "contract": probe.ECONOMIC_CONTRACT,
            "actuation_confirmed": False,
            "native_control_sequence": None,
            "applied": {"power_percent_by_cell": {"2": 80, "3": 80, "4": 80}},
        },
        "energy_evidence": {"native": False, "e2_ack": False, "evidence_version": ""},
        "collection_quality": {"collector_mode": "proxy", "proxy_latency_sample_count": 1},
        "next_metrics": {"collector_mode": "proxy"},
        "decision": {"id": 1, "collection_event_cycle": 1},
        "decision_stage_name": "allowed_stable",
    }
    result = probe.classify_row(row, {})
    assert result["classification"] == "unscorable"
    assert result["power_reduced"] is True
