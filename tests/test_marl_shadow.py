import unittest

from src.rapp_marl_shadow import MARLShadowRuntimeEvaluator


class TestMARLShadow(unittest.TestCase):
    def test_shadow_evaluator_produces_non_actuating_recommendation(self):
        evaluator = MARLShadowRuntimeEvaluator()
        marl_state = {
            'topology_id': 'greenran_fixed_marl_v1',
            'du_states': [
                {'du_id': 'du1', 'primary_slice': 'eMBB', 'state_vector': [0.8, 0.2, 0.1, 0.3, 0.8, 0.1, 0.1, 0.7, 0.6, 0.8]},
                {'du_id': 'du2', 'primary_slice': 'mMTC', 'state_vector': [0.4, 0.6, 0.2, 0.3, 0.2, 0.7, 0.1, 0.5, 0.4, 0.7]},
                {'du_id': 'du3', 'primary_slice': 'URLLC', 'state_vector': [0.3, 0.2, 0.9, 0.4, 0.1, 0.2, 0.7, 0.6, 0.5, 0.6]},
            ],
            'slice_state': {
                'eMBB': {'qos_pressure': 0.8},
                'mMTC': {'qos_pressure': 0.6},
                'URLLC': {'qos_pressure': 0.9},
            },
        }
        resource_snapshot = {'usable_budget': 0.95, 'resource_budget': 1.0, 'r_ran': 0.7, 'r_ai': 0.25}
        out = evaluator.evaluate(marl_state, resource_snapshot=resource_snapshot)
        self.assertTrue(out['enabled'])
        self.assertTrue(out['available'])
        self.assertEqual(out['mode'], 'shadow_only')
        self.assertEqual(len(out['du_recommendations']), 3)
        self.assertIn('shadow_r_ran', out)
        self.assertIn('delta_r_ran_vs_live', out)


if __name__ == '__main__':
    unittest.main()
