#!/usr/bin/env python3

import importlib.util
import sys
import unittest
from pathlib import Path


class VehicleCollectionPlanTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.project_root = Path(__file__).resolve().parents[1]
        cls.wrapper_module = cls._load_module(
            "run_vehicle_conflict_experiments_test",
            "scripts/run_vehicle_conflict_experiments.py",
        )

    @classmethod
    def _load_module(cls, module_name: str, relative_path: str):
        module_path = cls.project_root / relative_path
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module

    def test_collection_plan_uses_450_rows_per_vehicle_scenario(self):
        plan = self.wrapper_module.build_collection_plan(450, 6)

        self.assertEqual(plan["target_total_rows"], 1800)
        self.assertEqual(plan["target_rows_per_scenario"], 450)
        self.assertEqual(plan["max_rounds_per_scenario"], 6)
        self.assertEqual(plan["scenario_count"], 4)
        self.assertEqual(
            [scenario["slug"] for scenario in plan["scenarios"]],
            list(self.wrapper_module.DEFAULT_SCENARIOS),
        )
        self.assertTrue(all(scenario["target_rows"] == 450 for scenario in plan["scenarios"]))

    def test_total_target_rows_must_match_vehicle_scenario_split(self):
        args = self.wrapper_module.parse_args([
            "--target-total-rows",
            "1800",
            "--target-rows-per-scenario",
            "450",
        ])

        self.assertEqual(self.wrapper_module.resolve_target_rows_per_scenario(args), 450)

    def test_total_target_rows_rejects_non_divisible_values(self):
        args = self.wrapper_module.parse_args([
            "--target-total-rows",
            "1750",
            "--target-rows-per-scenario",
            "0",
        ])

        with self.assertRaises(SystemExit):
            self.wrapper_module.resolve_target_rows_per_scenario(args)

    def test_default_max_rounds_scales_with_target_rows(self):
        args = self.wrapper_module.parse_args(["--target-total-rows", "1800"])
        target_rows = self.wrapper_module.resolve_target_rows_per_scenario(args)

        self.assertEqual(
            self.wrapper_module.resolve_max_rounds_per_scenario(args, target_rows),
            6,
        )

    def test_build_command_resumes_latest_experiment_by_default(self):
        args = self.wrapper_module.parse_args(["--target-total-rows", "1800"])
        target_rows = self.wrapper_module.resolve_target_rows_per_scenario(args)
        max_rounds = self.wrapper_module.resolve_max_rounds_per_scenario(args, target_rows)

        command = self.wrapper_module.build_command(args, target_rows, max_rounds)

        self.assertIn("--continue", command)

    def test_build_command_can_force_fresh_experiment(self):
        args = self.wrapper_module.parse_args(["--target-total-rows", "1800", "--fresh"])
        target_rows = self.wrapper_module.resolve_target_rows_per_scenario(args)
        max_rounds = self.wrapper_module.resolve_max_rounds_per_scenario(args, target_rows)

        command = self.wrapper_module.build_command(args, target_rows, max_rounds)

        self.assertNotIn("--continue", command)


if __name__ == "__main__":
    unittest.main()
