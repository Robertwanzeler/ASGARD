import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.evaluate_asgard_paired_campaign import _readiness, build_report, paired_ci95
from scripts.evaluate_tasam_paired_campaign import (
    EXPECTED_STAGES,
    _feedback_requirement_satisfied,
    stage_counts,
)


class TestEvaluateTasamPairedCampaign(unittest.TestCase):
    def test_paired_ci95_is_strict_when_all_deltas_are_negative(self):
        interval = paired_ci95([-5.0, -4.0, -6.0, -3.0, -4.5] * 3)
        self.assertEqual(interval["n"], 15)
        self.assertLess(interval["high"], 0.0)
        self.assertEqual(interval["method"], "paired_student_t_two_sided")

    def test_paired_ci95_does_not_promote_missing_evidence(self):
        interval = paired_ci95([])
        self.assertIsNone(interval["high"])

    def test_asgard_campaign_is_blocked_without_approved_baseline_and_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = build_report(
                root / "campaign",
                baseline_manifest=root / "missing-baseline.json",
                checkpoint=root / "missing-checkpoint",
            )
        self.assertEqual(report["scientific_decision"], "blocked")
        self.assertFalse(report["approved"])
        self.assertIn("baseline_approved", report["rejection_reasons"])

    def test_asgard_readiness_requires_approved_multi_seed_native_pdcp_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline = root / "selected_vehicle_profile.json"
            baseline.write_text(json.dumps({
                "schema": "greenran.autonomous_vehicle_feasibility.v4",
                "profile": "tasam_training_balanced_v6_v2x_gbr_deadline_mc_fallback",
                "status": "passed",
                "scientific_decision": "approved",
                "promotion_eligible": True,
                "metric_contract": "per_pdu_cohort_v1",
                "provenance": {"metric_contract": {
                    "collector_mode": "pdcp_real",
                    "pdcp_source": "native_pdcp_pdu_tx_rx",
                    "proxy_allowed": False,
                }},
                "multi_seed_validation": {
                    "valid": True,
                    "required_seeds": [45, 46, 47],
                    "complete_seeds": [45, 46, 47],
                    "seed47_reused_from_phase1": True,
                    "provenance_compatible": True,
                },
            }), encoding="utf-8")
            checkpoint = root / "checkpoint"
            checkpoint.mkdir()
            (checkpoint / "tasam_marl_checkpoint_meta.json").write_text(json.dumps({
                "final_metrics": {"return": 1.0}, "parent_was_promoted": True,
                "replay_imported": True,
            }), encoding="utf-8")
            (checkpoint / "tasam_marl_actors.pt").write_bytes(b"frozen")
            status, reasons, _, _ = _readiness(
                baseline, checkpoint, profile="tasam_training_balanced_v6_v2x_gbr_deadline_mc_fallback"
            )
        self.assertEqual(status, "ready")
        self.assertEqual(reasons, [])

    def test_stage_counts_fall_back_to_decisions_sqlite(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            connection = sqlite3.connect(run_dir / "rapp_data_lake.db")
            connection.execute(
                "create table decisions_history (id integer primary key, collection_event_stage_name text)"
            )
            connection.executemany(
                "insert into decisions_history(collection_event_stage_name) values (?)",
                [(stage,) for stage in EXPECTED_STAGES],
            )
            connection.commit()
            connection.close()
            counts = stage_counts(run_dir)
        self.assertEqual(set(counts), set(EXPECTED_STAGES))

    def test_feedback_contract_allows_initial_pending_decision(self):
        self.assertTrue(_feedback_requirement_satisfied("train_no_armd", 298, 297))
        self.assertTrue(_feedback_requirement_satisfied("combined", 296, 295))
        self.assertFalse(_feedback_requirement_satisfied("combined", 296, 294))

    def test_rapp_only_does_not_require_tasam_feedback(self):
        self.assertTrue(_feedback_requirement_satisfied("rapp_only", 301, 0))
        self.assertFalse(_feedback_requirement_satisfied("rapp_only", 0, 0))


if __name__ == "__main__":
    unittest.main()
