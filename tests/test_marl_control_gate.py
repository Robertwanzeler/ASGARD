import unittest

from src.rapp_marl_control_gate import compute_control_gate


class TestMARLControlGate(unittest.TestCase):
    def test_gate_stays_shadow_only_without_runtime_outperformance(self):
        tasam_eval = {
            'best_run': {
                'readiness': 'control_candidate',
                'promote_shadow': True,
                'promote_control_candidate': True,
                'run_dir': '/tmp/tasam_marl_20260527_v5_calibrated',
            }
        }
        runtime_eval = {
            'readiness': 'not_beating_live',
            'summary': {
                'latest_policy_id': 'ta_sam_marl_shadow_v1:tasam_marl_20260527_v5_calibrated',
            },
        }
        gate = compute_control_gate(tasam_eval, runtime_eval, {})
        self.assertEqual(gate['status'], 'shadow_only')
        self.assertTrue(gate['allow_shadow'])
        self.assertFalse(gate['allow_control_trial'])

    def test_gate_requires_manual_approval_before_trial(self):
        tasam_eval = {
            'best_run': {
                'readiness': 'control_candidate',
                'promote_shadow': True,
                'promote_control_candidate': True,
                'run_dir': '/tmp/tasam_marl_20260527_v5_calibrated',
            }
        }
        runtime_eval = {
            'readiness': 'control_trial_candidate',
            'summary': {
                'latest_policy_id': 'ta_sam_marl_shadow_v1:tasam_marl_20260527_v5_calibrated',
            },
        }
        gate = compute_control_gate(tasam_eval, runtime_eval, {})
        self.assertEqual(gate['status'], 'trial_candidate')
        self.assertFalse(gate['allow_control_trial'])
        self.assertFalse(gate['manual_approval_valid'])

    def test_gate_allows_trial_when_manual_approval_matches(self):
        tasam_eval = {
            'best_run': {
                'readiness': 'control_candidate',
                'promote_shadow': True,
                'promote_control_candidate': True,
                'run_dir': '/tmp/tasam_marl_20260527_v5_calibrated',
            }
        }
        runtime_eval = {
            'readiness': 'control_trial_candidate',
            'summary': {
                'latest_policy_id': 'ta_sam_marl_shadow_v1:tasam_marl_20260527_v5_calibrated',
            },
        }
        manual_approval = {
            'approved': True,
            'approved_policy_id': 'ta_sam_marl_shadow_v1:tasam_marl_20260527_v5_calibrated',
            'approved_run_dir': '/tmp/tasam_marl_20260527_v5_calibrated',
        }
        gate = compute_control_gate(tasam_eval, runtime_eval, manual_approval)
        self.assertEqual(gate['status'], 'trial_approved')
        self.assertTrue(gate['allow_control_trial'])
        self.assertTrue(gate['manual_approval_valid'])


if __name__ == '__main__':
    unittest.main()
