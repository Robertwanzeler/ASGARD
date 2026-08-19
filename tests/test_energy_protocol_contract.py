#!/usr/bin/env python3

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


class EnergyProtocolContractTestCase(unittest.TestCase):
    def setUp(self):
        self.project_root = Path(__file__).resolve().parents[1]
        self.src_dir = self.project_root / "src"
        if str(self.src_dir) not in sys.path:
            sys.path.insert(0, str(self.src_dir))

        self.temp_dir = tempfile.mkdtemp(prefix="greenran_energy_protocol_")
        self.command_path = os.path.join(self.temp_dir, "energy_command.json")

        from energy_command_protocol import EnergyCommand, ACTIONS

        self.EnergyCommand = EnergyCommand
        self.ACTIONS = ACTIONS

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _read_command_file(self):
        with open(self.command_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def test_send_reduce_power_uses_conditional_reduce_contract(self):
        cmd = self.EnergyCommand(command_path=self.command_path)

        ok = cmd.send_reduce_power(reason="contract_test")

        self.assertTrue(ok)
        written = self._read_command_file()
        self.assertEqual(written["action"], "CONDITIONAL_REDUCE")
        self.assertEqual(written["power_level"], 70)
        self.assertEqual(written["ttl_seconds"], 3)
        self.assertEqual(written["ru_count"], 1)
        self.assertEqual(written["mmwave_count"], 1)

    def test_power_down_eco_writes_expected_payload(self):
        cmd = self.EnergyCommand(command_path=self.command_path)

        ok = cmd.send_power_down_eco(reason="eco_mode")

        self.assertTrue(ok)
        written = self._read_command_file()
        self.assertEqual(written["action"], "POWER_DOWN_ECO")
        self.assertEqual(written["power_level"], 25)
        self.assertEqual(written["ttl_seconds"], 5)
        self.assertEqual(written["ru_count"], 1)
        self.assertEqual(written["mmwave_count"], 1)

    def test_protocol_actions_are_accepted_by_xapp_parser(self):
        c_source = self.project_root / "flexric" / "examples" / "xApp" / "c" / "energy_saver" / "xapp_energy_saver.c"
        source_text = c_source.read_text(encoding="utf-8")

        required_actions = {
            "FULL_POWER",
            "CONDITIONAL_REDUCE",
            "POWER_DOWN",
            "POWER_DOWN_ECO",
            "MAINTAIN",
        }

        missing = []
        for action in required_actions:
            marker = f'strcmp(action_str, "{action}")'
            if marker not in source_text:
                missing.append(action)

        self.assertEqual(missing, [], f"xApp parser missing protocol actions: {missing}")

    def test_legacy_reduce_power_alias_remains_supported(self):
        self.assertIn("REDUCE_POWER", self.ACTIONS)

        c_source = self.project_root / "flexric" / "examples" / "xApp" / "c" / "energy_saver" / "xapp_energy_saver.c"
        source_text = c_source.read_text(encoding="utf-8")

        self.assertIn('strcmp(action_str, "REDUCE_POWER")', source_text)


if __name__ == "__main__":
    unittest.main()
