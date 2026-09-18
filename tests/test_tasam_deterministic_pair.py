import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.run_tasam_deterministic_pair import arm_command, build_pair_manifest
from scripts.tasam_pairing import canonical_schedule, storage_is_rw
from scripts.run_tasam_online_arm import build_environment


class TestTasamDeterministicPair(unittest.TestCase):
    def test_schedule_is_deterministic_and_covers_nine_stages(self):
        first = canonical_schedule("tasam_training_balanced_v3", 47, 600)
        second = canonical_schedule("tasam_training_balanced_v3", 47, 600)
        self.assertEqual(first, second)
        self.assertEqual(first["expected_stages"], [
            "allowed_bootstrap", "allowed_stable", "camera_conditional", "camera_blocked",
            "vehicle_conditional", "vehicle_blocked", "app2_conditional", "app2_blocked",
            "allowed_recovery",
        ])
        self.assertEqual(first["snapshot_slots"], 2400)
        self.assertTrue(first["schedule_id"].startswith("pair-"))

    def test_schedule_changes_when_pairing_inputs_change(self):
        a = canonical_schedule("tasam_training_balanced_v3", 47, 600)
        b = canonical_schedule("tasam_training_balanced_v3", 46, 600)
        self.assertNotEqual(a["schedule_id"], b["schedule_id"])

    def test_pair_manifest_has_same_target_and_explicit_modes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            schedule = canonical_schedule("tasam_training_balanced_v3", 47, 600)
            manifest = build_pair_manifest(root, schedule, checkpoint=root / "checkpoint", decision_target=280)
        self.assertEqual(manifest["decision_target"], 280)
        self.assertEqual(manifest["arms"]["rapp_only"], {"mode": "rapp_only", "armd": False, "tasam": False})
        self.assertEqual(manifest["arms"]["combined"], {"mode": "combined", "armd": True, "tasam": True})

    def test_arm_commands_share_schedule_and_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            schedule = canonical_schedule("tasam_training_balanced_v3", 47, 600)
            commands = [
                arm_command("rapp_only", root / "rapp_only", seed=47, profile="tasam_training_balanced_v3", wall_time=600,
                            decision_target=280, schedule_id=schedule["schedule_id"], schedule_file=root / "pairing_schedule.json", checkpoint=root / "checkpoint"),
                arm_command("combined", root / "combined", seed=47, profile="tasam_training_balanced_v3", wall_time=600,
                            decision_target=280, schedule_id=schedule["schedule_id"], schedule_file=root / "pairing_schedule.json", checkpoint=root / "checkpoint"),
            ]
        for command in commands:
            self.assertIn("--decision-target", command)
            self.assertEqual(command[command.index("--decision-target") + 1], "280")
            self.assertIn(schedule["schedule_id"], command)
        self.assertNotIn("--checkpoint", commands[0])
        self.assertIn("--checkpoint", commands[1])

    def test_pairing_environment_is_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            schedule = canonical_schedule("tasam_training_balanced_v3", 47, 600)
            env = build_environment(
                "combined", Path(tmp), 47, "tasam_training_balanced_v3", 600,
                decision_target=280, pairing_schedule_id=schedule["schedule_id"],
                pairing_schedule_file=Path(tmp) / "pairing_schedule.json",
            )
        self.assertEqual(env["GREENRAN_DECISION_TARGET"], "280")
        self.assertEqual(env["GREENRAN_PAIRING_SCHEDULE_ID"], schedule["schedule_id"])
        self.assertEqual(env["GREENRAN_PAIRING_SCHEDULE_FILE"], str(Path(tmp) / "pairing_schedule.json"))

    @patch("scripts.tasam_pairing.subprocess.run")
    def test_read_only_storage_is_rejected(self, run):
        run.return_value.returncode = 0
        run.return_value.stdout = "ro,relatime"
        ok, options = storage_is_rw(Path("/run/media/robert/GREENRAN"))
        self.assertFalse(ok)
        self.assertEqual(options, "ro,relatime")


if __name__ == "__main__":
    unittest.main()

