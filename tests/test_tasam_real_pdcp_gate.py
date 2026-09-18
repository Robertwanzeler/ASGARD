import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rapp_orchestrator import _is_real_pdcp_snapshot


def test_real_pdcp_snapshot_gate_accepts_fresh_proxy_free_metrics():
    assert _is_real_pdcp_snapshot({
        "collector_mode": "pdcp_real",
        "proxy_latency_sample_count": 0,
        "pdcp_stale": False,
    }) is True


def test_real_pdcp_snapshot_gate_rejects_warmup_proxy_and_stale_metrics():
    base = {
        "collector_mode": "pdcp_real",
        "proxy_latency_sample_count": 0,
        "pdcp_stale": False,
    }
    assert _is_real_pdcp_snapshot({}) is False
    assert _is_real_pdcp_snapshot({**base, "proxy_latency_sample_count": 1}) is False
    assert _is_real_pdcp_snapshot({**base, "pdcp_stale": True}) is False
    assert _is_real_pdcp_snapshot({**base, "collector_mode": "cuup_proxy"}) is False
