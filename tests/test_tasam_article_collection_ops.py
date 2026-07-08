import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from run_tasam_article_export import (
    build_export_command,
    build_filter_command,
    default_output_paths,
    default_trainable_paths,
)
from snapshot_sqlite_db import create_snapshot, snapshot_name


class TestTASAMArticleCollectionOps(unittest.TestCase):
    def test_snapshot_name_is_timestamped(self):
        stamp = datetime(2026, 6, 19, 12, 30, tzinfo=timezone.utc)
        self.assertEqual(snapshot_name("rapp_data_lake", stamp), "rapp_data_lake_20260619T123000Z.db")

    def test_create_snapshot_keeps_latest_backups_and_prunes_old_ones(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "rapp_data_lake.db"
            conn = sqlite3.connect(str(db_path))
            conn.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, value TEXT)")
            conn.execute("INSERT INTO sample(value) VALUES ('ok')")
            conn.commit()
            conn.close()

            snapshot_dir = tmp_path / "db_snapshots"
            snapshot_dir.mkdir()
            for name in (
                "rapp_data_lake_20260619T120000Z.db",
                "rapp_data_lake_20260619T121000Z.db",
            ):
                (snapshot_dir / name).write_text("old", encoding="utf-8")

            result = create_snapshot(db_path, snapshot_dir, "rapp_data_lake", retain=2)

            snapshot_path = Path(result["snapshot_path"])
            self.assertTrue(snapshot_path.exists())
            self.assertFalse((snapshot_dir / "rapp_data_lake_20260619T120000Z.db").exists())
            self.assertTrue((snapshot_dir / "latest_snapshot.json").exists())

            copied = sqlite3.connect(str(snapshot_path))
            row = copied.execute("SELECT value FROM sample").fetchone()
            copied.close()
            self.assertEqual(row[0], "ok")

    def test_live_export_wrapper_uses_official_dataset_names(self):
        output_dir = ROOT / "runs" / "tasam_article_ns3_collection" / "tasam_article_export"
        trace_path, summary_path, manifest_path = default_output_paths(output_dir)
        trainable_trace_path, trainable_summary_path = default_trainable_paths(output_dir)
        cmd = build_export_command(
            python_bin=sys.executable,
            db_path=ROOT / "runs" / "tasam_article_ns3_collection" / "rapp_data_lake.db",
            trace_path=trace_path,
            summary_path=summary_path,
            limit=100,
            allow_proxy=True,
            include_invalid=True,
        )
        filter_cmd = build_filter_command(
            python_bin=sys.executable,
            input_path=trace_path,
            output_path=trainable_trace_path,
            summary_path=trainable_summary_path,
            profile="rapp_online_trainable",
        )
        self.assertIn("export_tasam_article_dataset.py", cmd[1])
        self.assertEqual(trace_path.name, "tasam_article_trace.jsonl")
        self.assertEqual(summary_path.name, "tasam_article_export_summary.json")
        self.assertEqual(manifest_path.name, "latest_export.json")
        self.assertEqual(trainable_trace_path.name, "rapp_online_trainable_trace.jsonl")
        self.assertEqual(trainable_summary_path.name, "rapp_online_trainable_summary.json")
        self.assertIn("--limit", cmd)
        self.assertIn("--allow-proxy", cmd)
        self.assertIn("--include-invalid", cmd)
        self.assertIn("filter_tasam_trace.py", filter_cmd[1])
        self.assertIn("--profile", filter_cmd)
        self.assertIn("rapp_online_trainable", filter_cmd)


if __name__ == "__main__":
    unittest.main()
