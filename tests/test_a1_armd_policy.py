import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rapp_a1_interface import A1PolicyInterface


class A1ARMDPolicyTests(unittest.TestCase):
    def test_energy_policy_includes_armd_block(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            a1 = A1PolicyInterface(policy_dir=tmpdir)
            policy = a1.send_energy_policy(
                {
                    'energy_saver': 'BLOCKED',
                    'action': 'FULL_POWER',
                    'reason': 'ARMD(app1_throughput): throughput por câmera 24.0Mbps < 25Mbps',
                    'confidence': 1.0,
                    'armd_enabled': True,
                    'armd_mode': 'assist',
                    'armd_scenario': 'app1_throughput',
                    'armd_domain': 'app1',
                    'armd_source': 'protocol',
                    'armd_confidence': 1.0,
                    'armd_override_applied': False,
                    'armd_expected_energy_saver': 'BLOCKED',
                    'armd_expected_action': 'FULL_POWER',
                    'armd_reason': 'throughput por câmera 24.0Mbps < 25Mbps',
                    'armd_evidence': ['tp=24.0Mbps', 'cams=3'],
                }
            )
            self.assertIn('armd', policy)
            self.assertEqual(policy['armd']['scenario'], 'app1_throughput')
            self.assertTrue(policy['recommendations']['armd_verified'])
            self.assertFalse(policy['recommendations']['armd_protection_escalation'])

    def test_slice_policy_includes_armd_block(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            a1 = A1PolicyInterface(policy_dir=tmpdir)
            policy = a1.send_slice_policy(
                'NORMAL',
                cameras_demand={'active': 3},
                armd_info={
                    'enabled': True,
                    'mode': 'assist',
                    'scenario': 'vehicle_implicito',
                    'domain': 'app3',
                    'source': 'protocol',
                    'confidence': 1.0,
                    'override_applied': False,
                    'reason': 'KPI global saudável com degradação localizada no App3',
                },
            )
            self.assertIn('armd', policy)
            self.assertEqual(policy['armd']['scenario'], 'vehicle_implicito')
            self.assertEqual(policy['recommendations']['armd_attention_domain'], 'app3')


if __name__ == "__main__":
    unittest.main()
