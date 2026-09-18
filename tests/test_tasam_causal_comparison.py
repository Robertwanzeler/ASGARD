import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.evaluate_tasam_causal_comparison import build_report
from src.rapp_control_trial import JointControlTrial
from src.rapp_data_lake import DataLake


class TestTasamCausalComparison(unittest.TestCase):
    def test_sqlite_migration_adds_causal_columns_and_initial_observation(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'rapp_data_lake.db'
            lake = DataLake(str(db))
            lake.ensure_initial_energy_observation()
            decision_columns = {row[1] for row in lake.conn.execute('pragma table_info(decisions_history)')}
            shadow_columns = {row[1] for row in lake.conn.execute('pragma table_info(marl_shadow_comparison_history)')}
            self.assertIn('tasam_checkpoint_valid', decision_columns)
            self.assertIn('causal_score_delta', decision_columns)
            self.assertIn('energy_model_version', shadow_columns)
            self.assertEqual(lake.latest_energy_observation()['command'], 'INITIAL_OBSERVED_STATE')
            lake.close()

    def test_energy_command_is_correlated_to_the_economic_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            lake = DataLake(str(Path(tmp) / "rapp_data_lake.db"))
            lake.record_energy_command(
                command="POWER_DOWN_ECO", power_percent=25,
                ru_count=1, mmwave_count=1,
                action_correlation_id="economic:test:1",
                action_origin="ta_sam", application_status="command_sent",
            )
            self.assertEqual(lake.associate_energy_command_decision("economic:test:1", 77), 1)
            self.assertEqual(lake.update_energy_command_application("economic:test:1", "verified"), 1)
            command = lake.energy_command_for_correlation("economic:test:1")
            self.assertEqual(command["decision_id"], 77)
            self.assertEqual(command["application_status"], "verified")
            lake.close()

    def test_assistant_only_honors_canary_fraction(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {
                "GREENRAN_TASAM_ADVISOR_MODE": "assistant_only_control",
                "GREENRAN_CONTROL_TRIAL_FRACTION": "0.10",
            },
        ):
            trial = JointControlTrial({"enabled": True, "state_path": str(Path(tmp) / "state.json")})
            self.assertAlmostEqual(trial.fraction, 0.10)

    def test_shadow_pair_is_not_accepted_as_causal_treatment(self):
        baseline = Path("runs/tasam_smoke_pair_seed47_20260906_retry3/rapp_only")
        shadow = Path("runs/tasam_smoke_pair_seed47_20260906_retry4/combined_shadow")
        if not (baseline / "rapp_data_lake.db").is_file() or not (shadow / "rapp_data_lake.db").is_file():
            self.skipTest("smoke fixtures unavailable")
        report = build_report(baseline, shadow, seed=47, wall_time=120)
        self.assertEqual(report["schema"], "greenran.tasam.causal_comparison.v2")
        self.assertFalse(report["measurement_valid"])


if __name__ == "__main__":
    unittest.main()
