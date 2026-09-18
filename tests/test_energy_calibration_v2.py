import csv
import json
import tempfile
import unittest
from pathlib import Path

from scripts.calibrate_greenran_energy_model import calibrate
from scripts.revaluate_tasam_shadow import revaluate
from src.energy_calibration import integrate_native_energy_samples, load_calibration
from src.resource_index import resource_index


class EnergyCalibrationV2Tests(unittest.TestCase):
    def test_simulation_revaluation_excludes_hardware_metrics(self):
        db = Path("runs/tasam_local_causal_pilot_seed47_20260906_v11/shadow_300/rapp_data_lake.db")
        calibration = Path("runs/energy_calibration_sim_v2_from_v5.json")
        if not db.is_file() or not calibration.is_file():
            self.skipTest("v11/calibração v2 local indisponível")
        with tempfile.TemporaryDirectory() as tmp:
            payload = revaluate(db, Path(tmp) / "simulation_revaluation.json", calibration)
        self.assertEqual(payload["schema"], "greenran.tasam.shadow.simulation_revaluation.v1")
        self.assertEqual(payload["metric_scope"], "simulation")
        self.assertNotIn("resource_index", payload)
        self.assertNotIn("infrastructure", payload)
        self.assertIn("allocation", payload["simulation_metrics"])
        self.assertIn("energy", payload["simulation_metrics"])
        self.assertEqual(payload["decisions"], 300)

    def test_native_integration_and_resource_index(self):
        result = integrate_native_energy_samples([
            {"timestamp_s": 0, "energy_j": 0},
            {"timestamp_s": 2, "energy_j": 10},
        ])
        self.assertTrue(result["valid"])
        self.assertEqual(result["energy_j"], 10)
        current = {"totals": {"cpu_usage_usec": 50, "memory_byte_seconds": 200, "io_bytes": 300}}
        baseline = {"totals": {"cpu_usage_usec": 100, "memory_byte_seconds": 200, "io_bytes": 300}}
        index = resource_index(current, baseline)
        self.assertTrue(index["valid"])
        self.assertAlmostEqual(index["index"], 0.75)
        self.assertAlmostEqual(index["saving_fraction"], 0.25)

    def test_v2_load_preserves_physical_invalidity(self):
        calibration = load_calibration(Path("config/energy_calibration_sim_v2.json"))
        self.assertEqual(calibration["schema"], "greenran.energy_calibration.v2")
        self.assertFalse(calibration["absolute_scale_valid"])
        self.assertFalse(calibration["physical_wattmeter_available"])

    def test_fit_corpus_is_fail_closed_when_components_are_not_identifiable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "corpus"
            for seed in (45, 46, 47):
                for power in (25, 50, 75, 100):
                    for active in (1, 2, 3):
                        cell = root / f"seed_{seed:04d}" / f"power_{power:03d}_active_{active}" / "cells" / "cell"
                        energy = cell / "ns3_energy"
                        energy.mkdir(parents=True)
                        (cell / "calibration_manifest.json").write_text(json.dumps({"seed": seed, "power_percent": power, "active_cells": active}), encoding="utf-8")
                        for idx in (2, 3, 4):
                            with (energy / f"energyfilecell{idx}.csv").open("w", newline="", encoding="utf-8") as handle:
                                writer = csv.writer(handle)
                                writer.writerow(["Time", "NetEnergy", "TxPowerPercent", "ActiveCell"])
                                writer.writerow([0, 0, power, 1 if idx - 2 < active else 0])
                                writer.writerow([5, active * (5 + power / 100), power, 1 if idx - 2 < active else 0])
                                writer.writerow([10, active * (10 + power / 100), power, 1 if idx - 2 < active else 0])
            output = Path(tmp) / "v2.json"
            payload = calibrate(root, output, binary=Path("config/energy_calibration.json"), scenario=Path("config/greenran_fixed_scenario.json"), config=Path("config/greenran_fixed_scenario.json"))
            self.assertEqual(payload["status"], "experimental_combined_model")
            self.assertFalse(payload["calibration_rank_valid"])


if __name__ == "__main__":
    unittest.main()
