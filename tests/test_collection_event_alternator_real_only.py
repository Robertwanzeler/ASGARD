import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "collection_event_alternator.py"


def load_module(module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


class TestCollectionEventAlternatorRealOnly(unittest.TestCase):
    def test_balanced_v3_extends_every_stage_for_transition_pairing(self):
        module = load_module("collection_event_alternator_v3")
        profile = module.PROFILES["tasam_training_balanced_v3"]
        self.assertEqual(len(profile), 9)
        self.assertTrue(all(stage.duration_s == 12 for stage in profile))
        self.assertEqual(
            [stage.name for stage in profile],
            [stage.name for stage in module.PROFILES["tasam_training_balanced_v1"]],
        )

    def test_real_only_mode_disables_application_overrides(self):
        previous = os.environ.get("GREENRAN_COLLECTION_DISABLE_APP_OVERRIDES")
        os.environ["GREENRAN_COLLECTION_DISABLE_APP_OVERRIDES"] = "1"
        try:
            module = load_module("collection_event_alternator_real_only")
            profile = module.PROFILES["tasam_training_balanced_v1"]
            stage = profile[2]
            position = module.StagePosition(
                cycle_index=1,
                stage_index=3,
                sim_time_s=0.0,
                cycle_elapsed_s=0.0,
                stage_elapsed_s=0.0,
                stage_remaining_s=float(stage.duration_s),
                stage=stage,
            )
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "scenario.json"
                module.write_payload(path, "tasam_training_balanced_v1", position)
                payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertFalse(payload["app1_camera_override"]["enabled"])
            self.assertFalse(payload["app2_sensor_override"]["enabled"])
            self.assertFalse(payload["vehicle_override"]["enabled"])
            self.assertFalse(payload["network_health_override"]["enabled"])
            self.assertEqual(payload["network_health_override"]["mode"], "real_only_collection")
        finally:
            if previous is None:
                os.environ.pop("GREENRAN_COLLECTION_DISABLE_APP_OVERRIDES", None)
            else:
                os.environ["GREENRAN_COLLECTION_DISABLE_APP_OVERRIDES"] = previous


if __name__ == "__main__":
    unittest.main()
