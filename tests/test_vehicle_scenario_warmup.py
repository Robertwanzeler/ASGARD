import unittest

from scripts.run_conflict_experiments import (
    SCENARIOS,
    build_round_control_profile,
    scenario_capture_warmup_seconds,
)


class VehicleScenarioWarmupTests(unittest.TestCase):
    def test_vehicle_critical_and_implicito_use_warmup(self):
        scenario_map = {scenario.slug: scenario for scenario in SCENARIOS}
        self.assertEqual(scenario_capture_warmup_seconds(scenario_map["vehicle_critical"]), 15)
        self.assertEqual(scenario_capture_warmup_seconds(scenario_map["vehicle_implicito"]), 15)

    def test_latency_and_app2_sensitive_scenarios_also_use_warmup(self):
        scenario_map = {scenario.slug: scenario for scenario in SCENARIOS}
        self.assertEqual(scenario_capture_warmup_seconds(scenario_map["app1_latencia"]), 15)
        self.assertEqual(scenario_capture_warmup_seconds(scenario_map["app2_degradado_leve"]), 15)
        self.assertEqual(scenario_capture_warmup_seconds(scenario_map["app2_degradado_critico"]), 15)
        self.assertEqual(scenario_capture_warmup_seconds(scenario_map["vehicle_recovery"]), 15)

    def test_other_scenarios_keep_zero_warmup(self):
        scenario_map = {scenario.slug: scenario for scenario in SCENARIOS}
        self.assertEqual(scenario_capture_warmup_seconds(scenario_map["vehicle_warning"]), 0)
        self.assertEqual(scenario_capture_warmup_seconds(scenario_map["app1_throughput"]), 0)

    def test_app2_light_profile_avoids_packet_loss_trigger(self):
        scenario_map = {scenario.slug: scenario for scenario in SCENARIOS}
        stage = build_round_control_profile(scenario_map["app2_degradado_leve"], round_index=1, duration=600)[0]
        self.assertLess(stage["app2"]["packet_loss_percent"], 5.0)

    def test_app2_critical_profile_centers_on_connected_ratio(self):
        scenario_map = {scenario.slug: scenario for scenario in SCENARIOS}
        stage = build_round_control_profile(scenario_map["app2_degradado_critico"], round_index=1, duration=600)[0]
        self.assertEqual(stage["app2"]["connected_sensors"], 14)
        self.assertLess(stage["app2"]["packet_loss_percent"], 5.0)

    def test_app1_throughput_profile_stays_in_single_degraded_regime(self):
        scenario_map = {scenario.slug: scenario for scenario in SCENARIOS}
        stage1 = build_round_control_profile(scenario_map["app1_throughput"], round_index=1, duration=600)[0]
        stage2 = build_round_control_profile(scenario_map["app1_throughput"], round_index=2, duration=600)[0]
        self.assertEqual(stage1["app1"]["throughput_mbps"], 24.0)
        self.assertEqual(stage2["app1"]["throughput_mbps"], 24.0)


if __name__ == "__main__":
    unittest.main()
