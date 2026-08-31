import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from monitor_tasam_targeted_collection import (  # noqa: E402
    DEFAULT_TARGETS,
    parse_target_specs,
    record_is_valid_target,
    select_balanced_records,
)


def _record(stage: str, decision: str, *, valid: bool = True, proxy: float = 0.0, mode: str = "pdcp_real") -> dict:
    return {
        "timestamp": 100,
        "scenario_stage": stage,
        "decision": {
            "decision": decision,
            "collection_event_stage_authoritative": True,
            "armd_proposal_present": True,
            "armd_proposal_valid": True,
            "tasam_proposal_present": True,
            "tasam_proposal_valid": True,
        },
        "metrics": {"latency_p95_us": 1.0},
        "next_metrics": {"latency_p95_us": 2.0},
        "judge_feedback_observed": True,
        "collection_quality": {
            "valid_for_training": valid,
            "collector_mode": mode,
            "pdcp_real": mode == "pdcp_real",
            "proxy_latency_sample_count": proxy,
            "has_proxy": proxy > 0,
        },
    }


class TestTargetedCollection(unittest.TestCase):
    def test_default_targets_are_all_nine_stages(self):
        self.assertEqual(len(parse_target_specs(None)), 9)
        self.assertEqual(set(parse_target_specs(None)), set(DEFAULT_TARGETS))
        specs = [f"{stage}=500" for stage in DEFAULT_TARGETS]
        self.assertEqual(parse_target_specs(specs), DEFAULT_TARGETS)

    def test_only_real_valid_expected_stage_is_kept(self):
        keep, reason = record_is_valid_target(_record("app2_conditional", "CONDITIONAL"))
        self.assertTrue(keep)
        self.assertEqual(reason, "kept")
        # A wrong final verdict is retained as an RL sample for judge credit.
        keep, reason = record_is_valid_target(_record("app2_conditional", "ALLOWED"))
        self.assertTrue(keep)
        self.assertEqual(reason, "kept")
        for record in (
            _record("app2_conditional", "CONDITIONAL", valid=False),
            _record("vehicle_blocked", "BLOCKED", proxy=1.0),
            _record("vehicle_blocked", "BLOCKED", mode="synthetic"),
        ):
            keep, _ = record_is_valid_target(record)
            self.assertFalse(keep)

    def test_stage_must_be_authoritative(self):
        record = _record("camera_blocked", "BLOCKED")
        record["decision"]["collection_event_stage_authoritative"] = False
        keep, reason = record_is_valid_target(record)
        self.assertFalse(keep)
        self.assertEqual(reason, "missing_authoritative_stage")

    def test_selection_caps_each_stage_at_target(self):
        targets = {stage: 2 for stage in DEFAULT_TARGETS}
        records = []
        for stage, decision in (
            ("allowed_bootstrap", "ALLOWED"),
            ("allowed_stable", "ALLOWED"),
            ("camera_conditional", "CONDITIONAL"),
            ("camera_blocked", "BLOCKED"),
            ("app2_conditional", "CONDITIONAL"),
            ("vehicle_conditional", "CONDITIONAL"),
            ("app2_blocked", "BLOCKED"),
            ("vehicle_blocked", "BLOCKED"),
            ("allowed_recovery", "ALLOWED"),
        ):
            records.extend(_record(stage, decision) for _ in range(4))
        selected, counts, rejected = select_balanced_records(records, targets)
        self.assertEqual(len(selected), 18)
        self.assertEqual(dict(counts), targets)
        self.assertEqual(sum(rejected.values()), 0)


if __name__ == "__main__":
    unittest.main()
