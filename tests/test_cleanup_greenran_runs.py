from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "cleanup_greenran_runs.py"
SPEC = importlib.util.spec_from_file_location("cleanup_greenran_runs", SCRIPT)
assert SPEC and SPEC.loader
cleanup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cleanup)


class CleanupGreenranRunsTests(unittest.TestCase):
    def test_armd_and_current_campaigns_are_protected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            runs = Path(temp)
            armd = runs / "greenran_tasam_e2_active_20260827_v7"
            armd.mkdir()
            (armd / "arm_manifest.json").write_text("{}")
            current = runs / "tasam_local_causal_pilot_seed47_20260906_v11"
            current.mkdir()
            self.assertTrue(cleanup.protected_reasons(armd))
            self.assertTrue(cleanup.protected_reasons(current))

    def test_duplicate_snapshot_keeps_manifest_latest(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "tasam_article_ns3_collection"
            snapshots = root / "db_snapshots"
            snapshots.mkdir(parents=True)
            older = snapshots / "old.db"
            latest = snapshots / "latest.db"
            older.write_bytes(b"same sqlite contents")
            latest.write_bytes(b"same sqlite contents")
            (snapshots / "latest_snapshot.json").write_text(
                json.dumps({"latest_snapshot": str(latest)})
            )
            candidates: list[dict] = []
            cleanup.duplicate_snapshots(root, candidates)
            self.assertEqual([item["path"] for item in candidates], [str(older.resolve())])
            self.assertTrue(candidates[0]["allow_protected"])

    def test_current_candidate_checkpoint_is_retained(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "tasam_seed45_20260830"
            first = root / "candidates" / "candidate_0001"
            promoted = root / "candidates" / "candidate_0002"
            first.mkdir(parents=True)
            promoted.mkdir(parents=True)
            (root / "online_status.json").write_text(
                json.dumps({"status": "stopped", "active_checkpoint": str(promoted)})
            )
            candidates: list[dict] = []
            cleanup.add_old_campaign_candidates(root, candidates)
            paths = {item["path"] for item in candidates}
            self.assertIn(str(first.resolve()), paths)
            self.assertNotIn(str(promoted.resolve()), paths)

    def test_old_online_observation_compacts_only_derived_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            runs = Path(temp)
            root = runs / "tasam_online_observation_seed47_20260901_v7"
            traces = root / "adaptation_online" / "ns3_traces"
            replay = root / "adaptation_online" / "replay_buffer"
            traces.mkdir(parents=True)
            replay.mkdir(parents=True)
            (traces / "DlPdcpStats.txt").write_text("raw trace")
            (replay / "update.jsonl").write_text("temporary replay")
            (root / "campaign_manifest.json").write_text(json.dumps({"status": "adaptation_blocked"}))

            manifest = cleanup.build_manifest(runs, target_free_gb=50.0)
            paths = {item["path"] for item in manifest["candidates"]}

            self.assertIn(str(traces.resolve()), paths)
            self.assertIn(str(replay.resolve()), paths)
            self.assertNotIn(str(root.resolve()), paths)


if __name__ == "__main__":
    unittest.main()
