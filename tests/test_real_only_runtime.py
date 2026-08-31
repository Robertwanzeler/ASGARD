import unittest

from src.real_only_runtime import real_only_status


class TestRealOnlyRuntime(unittest.TestCase):
    def payload(self):
        return {
            'collector_mode': 'pdcp_real',
            'proxy_latency_sample_count': 0,
            'real_latency_sample_count': 12,
            'pdcp_stale': False,
            'rlc_stale': False,
            'mac_stale': False,
            'timestamp': 1_000_000,
        }

    def test_accepts_fresh_real_pdcp(self):
        ok, reason = real_only_status(self.payload(), now_ms=1_005_000, max_age_s=30)
        self.assertTrue(ok)
        self.assertEqual(reason, 'ok')

    def test_rejects_proxy(self):
        metrics = self.payload()
        metrics['proxy_latency_sample_count'] = 1
        self.assertEqual(real_only_status(metrics, now_ms=1_005_000, max_age_s=30), (False, 'proxy_latency_present'))

    def test_rejects_stale_pdcp(self):
        metrics = self.payload()
        metrics['pdcp_stale'] = True
        self.assertEqual(real_only_status(metrics, now_ms=1_005_000, max_age_s=30), (False, 'pdcp_stale_true'))

    def test_rejects_old_payload(self):
        self.assertEqual(real_only_status(self.payload(), now_ms=1_040_001, max_age_s=30), (False, 'metrics_stale_age_40.0s'))


if __name__ == '__main__':
    unittest.main()
