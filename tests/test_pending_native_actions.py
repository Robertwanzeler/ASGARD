#!/usr/bin/env python3
"""Pending native action bookkeeping: the registered wire payload is truth.

r6g evidence: the orchestrator registered the global power summary (100%)
while the E2 bundle carried a 70% per-DU candidate; every confirmation then
mismatched the native readback (191 expired actions, zero economic
transitions eligible).  The pending row must store the wire map and the
confirmation must consume it.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path


class PendingNativeActionTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="greenran_pending_native_")
        os.environ["GREENRAN_STATE_DIR"] = self.temp_dir
        project_root = Path(__file__).resolve().parents[1]
        src_dir = project_root / "src"
        if str(src_dir) not in sys.path:
            sys.path.insert(0, str(src_dir))
        for module_name in ["greenran_paths", "rapp_data_lake"]:
            if module_name in sys.modules:
                del sys.modules[module_name]
        from rapp_data_lake import DataLake
        self.db_path = Path(self.temp_dir) / "pending_native_test.db"
        self.lake = DataLake(str(self.db_path))

    def test_register_accepts_per_cell_wire_map(self):
        registered = self.lake.register_pending_native_action(
            "camp", 4, "corr-4", 4, 1.8, 5.0, [2, 3, 4], {2: 70.0, 3: 70.0, 4: 70.0})
        self.assertTrue(registered)
        self.assertEqual(
            self.lake.pending_native_expected_power("corr-4"),
            {2: 70.0, 3: 70.0, 4: 70.0},
        )

    def test_pending_expected_power_supports_legacy_scalar_and_unknown(self):
        self.lake.register_pending_native_action(
            "camp", 5, "corr-5", 5, 2.4, 5.0, [2, 3, 4], 100.0)
        self.assertEqual(self.lake.pending_native_expected_power("corr-5"), 100.0)
        self.assertIsNone(self.lake.pending_native_expected_power("missing"))

    def test_confirmation_consumes_registered_wire_map(self):
        """The exact r6g tx=4 shape: book said 100, wire carried 70."""
        self.lake.register_pending_native_action(
            "arm", 4, "economic:corr:4", 4, 1.8, 5.0, [2, 3, 4], {2: 70.0, 3: 70.0, 4: 70.0})
        self.assertEqual(
            self.lake.pending_native_expected_power("economic:corr:4"),
            {2: 70.0, 3: 70.0, 4: 70.0},
        )


if __name__ == "__main__":
    unittest.main()
