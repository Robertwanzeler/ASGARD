import unittest
import json

from src.tasam_learning_meter import build_learning_meter


def row(**overrides):
    value = {
        "economic_application_status": "applied",
        "tasam_actuation_applied": 1,
        "economic_training_eligible": 1,
        "economic_promotion_eligible": 1,
        "tasam_checkpoint_valid": 1,
        "tasam_evidence_valid": 1,
        "tasam_fallback_used": 0,
        "economic_safety_isolated": 0,
        "topology_valid": 1,
        "realized_energy_saving_fraction": 0.10,
        "realized_allocation_saving_fraction": 0.05,
        "tasam_online_reward": 0.20,
        "tasam_sla_penalty": 0.0,
        "live_power_w": 100.0,
        "shadow_power_w": 90.0,
        "ran_allocation": 0.3,
        "ai_allocation": 0.3,
        "economic_action_json": json.dumps({
            "live_candidate": {
                "power_w": 100.0,
                "total_allocation": 0.7,
                "ran_allocation": 0.4,
                "ai_allocation": 0.3,
            },
            "applied": {
                "power_w": 90.0,
                "total_allocation": 0.6,
                "ran_allocation": 0.35,
                "ai_allocation": 0.25,
            },
        }),
    }
    value.update(overrides)
    return value


class TestTasamLearningMeter(unittest.TestCase):
    def test_disabled_training_is_zero_and_cannot_claim_learning(self):
        meter = build_learning_meter([row() for _ in range(10)], {
            "ml_enabled": False,
            "retrain_enabled": False,
            "updates_completed": 0,
        })
        self.assertEqual(meter["learning_meter"], 0.0)
        self.assertEqual(meter["status"], "DISABLED")

    def test_applied_economic_rows_produce_comparison_and_learning_state(self):
        meter = build_learning_meter([row() for _ in range(70)], {
            "ml_enabled": True,
            "retrain_enabled": True,
            "updates_completed": 1,
            "candidate_promoted": False,
        })
        self.assertGreater(meter["learning_meter"], 0.0)
        self.assertEqual(meter["economic_transitions"], 70)
        self.assertEqual(meter["comparison_sample_count"], 70)
        self.assertEqual(meter["comparison"]["rows"][0]["live_ran_allocation"], 0.4)
        self.assertEqual(meter["comparison"]["rows"][0]["treatment_ran_allocation"], 0.35)
        self.assertIn(meter["status"], {"LEARNING", "IMPROVING"})

    def test_shadow_rejected_and_safety_rows_do_not_count(self):
        rows = [
            row(),
            row(economic_application_status="rejected", tasam_actuation_applied=0),
            row(economic_safety_isolated=1),
            row(tasam_fallback_used=1),
        ]
        meter = build_learning_meter(rows, {
            "ml_enabled": True,
            "retrain_enabled": True,
            "updates_completed": 1,
        })
        self.assertEqual(meter["economic_transitions"], 1)
        self.assertEqual(meter["comparison_sample_count"], 1)

    def test_sla_penalty_blocks_learning_score(self):
        meter = build_learning_meter([row(tasam_sla_penalty=1.0) for _ in range(70)], {
            "ml_enabled": True,
            "retrain_enabled": True,
            "updates_completed": 1,
        })
        self.assertEqual(meter["economic_transitions"], 0)
        self.assertEqual(meter["status"], "INSUFFICIENT_DATA")

    def test_rejected_penalty_does_not_mark_applied_learning_as_regressing(self):
        rows = [
            row() for _ in range(70)
        ] + [
            row(
                economic_application_status="rejected",
                tasam_actuation_applied=0,
                economic_training_eligible=0,
                economic_promotion_eligible=0,
                tasam_sla_penalty=1.0,
            )
            for _ in range(20)
        ]
        meter = build_learning_meter(rows, {
            "ml_enabled": True,
            "retrain_enabled": True,
            "updates_completed": 1,
        })
        self.assertTrue(meter["safety_ok"])
        self.assertEqual(meter["economic_regression_count"], 0)
        self.assertNotEqual(meter["status"], "REGRESSING")


if __name__ == "__main__":
    unittest.main()
