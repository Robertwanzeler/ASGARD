import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rapp_ml_predictor import LEGACY_FEATURE_COLS, MLPredictor  # noqa: E402


class TestRappMlPredictor(unittest.TestCase):
    def test_load_runtime_feature_cols_prefers_training_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            model_dir = Path(tmp)
            (model_dir / "training_report.json").write_text(
                json.dumps(
                    {
                        "features": [
                            "cvar_ms",
                            "stage_camera_blocked",
                            "stage_unknown",
                        ]
                    }
                ),
                encoding="utf-8",
            )

            with mock.patch.object(MLPredictor, "_load_models", lambda self: None):
                predictor = MLPredictor(model_dir=str(model_dir))

            self.assertEqual(
                predictor._load_runtime_feature_cols(),
                ["cvar_ms", "stage_camera_blocked", "stage_unknown"],
            )

    def test_prepare_features_supports_stage_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(MLPredictor, "_load_models", lambda self: None):
                predictor = MLPredictor(model_dir=tmp)

            predictor.feature_cols = ["cvar_ms", "stage_camera_blocked", "stage_unknown"]
            features = predictor._prepare_features(
                {
                    "cvar_per_ue_us": 32000.0,
                    "cvar_p95_us": 35000.0,
                    "latency_p95_per_ue_us": 22000.0,
                    "global_avg_latency_us": 18000.0,
                    "variance_per_ue_us2": 4000000.0,
                    "total_active_cameras": 10,
                    "total_active_ues": 100,
                    "total_critical_ues": 6,
                    "throughput_kbps": 25000.0,
                    "packet_loss_rate": 0.01,
                    "jitter_ms": 1.8,
                    "tx_rx_ratio": 1.1,
                    "scenario_stage": "camera_blocked",
                }
            )

            self.assertEqual(features.shape, (1, 3))
            self.assertAlmostEqual(features[0][0], 32.0, places=6)
            self.assertEqual(features[0][1], 1.0)
            self.assertEqual(features[0][2], 0.0)

    def test_load_runtime_feature_cols_falls_back_to_legacy_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(MLPredictor, "_load_models", lambda self: None):
                predictor = MLPredictor(model_dir=tmp)

            self.assertEqual(predictor._load_runtime_feature_cols(), LEGACY_FEATURE_COLS)


if __name__ == "__main__":
    unittest.main()
