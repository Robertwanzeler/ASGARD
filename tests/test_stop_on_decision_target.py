import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import stop_on_decision_target as watcher
from scripts import wall_clock_collection_supervisor as wall_clock


class TestDecisionTargetWatcher(unittest.TestCase):
    def test_native_tasam_actuator_is_owned_by_collection_shutdown(self):
        names = {name for name, _tokens in wall_clock.PROCESS_SPECS}
        self.assertIn("xapp_tasam_actuator.pid", names)
        self.assertIn("xapp_tasam_actuator.pid", watcher.PID_NAMES)

    def test_stop_run_is_idempotent_for_stale_pid_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            (state_dir / "wall_clock_supervisor.pid").write_text("999999991\n", encoding="utf-8")
            (state_dir / "rapp.pid").write_text("999999992\n", encoding="utf-8")
            self.assertEqual(watcher.stop_run(state_dir), [
                {"pid": 999999991, "pid_file": "wall_clock_supervisor.pid", "signal": "already_stopped"},
                {"pid": 999999992, "pid_file": "rapp.pid", "signal": "already_stopped"},
            ])

    def test_wall_clock_supervisor_is_stopped_before_workers(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            (state_dir / "wall_clock_supervisor.pid").write_text("101\n", encoding="utf-8")
            (state_dir / "rapp.pid").write_text("102\n", encoding="utf-8")
            calls = []

            def fake_stop(pid, name, **_kwargs):
                calls.append((pid, name))
                return {"pid": pid, "pid_file": name, "signal": "SIGTERM", "exited": True}

            with patch.object(watcher, "_pid_alive", return_value=True), patch.object(watcher, "_stop_pid", side_effect=fake_stop):
                watcher.stop_run(state_dir)
            self.assertEqual(calls, [(101, "wall_clock_supervisor.pid"), (102, "rapp.pid")])

    def test_wall_clock_signal_does_not_kill_launcher_process_group(self):
        calls = []

        def fake_signal(pid, sig, **kwargs):
            calls.append((pid, kwargs.get("use_group")))
            return "SIGTERM"

        with patch.object(watcher, "_pid_alive", return_value=True), \
             patch.object(watcher, "signal_scoped_process", side_effect=fake_signal), \
             patch.object(watcher, "_wait_pid_exit", return_value=True):
            watcher._stop_pid(101, "wall_clock_supervisor.pid")
            watcher._stop_pid(102, "rapp.pid", use_group=True)
        self.assertEqual(calls, [(101, False), (102, True)])

    def test_stop_run_uses_worker_process_group_but_protects_wall_supervisor(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            (state_dir / "wall_clock_supervisor.pid").write_text("101\n", encoding="utf-8")
            (state_dir / "csv_metrics.pid").write_text("102\n", encoding="utf-8")
            calls = []

            def fake_stop(pid, name, **kwargs):
                calls.append((pid, name, kwargs["use_group"]))
                return {"pid": pid, "pid_file": name, "signal": "SIGTERM", "exited": True}

            with patch.object(watcher, "_pid_alive", return_value=True), patch.object(watcher, "_stop_pid", side_effect=fake_stop):
                watcher.stop_run(state_dir)
            self.assertEqual(calls, [
                (101, "wall_clock_supervisor.pid", False),
                (102, "csv_metrics.pid", True),
            ])

    def test_stop_run_drains_decision_target_watcher(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            (state_dir / "wall_clock_supervisor.pid").write_text("101\n", encoding="utf-8")
            (state_dir / "decision_target_supervisor.pid").write_text("102\n", encoding="utf-8")
            calls = []

            def fake_stop(pid, name, **kwargs):
                calls.append((pid, name, kwargs["use_group"]))
                return {"pid": pid, "pid_file": name, "signal": "SIGTERM", "exited": True}

            with patch.object(watcher, "_pid_alive", return_value=True), patch.object(watcher, "_stop_pid", side_effect=fake_stop):
                watcher.stop_run(state_dir)
            self.assertEqual(calls, [
                (101, "wall_clock_supervisor.pid", False),
                (102, "decision_target_supervisor.pid", True),
            ])

    def test_worker_in_arm_group_is_signaled_by_pid_only(self):
        with patch.object(watcher.os, "getpgid", return_value=333), \
             patch.object(watcher.os, "getpgrp", return_value=999), \
             patch.object(watcher, "_protected_process_groups", return_value={333}), \
             patch.object(watcher.os, "kill") as kill, \
             patch.object(watcher.os, "killpg") as killpg:
            result = watcher.signal_scoped_process(101, watcher.signal.SIGTERM)
        self.assertEqual(result, "SIGTERM")
        kill.assert_called_once_with(101, watcher.signal.SIGTERM)
        killpg.assert_not_called()

    def test_stale_worker_pid_drains_only_its_surviving_greenran_group(self):
        with patch.object(watcher, "_pid_alive", return_value=False), \
             patch.object(watcher, "_protected_process_groups", return_value=set()), \
             patch.object(watcher, "_greenran_group_members", side_effect=[[202], [], []]), \
             patch.object(watcher.os, "killpg") as killpg:
            result = watcher._stop_pid(202, "csv_metrics.pid", use_group=True)
        self.assertEqual(result["signal"], "already_stopped")
        self.assertEqual(result["orphan_group"], 202)
        self.assertTrue(result["orphan_exited"])
        killpg.assert_called_once_with(202, watcher.signal.SIGTERM)

    def test_decision_count_uses_jsonl_when_wal_count_lags(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            db_path = state_dir / "rapp_data_lake.db"
            (state_dir / "rapp_decisions.jsonl").write_text(
                '{"decision": 1}\n{"decision": 2}\n{"decision": 3}\n',
                encoding="utf-8",
            )
            with patch.object(watcher, "_sqlite_decision_count", return_value=2):
                self.assertEqual(watcher.decision_count(db_path, state_dir), 3)

    def test_local_campaign_defaults_to_v5(self):
        root = Path(__file__).resolve().parents[1]
        launcher = (root / "scripts/run_tasam_local_causal_campaign.sh").read_text(encoding="utf-8")
        service = (root / "systemd/greenran-tasam-causal-pilot.service").read_text(encoding="utf-8")
        self.assertIn("tasam_local_causal_pilot_seed47_20260906_v11", launcher)
        self.assertIn("tasam_local_causal_pilot_seed47_20260906_v11", service)
        self.assertNotIn("LOCAL_CAMPAIGN_DIR=/home/robert/orange_nuclear/runs/tasam_local_causal_pilot_seed47_20260906_v4", service)


if __name__ == "__main__":
    unittest.main()
