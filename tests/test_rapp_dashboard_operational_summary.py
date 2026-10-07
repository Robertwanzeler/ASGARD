import unittest
from unittest.mock import patch

from rapp_dashboard import _build_operational_summary, get_camera_protection


def metric(*, p95=20.0, cvar=30.0, throughput=40.0, active_cameras=3,
           real=10, proxy=0, stale=False):
    return {
        'timestamp': 1,
        'p95_ms': p95,
        'cvar_ms': cvar,
        'throughput_kbps': throughput * 1000.0,
        'active_cameras': active_cameras,
        'real_latency_sample_count': real,
        'proxy_latency_sample_count': proxy,
        'pdcp_stale': stale,
    }


def summary(status='running', latest_metric=None, decision=None, guards=None):
    latest_metric = latest_metric or {}
    return _build_operational_summary(
        status,
        latest_metric,
        {
            'real_samples': latest_metric.get('real_latency_sample_count', 0),
            'proxy_samples': latest_metric.get('proxy_latency_sample_count', 0),
            'pdcp_real': latest_metric.get('real_latency_sample_count', 0) > 0,
        },
        decision or {},
        guards or {'floor_feasible': True},
        {'guard': {}},
    )


class DashboardOperationalSummaryTests(unittest.TestCase):
    def test_healthy_real_run_is_ok(self):
        result = summary(latest_metric=metric())
        self.assertEqual(result['level'], 'ok')
        self.assertEqual(result['critical_count'], 0)
        self.assertEqual(result['warning_count'], 0)

    def test_historical_run_is_explicitly_informational(self):
        result = summary('historical', metric())
        self.assertEqual(result['level'], 'info')
        self.assertIn('historical_run', [item['code'] for item in result['items']])

    def test_stale_run_is_critical_even_with_last_metric(self):
        result = summary('stale', metric())
        self.assertEqual(result['level'], 'critical')
        self.assertIn('telemetry_unavailable', [item['code'] for item in result['items']])

    def test_missing_data_is_critical(self):
        result = summary(latest_metric={})
        self.assertEqual(result['level'], 'critical')
        self.assertIn('telemetry_missing', [item['code'] for item in result['items']])

    def test_proxy_and_blocked_decision_are_warnings(self):
        result = summary(
            latest_metric=metric(real=0, proxy=4),
            decision={'decision': 'BLOCKED', 'reason': 'guard'},
        )
        codes = [item['code'] for item in result['items']]
        self.assertEqual(result['level'], 'warning')
        self.assertIn('pdcp_proxy', codes)
        self.assertIn('decision_blocked', codes)

    def test_quality_and_throughput_breaches_are_critical(self):
        result = summary(latest_metric=metric(p95=130, cvar=260, throughput=20))
        codes = [item['code'] for item in result['items']]
        self.assertEqual(result['level'], 'critical')
        self.assertIn('p95_critical', codes)
        self.assertIn('cvar_critical', codes)
        self.assertIn('throughput_sla', codes)

    def test_stale_pdcp_and_floor_violation_are_critical(self):
        result = summary(
            latest_metric=metric(stale=True),
            guards={'floor_feasible': False, 'per_ue_floor_violations': 2},
        )
        codes = [item['code'] for item in result['items']]
        self.assertEqual(result['level'], 'critical')
        self.assertIn('pdcp_stale', codes)
        self.assertIn('floor_violation', codes)

    def test_historical_aggregate_does_not_claim_live_camera_sla(self):
        with patch('rapp_dashboard.get_current_metrics', return_value={
            'data_mode': 'historical_db',
            'active_cameras': 3,
            'global_metrics': {
                'total_active_cameras': 3,
                'latency_p95_us': 22641.7,
                'throughput_kbps': 180017.6,
            },
        }):
            result = get_camera_protection()
        self.assertEqual(result['status'], 'HISTÓRICO')
        self.assertEqual(result['active_cameras'], 3)
        self.assertIsNone(result['sla_compliant'])

    def test_missing_camera_metrics_are_not_marked_compliant(self):
        with patch('rapp_dashboard.get_current_metrics', return_value=None):
            result = get_camera_protection()
        self.assertEqual(result['status'], 'SEM DADOS')
        self.assertIsNone(result['sla_compliant'])


if __name__ == '__main__':
    unittest.main()
