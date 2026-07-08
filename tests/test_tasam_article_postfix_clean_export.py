import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from filter_tasam_trace import POSTFIX_CLEAN_SINCE_TS  # noqa: E402
from run_tasam_article_postfix_clean_export import (  # noqa: E402
    build_filter_command,
    default_output_paths,
)


class TestTasamArticlePostfixCleanExport(unittest.TestCase):
    def test_default_output_paths_use_official_clean_names(self):
        output_dir = ROOT / "runs" / "tasam_article_ns3_collection" / "tasam_article_export"
        raw_trace, clean_trace, summary_path, manifest_path = default_output_paths(output_dir)
        self.assertEqual(raw_trace.name, "tasam_article_trace.jsonl")
        self.assertEqual(clean_trace.name, "tasam_article_trace_postfix_clean.jsonl")
        self.assertEqual(summary_path.name, "tasam_article_trace_postfix_clean_summary.json")
        self.assertEqual(manifest_path.name, "latest_postfix_clean.json")

    def test_build_filter_command_targets_postfix_clean_profile(self):
        output_dir = ROOT / "runs" / "tasam_article_ns3_collection" / "tasam_article_export"
        raw_trace, clean_trace, summary_path, _manifest_path = default_output_paths(output_dir)
        command = build_filter_command(sys.executable, raw_trace, clean_trace, summary_path)
        self.assertIn("filter_tasam_trace.py", command[1])
        self.assertEqual(command[-1], "postfix_clean")
        self.assertIn(str(clean_trace), command)
        self.assertIn(str(summary_path), command)
        self.assertGreater(POSTFIX_CLEAN_SINCE_TS, 0)


if __name__ == "__main__":
    unittest.main()
