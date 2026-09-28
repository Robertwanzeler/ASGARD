#!/usr/bin/env python3
"""Adaptive ASGARD energy-envelope state machine."""

import json
import tempfile
import unittest
from pathlib import Path

import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tasam_dynamic_floor import (  # noqa: E402
    BASELINE_SIGNATURE_SCHEMA,
    DYNAMIC_FLOOR_CONTRACT,
    SAFE_POWER_FLOOR_LEDGER_SCHEMA,
    DynamicFloorError,
    atomic_write_state,
    attributable_violations,
    dynamic_floor_candidate,
    load_baseline_signature,
    load_dynamic_floor_ledger,
    project_asgard_power,
    project_discretionary_symbol_budget,
    sha256_file,
)


class DynamicFloorTest(unittest.TestCase):
    def test_holds_then_descends_5pp_after_5_healthy_windows(self):
        state = None
        for expected_floor in (100, 100, 100, 100, 95):
            command, state = dynamic_floor_candidate(state)
            self.assertEqual(set(command.values()), {expected_floor})
        self.assertEqual(state["floor_percent"], 95)

    def test_descends_to_min_floor_25(self):
        state = None
        floors = []
        for _ in range(100):
            command, state = dynamic_floor_candidate(state)
            floors.append(state["floor_percent"])
        self.assertEqual(min(floors), 25)
        self.assertEqual(state["floor_percent"], 25)

    def test_first_attributable_violation_retreats_10pp(self):
        state = {"floor_percent": 45, "descend_streak": 4, "stable_streak": 9}
        violation = (50, 16, "vehicle_loss")
        command, state = dynamic_floor_candidate(
            state, current_violations=[violation], baseline_signature=[(88, 16, "vehicle_loss")])
        self.assertEqual(state["floor_percent"], 55)
        self.assertFalse(state["descent_enabled"])
        self.assertEqual(state["attributable_last_window"], [violation])
        self.assertEqual(set(command.values()), {55})

    def test_attributable_violation_retreats_only_the_serving_du(self):
        _, state = dynamic_floor_candidate(
            {"floor_percent_by_cell": {"2": 45, "3": 45, "4": 45}},
            current_violations=[(50, 16, "vehicle_loss")],
            baseline_signature=[], affected_cells=[3], window_id="pdcp:50",
        )
        self.assertEqual(state["floor_percent_by_cell"], {"2": 45, "3": 55, "4": 45})
        self.assertEqual(state["affected_cells_last_window"], [3])

    def test_signature_violation_is_not_attributable_and_holds_floor(self):
        state = {"floor_percent": 45, "descend_streak": 4}
        command, state = dynamic_floor_candidate(
            state,
            current_violations=[(88, 16, "vehicle_loss")],
            baseline_signature=[(88, 16, "vehicle_loss")],
        )
        self.assertEqual(state["floor_percent"], 45)
        self.assertEqual(state["retreats"], 0)
        self.assertEqual(state["descend_streak"], 0)
        self.assertEqual(set(command.values()), {45})

    def test_campaign_minimum_45_never_descends_below_calibrated_point(self):
        state = None
        for window in range(1, 31):
            _, state = dynamic_floor_candidate(
                state,
                start_percent=45,
                minimum_percent=45,
                window_id=window,
            )
        self.assertEqual(set(state["floor_percent_by_cell"].values()), {45})

    def test_descent_reenabled_after_10_stable_windows(self):
        state = {"floor_percent": 55, "descent_enabled": False, "retreats": 1}
        for _ in range(9):
            _, state = dynamic_floor_candidate(state)
            self.assertFalse(state["descent_enabled"])
        _, state = dynamic_floor_candidate(state)
        self.assertTrue(state["descent_enabled"])
        self.assertEqual(state["floor_percent"], 55)
        # 5 more healthy windows descend one step.
        for _ in range(4):
            _, state = dynamic_floor_candidate(state)
        self.assertEqual(state["floor_percent"], 55)
        _, state = dynamic_floor_candidate(state)
        self.assertEqual(state["floor_percent"], 50)

    def test_duplicate_window_and_violation_retreat_only_once(self):
        violation = (50, 16, "vehicle_loss")
        _, state = dynamic_floor_candidate(
            {"floor_percent": 45},
            current_violations=[violation],
            start_percent=45,
            minimum_percent=45,
            window_id="pdcp:50",
            power_transaction_id=17,
        )
        self.assertEqual(state["floor_percent"], 55)
        self.assertEqual(state["retreats"], 1)
        self.assertEqual(state["window_sequence_id"], 1)
        _, duplicate = dynamic_floor_candidate(
            state,
            current_violations=[violation],
            start_percent=45,
            minimum_percent=45,
            window_id="pdcp:50",
            power_transaction_id=17,
        )
        self.assertEqual(duplicate, state)
        _, repeated_key = dynamic_floor_candidate(
            duplicate,
            current_violations=[violation],
            start_percent=45,
            minimum_percent=45,
            window_id="pdcp:51",
            power_transaction_id=18,
        )
        self.assertEqual(repeated_key["floor_percent"], 55)
        self.assertEqual(repeated_key["retreats"], 1)
        self.assertEqual(repeated_key["window_sequence_id"], 2)
        _, out_of_order_duplicate = dynamic_floor_candidate(
            repeated_key,
            current_violations=[violation],
            start_percent=45,
            minimum_percent=45,
            window_id="pdcp:50",
            power_transaction_id=17,
        )
        self.assertEqual(out_of_order_duplicate, repeated_key)

    def test_asgard_request_is_clamped_below_floor_and_preserved_above(self):
        selected, evidence = project_asgard_power(
            {2: 40, 3: 70, 4: 45},
            {2: 45, 3: 45, 4: 45},
        )
        self.assertEqual(selected, {2: 45, 3: 70, 4: 45})
        self.assertEqual(evidence["actor_preserved_cells"], [3, 4])
        self.assertEqual(evidence["actor_influenced_cells"], [3])
        sleep_clamped, _ = project_asgard_power(
            {2: 0, 3: 45, 4: 45}, {2: 45, 3: 45, 4: 45}
        )
        self.assertEqual(sleep_clamped[2], 45)
        isolated, isolated_evidence = project_asgard_power(
            selected, {2: 45, 3: 45, 4: 45}, isolated=True,
        )
        self.assertEqual(set(isolated.values()), {100})
        self.assertFalse(isolated_evidence["actor_influenced"])

    def test_asgard_resource_action_becomes_a_physical_discretionary_cap(self):
        caps, evidence = project_discretionary_symbol_budget(0.6, 0.5)
        self.assertEqual(caps, {2: 3000, 3: 3000, 4: 3000})
        self.assertEqual(evidence["requested_discretionary_dl_fraction"], 0.3)
        sleeping_caps, _ = project_discretionary_symbol_budget(0.6, 0.5, active_cells=[3, 4])
        self.assertEqual(sleeping_caps, {3: 3000, 4: 3000})

    def test_attribution_set_difference(self):
        signature = [(88, 16, "vehicle_loss"), (89, 16, "vehicle_loss")]
        current = [(50, 16, "vehicle_loss"), *signature]
        self.assertEqual(
            attributable_violations(current, signature),
            {(50, 16, "vehicle_loss")},
        )

    def test_load_baseline_signature_from_strict_pair_report(self):
        report = {
            "sla_signature": {
                "baseline_violation_keys": [[88, 16, "vehicle_loss"],
                                            [89, 16, "vehicle_p95_latency"]],
            }
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.json"
            path.write_text(json.dumps(report))
            signature = load_baseline_signature(str(path))
        self.assertEqual(signature, {(88, 16, "vehicle_loss"),
                                     (89, 16, "vehicle_p95_latency")})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty.json"
            path.write_text(json.dumps({}))
            with self.assertRaises(DynamicFloorError):
                load_baseline_signature(str(path))

    def test_ledger_v2_binds_physics_and_baseline_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            signature_path = root / "strict.json"
            signature_path.write_text(json.dumps({
                "schema": BASELINE_SIGNATURE_SCHEMA,
                "seed": 43,
                "profile": "profile-v6",
                "experiment_contract": {"baseline_manifest": "r26/arm_manifest.json"},
                "sla_signature": {"baseline_violation_keys": []},
            }))
            ledger_path = root / "ledger.json"
            ledger_path.write_text(json.dumps({
                "schema": SAFE_POWER_FLOOR_LEDGER_SCHEMA,
                "status": "candidate",
                "seed": 43,
                "profile": "profile-v6",
                "initial_floor_percent_by_cell": {"2": 25, "3": 25, "4": 25},
                "minimum_floor_percent_by_cell": {"2": 25, "3": 25, "4": 25},
                "previous_validated_floor_percent_by_cell": {"2": 60, "3": 60, "4": 60},
                "baseline_signature_sha256": sha256_file(signature_path),
                "initial_checkpoint": "r26/asgard",
                "initial_checkpoint_sha256": "d" * 64,
                "physics_evidence": {
                    "status": "validated",
                    "candidate_power_percent": 25,
                    "energy_reduction_fraction": 0.377,
                    "curve_power_percent": [100, 70, 45, 25],
                    "baseline_sha256": "a" * 64,
                    "intermediate_sha256": "c" * 64,
                    "candidate_sha256": "b" * 64,
                    "low_power_sha256": "e" * 64,
                },
                "sleep_evidence": {"status": "validated", "run_sha256": "f" * 64},
            }))
            ledger = load_dynamic_floor_ledger(
                ledger_path,
                expected_seed=43,
                expected_profile="profile-v6",
                baseline_signature_path=signature_path,
            )
            self.assertEqual(ledger["minimum_floor_percent_by_cell"]["2"], 25)
            load_baseline_signature(
                signature_path,
                expected_seed=43,
                expected_profile="profile-v6",
                strict_contract=True,
            )
            state_path = root / "state.json"
            atomic_write_state(state_path, {"contract": DYNAMIC_FLOOR_CONTRACT})
            self.assertEqual(json.loads(state_path.read_text())["contract"], DYNAMIC_FLOOR_CONTRACT)

            bad_ledger = json.loads(ledger_path.read_text())
            bad_ledger["physics_evidence"]["energy_reduction_fraction"] = 0.0
            ledger_path.write_text(json.dumps(bad_ledger))
            with self.assertRaises(DynamicFloorError):
                load_dynamic_floor_ledger(
                    ledger_path, baseline_signature_path=signature_path,
                )

    def test_state_carries_contract_and_rejects_corrupt_floor(self):
        _, state = dynamic_floor_candidate(None)
        self.assertEqual(state["contract"], DYNAMIC_FLOOR_CONTRACT)
        with self.assertRaises(DynamicFloorError):
            dynamic_floor_candidate({"floor_percent": 10})


if __name__ == "__main__":
    unittest.main()
