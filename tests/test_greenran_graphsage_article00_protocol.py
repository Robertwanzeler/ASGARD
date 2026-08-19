import unittest
from pathlib import Path

from scripts.run_greenran_graphsage_article00_protocol import (
    DEFAULT_EPOCHS,
    DEFAULT_SEEDS,
    DEFAULT_SUBSET_SIZES,
    DEFAULT_TARGET_EPOCH,
    DEFAULT_THRESHOLDS,
    DISCARDED_RUNS,
    OFFICIAL_SCENARIOS,
    protocol_manifest,
    selected_scenarios,
)


class _Args:
    target_epoch = DEFAULT_TARGET_EPOCH
    goal_f1 = 1.0
    hidden_dim = 16
    embed_dim = 16
    dropout = 0.10
    learning_rate = 0.001
    weight_decay = 1e-4
    selection_mode = "f1"


class GreenRANGraphSAGEArticle00ProtocolTests(unittest.TestCase):
    def test_selected_scenarios_defaults_to_frozen_matrix(self):
        selected = selected_scenarios([])
        self.assertEqual(len(selected), 10)
        self.assertEqual(selected[0].scenario, "vehicle_warning")
        self.assertEqual(selected[-1].scenario, "recuperacao")

    def test_clean_vehicle_scenarios_use_warmup_rerun_sources(self):
        selected = {item.scenario: item for item in OFFICIAL_SCENARIOS}
        self.assertEqual(
            selected["vehicle_critical"].experiment_dir,
            "experimentos_conflitos_vehicle_clean/20260515_194647_conflict_protocol",
        )
        self.assertEqual(
            selected["vehicle_implicito"].experiment_dir,
            "experimentos_conflitos_vehicle_clean/20260515_194647_conflict_protocol",
        )
        self.assertEqual(
            selected["vehicle_recovery"].experiment_dir,
            "experimentos_conflitos_vehicle_recovery_clean/20260516_101015_conflict_protocol",
        )

    def test_clean_app12_scenarios_use_rerun_sources(self):
        selected = {item.scenario: item for item in OFFICIAL_SCENARIOS}
        self.assertEqual(
            selected["app1_throughput"].experiment_dir,
            "experimentos_conflitos_app1_throughput_clean/20260516_122522_conflict_protocol",
        )
        self.assertEqual(
            selected["app1_latencia"].experiment_dir,
            "experimentos_conflitos_app12_clean/20260515_224240_conflict_protocol",
        )
        self.assertEqual(
            selected["app2_degradado_leve"].experiment_dir,
            "experimentos_conflitos_app12_clean/20260515_224240_conflict_protocol",
        )
        self.assertEqual(
            selected["app2_degradado_critico"].experiment_dir,
            "experimentos_conflitos_app12_clean/20260515_224240_conflict_protocol",
        )

    def test_selected_scenarios_rejects_unknown_filters(self):
        with self.assertRaises(SystemExit):
            selected_scenarios(["nao_existe"])

    def test_manifest_freezes_valid_and_discarded_runs(self):
        manifest = protocol_manifest(
            output_root=Path("/tmp/graphsage_article00_protocol_test"),
            trainer_python="/usr/bin/python3",
            selected=selected_scenarios(["conflito_implicito", "recuperacao"]),
            thresholds=DEFAULT_THRESHOLDS,
            seeds=(42,),
            subset_sizes=DEFAULT_SUBSET_SIZES,
            epochs=DEFAULT_EPOCHS,
            args=_Args(),
        )
        self.assertEqual(manifest["official_target"]["target_epoch"], 200)
        self.assertEqual(manifest["official_target"]["official_subset_for_comparison"], 450)
        self.assertEqual(len(manifest["frozen_matrix"]["selected_runs"]), 2)
        self.assertEqual(len(manifest["frozen_matrix"]["discarded_runs"]), len(DISCARDED_RUNS))
        self.assertEqual(len(manifest["runs"]), 6)
        first = manifest["runs"][0]
        self.assertEqual(first["scenario"], "conflito_implicito")
        self.assertEqual(first["threshold"], 0.2)
        self.assertEqual(first["seed"], 42)
        self.assertTrue(first["output_dir"].endswith("threshold_0_2/seed_42/conflito_implicito"))


if __name__ == "__main__":
    unittest.main()
