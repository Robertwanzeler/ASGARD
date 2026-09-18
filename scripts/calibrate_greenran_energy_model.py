#!/usr/bin/env python3
"""Fit and validate the relative native-ns-3 GreenRAN energy corpus."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
from typing import Any


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _num(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return parsed if math.isfinite(parsed) else float("nan")


def read_native_run(run_dir: Path) -> list[dict[str, float]]:
    manifest = json.loads((run_dir / "calibration_manifest.json").read_text(encoding="utf-8"))
    power = _num(manifest.get("power_percent"))
    active = _num(manifest.get("active_cells"))
    if not (math.isfinite(power) and 25 <= power <= 100 and math.isfinite(active) and active >= 1):
        raise ValueError(f"manifesto inválido: {run_dir}")
    rows: list[dict[str, float]] = []
    files = sorted((run_dir / "ns3_energy").glob("energyfilecell*.csv"))
    if len(files) < 3:
        raise ValueError(f"menos de três séries nativas: {run_dir}")
    for path in files:
        with path.open(newline="", encoding="utf-8") as handle:
            samples = list(csv.DictReader(handle))
        if len(samples) < 3:
            raise ValueError(f"série nativa incompleta: {path}")
        times = [_num(row.get("Time")) for row in samples]
        energies = [_num(row.get("NetEnergy")) for row in samples]
        observed_power = [_num(row.get("TxPowerPercent")) for row in samples if row.get("TxPowerPercent") not in (None, "")]
        observed_active = [_num(row.get("ActiveCell")) for row in samples if row.get("ActiveCell") not in (None, "")]
        if any(not math.isfinite(value) for value in times + energies) or times[-1] <= times[0]:
            raise ValueError(f"timestamps/energia inválidos: {path}")
        if not observed_power or any(abs(value - power) > 1e-6 for value in observed_power):
            raise ValueError(f"potência solicitada não observada: {path}")
        if not observed_active or any(value not in (0.0, 1.0) for value in observed_active):
            raise ValueError(f"estado ativo ausente/inválido: {path}")
        delta = max(0.0, energies[-1] - energies[0])
        duration = times[-1] - times[0]
        rows.append({
            "seed": _num(manifest.get("seed")),
            "power_percent": power,
            "active_cells": active,
            "native_sim_energy_j": delta,
            "native_sim_power_w": delta / duration,
            "duration_s": duration,
            "active_observed": observed_active[-1],
        })
    observed_cells = sum(1 for row in rows if row.get("active_observed", 0.0) > 0.5)
    if observed_cells != int(active):
        raise ValueError(f"contagem de células ativa inconsistente: {run_dir}")
    return rows


def _fit(rows: list[dict[str, float]]) -> dict[str, Any]:
    # Per-cell model y = idle + dynamic*x, x=power/100.  The nonnegative
    # slope/intercept projection preserves monotonicity without scipy.
    active_rows = [row for row in rows if row.get("active_observed", 0.0) > 0.5]
    x = [row["power_percent"] / 100.0 for row in active_rows]
    y = [row["native_sim_power_w"] for row in active_rows]
    xbar, ybar = sum(x) / len(x), sum(y) / len(y)
    denominator = sum((value - xbar) ** 2 for value in x)
    slope = sum((a - xbar) * (b - ybar) for a, b in zip(x, y)) / denominator if denominator else 0.0
    slope = max(0.0, slope)
    intercept = max(0.0, ybar - slope * xbar)
    predictions = [intercept + slope * value for value in x]
    residuals = [actual - predicted for actual, predicted in zip(y, predictions)]
    sse = sum(value * value for value in residuals)
    sst = sum((value - ybar) ** 2 for value in y)
    rmse = math.sqrt(sse / len(y))
    r2 = 1.0 - sse / sst if sst > 0 else 0.0
    max_error = max((abs(error) / abs(actual) * 100.0) if actual else 0.0 for error, actual in zip(residuals, y))
    by_power: dict[float, float] = {}
    for row in active_rows:
        by_power.setdefault(row["power_percent"], 0.0)
        by_power[row["power_percent"]] += row["native_sim_power_w"] / row["active_cells"]
    means = [by_power[key] / sum(1 for row in active_rows if row["power_percent"] == key) for key in sorted(by_power)]
    monotonic = all(b + 1e-12 >= a for a, b in zip(means, means[1:]))
    return {
        "idle_w": intercept,
        "dynamic_w": slope,
        "active_w": intercept + slope,
        "rmse_w": rmse,
        "r2": r2,
        "max_abs_percent_error": max_error,
        "monotonic": monotonic,
        "rank_valid": len(set(x)) >= 2 and len(active_rows) >= 3,
    }


def calibrate(corpus: Path, output: Path, *, binary: Path, scenario: Path, config: Path) -> dict[str, Any]:
    run_dirs = sorted(path for path in corpus.glob("seed_*/*/cells/*") if (path / "calibration_manifest.json").is_file())
    rows = [row for path in run_dirs for row in read_native_run(path)]
    if len(run_dirs) != 36:
        raise ValueError(f"corpus incompleto: {len(run_dirs)}/36 células")
    fit = _fit(rows)
    # The current sweep varies active mmWave cells only.  It estimates the
    # combined radio model but cannot identify independent RU/mmWave terms.
    component_identification = False
    accepted = fit["r2"] >= 0.90 and fit["max_abs_percent_error"] <= 10.0 and fit["monotonic"] and component_identification
    payload = {
        "schema": "greenran.energy_calibration.v2",
        "calibration_version": "sim_native_v2_36cell",
        "status": "promoted_for_relative_simulation" if accepted else "experimental_combined_model",
        "energy_reference_source": "ns3_device_energy_model",
        "absolute_scale_valid": False,
        "physical_wattmeter_available": False,
        "calibration_corpus_id": corpus.name,
        "calibration_rank_valid": bool(accepted),
        "fit_error": fit["rmse_w"],
        "validation": {key: fit[key] for key in ("rmse_w", "r2", "max_abs_percent_error", "monotonic")},
        "seeds": [45, 46, 47],
        "power_percent": [25, 50, 75, 100],
        "active_cells": [1, 2, 3],
        "components": {
            "ru": {"idle_w": 0.0, "active_w": 0.0, "source": "not_identified"},
            "mmwave": {"idle_w": fit["idle_w"], "active_w": fit["active_w"], "source": "combined_native_ns3_fit"},
        },
        "combined_model": fit,
        "artifacts": {"binary_sha256": _sha256(binary), "scenario_sha256": _sha256(scenario), "config_sha256": _sha256(config)},
        "limitations": [
            "Energia nativa do ns-3 é referência relativa de simulação, não consumo físico.",
            "A campanha atual não identifica RU e mmWave independentemente; promoção operacional bloqueada.",
            "CPU, memória e I/O permanecem em índice separado.",
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--scenario", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    payload = calibrate(args.corpus, args.output, binary=args.binary, scenario=args.scenario, config=args.config)
    print(json.dumps({"output": str(args.output), "status": payload["status"], "r2": payload["validation"]["r2"], "rank_valid": payload["calibration_rank_valid"]}, ensure_ascii=False))
    return 0 if payload["status"] == "promoted_for_relative_simulation" else 2


if __name__ == "__main__":
    raise SystemExit(main())
