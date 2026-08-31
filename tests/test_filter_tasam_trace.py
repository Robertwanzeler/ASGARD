import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from filter_tasam_trace import (  # noqa: E402
    POSTFIX_CLEAN_SINCE_TS,
    POSTFIX_CLEAN_EXPECTED_DECISION,
    main,
    profile_rules,
    should_keep,
)


def _record(
    *,
    timestamp: int,
    stage: str,
    decision: str,
    metrics: dict | None = None,
    next_metrics: dict | None = None,
    valid_for_training: bool = True,
) -> dict:
    return {
        "timestamp": timestamp,
        "scenario_stage": stage,
        "decision": {"decision": decision},
        "metrics": metrics if metrics is not None else {"latency_p95_us": 24000.0, "cvar_per_ue_us": 32000.0},
        "next_metrics": next_metrics if next_metrics is not None else {"latency_p95_us": 24000.0, "cvar_per_ue_us": 32000.0},
        "next_global_state": {"state_vector": [0.1]},
        "next_slice_state": {"URLLC": {}},
        "next_du_states": [{"du_id": "du0"}],
        "collection_quality": {
            "valid_for_training": valid_for_training,
            "collector_mode": "pdcp_real",
            "pdcp_real": True,
            "proxy_latency_sample_count": 0.0,
        },
    }


