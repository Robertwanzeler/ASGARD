#!/usr/bin/env python3
"""Re-evaluate an existing GreenRAN campaign with calibrated RU/mmWave energy."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from evaluate_tasam_network_campaign import aggregate, pair_result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-report", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--energy-calibration", type=Path)
    args = parser.parse_args()

    source = json.loads(args.source_report.resolve().read_text(encoding="utf-8"))
    source_pairs = source.get("pairs") or []
    if not source_pairs:
        raise SystemExit("relatório de origem não contém pares")

    output_root = args.output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    pairs = []
    total = len(source_pairs)
    for index, old_pair in enumerate(source_pairs, start=1):
        baseline_dir = Path(old_pair["baseline"]["run_dir"])
        assistant_dir = Path(old_pair["assistant"]["run_dir"])
        seed = int(old_pair.get("seed", 0))
        repetition = int(old_pair.get("repetition", index))
        profile = str(old_pair.get("scenario_profile", ""))
        model_tag = next(
            (part for part in assistant_dir.parts if part.startswith("model_")),
            "model_unknown",
        )
        pair_dir = output_root / model_tag / f"scenario_{seed}" / f"rep_{repetition}"
        pair_path = pair_dir / "network_pair.json"
        if pair_path.is_file():
            try:
                saved = json.loads(pair_path.read_text(encoding="utf-8"))
                saved_pair = (saved.get("pairs") or [None])[0]
                if isinstance(saved_pair, dict) and saved_pair.get("valid"):
                    pairs.append(saved_pair)
                    print(f"[{index}/{total}] seed={seed} rep={repetition} já reavaliado; reutilizando", flush=True)
                    continue
            except (OSError, json.JSONDecodeError, TypeError, IndexError):
                pass
        print(f"[{index}/{total}] seed={seed} rep={repetition} lendo bancos", flush=True)
        fresh = pair_result(
            baseline_dir,
            assistant_dir,
            seed,
            repetition,
            profile,
            allow_metric_gap=True,
            calibration_path=args.energy_calibration,
        )
        pair_dir.mkdir(parents=True, exist_ok=True)
        (pair_dir / "network_pair.json").write_text(
            json.dumps({"pairs": [fresh]}, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        pairs.append(fresh)
        print(
            f"    valid={fresh['valid']} energy_saving={fresh['deltas'].get('energy_relative_saving', 0.0):+.4%} "
            f"composite={fresh['deltas']['composite_index']:+.6f}",
            flush=True,
        )

    report = {
        "schema": "greenran.tasam_calibrated_energy_revaluation.v1",
        "source_report": str(args.source_report.resolve()),
        "energy_policy": {
            "kind": "calibrated_ru_mmwave_power_model",
            "calibration_path": str(args.energy_calibration.resolve()) if args.energy_calibration else None,
            "physical_meter_available": False,
        },
        "pairs": pairs,
        "aggregate": aggregate(pairs),
    }
    report_path = output_root / "calibrated_energy_report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(report_path), "aggregate": report["aggregate"]}, ensure_ascii=False, indent=2))
    return 0 if report["aggregate"]["approved"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
