#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "runs" / "experimentos_conflitos"
DEFAULT_SCENARIOS = (
    "vehicle_warning",
    "vehicle_critical",
    "vehicle_implicito",
    "vehicle_recovery",
)
DEFAULT_TARGET_ROWS_PER_SCENARIO = 450


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Monitor dedicated App3 vehicle conflict collection progress.")
    parser.add_argument(
        "--experiment-dir",
        default="",
        help="vehicle collection experiment directory; defaults to the latest *_conflict_protocol run",
    )
    parser.add_argument(
        "--output-root",
        default=str(DEFAULT_OUTPUT_ROOT),
        help="root directory that contains conflict protocol runs",
    )
    parser.add_argument(
        "--target-rows-per-scenario",
        type=int,
        default=DEFAULT_TARGET_ROWS_PER_SCENARIO,
        help="expected vehicle rows per scenario",
    )
    return parser.parse_args()


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def latest_experiment(output_root: Path) -> Path:
    candidates = sorted(
        [path for path in output_root.iterdir() if path.is_dir() and path.name.endswith("_conflict_protocol")],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise SystemExit(f"no conflict protocol runs found under {output_root}")
    return candidates[0]


def scenario_progress(experiment_dir: Path, scenario: str) -> dict:
    rounds_dir = experiment_dir / scenario / "rounds"
    rows = 0
    rounds = 0
    last_summary = {}
    if rounds_dir.exists():
        for path in sorted(rounds_dir.glob("round_*/round_summary.json")):
            summary = read_json(path)
            if not summary:
                continue
            rows += int(summary.get("rows", 0) or 0)
            rounds += 1
            last_summary = summary
    return {
        "scenario": scenario,
        "rows": rows,
        "rounds": rounds,
        "last_end_iso": last_summary.get("end_iso", ""),
        "last_confirmed": int(last_summary.get("confirmed_by_data", 0) or 0),
        "last_weak": int(last_summary.get("weak_or_low_support", 0) or 0),
    }


def main() -> int:
    args = parse_args()
    output_root = Path(args.output_root).resolve()
    experiment_dir = Path(args.experiment_dir).resolve() if args.experiment_dir else latest_experiment(output_root)
    control = read_json(Path("/tmp/article00_scenario_control.json"))
    app3_snapshot = read_json(Path("/tmp/app3_veicular/monitoring_snapshot.json"))

    progress = [scenario_progress(experiment_dir, scenario) for scenario in DEFAULT_SCENARIOS]
    target_rows_per_scenario = int(args.target_rows_per_scenario)
    target_total_rows = target_rows_per_scenario * len(DEFAULT_SCENARIOS)
    total_rows = sum(item["rows"] for item in progress)

    print(f"Experimento: {experiment_dir}")
    print(
        f"Ativo agora: {control.get('scenario', 'desconhecido')} | "
        f"rodada {control.get('round', '?')}/{control.get('rounds_total', '?')}"
    )
    print(f"Total: {total_rows}/{target_total_rows} rows")
    print("")

    for item in progress:
        percent = (item["rows"] / target_rows_per_scenario * 100.0) if target_rows_per_scenario > 0 else 0.0
        print(
            f"{item['scenario']}: {item['rows']}/{target_rows_per_scenario} rows "
            f"({percent:.1f}%) | rodadas={item['rounds']} | "
            f"último confirmed={item['last_confirmed']} weak={item['last_weak']}"
        )

    vehicles = app3_snapshot.get("vehicles", {}) if isinstance(app3_snapshot, dict) else {}
    print("")
    print(
        "App3 live: "
        f"total={int(vehicles.get('total_vehicles', 0) or 0)} | "
        f"high_risk={int(vehicles.get('high_risk_vehicles', 0) or 0)} | "
        f"medium_risk={int(vehicles.get('medium_risk_vehicles', 0) or 0)} | "
        f"degraded={int(vehicles.get('degraded_autonomy_vehicles', 0) or 0)} | "
        f"latency={float(vehicles.get('max_latency_ms', 0.0) or 0.0):.1f}ms | "
        f"loss={float(vehicles.get('max_packet_loss_percent', 0.0) or 0.0):.1f}%"
    )
    print(f"Snapshot App3: {app3_snapshot.get('simulation', {}).get('timestamp_iso', 'desconhecido')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