class TestFilterTasamTrace(unittest.TestCase):
    def test_judge_credit_profile_requires_observed_feedback(self):
        rules = profile_rules("judge_credit_trainable")
        args = mock.Mock(max_p95_ms=None, max_cvar_ms=None)
        record = _record(
            timestamp=POSTFIX_CLEAN_SINCE_TS,
            stage="vehicle_blocked",
            decision="BLOCKED",
        )
        keep, reason = should_keep(record, rules, args)
        self.assertFalse(keep)
        self.assertEqual(reason, "missing_judge_feedback")

        record["judge_feedback_observed"] = True
        record["tasam_credit"] = 1.0
        keep, reason = should_keep(record, rules, args)
        self.assertTrue(keep)
        self.assertEqual(reason, "kept")

    def test_judge_credit_profile_accepts_final_real_transition_without_next_metrics(self):
        rules = profile_rules("judge_credit_trainable")
        args = mock.Mock(max_p95_ms=None, max_cvar_ms=None)
        record = _record(
            timestamp=POSTFIX_CLEAN_SINCE_TS,
            stage="vehicle_blocked",
            decision="BLOCKED",
            next_metrics={},
        )
        record.update({
            "next_global_state": {"state_vector": [0.1]},
            "next_slice_state": {"URLLC": {}},
            "next_du_states": [{"du_id": "du0"}],
            "judge_feedback_observed": True,
        })
        keep, reason = should_keep(record, rules, args)
        self.assertTrue(keep)
        self.assertEqual(reason, "kept")

    def test_rapp_online_trainable_keeps_controlled_runtime_samples(self):
        rules = profile_rules("rapp_online_trainable")
        self.assertTrue(rules["require_pdcp_real"])
        self.assertTrue(rules["require_proxy_free"])
        self.assertEqual(rules["expected_stage_decisions"], POSTFIX_CLEAN_EXPECTED_DECISION)

        args = mock.Mock(max_p95_ms=None, max_cvar_ms=None)
        keep, reason = should_keep(
            {
                **_record(
                    timestamp=POSTFIX_CLEAN_SINCE_TS,
                    stage="camera_conditional",
                    decision="CONDITIONAL",
                ),
                "collection_quality": {
                    "valid_for_training": True,
                    "collector_mode": "pdcp_stale",
                    "pdcp_real": False,
                    "proxy_latency_sample_count": 71.0,
                },
            },
            rules,
            args,
        )
        self.assertFalse(keep)
        self.assertEqual(reason, "proxy_latency")

        keep, reason = should_keep(
            _record(
                timestamp=POSTFIX_CLEAN_SINCE_TS,
                stage="camera_conditional",
                decision="CONDITIONAL",
            ),
            rules,
            args,
        )
        self.assertTrue(keep)
        self.assertEqual(reason, "kept")

    def test_postfix_clean_rules_define_expected_stage_decisions(self):
        rules = profile_rules("postfix_clean")
        self.assertEqual(rules["since_ts"], POSTFIX_CLEAN_SINCE_TS)
        self.assertEqual(rules["expected_stage_decisions"], POSTFIX_CLEAN_EXPECTED_DECISION)
        self.assertTrue(rules["require_metrics"])
        self.assertTrue(rules["require_next_metrics"])

    def test_should_keep_rejects_postfix_clean_failure_modes(self):
        rules = profile_rules("postfix_clean")
        args = mock.Mock(max_p95_ms=None, max_cvar_ms=None)

        keep, reason = should_keep(
            _record(
                timestamp=POSTFIX_CLEAN_SINCE_TS - 5,
                stage="allowed_stable",
                decision="ALLOWED",
            ),
            rules,
            args,
        )
        self.assertFalse(keep)
        self.assertEqual(reason, "pre_fix")

        keep, reason = should_keep(
            _record(
                timestamp=POSTFIX_CLEAN_SINCE_TS,
                stage="allowed_stable",
                decision="ALLOWED",
                metrics={},
            ),
            rules,
            args,
        )
        self.assertFalse(keep)
        self.assertEqual(reason, "missing_metrics")

        keep, reason = should_keep(
            _record(
                timestamp=POSTFIX_CLEAN_SINCE_TS,
                stage="allowed_stable",
                decision="ALLOWED",
                next_metrics={},
            ),
            rules,
            args,
        )
        self.assertFalse(keep)
        self.assertEqual(reason, "missing_next_metrics")

        keep, reason = should_keep(
            _record(
                timestamp=POSTFIX_CLEAN_SINCE_TS,
                stage="conflict_context",
                decision="BLOCKED",
            ),
            rules,
            args,
        )
        self.assertFalse(keep)
        self.assertEqual(reason, "unexpected_stage")

        keep, reason = should_keep(
            _record(
                timestamp=POSTFIX_CLEAN_SINCE_TS,
                stage="camera_blocked",
                decision="CONDITIONAL",
            ),
            rules,
            args,
        )
        self.assertFalse(keep)
        self.assertEqual(reason, "unexpected_decision_for_stage")

    def test_main_writes_postfix_clean_summary_with_purity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "input.jsonl"
            output_path = root / "output.jsonl"
            summary_path = root / "summary.json"
            records = [
                _record(timestamp=POSTFIX_CLEAN_SINCE_TS - 5, stage="allowed_stable", decision="ALLOWED"),
                _record(timestamp=POSTFIX_CLEAN_SINCE_TS, stage="allowed_stable", decision="ALLOWED"),
                _record(timestamp=POSTFIX_CLEAN_SINCE_TS + 5, stage="camera_blocked", decision="BLOCKED"),
                _record(timestamp=POSTFIX_CLEAN_SINCE_TS + 10, stage="camera_blocked", decision="CONDITIONAL"),
                _record(timestamp=POSTFIX_CLEAN_SINCE_TS + 15, stage="vehicle_conditional", decision="CONDITIONAL", metrics={}),
                _record(timestamp=POSTFIX_CLEAN_SINCE_TS + 20, stage="unexpected", decision="ALLOWED"),
            ]
            input_path.write_text(
                "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
                encoding="utf-8",
            )

            argv = [
                "filter_tasam_trace.py",
                "--input-jsonl",
                str(input_path),
                "--output-jsonl",
                str(output_path),
                "--summary-json",
                str(summary_path),
                "--profile",
                "postfix_clean",
            ]
            with mock.patch.object(sys, "argv", argv):
                rc = main()

            self.assertEqual(rc, 0)
            kept_lines = output_path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(kept_lines), 2)

            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["rows_after"], 2)
            self.assertEqual(summary["drop_reasons"]["pre_fix"], 1)
            self.assertEqual(summary["drop_reasons"]["unexpected_decision_for_stage"], 1)
            self.assertEqual(summary["drop_reasons"]["missing_metrics"], 1)
            self.assertEqual(summary["drop_reasons"]["unexpected_stage"], 1)
            self.assertEqual(summary["stage_purity_after"]["allowed_stable"]["purity"], 1.0)
            self.assertEqual(summary["stage_purity_after"]["camera_blocked"]["purity"], 1.0)


if __name__ == "__main__":
    unittest.main()
