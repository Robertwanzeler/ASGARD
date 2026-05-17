from __future__ import annotations

import json
from pathlib import Path


def load_json(path: Path) -> dict:
    with path.open() as f:
        return json.load(f)


def load_scenario_reports(experiment_dir: Path) -> list[dict]:
    report_path = experiment_dir / "experiment_report.json"
    if not report_path.exists():
        raise FileNotFoundError(f"experiment_report.json not found under {experiment_dir}")

    aggregate_report = load_json(report_path)
    aggregate_reports = list(aggregate_report.get("scenario_reports", []) or [])
    by_scenario = {
        entry.get("scenario"): entry
        for entry in aggregate_reports
        if entry.get("scenario")
    }

    for scenario_dir in sorted(path for path in experiment_dir.iterdir() if path.is_dir()):
        scenario_report_path = scenario_dir / "scenario_report.json"
        if not scenario_report_path.exists():
            continue
        scenario_report = load_json(scenario_report_path)
        scenario = scenario_report.get("scenario")
        if not scenario or scenario in by_scenario:
            continue
        by_scenario[scenario] = scenario_report

    return list(by_scenario.values())
