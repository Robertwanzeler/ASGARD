import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import run_tasam_article_offline_collection as offline_collection  # noqa: E402
from run_tasam_article_offline_collection import (  # noqa: E402
    build_parser,
    apply_runtime_overrides,
    build_round_config,
    collect_round,
    merge_round_traces,
    progress_signature_advanced,
)


class TestTASAMOfflineCollection(unittest.TestCase):
    def test_parser_defaults_enable_stall_watchdog(self):
        parser = build_parser()
        args = parser.parse_args([])
        self.assertEqual(args.stall_timeout_seconds, 120.0)

    def test_apply_runtime_overrides_disables_armd_by_default(self):
        class Args:
            armd_mode = "off"
            pdcp_stale_seconds = 3600.0

        env = apply_runtime_overrides({"BASE": "1"}, Args())
        self.assertEqual(env["GREENRAN_ARMD_MODE"], "off")
        self.assertEqual(env["GREENRAN_PDCP_STALE_SECONDS"], "3600.0")

    def test_build_round_config_aligns_counts_and_ranges(self):
        base = {
            "scenario_id": "base",
            "ns3": {},
            "device_roles": {},
            "apps": {"app1": {}, "app3": {}},
            "marl": {"logical_dus": [{"du_id": "du0"}, {"du_id": "du1"}, {"du_id": "du2"}, {"du_id": "du3"}]},
        }

        class Args:
            ue_count = 50
            camera_ue_count = 19
            vehicle_ue_count = 12
            mmwave_enb_nodes = 3
            logical_du_count = 3
            ue_speed_min = 0.0
            ue_speed_max = 3.0

        payload = build_round_config(base, Args(), 7)
        self.assertEqual(payload["scenario_id"], "tasam_article_offline_round_0007")
        self.assertEqual(payload["ns3"]["total_ues"], 50)
        self.assertEqual(payload["ns3"]["camera_imsis"][0], 1)
        self.assertEqual(payload["ns3"]["camera_imsis"][-1], 19)
        self.assertEqual(payload["ns3"]["background_imsi_range"], [20, 38])
        self.assertEqual(payload["device_roles"]["vehicle_imsi_range"], [39, 50])
        self.assertEqual(payload["apps"]["app1"]["active_cameras"], 19)
        self.assertEqual(payload["apps"]["app3"]["max_vehicles"], 12)
        self.assertEqual(payload["apps"]["app3"]["base_imsi"], 39)
        self.assertEqual(payload["marl"]["logical_du_count"], 3)
        self.assertEqual(len(payload["marl"]["logical_dus"]), 3)

    def test_merge_round_traces_concatenates_non_empty_rounds(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            round_a = root / "round_0001" / "export"
            round_b = root / "round_0002" / "export"
            round_a.mkdir(parents=True, exist_ok=True)
            round_b.mkdir(parents=True, exist_ok=True)
            (round_a / "tasam_article_trace.jsonl").write_text('{"a":1}\n{"a":2}\n', encoding="utf-8")
            (round_b / "tasam_article_trace.jsonl").write_text("", encoding="utf-8")

            payload = merge_round_traces(
                root,
                [
                    {
                        "round_id": "round_0001",
                        "trace_jsonl": str(round_a / "tasam_article_trace.jsonl"),
                        "decision_counts": {"BLOCKED": 2},
                        "stage_counts": {"blocked_camera_overload": 2},
                        "collection_event_profile": "drl_balanced_blocked_v1",
                    },
                    {"round_id": "round_0002", "trace_jsonl": str(round_b / "tasam_article_trace.jsonl")},
                ],
            )

            merged = (root / "tasam_article_trace.jsonl").read_text(encoding="utf-8")
            self.assertIn('{"a":1}', merged)
            self.assertIn('{"a":2}', merged)
            self.assertEqual(payload["successful_rounds"], 1)
            self.assertEqual(payload["written_transitions_total"], 2)
            self.assertEqual(payload["decision_counts_total"]["BLOCKED"], 2)
            self.assertEqual(payload["stage_counts_total"]["blocked_camera_overload"], 2)
            self.assertEqual(payload["collection_event_profiles"]["drl_balanced_blocked_v1"], 2)
            summary = json.loads((root / "offline_collection_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["written_transitions_total"], 2)

    def test_progress_signature_advanced_requires_real_progress(self):
        self.assertTrue(progress_signature_advanced((4.0, 4.0, 100, 90, 50), (3.0, 3.0, 100, 90, 50)))
        self.assertTrue(progress_signature_advanced((4.0, 5.0, 100, 90, 50), (4.0, 4.0, 100, 90, 50)))
        self.assertTrue(progress_signature_advanced((0.0, 0.0, 100, 0, 0), (0.0, 0.0, 0, 0, 0)))
        self.assertFalse(progress_signature_advanced((4.0, 4.0, 100, 90, 50), (4.0, 4.0, 100, 90, 50)))
        self.assertFalse(progress_signature_advanced((4.0, 4.0, 101, 90, 50), (4.0, 4.0, 100, 90, 50)))

    def test_collect_round_stops_writers_before_waiting_for_settle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = {
                "scenario_id": "base",
                "ns3": {},
                "device_roles": {},
                "apps": {"app1": {}, "app3": {}},
                "marl": {"logical_dus": [{"du_id": "du0"}, {"du_id": "du1"}, {"du_id": "du2"}]},
            }

            class Args:
                output_root = str(root)
                dry_run = False
                settle_seconds = 1.0
                post_ns3_grace_seconds = 0.0
                collector_poll_interval = 0.25
                orchestrator_interval = 0.25
                sim_time = 5
                ue_count = 8
                camera_ue_count = 3
                vehicle_ue_count = 2
                mmwave_enb_nodes = 3
                logical_du_count = 3
                ue_speed_min = 0.0
                ue_speed_max = 1.0
                ran_pressure_profile = "drl_article_v1"
                collection_event_profile = "drl_balanced_blocked_v1"
                collection_event_time_source = "sim"
                export_limit = 0
                allow_proxy = False

            events: list[str] = []

            def fake_run(*args, **kwargs):
                return None

            def fake_start_process(*args, **kwargs):
                proc = mock.Mock()
                proc.pid = 100 + len(events)
                proc.poll.side_effect = [None, 0]
                proc.wait.return_value = 0
                proc._label = Path(kwargs["log_path"]).name
                return proc

            def fake_terminate_process(proc, timeout=10.0):
                events.append(f"terminate:{proc._label}")

            def fake_wait_for_round_settle(db_path, settle_seconds, dry_run):
                events.append("wait_for_settle")
                return {"extended_metrics": 1, "decisions_history": 1}

            def fake_export_round(paths, args, dry_run):
                events.append("export_round")
                return {"written_transitions": 1, "candidate_snapshots": 2, "decision_counts": {"BLOCKED": 1}, "stage_counts": {"blocked_camera_overload": 1}}

            def fake_cleanup_round_xapps(env, dry_run):
                events.append("cleanup_round_xapps")

            with mock.patch.object(offline_collection, "run", side_effect=fake_run), \
                mock.patch.object(offline_collection, "start_process", side_effect=fake_start_process), \
                mock.patch.object(offline_collection, "terminate_process", side_effect=fake_terminate_process), \
                mock.patch.object(offline_collection, "cleanup_round_xapps", side_effect=fake_cleanup_round_xapps), \
                mock.patch.object(offline_collection, "wait_for_round_settle", side_effect=fake_wait_for_round_settle), \
                mock.patch.object(offline_collection, "export_round", side_effect=fake_export_round), \
                mock.patch.object(offline_collection, "hydrate_missing_traces", return_value=None), \
                mock.patch.object(offline_collection, "read_round_progress_signature", return_value=(0.0, 0.0, 0, 0, 0)):
                payload = collect_round(1, Args(), base, Path(sys.executable))

            self.assertEqual(payload["ns3_exit_code"], 0)
            self.assertEqual(
                events,
                [
                    "terminate:collection_event_alternator.log",
                    "terminate:csv_metrics.log",
                    "terminate:rapp.log",
                    "cleanup_round_xapps",
                    "wait_for_settle",
                    "export_round",
                ],
            )
            self.assertEqual(payload["collection_event_profile"], "drl_balanced_blocked_v1")


if __name__ == "__main__":
    unittest.main()
