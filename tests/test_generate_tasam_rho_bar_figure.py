import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from generate_tasam_rho_bar_figure import (  # noqa: E402
    DISPLAY_MODES,
    build_plot_payload,
    resolve_scenarios,
)

BASE_SCENARIO_ORDER = ("equal", "non_equal", "dynamic")
BASE_SCENARIO_RHO_VALUES = {
    "equal": 0.01,
    "non_equal": 0.02,
    "dynamic": 0.05,
}


def _write_summary(root: Path, scenario: str, mode: str, eval_return: float, cumulative_return: float) -> None:
    target = root / scenario / mode
    target.mkdir(parents=True, exist_ok=True)
    payload = {
        "final_metrics": {
            "eval_return": eval_return,
            "cumulative_return": cumulative_return,
        }
    }
    (target / "tasam_marl_summary.json").write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )


class GenerateTasamRhoBarFigureTests(unittest.TestCase):
    def test_build_plot_payload_reads_expected_modes_and_rho_mapping(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            value = 1.0
            for scenario in BASE_SCENARIO_ORDER:
                for mode in DISPLAY_MODES:
                    _write_summary(root, scenario, mode, eval_return=value, cumulative_return=value * 10.0)
                    value += 1.0

            rows = build_plot_payload(root, "eval_return")

            self.assertEqual(len(rows), len(BASE_SCENARIO_ORDER) * len(DISPLAY_MODES))
            for scenario in BASE_SCENARIO_ORDER:
                scenario_rows = [row for row in rows if row["scenario"] == scenario]
                self.assertEqual({row["mode"] for row in scenario_rows}, set(DISPLAY_MODES))
                self.assertTrue(all(row["rho_value"] == BASE_SCENARIO_RHO_VALUES[scenario] for row in scenario_rows))

    def test_build_plot_payload_raises_when_summary_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for scenario in BASE_SCENARIO_ORDER:
                for mode in DISPLAY_MODES:
                    if scenario == "dynamic" and mode == "critic_sam":
                        continue
                    _write_summary(root, scenario, mode, eval_return=1.0, cumulative_return=2.0)

            with self.assertRaises(FileNotFoundError):
                build_plot_payload(root, "eval_return")

    def test_build_plot_payload_raises_when_metric_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for scenario in BASE_SCENARIO_ORDER:
                for mode in DISPLAY_MODES:
                    target = root / scenario / mode
                    target.mkdir(parents=True, exist_ok=True)
                    payload = {"final_metrics": {"cumulative_return": 2.0}}
                    (target / "tasam_marl_summary.json").write_text(
                        json.dumps(payload, indent=2) + "\n",
                        encoding="utf-8",
                    )

            with self.assertRaises(KeyError):
                build_plot_payload(root, "eval_return")

    def test_resolve_scenarios_includes_custom_rho_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for scenario in BASE_SCENARIO_ORDER:
                (root / scenario).mkdir(parents=True, exist_ok=True)
            for mode in DISPLAY_MODES:
                _write_summary(root, "rho_0_03", mode, eval_return=1.0, cumulative_return=2.0)
                _write_summary(root, "rho_0_04", mode, eval_return=1.0, cumulative_return=2.0)

            scenarios = resolve_scenarios(root)

            self.assertEqual(
                scenarios,
                [
                    ("equal", 0.01),
                    ("non_equal", 0.02),
                    ("rho_0_03", 0.03),
                    ("rho_0_04", 0.04),
                    ("dynamic", 0.05),
                ],
            )

    def test_resolve_scenarios_ignores_incomplete_custom_rho_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for scenario in BASE_SCENARIO_ORDER:
                (root / scenario).mkdir(parents=True, exist_ok=True)
            _write_summary(root, "rho_0_03", "both_sam", eval_return=1.0, cumulative_return=2.0)

            scenarios = resolve_scenarios(root)

            self.assertEqual(
                scenarios,
                [
                    ("equal", 0.01),
                    ("non_equal", 0.02),
                    ("dynamic", 0.05),
                ],
            )


if __name__ == "__main__":
    unittest.main()
