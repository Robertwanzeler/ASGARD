import json
import tempfile
import unittest
from pathlib import Path

from training.graphsage_report_utils import load_scenario_reports


class TrainGraphSAGEConflictReportsTests(unittest.TestCase):
    def test_load_scenario_reports_merges_missing_individual_reports(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "vehicle_warning").mkdir()
            (root / "vehicle_recovery").mkdir()

            with (root / "experiment_report.json").open("w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "scenario_reports": [
                            {"scenario": "vehicle_recovery", "full_export": {}, "subsets": []}
                        ]
                    },
                    handle,
                )

            with (root / "vehicle_warning" / "scenario_report.json").open("w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "scenario": "vehicle_warning",
                        "full_export": {"graph_path": "warning.json"},
                        "subsets": [{"requested_rows": 50}],
                    },
                    handle,
                )

            with (root / "vehicle_recovery" / "scenario_report.json").open("w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "scenario": "vehicle_recovery",
                        "full_export": {"graph_path": "recovery.json"},
                        "subsets": [{"requested_rows": 50}],
                    },
                    handle,
                )

            reports = load_scenario_reports(root)
            scenarios = sorted(item["scenario"] for item in reports)
            self.assertEqual(scenarios, ["vehicle_recovery", "vehicle_warning"])


if __name__ == "__main__":
    unittest.main()
