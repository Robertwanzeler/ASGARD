import unittest

from src.greenran_marl_topology import build_du_state_snapshot, load_logical_du_topology


class TestMARLTopology(unittest.TestCase):
    def test_manifest_topology_has_three_logical_dus(self):
        topology = load_logical_du_topology()
        self.assertEqual(topology.get('logical_du_count'), 3)
        self.assertEqual(len(topology.get('logical_dus', [])), 3)

    def test_du_snapshot_exports_slice_and_global_state(self):
        snapshot = build_du_state_snapshot(
            camera_metrics={'active_cameras': 3, 'throughput_mbps': 18.0, 'latency_ms': 82.0},
            app2_metrics={'total_sensors': 12, 'active_sensors': 10, 'connected_ratio': 0.84, 'delivery_success_percent': 91.0, 'avg_latency_ms': 420.0},
            vehicle_metrics={'total_vehicles': 5, 'high_risk_vehicles': 2, 'max_latency_ms': 95.0, 'max_packet_loss_percent': 6.0},
            network_health={'p95_us': 88000, 'cvar_us': 140000},
            resource_snapshot={'usable_budget': 0.95, 'resource_budget': 1.0, 'd_ran': 0.82, 'd_ai': 0.44, 'r_ran': 0.71, 'r_ai': 0.24, 'ran_completion_ratio': 0.86, 'ai_completion_ratio': 0.73, 'utilization_ratio': 0.95, 'ai_components': {'app2_pressure': 0.6, 'vehicle_pressure': 0.4}},
        )
        self.assertIn('slice_state', snapshot)
        self.assertIn('du_states', snapshot)
        self.assertIn('global_state', snapshot)
        self.assertEqual(len(snapshot['du_states']), 3)
        self.assertIn('eMBB', snapshot['slice_state'])
        self.assertIn('mMTC', snapshot['slice_state'])
        self.assertIn('URLLC', snapshot['slice_state'])
        self.assertEqual(len(snapshot['global_state']['state_vector']), 10)
        for du in snapshot['du_states']:
            self.assertEqual(len(du['state_vector']), 10)


if __name__ == '__main__':
    unittest.main()
