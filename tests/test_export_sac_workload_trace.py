import csv
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class ExportSacWorkloadTraceTests(unittest.TestCase):
    def setUp(self):
        self.project_root = Path(__file__).resolve().parents[1]
        self.src_dir = self.project_root / "src"
        if str(self.src_dir) not in sys.path:
            sys.path.insert(0, str(self.src_dir))
        self.temp_dir = tempfile.mkdtemp(prefix="greenran_sac_export_")
        self.db_path = Path(self.temp_dir) / "rapp_data_lake.db"
        self.csv_path = Path(self.temp_dir) / "workload.csv"

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_export_includes_controller_metadata_columns(self):
        from rapp_data_lake import DataLake

        lake = DataLake(str(self.db_path))
        lake.record_resource_allocation_snapshot(
            {
                "controller_id": "caora_awac_actor",
                "target_policy_id": "caora_awac_resource_allocation",
                "decision_domain": "resource_allocation",
                "action_semantics": "resource_share_delta",
                "resource_budget": 1.0,
                "usable_budget": 0.8,
                "d_ran": 0.45,
                "d_ai": 0.32,
                "r_ran": 0.55,
                "r_ai": 0.25,
                "delta_r_ran": 0.02,
                "delta_r_ai": -0.01,
                "ran_completion_ratio": 0.99,
                "ai_completion_ratio": 0.86,
                "utilization_ratio": 0.8,
            },
            timestamp=1770000000,
        )

        subprocess.run(
            [
                sys.executable,
                str(self.project_root / "scripts" / "export_sac_workload_trace.py"),
                "--db",
                str(self.db_path),
                "--output-csv",
                str(self.csv_path),
            ],
            cwd=self.project_root,
            check=True,
        )

        with self.csv_path.open("r", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["controller_id"], "caora_awac_actor")
        self.assertEqual(rows[0]["target_policy_id"], "caora_awac_resource_allocation")
        self.assertEqual(rows[0]["decision_domain"], "resource_allocation")
        self.assertEqual(rows[0]["action_semantics"], "resource_share_delta")

    def test_export_default_db_uses_greenran_state_dir(self):
        env = os.environ.copy()
        env["GREENRAN_STATE_DIR"] = self.temp_dir

        subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import sys; "
                    "sys.path.insert(0, 'src'); "
                    "from rapp_data_lake import DataLake; "
                    "lake=DataLake(); "
                    "lake.record_resource_allocation_snapshot({"
                    "'controller_id':'caora_awac_actor',"
                    "'target_policy_id':'caora_awac_resource_allocation',"
                    "'decision_domain':'resource_allocation',"
                    "'action_semantics':'resource_share_delta',"
                    "'resource_budget':1.0,"
                    "'usable_budget':0.8,"
                    "'d_ran':0.4,"
                    "'d_ai':0.3,"
                    "'r_ran':0.5,"
                    "'r_ai':0.3,"
                    "'delta_r_ran':0.01,"
                    "'delta_r_ai':-0.01,"
                    "'ran_completion_ratio':1.0,"
                    "'ai_completion_ratio':0.85,"
                    "'utilization_ratio':0.8"
                    "}, timestamp=1770000001)"
                ),
            ],
            cwd=self.project_root,
            env=env,
            check=True,
        )

        subprocess.run(
            [
                sys.executable,
                str(self.project_root / "scripts" / "export_sac_workload_trace.py"),
                "--output-csv",
                str(self.csv_path),
            ],
            cwd=self.project_root,
            env=env,
            check=True,
        )

        with self.csv_path.open("r", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["controller_id"], "caora_awac_actor")


if __name__ == "__main__":
    unittest.main()
