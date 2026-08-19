#!/usr/bin/env python3

import importlib.util
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


class RuntimePathConsistencyTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="greenran_runtime_paths_")
        os.environ["GREENRAN_STATE_DIR"] = self.temp_dir
        self.project_root = Path(__file__).resolve().parents[1]
        self.src_dir = self.project_root / "src"
        if str(self.src_dir) not in sys.path:
            sys.path.insert(0, str(self.src_dir))

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        os.environ.pop("GREENRAN_STATE_DIR", None)

    def _load_module(self, module_name: str, relative_path: str):
        for stale_name in [module_name, "greenran_paths"]:
            if stale_name in sys.modules:
                del sys.modules[stale_name]

        module_path = self.project_root / relative_path
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module

    def test_app1_services_uses_runtime_state_dir_for_scenario_control(self):
        module = self._load_module(
            "app1_services_runtime_test",
            "apps/app1_vigilancia/backend/services.py",
        )
        expected = Path(self.temp_dir) / "article00_scenario_control.json"
        self.assertEqual(module.ARTICLE00_SCENARIO_CONTROL_FILE, expected)

    def test_app2_simulator_uses_runtime_state_dir(self):
        module = self._load_module(
            "app2_simulator_runtime_test",
            "apps/app2_monitoramento/backend/simulate_sensors.py",
        )
        expected_state_dir = Path(self.temp_dir)
        self.assertEqual(module.STATE_DIR, expected_state_dir)
        self.assertEqual(module.APP2_STATE_DIR, expected_state_dir / "app2_monitoramento")
        self.assertEqual(module.SCENARIO_CONTROL_FILE, expected_state_dir / "article00_scenario_control.json")

    def test_scenario_watcher_uses_runtime_state_dir(self):
        module = self._load_module(
            "scenario_watcher_runtime_test",
            "scripts/watch_scenario_runtime.py",
        )
        expected_state_dir = Path(self.temp_dir)
        self.assertEqual(module.METRICS_PATH, expected_state_dir / "xapp_metrics" / "extended_metrics.json")
        self.assertEqual(module.APP2_SNAPSHOT_PATH, expected_state_dir / "app2_monitoramento" / "monitoring_snapshot.json")
        self.assertEqual(module.DEFAULT_OUTPUT, expected_state_dir / "greenran_scenario_watch.jsonl")


if __name__ == "__main__":
    unittest.main()
