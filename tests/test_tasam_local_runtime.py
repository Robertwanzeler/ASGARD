import json
import tempfile
import unittest
from pathlib import Path

from scripts.preflight_tasam_causal_pilot import _local_path_check
from scripts.run_tasam_causal_pilot import DEFAULT_LOCAL_CHECKPOINT
from scripts.run_tasam_deterministic_pair import DEFAULT_CHECKPOINT as DEFAULT_PAIR_CHECKPOINT
from scripts.run_tasam_online_arm import _validate_checkpoint, _validate_local_path, _validate_runtime_contract


class TestTasamLocalRuntime(unittest.TestCase):
    def test_external_path_is_rejected(self):
        result = _local_path_check(Path("/run/media/robert/GREENRAN/checkpoint"), "checkpoint")
        self.assertFalse(result["valid"])
        with self.assertRaises(SystemExit):
            _validate_local_path(Path("/run/media/robert/GREENRAN/checkpoint"), "checkpoint")

    def test_local_path_is_accepted(self):
        path = Path(__file__).resolve().parents[1] / "runs" / "local-test"
        self.assertTrue(_local_path_check(path, "run-dir")["valid"])

    def test_causal_pilot_default_is_local(self):
        self.assertTrue(DEFAULT_LOCAL_CHECKPOINT.is_relative_to(Path(__file__).resolve().parents[1]))

    def test_deterministic_pair_default_is_local(self):
        self.assertTrue(DEFAULT_PAIR_CHECKPOINT.is_relative_to(Path(__file__).resolve().parents[1]))

    def test_importer_requires_explicit_source(self):
        import scripts.import_tasam_checkpoint_local as importer
        self.assertFalse(hasattr(importer, "DEFAULT_SOURCE"))

    def test_runtime_defaults_do_not_reference_external_hd(self):
        root = Path(__file__).resolve().parents[1]
        for relative in (
            "scripts/run_tasam_seed47_category_curriculum.sh",
            "scripts/run_tasam_deterministic_pair.py",
            "scripts/import_tasam_checkpoint_local.py",
        ):
            self.assertNotIn("/run/media/robert/GREENRAN", (root / relative).read_text())

    def test_local_only_runtime_rejects_external_environment_path(self):
        with self.assertRaises(SystemExit):
            _validate_runtime_contract(
                {"GREENRAN_LOCAL_ONLY": "1", "GREENRAN_DB_PATH": "/run/media/robert/GREENRAN/db"},
                "rapp_only",
                Path("runs/tasam_local_checkpoint_seed47_20260906"),
            )

    def test_legacy_joint_action_shape_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            checkpoint = Path(tmp)
            metadata = {
                "du_count": 3,
                "du_state_dim": 13,
                "global_state_dim": 13,
                "joint_action_dim": 9,
                "uses_global_energy_infra_actor": True,
                "global_actor_path": "tasam_marl_global_actor.pt",
                "category_head_path": "tasam_marl_category_head.pt",
                "power_head_path": "tasam_marl_power_head.pt",
                "allocation_head_path": "tasam_marl_allocation_head.pt",
            }
            (checkpoint / "tasam_marl_checkpoint_meta.json").write_text(json.dumps(metadata), encoding="utf-8")
            for name in metadata.values():
                if isinstance(name, str) and name.endswith(".pt"):
                    (checkpoint / name).write_bytes(b"fixture")
            with self.assertRaises(SystemExit):
                _validate_checkpoint(checkpoint)

    def test_local_import_manifest_is_json_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "local_import_manifest.json"
            path.write_text(json.dumps({"schema": "greenran.tasam.local_checkpoint_import.v1"}), encoding="utf-8")
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema"], "greenran.tasam.local_checkpoint_import.v1")


if __name__ == "__main__":
    unittest.main()
