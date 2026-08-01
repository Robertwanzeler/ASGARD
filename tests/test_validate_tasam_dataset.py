import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from validate_tasam_dataset import validate_trace  # noqa: E402


def _record(timestamp: int, *, latency: float, topology: str = "greenran_fixed_marl_v1") -> dict:
    dus = [
        {
            "du_id": f"du_{idx}",
            "state_vector": [0.1] * 10,
            "slice_mix": {"eMBB": 0.5, "mMTC": 0.3, "URLLC": 0.2},
        }
        for idx in range(3)
    ]
    return {
        "schema": "greenran.tasam_article_transition.v1",
        "timestamp": timestamp,
        "topology_id": topology,
        "global_state": {"topology_id": topology, "state_vector": [0.2] * 10},
        "du_states": dus,
        "next_global_state": {"topology_id": topology, "state_vector": [0.3] * 10},
        "next_du_states": dus,
        "action": {"r_ran": 0.5, "r_ai": 0.4},
        "reward_hint": 0.4,
        "decision": {"decision": "ALLOWED"},
        "metrics": {"latency_p95_us": latency, "cvar_per_ue_us": latency + 1},
        "collection_quality": {
            "collector_mode": "pdcp_real",
            "pdcp_real": True,
            "has_proxy": False,
            "proxy_latency_sample_count": 0,
            "valid_for_training": True,
            "sim_reset": False,
        },
    }


def _args(**overrides):
    values = {
        "expected_topology_id": "greenran_fixed_marl_v1",
        "expected_du_count": 3,
        "expected_du_state_dim": 10,
        "expected_action_dim": 3,
        "min_transitions": 2,
        "min_distinct_latency_values": 2,
        "min_class_ratio": 0.0,
        "allow_invalid": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class ValidateTasamDatasetTests(unittest.TestCase):
    def _write(self, records):
        tmp = tempfile.TemporaryDirectory()
        path = Path(tmp.name) / "trace.jsonl"
        path.write_text("".join(json.dumps(row) + "\n" for row in records), encoding="utf-8")
        return tmp, path

    def test_accepts_real_three_du_trace_with_variable_latency(self):
        tmp, path = self._write([_record(1, latency=100), _record(2, latency=120)])
        try:
            report = validate_trace(path, _args())
            self.assertTrue(report["training_ready"], report["errors"])
            self.assertEqual(report["du_counts"], {"3": 2})
        finally:
            tmp.cleanup()

    def test_blocks_constant_latency_signal(self):
        tmp, path = self._write([_record(1, latency=100), _record(2, latency=100)])
        try:
            report = validate_trace(path, _args())
            self.assertFalse(report["training_ready"])
            self.assertTrue(any("distinct values" in item for item in report["errors"]))
        finally:
            tmp.cleanup()

    def test_blocks_six_du_trace_for_greenran_policy(self):
        record = _record(1, latency=100)
        record["timestamp"] = 2
        record["topology_id"] = "article_ns3_marl_v1"
        record["global_state"]["topology_id"] = "article_ns3_marl_v1"
        record["du_states"] = record["du_states"] + [{"state_vector": [0.1] * 10} for _ in range(3)]
        record["next_du_states"] = record["du_states"]
        tmp, path = self._write([record])
        try:
            report = validate_trace(path, _args(min_transitions=1))
            self.assertFalse(report["training_ready"])
            self.assertTrue(any("topology_id" in item or "du_count" in item for item in report["errors"]))
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
