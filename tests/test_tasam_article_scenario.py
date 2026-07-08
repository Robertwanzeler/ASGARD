import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


class TestTASAMArticleScenario(unittest.TestCase):
    def test_ns3_config_matches_article_contract(self):
        root = Path(__file__).resolve().parents[1]
        payload = json.loads((root / "config" / "tasam_article_ns3_collection.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["schema"], "greenran.tasam.article_ns3_collection.v1")
        self.assertEqual(payload["ns3"]["total_ues"], 200)
        self.assertEqual(payload["ns3"]["mmwave_enb_nodes"], 6)
        self.assertEqual(payload["marl"]["logical_du_count"], 6)
        self.assertEqual(payload["apps"]["app1"]["active_cameras"], 80)
        self.assertEqual(payload["apps"]["app3"]["max_vehicles"], 40)

    def test_official_runner_uses_ns3_export_not_synthetic_generator(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [
                sys.executable,
                str(root / "scripts" / "run_tasam_article_reproduction.py"),
                "--db",
                "/tmp/tasam_article_ns3_collection.db",
                "--dry-run",
            ],
            cwd=root,
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        )
        stdout = result.stdout
        self.assertIn("export_tasam_article_dataset.py", stdout)
        self.assertIn("train_tasam_marl.py", stdout)
        self.assertNotIn("generate_tasam_article_scenario.py", stdout)
        self.assertIn("runs/tasam_article_reproduction/tasam_selective", stdout)

    def test_official_runner_can_reuse_existing_trace_jsonl(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [
                sys.executable,
                str(root / "scripts" / "run_tasam_article_reproduction.py"),
                "--trace-jsonl",
                "/tmp/prebuilt_tasam_trace.jsonl",
                "--skip-export",
                "--dry-run",
            ],
            cwd=root,
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        )
        stdout = result.stdout
        self.assertNotIn("export_tasam_article_dataset.py", stdout)
        self.assertIn("/tmp/prebuilt_tasam_trace.jsonl", stdout)
        self.assertIn("train_tasam_marl.py", stdout)

    def test_active_db_env_overrides_greenran_real_default(self):
        root = Path(__file__).resolve().parents[1]
        env = os.environ.copy()
        env["GREENRAN_TASAM_ACTIVE_DB"] = "/tmp/new_active_collection.db"
        result = subprocess.run(
            [
                sys.executable,
                str(root / "scripts" / "run_tasam_greenran_real.py"),
                "--dry-run",
            ],
            cwd=root,
            check=True,
            stdout=subprocess.PIPE,
            text=True,
            env=env,
        )
        self.assertIn("/tmp/new_active_collection.db", result.stdout)
        self.assertNotIn("20260609_tasam_article_v1_recovery_state", result.stdout)

    def test_active_db_env_overrides_article_suite_default(self):
        root = Path(__file__).resolve().parents[1]
        env = os.environ.copy()
        env["GREENRAN_TASAM_ACTIVE_DB"] = "/tmp/new_active_collection.db"
        result = subprocess.run(
            [
                sys.executable,
                str(root / "scripts" / "run_tasam_article_suite.py"),
                "--dry-run",
            ],
            cwd=root,
            check=True,
            stdout=subprocess.PIPE,
            text=True,
            env=env,
        )
        self.assertIn("/tmp/new_active_collection.db", result.stdout)
        self.assertNotIn("20260609_tasam_article_v1_recovery_state", result.stdout)

    def test_collection_entrypoint_declares_snapshot_and_live_export_services(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "scripts" / "run_tasam_article_ns3_collection.sh").read_text(encoding="utf-8")
        self.assertIn("snapshot_sqlite_db.py", script)
        self.assertIn("run_tasam_article_export.py", script)
        self.assertIn("run_tasam_true_online_real.py", script)
        self.assertIn("run_rapp_online_retrain.py", script)
        self.assertIn("GREENRAN_DB_SNAPSHOT_DIR", script)
        self.assertIn("GREENRAN_TASAM_EXPORT_DIR", script)
        self.assertIn("GREENRAN_TASAM_TRUE_ONLINE_DIR", script)
        self.assertIn("GREENRAN_TASAM_TRUE_ONLINE_MIN_NEW_SNAPSHOTS", script)
        self.assertIn("GREENRAN_TASAM_TRUE_ONLINE_MIN_TRAINABLE_TRANSITIONS", script)
        self.assertIn("GREENRAN_TASAM_TRUE_ONLINE_BOOTSTRAP_EPOCHS", script)
        self.assertIn("GREENRAN_TASAM_TRUE_ONLINE_EPOCHS_PER_UPDATE", script)
        self.assertIn('GREENRAN_COLLECTION_DISABLE_APP_OVERRIDES="1"', script)
        self.assertIn('GREENRAN_RAN_PRESSURE_PROFILE="${GREENRAN_RAN_PRESSURE_PROFILE:-tasam_training_balanced_v1}"', script)
        self.assertIn("GREENRAN_RAPP_ONLINE_RETRAIN_TARGET", script)
        self.assertIn("GREENRAN_RAPP_ONLINE_RETRAIN_MIN_NEW_ROWS", script)
        self.assertIn('GREENRAN_ML_RETRAIN_ENABLED', script)
        self.assertIn('GREENRAN_TASAM_TRUE_ONLINE_ENABLED', script)
        self.assertIn("GREENRAN_STATE_DIR='$GREENRAN_STATE_DIR' python3 scripts/status.py", script)
        self.assertIn('start_if_missing "$GREENRAN_NS3_SUPERVISOR_PID" start_ns3', script)
        self.assertIn('start_if_missing "$GREENRAN_TASAM_TRUE_ONLINE_PID" start_tasam_true_online_real_service', script)
        self.assertIn('if [[ "${GREENRAN_ML_RETRAIN_ENABLED:-true}" == "true" ]]; then', script)
        self.assertIn('setsid env \\', script)
        self.assertIn('GREENRAN_NS3_CAMERA_UE_COUNT="$GREENRAN_NS3_CAMERA_UE_COUNT"', script)
        self.assertIn('GREENRAN_REQUIRE_REAL_PDCP="1"', script)
        self.assertIn('GREENRAN_NS3_ENABLE_TRACES_AFTER_ATTACH="${GREENRAN_NS3_ENABLE_TRACES_AFTER_ATTACH:-0}"', script)
        self.assertIn('GREENRAN_NS3_USE_MC_UE_DEVICES="${GREENRAN_NS3_USE_MC_UE_DEVICES:-true}"', script)

    def test_ns3_supervisor_uses_article_cli_arguments_for_pdcp_traces(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "scripts" / "start_ns3_supervisor.sh").read_text(encoding="utf-8")
        self.assertIn('STATE_DIR="${GREENRAN_STATE_DIR:-$PROJECT_ROOT/runs/tasam_article_ns3_collection}"', script)
        self.assertIn('--ueCount="$NS3_UE_COUNT"', script)
        self.assertIn('--cameraUeCount="$NS3_CAMERA_UE_COUNT"', script)
        self.assertIn('--vehicleUeCount="$NS3_VEHICLE_UE_COUNT"', script)
        self.assertIn('--mmWaveEnbNodes="$NS3_MMWAVE_ENB_NODES"', script)
        self.assertIn('--ueSpeedMin="$NS3_UE_SPEED_MIN"', script)
        self.assertIn('--ueSpeedMax="$NS3_UE_SPEED_MAX"', script)
        self.assertIn('--enableTracesAfterAttach="$NS3_ENABLE_TRACES_AFTER_ATTACH"', script)
        self.assertIn('--useMcUeDevices="$NS3_USE_MC_UE_DEVICES"', script)

    def test_stop_script_cleans_ns3_supervisor_process_tree(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "scripts" / "stop_tasam_article_ns3_collection.sh").read_text(encoding="utf-8")
        self.assertIn("kill_process_tree_by_pattern", script)
        self.assertIn('$PROJECT_ROOT/scripts/start_ns3_supervisor.sh', script)

    def test_restart_ns3_only_uses_active_state_env_not_recovery_default(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "scripts" / "restart_ns3_only.sh").read_text(encoding="utf-8")
        self.assertIn('GREENRAN_TASAM_ACTIVE_STATE_DIR', script)
        self.assertIn('runs/tasam_article_ns3_collection', script)
        self.assertNotIn('20260609_tasam_article_v1_recovery_state', script)


if __name__ == "__main__":
    unittest.main()
