import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.validate_tasam_stress_run import validate


def make_db(root: Path, metric_count: int) -> None:
    root.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(root / "rapp_data_lake.db")
    conn.executescript(
        """
        create table decisions_history (id integer primary key);
        create table extended_metrics (
            id integer primary key, collector_mode text,
            real_latency_sample_count integer,
            proxy_latency_sample_count integer, pdcp_stale integer
        );
        """
    )
    conn.executemany("insert into decisions_history values (?)", [(i,) for i in range(1, 66)])
    conn.executemany(
        "insert into extended_metrics values (?, 'pdcp_real', 20, 0, 0)",
        [(i,) for i in range(1, metric_count + 1)],
    )
    conn.commit()
    conn.close()


class TestValidateTasamStressRun(unittest.TestCase):
    def test_short_metric_window_is_not_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_db(root, 44)
            result = validate(root, "baseline", 65)
            self.assertFalse(result["ready"])
            self.assertIn("extended_metric_rows=44, expected=65", result["failures"])
            self.assertIn("real_pdcp_rows=44, expected=65", result["failures"])

    def test_exact_real_metric_window_is_ready_for_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_db(root, 65)
            result = validate(root, "baseline", 65)
            self.assertTrue(result["ready"])
            self.assertEqual(result["real_pdcp_rows"], 65)


if __name__ == "__main__":
    unittest.main()
