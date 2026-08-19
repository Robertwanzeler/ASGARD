import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rapp_data_lake import DataLake
from rapp_network_improvement import build_network_improvement


class RappNetworkImprovementTests(unittest.TestCase):
    def test_build_network_improvement_matches_expected_percentages(self):
        allowed = build_network_improvement({"cvar_us": 32000.0, "p95_us": 24000.0}, source="override")
        conditional = build_network_improvement({"cvar_us": 95000.0, "p95_us": 72000.0}, source="override")
        blocked = build_network_improvement({"cvar_us": 118000.0, "p95_us": 78000.0}, source="override")

        self.assertTrue(allowed["improvement_valid"])
        self.assertAlmostEqual(allowed["network_improvement_pct"], 93.39797709923664, places=3)
        self.assertAlmostEqual(conditional["network_improvement_pct"], 80.40024757685166, places=3)
        self.assertAlmostEqual(blocked["network_improvement_pct"], 75.65504435733443, places=3)

    def test_data_lake_persists_network_improvement_columns(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "improvement_test.db"
            lake = DataLake(db_path=str(db_path))
            columns = {
                row[1] for row in lake.conn.execute("PRAGMA table_info(decisions_history)").fetchall()
            }
            self.assertIn("network_improvement_pct", columns)
            self.assertIn("cvar_improvement_pct", columns)
            self.assertIn("p95_improvement_pct", columns)
            self.assertIn("baseline_cvar_us", columns)
            self.assertIn("baseline_p95_us", columns)
            self.assertIn("improvement_source", columns)
            self.assertIn("improvement_valid", columns)

            lake.record_decision(
                {
                    "energy_saver": "ALLOWED",
                    "reason": "test",
                    "network_health": {
                        "network_improvement_pct": 93.4,
                        "cvar_improvement_pct": 93.4,
                        "p95_improvement_pct": 95.0,
                        "baseline_cvar_us": 484700.0,
                        "baseline_p95_us": 484700.0,
                        "improvement_source": "scenario_control_override",
                        "improvement_valid": True,
                    },
                },
                timestamp=1782930635,
            )
            row = lake.conn.execute(
                """
                select network_improvement_pct, cvar_improvement_pct, p95_improvement_pct,
                       baseline_cvar_us, baseline_p95_us, improvement_source, improvement_valid
                from decisions_history
                where timestamp=?
                """,
                (1782930635,),
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertAlmostEqual(float(row[0] or 0.0), 93.4, places=3)
            self.assertAlmostEqual(float(row[1] or 0.0), 93.4, places=3)
            self.assertAlmostEqual(float(row[2] or 0.0), 95.0, places=3)
            self.assertAlmostEqual(float(row[3] or 0.0), 484700.0, places=3)
            self.assertAlmostEqual(float(row[4] or 0.0), 484700.0, places=3)
            self.assertEqual(row[5], "scenario_control_override")
            self.assertEqual(int(row[6] or 0), 1)
            lake.close()


if __name__ == "__main__":
    unittest.main()
