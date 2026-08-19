import json
import unittest
from pathlib import Path


class TASAMDRLTracksTests(unittest.TestCase):
    def test_manifest_declares_three_official_tracks(self):
        root = Path(__file__).resolve().parents[1]
        payload = json.loads((root / "config" / "tasam_drl_tracks.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["schema"], "greenran.tasam_drl_tracks.v1")
        self.assertTrue(payload["keep_greenran_architecture"])
        self.assertTrue(payload["keep_armd_greenran_untouched"])

        tracks = payload["tracks"]
        self.assertEqual(len(tracks), 3)
        self.assertEqual(
            [track["track_id"] for track in tracks],
            ["greenran_tasam", "tasam_article_reproduction", "tasam_reference_base"],
        )

    def test_manifest_entrypoints_match_tasam_family(self):
        root = Path(__file__).resolve().parents[1]
        payload = json.loads((root / "config" / "tasam_drl_tracks.json").read_text(encoding="utf-8"))
        tracks = {track["track_id"]: track for track in payload["tracks"]}
        self.assertEqual(tracks["greenran_tasam"]["entrypoint"], "scripts/run_tasam_greenran_real.py")
        self.assertEqual(
            tracks["tasam_article_reproduction"]["entrypoint"],
            "scripts/run_tasam_article_reproduction.py",
        )
        self.assertEqual(tracks["tasam_reference_base"]["entrypoint"], "scripts/run_tasam_legacy_real.py")
        self.assertEqual(
            tracks["tasam_article_reproduction"]["scenario_source"],
            "config/tasam_article_ns3_collection.json",
        )
        self.assertEqual(
            tracks["tasam_article_reproduction"]["collection_entrypoint"],
            "scripts/run_tasam_article_ns3_collection.sh",
        )
        self.assertEqual(
            tracks["tasam_article_reproduction"]["runtime_policy"],
            "heuristic_collection_only",
        )
        self.assertEqual(
            tracks["tasam_article_reproduction"]["operation_mode"],
            "continuous_collection",
        )
        self.assertEqual(
            tracks["tasam_article_reproduction"]["storage_backend"],
            "sqlite",
        )
        self.assertEqual(
            tracks["tasam_article_reproduction"]["snapshot_dir"],
            "runs/tasam_article_ns3_collection/db_snapshots",
        )
        self.assertEqual(
            tracks["tasam_article_reproduction"]["dataset_export_dir"],
            "runs/tasam_article_ns3_collection/tasam_article_export",
        )
        self.assertEqual(
            tracks["tasam_article_reproduction"]["dataset_source_mode"],
            "sqlite_export",
        )


if __name__ == "__main__":
    unittest.main()
