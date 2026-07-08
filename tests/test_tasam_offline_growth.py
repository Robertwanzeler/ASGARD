import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from run_tasam_article_offline_growth import (  # noqa: E402
    aggregate_decision_counts,
    build_parser,
    build_growth_summary,
    combine_decision_counts,
    ensure_local_merge,
    controlled_targets,
    count_trace_lines,
    determine_collection_phase,
    load_reference_summary,
    load_existing_round_payloads,
    main,
    recover_resumable_round_payloads,
    try_repair_round_artifacts,
    remaining_decision_targets,
    target_reached,
    validate_round_artifacts,
)
from collection_event_alternator import PROFILES  # noqa: E402


def _write_valid_round(root: Path, round_index: int, written_transitions: int = 248) -> Path:
    round_dir = root / f"round_{round_index:04d}"
    trace_dir = round_dir / "state" / "ns3_traces"
    export_dir = round_dir / "export"
    trace_dir.mkdir(parents=True, exist_ok=True)
    export_dir.mkdir(parents=True, exist_ok=True)

    for name in (
        "DlPdcpStats.txt",
        "DlRlcStats.txt",
        "UlPdcpStats.txt",
        "UlRlcStats.txt",
        "DlE2PdcpStats.txt",
        "DlE2RlcStats.txt",
        "UlE2PdcpStats.txt",
        "UlE2RlcStats.txt",
    ):
        (trace_dir / name).write_text("ok\n", encoding="utf-8")

    trace_jsonl = export_dir / "tasam_article_trace.jsonl"
    trace_jsonl.write_text(
        "".join('{"idx": %d}\n' % idx for idx in range(written_transitions)),
        encoding="utf-8",
    )
    (export_dir / "tasam_article_export_summary.json").write_text(
        json.dumps(
            {
                "written_transitions": written_transitions,
                "candidate_snapshots": written_transitions + 2,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return round_dir


def _write_incomplete_round(root: Path, round_index: int) -> Path:
    round_dir = root / f"round_{round_index:04d}"
    trace_dir = round_dir / "state" / "ns3_traces"
    export_dir = round_dir / "export"
    trace_dir.mkdir(parents=True, exist_ok=True)
    export_dir.mkdir(parents=True, exist_ok=True)
    (trace_dir / "DlPdcpStats.txt").write_text("partial\n", encoding="utf-8")
    return round_dir


def _write_repairable_round(root: Path, round_index: int) -> Path:
    round_dir = root / f"round_{round_index:04d}"
    trace_dir = round_dir / "state" / "ns3_traces"
    trace_dir.mkdir(parents=True, exist_ok=True)
    (round_dir / "export").mkdir(parents=True, exist_ok=True)
    for name in (
        "DlPdcpStats.txt",
        "DlRlcStats.txt",
        "UlPdcpStats.txt",
        "UlRlcStats.txt",
        "DlE2PdcpStats.txt",
        "DlE2RlcStats.txt",
        "UlE2PdcpStats.txt",
        "UlE2RlcStats.txt",
    ):
        (trace_dir / name).write_text("ok\n", encoding="utf-8")
    (round_dir / "state" / "rapp_data_lake.db").write_text("stub\n", encoding="utf-8")
    return round_dir


class TestTASAMOfflineGrowth(unittest.TestCase):
    def test_growth_parser_defaults_to_wall_clock_control_and_armd_off(self):
        parser = build_parser()
        args = parser.parse_args([])
        self.assertEqual(args.collection_event_time_source, "wall")
        self.assertEqual(args.armd_mode, "off")
        self.assertEqual(args.allowed_control_profile, "drl_allowed_only_v1")

    def test_validate_round_artifacts_accepts_realistic_round(self):
        with tempfile.TemporaryDirectory() as tmp:
            round_dir = _write_valid_round(Path(tmp), 1, written_transitions=248)
            self.assertEqual(validate_round_artifacts(round_dir, min_round_transitions=200), [])

    def test_validate_round_artifacts_flags_missing_trace_and_small_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            round_dir = _write_valid_round(Path(tmp), 1, written_transitions=50)
            (round_dir / "state" / "ns3_traces" / "DlPdcpStats.txt").unlink()
            problems = validate_round_artifacts(round_dir, min_round_transitions=200)
            self.assertTrue(any("missing trace file: DlPdcpStats.txt" in problem for problem in problems))
            self.assertTrue(any("written_transitions below minimum" in problem for problem in problems))

    def test_load_existing_round_payloads_reads_round_order_and_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_valid_round(root, 2, written_transitions=260)
            _write_valid_round(root, 1, written_transitions=248)

            payloads = load_existing_round_payloads(root, min_round_transitions=200)

            self.assertEqual([payload["round_id"] for payload in payloads], ["round_0001", "round_0002"])
            self.assertEqual(payloads[0]["written_transitions"], 248)
            self.assertEqual(payloads[1]["written_transitions"], 260)
            self.assertEqual(count_trace_lines(root / "round_0002" / "export" / "tasam_article_trace.jsonl"), 260)

    def test_controlled_phase_switches_from_blocked_to_borderline(self):
        class Args:
            target_blocked = 1000
            target_allowed = 1000
            target_conditional = 1000
            blocked_control_profile = "drl_balanced_blocked_v1"
            borderline_control_profile = "drl_balanced_borderline_v1"
            allowed_control_profile = "drl_allowed_only_v1"

        decision_counts = {"BLOCKED": 1000, "ALLOWED": 120, "CONDITIONAL": 80}
        self.assertEqual(controlled_targets(Args()), {"BLOCKED": 1000, "ALLOWED": 1000, "CONDITIONAL": 1000})
        self.assertEqual(
            determine_collection_phase(Args(), decision_counts),
            ("borderline_phase", "drl_balanced_borderline_v1"),
        )
        self.assertEqual(
            remaining_decision_targets(decision_counts, controlled_targets(Args())),
            {"BLOCKED": 0, "ALLOWED": 880, "CONDITIONAL": 920},
        )
        self.assertFalse(target_reached(Args(), current_total=1200, decision_counts=decision_counts))

    def test_controlled_phase_switches_to_allowed_only_after_blocked_and_conditional_are_satisfied(self):
        class Args:
            target_blocked = 1000
            target_allowed = 1000
            target_conditional = 1000
            blocked_control_profile = "drl_balanced_blocked_v1"
            borderline_control_profile = "drl_balanced_borderline_v1"
            allowed_control_profile = "drl_allowed_only_v1"

        decision_counts = {"BLOCKED": 1240, "ALLOWED": 120, "CONDITIONAL": 1040}
        self.assertEqual(
            determine_collection_phase(Args(), decision_counts),
            ("allowed_phase", "drl_allowed_only_v1"),
        )
        self.assertEqual(
            remaining_decision_targets(decision_counts, controlled_targets(Args())),
            {"BLOCKED": 0, "ALLOWED": 880, "CONDITIONAL": 0},
        )

    def test_reference_root_counts_are_used_when_combining_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            reference_root = Path(tmp) / "reference"
            reference_root.mkdir(parents=True, exist_ok=True)
            (reference_root / "offline_collection_summary.json").write_text(
                json.dumps(
                    {
                        "written_transitions_total": 3000,
                        "decision_counts_total": {"BLOCKED": 1500, "ALLOWED": 7, "CONDITIONAL": 1100},
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            reference = load_reference_summary(reference_root)
            combined = combine_decision_counts(
                {"BLOCKED": 0, "ALLOWED": 220, "CONDITIONAL": 0},
                reference["decision_counts_total"],
            )

            self.assertEqual(reference["written_transitions_total"], 3000)
            self.assertEqual(combined["BLOCKED"], 1500)
            self.assertEqual(combined["ALLOWED"], 227)
            self.assertEqual(combined["CONDITIONAL"], 1100)

    def test_allowed_only_profile_keeps_app2_fully_connected_for_real_25_sensor_snapshot(self):
        profile = PROFILES["drl_allowed_only_v1"]
        for stage in profile:
            app2 = stage.app2
            self.assertEqual(app2["connected_sensors"], 25)
            self.assertEqual(app2["error_sensors"], 0)
            self.assertEqual(app2["low_battery_sensors"], 0)

    def test_build_growth_summary_reports_allowed_phase_before_first_round(self):
        class Args:
            target_transitions = 10000
            target_blocked = 1000
            target_allowed = 1000
            target_conditional = 1000
            rounds_per_block = 3
            blocked_control_profile = "drl_balanced_blocked_v1"
            borderline_control_profile = "drl_balanced_borderline_v1"
            allowed_control_profile = "drl_allowed_only_v1"

        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp) / "allowed_backfill"
            summary = build_growth_summary(
                Args(),
                output_root,
                blocks_run=0,
                current_total=0,
                effective_total=13898,
                local_decision_counts={"BLOCKED": 0, "ALLOWED": 0, "CONDITIONAL": 0},
                decision_counts={"BLOCKED": 8303, "ALLOWED": 7, "CONDITIONAL": 5588},
                reference_summary={"reference_root": "/tmp/reference"},
                reference_transitions=13898,
                reference_decision_counts={"BLOCKED": 8303, "ALLOWED": 7, "CONDITIONAL": 5588},
                recovered_rounds=[],
                round_payloads=[],
                merged_trace_jsonl=str(output_root / "tasam_article_trace.jsonl"),
            )

            self.assertEqual(summary["collection_phase"], "allowed_phase")
            self.assertEqual(summary["active_collection_event_profile"], "drl_allowed_only_v1")
            self.assertEqual(summary["decision_targets_remaining"]["ALLOWED"], 993)

    def test_aggregate_decision_counts_sums_all_round_summaries(self):
        counts = aggregate_decision_counts(
            [
                {"decision_counts": {"BLOCKED": 10, "ALLOWED": 3}},
                {"decision_counts": {"CONDITIONAL": 4, "ALLOWED": 2}},
            ]
        )
        self.assertEqual(counts["BLOCKED"], 10)
        self.assertEqual(counts["ALLOWED"], 5)
        self.assertEqual(counts["CONDITIONAL"], 4)

    def test_ensure_local_merge_rebuilds_consolidated_trace_from_existing_rounds(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_valid_round(root, 1, written_transitions=248)
            _write_valid_round(root, 2, written_transitions=216)

            payloads = load_existing_round_payloads(root, min_round_transitions=200)
            merged = ensure_local_merge(root, payloads)

            self.assertEqual(count_trace_lines(Path(merged["merged_trace_jsonl"])), 464)
            summary = json.loads((root / "offline_collection_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["written_transitions_total"], 464)

    def test_main_repairs_existing_round_and_writes_local_merge_when_target_is_already_reached(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "run"
            reference_root = Path(tmp) / "reference"
            root.mkdir(parents=True, exist_ok=True)
            reference_root.mkdir(parents=True, exist_ok=True)
            _write_repairable_round(root, 1)
            (reference_root / "offline_collection_summary.json").write_text(
                json.dumps(
                    {
                        "written_transitions_total": 13898,
                        "decision_counts_total": {"BLOCKED": 8303, "ALLOWED": 7, "CONDITIONAL": 5588},
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            def fake_export(paths, repair_args, dry_run):
                self.assertFalse(dry_run)
                trace_jsonl = Path(paths["trace_jsonl"])
                summary_json = Path(paths["summary_json"])
                trace_jsonl.write_text("".join('{"idx": %d}\n' % idx for idx in range(248)), encoding="utf-8")
                summary_json.write_text(
                    json.dumps(
                        {
                            "written_transitions": 248,
                            "candidate_snapshots": 250,
                            "decision_counts": {"ALLOWED": 248, "BLOCKED": 0, "CONDITIONAL": 0},
                        },
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                return {
                    "written_transitions": 248,
                    "candidate_snapshots": 250,
                    "decision_counts": {"ALLOWED": 248, "BLOCKED": 0, "CONDITIONAL": 0},
                }

            argv = [
                "run_tasam_article_offline_growth.py",
                "--output-root",
                str(root),
                "--reference-root",
                str(reference_root),
                "--target-blocked",
                "1000",
                "--target-allowed",
                "100",
                "--target-conditional",
                "1000",
                "--ns3-bin",
                str(ROOT / "README.md"),
            ]
            with mock.patch.object(sys, "argv", argv):
                with mock.patch("run_tasam_article_offline_growth.resolve_ns3_bin", return_value=Path("/tmp/ns3")):
                    with mock.patch("run_tasam_article_offline_growth.load_base_config", return_value={}):
                        with mock.patch("run_tasam_article_offline_growth.export_round", side_effect=fake_export):
                            rc = main()

            self.assertEqual(rc, 0)
            self.assertTrue((root / "offline_collection_summary.json").exists())
            self.assertEqual(count_trace_lines(root / "tasam_article_trace.jsonl"), 248)
            growth_summary = json.loads((root / "offline_growth_summary.json").read_text(encoding="utf-8"))
            self.assertTrue(growth_summary["target_reached"])
            self.assertEqual(growth_summary["local_decision_counts_current"]["ALLOWED"], 248)

    def test_recover_resumable_round_payloads_archives_trailing_incomplete_round(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_valid_round(root, 1, written_transitions=248)
            _write_incomplete_round(root, 2)

            payloads, recovered = recover_resumable_round_payloads(root, min_round_transitions=200)

            self.assertEqual([payload["round_id"] for payload in payloads], ["round_0001"])
            self.assertEqual(len(recovered), 1)
            self.assertEqual(recovered[0]["round_id"], "round_0002")
            self.assertFalse((root / "round_0002").exists())
            self.assertTrue(Path(recovered[0]["archived_path"]).exists())
            self.assertTrue((Path(recovered[0]["archived_path"]) / "recovery_manifest.json").exists())

    def test_try_repair_round_artifacts_rebuilds_missing_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            round_dir = _write_repairable_round(Path(tmp), 1)

            with mock.patch(
                "run_tasam_article_offline_growth.export_round",
                side_effect=lambda paths, args, dry_run: (
                    Path(paths["trace_jsonl"]).write_text(
                        "".join('{"idx": %d}\n' % idx for idx in range(220)),
                        encoding="utf-8",
                    ),
                    Path(paths["summary_json"]).write_text(
                        json.dumps({"written_transitions": 220, "candidate_snapshots": 223}) + "\n",
                        encoding="utf-8",
                    ),
                ),
            ):
                repaired, problems = try_repair_round_artifacts(round_dir, min_round_transitions=200)

            self.assertTrue(repaired)
            self.assertEqual(problems, [])
            self.assertTrue((round_dir / "export" / "tasam_article_trace.jsonl").exists())
            self.assertTrue((round_dir / "export" / "tasam_article_export_summary.json").exists())

    def test_recover_resumable_round_payloads_repairs_exportable_round_before_archiving(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_valid_round(root, 1, written_transitions=248)
            round_dir = _write_repairable_round(root, 2)

            def fake_export(paths, args, dry_run):
                Path(paths["trace_jsonl"]).write_text(
                    "".join('{"idx": %d}\n' % idx for idx in range(230)),
                    encoding="utf-8",
                )
                Path(paths["summary_json"]).write_text(
                    json.dumps({"written_transitions": 230, "candidate_snapshots": 231}) + "\n",
                    encoding="utf-8",
                )
                return {"written_transitions": 230, "candidate_snapshots": 231}

            with mock.patch("run_tasam_article_offline_growth.export_round", side_effect=fake_export):
                payloads, recovered = recover_resumable_round_payloads(root, min_round_transitions=200)

            self.assertEqual([payload["round_id"] for payload in payloads], ["round_0001", "round_0002"])
            self.assertEqual(payloads[1]["written_transitions"], 230)
            self.assertEqual(recovered, [])
            self.assertTrue((round_dir / "export" / "tasam_article_trace.jsonl").exists())

    def test_recover_resumable_round_payloads_archives_everything_after_first_invalid_round(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_valid_round(root, 1, written_transitions=248)
            _write_incomplete_round(root, 2)
            _write_valid_round(root, 3, written_transitions=260)

            payloads, recovered = recover_resumable_round_payloads(root, min_round_transitions=200)

            self.assertEqual([payload["round_id"] for payload in payloads], ["round_0001"])
            self.assertEqual([item["round_id"] for item in recovered], ["round_0002", "round_0003"])
            self.assertFalse((root / "round_0002").exists())
            self.assertFalse((root / "round_0003").exists())


if __name__ == "__main__":
    unittest.main()
