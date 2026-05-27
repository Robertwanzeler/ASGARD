#!/usr/bin/env python3

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


class FixedScenarioManifestTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="greenran_fixed_scenario_")
        self.project_root = Path(__file__).resolve().parents[1]
        self.src_dir = self.project_root / "src"
        if str(self.src_dir) not in sys.path:
            sys.path.insert(0, str(self.src_dir))

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        os.environ.pop("GREENRAN_FIXED_SCENARIO_CONFIG", None)
        for stale_name in ["greenran_paths", "carla_ns3_mapper"]:
            sys.modules.pop(stale_name, None)

    def _load_module(self, module_name: str, relative_path: str):
        for stale_name in [module_name, "greenran_paths", "carla_ns3_mapper"]:
            sys.modules.pop(stale_name, None)

        module_path = self.project_root / relative_path
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module

    def test_repo_manifest_exposes_canonical_fixed_scenario_counts(self):
        greenran_paths = self._load_module("greenran_paths_repo_manifest_test", "src/greenran_paths.py")

        self.assertEqual(greenran_paths.get_fixed_total_ues(), 12)
        self.assertEqual(greenran_paths.get_fixed_active_cameras(), 3)
        self.assertEqual(greenran_paths.get_fixed_vehicle_base_imsi(), 16)
        self.assertEqual(greenran_paths.get_fixed_max_vehicles(), 5)

    def test_env_override_updates_loader_and_mapper_defaults(self):
        override_path = Path(self.temp_dir) / "scenario_override.json"
        override_path.write_text(
            json.dumps({
                "ns3": {"total_ues": 18},
                "apps": {
                    "app1": {"active_cameras": 4},
                    "app3": {"base_imsi": 31, "max_vehicles": 7},
                },
            }),
            encoding="utf-8",
        )
        os.environ["GREENRAN_FIXED_SCENARIO_CONFIG"] = str(override_path)

        greenran_paths = self._load_module("greenran_paths_override_test", "src/greenran_paths.py")
        mapper_module = self._load_module("carla_ns3_mapper_override_test", "src/carla_ns3_mapper.py")

        self.assertEqual(greenran_paths.get_fixed_total_ues(), 18)
        self.assertEqual(greenran_paths.get_fixed_active_cameras(), 4)
        self.assertEqual(greenran_paths.get_fixed_vehicle_base_imsi(), 31)
        self.assertEqual(greenran_paths.get_fixed_max_vehicles(), 7)

        args = mapper_module.build_arg_parser().parse_args([])
        self.assertEqual(args.base_imsi, 31)
        self.assertEqual(args.max_vehicles, 7)


if __name__ == "__main__":
    unittest.main()
