import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "compact_greenran_artifacts.py"
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("compact_greenran_artifacts", SCRIPT)
assert SPEC and SPEC.loader
compact = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(compact)


class CompactGreenranArtifactsTests(unittest.TestCase):
    def test_inventory_can_be_restricted_to_explicit_campaigns_and_hashes_files(self):
        with tempfile.TemporaryDirectory() as temp:
            runs = Path(temp)
            selected = runs / "selected"
            other = runs / "other"
            (selected / "ns3_traces").mkdir(parents=True)
            (other / "ns3_traces").mkdir(parents=True)
            selected_file = selected / "ns3_traces" / "trace.txt"
            selected_file.write_text("selected trace" * 100_000)
            (other / "ns3_traces" / "trace.txt").write_text("other trace" * 100_000)
            entries = compact._inventory(runs, [selected])
            self.assertEqual({item["campaign"] for item in entries}, {str(selected.resolve())})
            self.assertEqual(entries[0]["sha256"], compact._sha256(selected_file))

    def test_campaign_outside_runs_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            runs = Path(temp) / "runs"
            runs.mkdir()
            with self.assertRaises(ValueError):
                compact._resolve_campaigns(runs, [str(Path(temp).resolve())])


if __name__ == "__main__":
    unittest.main()
