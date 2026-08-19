import json
import tempfile
import unittest
from pathlib import Path

from scripts.run_greenran_graphsage_calibration import (
    PROFILE_STRATEGIES,
    PROFILES,
    SCENARIO_CONFIG,
    resolve_profile_for_scenario,
    unresolved_scenarios_from_calibration,
    weak_scenarios_from_protocol,
)


class GreenRANGraphSAGECalibrationTests(unittest.TestCase):
    def test_weak_scenarios_selection_filters_by_threshold(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            payload = {
                "rows": [
                    {"threshold": 0.5, "subset_size": 450, "scenario": "app1_latencia", "target_met": False},
                    {"threshold": 0.5, "subset_size": 450, "scenario": "vehicle_warning", "target_met": True},
                    {"threshold": 0.2, "subset_size": 450, "scenario": "app2_degradado_leve", "target_met": False},
                    {"threshold": 0.5, "subset_size": 150, "scenario": "ignored_subset", "target_met": False},
                ]
            }
            with (root / "protocol_summary.json").open("w", encoding="utf-8") as handle:
                json.dump(payload, handle)

            selected = weak_scenarios_from_protocol(root, (0.5,))
            self.assertEqual(selected, ["app1_latencia"])

    def test_scenario_config_has_expected_modes(self):
        self.assertEqual(SCENARIO_CONFIG["app2_degradado_leve"]["domain_scope_mode"], "service")
        self.assertEqual(SCENARIO_CONFIG["vehicle_critical"]["domain_scope"], "app3")
        self.assertEqual(SCENARIO_CONFIG["recuperacao"]["domain_scope"], "mixed")

    def test_unresolved_scenarios_from_calibration_reads_only_open_cases(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            payload = {
                "aggregates": [
                    {"threshold": 0.5, "scenario": "recuperacao", "target_hits": 5, "completed_seeds": 5},
                    {"threshold": 0.5, "scenario": "app1_latencia", "target_hits": 0, "completed_seeds": 5},
                    {"threshold": 0.2, "scenario": "ignored_threshold", "target_hits": 0, "completed_seeds": 5},
                ]
            }
            with (root / "calibration_summary.json").open("w", encoding="utf-8") as handle:
                json.dump(payload, handle)

            selected = unresolved_scenarios_from_calibration(root, (0.5,))
            self.assertEqual(selected, {"app1_latencia"})

    def test_family_strategy_resolves_profiles(self):
        self.assertIn("family_v1", PROFILE_STRATEGIES)
        self.assertEqual(resolve_profile_for_scenario("family_v1", "ignored", "app1_latencia"), "app1_balanced_v1")
        self.assertEqual(resolve_profile_for_scenario("family_v1", "ignored", "vehicle_critical"), "app3_balanced_v1")

    def test_vehicle_recovery_recall_profile_exists(self):
        self.assertIn("vehicle_recovery_recall_v1", PROFILES)
        self.assertEqual(PROFILES["vehicle_recovery_recall_v1"]["selection_mode"], "f1")
        self.assertEqual(PROFILES["vehicle_recovery_recall_v1"]["fp_penalty_weight"], 0.0)


if __name__ == "__main__":
    unittest.main()
