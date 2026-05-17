import unittest

from scripts.run_conflict_experiments import should_stop_scenario_collection


class ConflictExperimentStopConditionTest(unittest.TestCase):
    def test_target_rows_stop_immediately_when_reached(self):
        should_stop, reason = should_stop_scenario_collection(
            target_enabled=True,
            cumulative_rows=487,
            target_rows=450,
            round_index=4,
            minimum_rounds=6,
            max_rounds=6,
        )
        self.assertTrue(should_stop)
        self.assertEqual(reason, "target_reached")

    def test_non_target_mode_stops_on_minimum_rounds(self):
        should_stop, reason = should_stop_scenario_collection(
            target_enabled=False,
            cumulative_rows=0,
            target_rows=0,
            round_index=3,
            minimum_rounds=3,
            max_rounds=6,
        )
        self.assertTrue(should_stop)
        self.assertEqual(reason, "minimum_rounds_done")

    def test_target_mode_stops_on_round_cap_when_target_not_reached(self):
        should_stop, reason = should_stop_scenario_collection(
            target_enabled=True,
            cumulative_rows=362,
            target_rows=450,
            round_index=6,
            minimum_rounds=3,
            max_rounds=6,
        )
        self.assertTrue(should_stop)
        self.assertEqual(reason, "round_cap_before_target")


if __name__ == "__main__":
    unittest.main()
