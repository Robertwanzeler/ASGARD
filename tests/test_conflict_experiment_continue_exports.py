import json
import tempfile
import unittest
from pathlib import Path

from scripts.run_conflict_experiments import load_round_summaries_for_scenario


class ConflictExperimentContinueExportsTests(unittest.TestCase):
    def test_load_round_summaries_for_scenario_reads_persisted_rounds(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rounds_dir = root / "vehicle_recovery" / "rounds"
            for idx in (1, 2):
                round_dir = rounds_dir / f"round_{idx:02d}"
                round_dir.mkdir(parents=True, exist_ok=True)
                payload = {
                    "scenario": "vehicle_recovery",
                    "round": idx,
                    "start_ts": 100 + idx,
                    "end_ts": 200 + idx,
                    "rows": 60 + idx,
                }
                with (round_dir / "round_summary.json").open("w") as handle:
                    json.dump(payload, handle)

            summaries = load_round_summaries_for_scenario(root, "vehicle_recovery")
            self.assertEqual([item["round"] for item in summaries], [1, 2])
            self.assertEqual(sum(item["rows"] for item in summaries), 123)


if __name__ == "__main__":
    unittest.main()
