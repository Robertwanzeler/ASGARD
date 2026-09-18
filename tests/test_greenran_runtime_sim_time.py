import os
import unittest
from unittest import mock

from greenran_runtime import load_runtime_config


class GreenranRuntimeSimTimeTests(unittest.TestCase):
    def test_fractional_simulation_time_is_accepted(self):
        with mock.patch.dict(os.environ, {"GREENRAN_SIM_TIME": "330.5"}, clear=False):
            config = load_runtime_config()
        self.assertEqual(config["simulation"]["default_sim_time_seconds"], 330.5)


if __name__ == "__main__":
    unittest.main()
