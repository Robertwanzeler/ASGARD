import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "drlexp" / "src"))

try:
    from drl.ta_sam_marl import (
        MARLRecord,
        TASAMMultiAgentTrainer,
        calibrate_td_variance_threshold,
        linear_rho_schedule,
        td_scaled_rho,
    )
except ModuleNotFoundError as exc:
    if exc.name not in {"torch", "numpy", "pandas", "sklearn", "matplotlib"}:
        raise
    MARLRecord = TASAMMultiAgentTrainer = calibrate_td_variance_threshold = None
    linear_rho_schedule = td_scaled_rho = None


@unittest.skipIf(TASAMMultiAgentTrainer is None, "torch is not installed in this Python interpreter")
class TestTASAMMARL(unittest.TestCase):
    def test_threshold_caps_to_min_selected_fraction_cutoff(self):
        errors = [0.01, 0.02, 0.03, 0.04, 0.05, 0.50, 0.60, 0.70, 0.80, 0.90]
        threshold = calibrate_td_variance_threshold(errors, requested_threshold=10.0, min_selected_fraction=0.20)
        selected = [value for value in errors if value >= threshold]
        self.assertGreaterEqual(len(selected), 2)
        self.assertEqual(threshold, 0.80)

    def test_train_epoch_uses_per_record_td_error_for_selection(self):
        trainer = TASAMMultiAgentTrainer(du_count=1, du_state_dim=2, global_state_dim=2, lr=1e-4)
        records = [
            MARLRecord(global_state=[0.0, 0.0], du_states=[[0.1, 0.2]], reward=0.0, target_actions=[[1.0, 0.0, 0.0]]),
            MARLRecord(global_state=[1.0, 1.0], du_states=[[0.9, 0.8]], reward=10.0, target_actions=[[0.0, 1.0, 0.0]]),
            MARLRecord(global_state=[0.2, 0.1], du_states=[[0.2, 0.1]], reward=0.1, target_actions=[[0.0, 0.0, 1.0]]),
            MARLRecord(global_state=[0.3, 0.3], du_states=[[0.3, 0.3]], reward=0.2, target_actions=[[1.0, 0.0, 0.0]]),
        ]
        metrics = trainer.train_epoch(
            records,
            td_var_threshold=100.0,
            min_selected_fraction=0.25,
            warmup=False,
            bc_weight=1.0,
            value_weight=0.1,
        )
        self.assertGreaterEqual(metrics["selected_fraction"], 0.25)
        self.assertLess(metrics["selected_fraction"], 1.0)
        self.assertGreater(metrics["selected_agents"], 0.0)

    def test_baseline_modes_update_all_records(self):
        trainer = TASAMMultiAgentTrainer(
            du_count=1,
            du_state_dim=2,
            global_state_dim=2,
            lr=1e-4,
            sam_mode="no_sam",
        )
        records = [
            MARLRecord(global_state=[0.0, 0.0], du_states=[[0.1, 0.2]], reward=0.0, target_actions=[[1.0, 0.0, 0.0]]),
            MARLRecord(global_state=[1.0, 1.0], du_states=[[0.9, 0.8]], reward=10.0, target_actions=[[0.0, 1.0, 0.0]]),
            MARLRecord(global_state=[0.2, 0.1], du_states=[[0.2, 0.1]], reward=0.1, target_actions=[[0.0, 0.0, 1.0]]),
        ]
        metrics = trainer.train_epoch(records, td_var_threshold=100.0, min_selected_fraction=0.10)
        self.assertEqual(metrics["selected_fraction"], 1.0)
        self.assertEqual(metrics["selected_agents"], 3.0)

    def test_rho_schedule_and_td_scaling(self):
        self.assertAlmostEqual(linear_rho_schedule(0.05, 0.01, 0.0), 0.05)
        self.assertAlmostEqual(linear_rho_schedule(0.05, 0.01, 1.0), 0.01)
        self.assertAlmostEqual(td_scaled_rho(0.05, 0.01, td_value=0.0, td_max=1.0), 0.01)
        self.assertAlmostEqual(td_scaled_rho(0.05, 0.01, td_value=1.0, td_max=1.0), 0.05)


if __name__ == "__main__":
    unittest.main()
