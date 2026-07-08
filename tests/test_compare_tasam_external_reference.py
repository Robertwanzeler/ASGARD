import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from compare_tasam_external_reference import build_comparison_payload  # noqa: E402


class CompareTasamExternalReferenceTests(unittest.TestCase):
    def test_build_comparison_payload_matches_positive_tasam_direction(self):
        external_reference = {
            "label": "notebook_ref",
            "scenario_family": "article_reference",
            "artifact_path": "/tmp/ref.zip",
            "derived": {
                "tasam_advantage_last100_pct": 27.0,
                "tasam_advantage_global_pct": 12.0,
                "sac_peak_to_final_pct": -14.3,
                "ta_peak_to_final_pct": -6.7,
                "tasam_reduces_forgetting": True,
            },
        }
        baseline_summary = {
            "completed_epochs": 200,
            "target_epochs": 200,
            "best_checkpoint": {"epoch": 180, "metrics": {"eval_return": 40.0}},
            "final_metrics": {"epoch": 200, "eval_return": 36.0, "critic_loss": 0.2, "selected_fraction": 0.2, "alpha": 0.03},
            "history": [
                {"epoch": 180, "eval_return": 40.0},
                {"epoch": 200, "eval_return": 36.0},
            ],
        }
        tasam_summary = {
            "completed_epochs": 200,
            "target_epochs": 200,
            "best_checkpoint": {"epoch": 190, "metrics": {"eval_return": 46.0}},
            "final_metrics": {"epoch": 200, "eval_return": 44.0, "critic_loss": 0.18, "selected_fraction": 0.12, "alpha": 0.04},
            "history": [
                {"epoch": 190, "eval_return": 46.0},
                {"epoch": 200, "eval_return": 44.0},
            ],
        }

        payload = build_comparison_payload(external_reference, baseline_summary, tasam_summary)

        self.assertAlmostEqual(payload["comparison"]["local_best_advantage_pct"], 15.0, places=5)
        self.assertAlmostEqual(payload["comparison"]["local_final_advantage_pct"], 22.222222, places=4)
        self.assertTrue(payload["comparison"]["method_direction_match"])
        self.assertTrue(payload["comparison"]["stability_direction_match"])


if __name__ == "__main__":
    unittest.main()
