import unittest

from scripts.run_greenran_graphsage_hybrid_final import AggregateRow, select_best_source


class GreenRANGraphSAGEHybridFinalTests(unittest.TestCase):
    def test_select_best_source_prefers_higher_mean_f1(self):
        rows = [
            AggregateRow("protocol", "app1_latencia", 0.5, 5, 0, 0.79, 0.79),
            AggregateRow("family_v1", "app1_latencia", 0.5, 5, 0, 0.84, 0.84),
        ]
        selected = select_best_source(rows, 0.5)
        self.assertEqual(selected["app1_latencia"].source, "family_v1")

    def test_select_best_source_uses_hits_before_source_priority(self):
        rows = [
            AggregateRow("protocol", "vehicle_recovery", 0.5, 5, 4, 0.945455, 0.96),
            AggregateRow("calibration_v1", "vehicle_recovery", 0.5, 5, 2, 0.945455, 0.96),
        ]
        selected = select_best_source(rows, 0.5)
        self.assertEqual(selected["vehicle_recovery"].source, "protocol")

    def test_select_best_source_uses_priority_on_exact_tie(self):
        rows = [
            AggregateRow("protocol", "app2_degradado_critico", 0.5, 5, 0, 0.857143, 0.857143),
            AggregateRow("calibration_v1", "app2_degradado_critico", 0.5, 5, 0, 0.857143, 0.857143),
        ]
        selected = select_best_source(rows, 0.5)
        self.assertEqual(selected["app2_degradado_critico"].source, "protocol")

    def test_select_best_source_prefers_protocol_when_it_improves(self):
        rows = [
            AggregateRow("family_v1", "vehicle_critical", 0.5, 5, 0, 0.664706, 0.682353),
            AggregateRow("protocol", "vehicle_critical", 0.5, 5, 5, 1.0, 1.0),
        ]
        selected = select_best_source(rows, 0.5)
        self.assertEqual(selected["vehicle_critical"].source, "protocol")

    def test_select_best_source_prefers_protocol_for_promoted_app12_scenarios(self):
        rows = [
            AggregateRow("family_v1", "app1_latencia", 0.5, 5, 0, 0.842105, 0.842105),
            AggregateRow("protocol", "app1_latencia", 0.5, 5, 5, 1.0, 1.0),
        ]
        selected = select_best_source(rows, 0.5)
        self.assertEqual(selected["app1_latencia"].source, "protocol")

    def test_select_best_source_prefers_protocol_for_promoted_app1_throughput(self):
        rows = [
            AggregateRow("family_v1", "app1_throughput", 0.5, 5, 3, 0.929524, 0.929524),
            AggregateRow("protocol", "app1_throughput", 0.5, 5, 5, 1.0, 1.0),
        ]
        selected = select_best_source(rows, 0.5)
        self.assertEqual(selected["app1_throughput"].source, "protocol")

    def test_select_best_source_prefers_vehicle_recovery_calibration_when_it_closes_gap(self):
        rows = [
            AggregateRow("protocol", "vehicle_recovery", 0.5, 5, 4, 0.945455, 0.96),
            AggregateRow("calibration_vehicle_recovery", "vehicle_recovery", 0.5, 5, 5, 1.0, 1.0),
        ]
        selected = select_best_source(rows, 0.5)
        self.assertEqual(selected["vehicle_recovery"].source, "calibration_vehicle_recovery")


if __name__ == "__main__":
    unittest.main()
