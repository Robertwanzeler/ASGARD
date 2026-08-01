#!/usr/bin/env python3
"""Run repeated 25-epoch TA-SAM offline rounds until metrics plateau."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from summarize_tasam_runs import MODES, load_summary, row_for


ROOT = Path(__file__).resolve().parents[1]
OFFICIAL_COLLECTION_DB = ROOT / "runs" / "tasam_article_ns3_collection" / "rapp_data_lake.db"
DEFAULT_OUTPUT = ROOT / "runs" / "tasam_article_reproduction" / "offline_rounds"


def resolve_default_db() -> Path:
    for env_name in ("GREENRAN_TASAM_ACTIVE_DB", "GREENRAN_DB_PATH"):
        raw = os.environ.get(env_name, "").strip()
        if raw:
            return Path(raw)
    return OFFICIAL_COLLECTION_DB


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run repeated TA-SAM offline rounds until convergence")
    parser.add_argument("--db", default=str(resolve_default_db()), help="ns-3 article collection SQLite DB")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT), help="Rounds output directory")
    parser.add_argument("--epochs", type=int, default=25, help="Training epochs per round")
    parser.add_argument("--modes", default=",".join(MODES), help="Comma-separated modes to train")
    parser.add_argument("--limit", type=int, default=None, help="Optional transition limit for smoke runs")
    parser.add_argument("--allow-proxy", action="store_true", help="Legacy compatibility option for exploratory runs")
    parser.add_argument("--seed", type=int, default=42, help="Deterministic seed")
    parser.add_argument("--train-python", default=None, help="Training Python executable")
    parser.add_argument("--plateau-rounds", type=int, default=3, help="Consecutive plateau rounds required for convergence")
    parser.add_argument(
        "--improvement-threshold-pct",
        type=float,
        default=2.0,
        help="Minimum eval_return improvement percentage required to count as a new gain",
    )
    parser.add_argument(
        "--max-rounds",
        type=int,
        default=0,
        help="Maximum rounds to run; 0 means keep running until convergence",
    )
    parser.add_argument("--sleep-seconds", type=float, default=0.0, help="Optional sleep between rounds")
    parser.add_argument("--dry-run", action="store_true", help="Print the next round command without executing it")
    return parser


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _round_dir(output_root: Path, index: int) -> Path:
    return output_root / f"round_{index:04d}"


def _iter_round_dirs(output_root: Path) -> list[Path]:
    return sorted(
        (
            path
            for path in output_root.glob("round_*")
            if path.is_dir() and path.name[6:].isdigit()
        ),
        key=lambda path: int(path.name[6:]),
    )


def _load_export_summary(round_dir: Path) -> dict[str, Any]:
    path = round_dir / "tasam_article_export_summary.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _load_mode_rows(round_dir: Path, modes: tuple[str, ...]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for mode in modes:
        summary = load_summary(round_dir / mode)
        if summary:
            rows.append(row_for(mode, summary))
    return rows


def _best_row(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None
    return max(
        rows,
        key=lambda row: (
            _safe_float(row.get("eval_return"), 0.0),
            _safe_float(row.get("cumulative_return"), 0.0),
        ),
    )


def _improvement_pct(current: float, previous: float) -> float:
    current = _safe_float(current, 0.0)
    previous = _safe_float(previous, 0.0)
    if previous == 0.0:
        return 100.0 if current > 0.0 else 0.0
    return ((current - previous) / abs(previous)) * 100.0


def _stable_against_previous(current_best: dict[str, Any], previous_best: dict[str, Any] | None) -> bool:
    if previous_best is None:
        return True
    current_critic = _safe_float(current_best.get("critic_loss"), 0.0)
    previous_critic = max(_safe_float(previous_best.get("critic_loss"), 0.0), 1e-12)
    current_action_var = _safe_float(current_best.get("action_var_mean"), 0.0)
    previous_action_var = _safe_float(previous_best.get("action_var_mean"), 0.0)
    current_selected = _safe_float(current_best.get("selected_fraction"), 0.0)

    critic_ok = current_critic <= previous_critic * 1.5
    action_var_ok = previous_action_var <= 0.0 or current_action_var >= previous_action_var * 0.5
    selected_ok = current_selected > 0.0
    return critic_ok and action_var_ok and selected_ok


def _evaluate_rounds(output_root: Path, modes: tuple[str, ...], improvement_threshold_pct: float, plateau_rounds: int) -> dict[str, Any]:
    evaluated_rounds: list[dict[str, Any]] = []
    best_eval_so_far: dict[str, Any] | None = None
    previous_best_row: dict[str, Any] | None = None
    plateau_streak = 0

    for round_dir in _iter_round_dirs(output_root):
        export_summary = _load_export_summary(round_dir)
        mode_rows = _load_mode_rows(round_dir, modes)
        best_row = _best_row(mode_rows)
        if best_row is None:
            continue

        current_eval = _safe_float(best_row.get("eval_return"), 0.0)
        previous_best_eval = _safe_float((best_eval_so_far or {}).get("eval_return"), 0.0)
        improvement_pct = _improvement_pct(current_eval, previous_best_eval)
        stable = _stable_against_previous(best_row, previous_best_row)

        if best_eval_so_far is None:
            status = "improving"
            plateau_streak = 0
        elif not stable:
            status = "unstable"
            plateau_streak = 0
        elif current_eval > previous_best_eval and improvement_pct >= improvement_threshold_pct:
            status = "improving"
            plateau_streak = 0
        else:
            plateau_streak += 1
            status = "plateau"

        round_payload = {
            "round_id": round_dir.name,
            "round_index": int(round_dir.name[6:]),
            "round_dir": str(round_dir),
            "written_transitions": int(export_summary.get("written_transitions", 0) or 0),
            "candidate_snapshots": int(export_summary.get("candidate_snapshots", 0) or 0),
            "best_mode": best_row.get("mode", ""),
            "best_checkpoint_dir": str(round_dir / str(best_row.get("mode", ""))),
            "best_metrics": {
                "eval_return": current_eval,
                "cumulative_return": _safe_float(best_row.get("cumulative_return"), 0.0),
                "critic_loss": _safe_float(best_row.get("critic_loss"), 0.0),
                "action_var_mean": _safe_float(best_row.get("action_var_mean"), 0.0),
                "selected_fraction": _safe_float(best_row.get("selected_fraction"), 0.0),
            },
            "mode_rows": mode_rows,
            "status": status,
            "stable_vs_previous": stable,
            "improvement_pct_vs_best": improvement_pct,
            "plateau_streak": plateau_streak,
        }
        evaluated_rounds.append(round_payload)

        if best_eval_so_far is None or current_eval > _safe_float(best_eval_so_far.get("eval_return"), 0.0):
            best_eval_so_far = best_row
        previous_best_row = best_row

    converged = bool(evaluated_rounds) and plateau_streak >= plateau_rounds
    if converged and evaluated_rounds[-1]["status"] == "plateau":
        evaluated_rounds[-1]["status"] = "converged"

    best_round = None
    if evaluated_rounds:
        best_round = max(
            evaluated_rounds,
            key=lambda row: (
                _safe_float((row.get("best_metrics") or {}).get("eval_return"), 0.0),
                _safe_float((row.get("best_metrics") or {}).get("cumulative_return"), 0.0),
            ),
        )

    return {
        "schema": "greenran.tasam_offline_rounds.v1",
        "output_root": str(output_root),
        "round_count": len(evaluated_rounds),
        "plateau_rounds_required": plateau_rounds,
        "improvement_threshold_pct": improvement_threshold_pct,
        "plateau_streak": plateau_streak,
        "converged": converged,
        "best_round": best_round,
        "rounds": evaluated_rounds,
    }


def _write_summary(output_root: Path, payload: dict[str, Any]) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "offline_rounds_summary.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _build_round_command(args: argparse.Namespace, round_dir: Path) -> list[str]:
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "run_tasam_article_reproduction.py"),
        "--db",
        str(args.db),
        "--output-root",
        str(round_dir),
        "--epochs",
        str(args.epochs),
        "--modes",
        args.modes,
        "--seed",
        str(args.seed),
    ]
    if args.limit is not None:
        cmd.extend(["--limit", str(args.limit)])
    if args.allow_proxy:
        cmd.append("--allow-proxy")
    if args.train_python:
        cmd.extend(["--train-python", str(args.train_python)])
    return cmd


def main() -> int:
    args = build_parser().parse_args()
    output_root = Path(args.output_root)
    modes = tuple(mode.strip() for mode in args.modes.split(",") if mode.strip())
    invalid = sorted(set(modes) - set(MODES))
    if invalid:
        raise SystemExit(f"Invalid modes: {invalid}. Valid modes: {MODES}")

    summary = _evaluate_rounds(output_root, modes, args.improvement_threshold_pct, args.plateau_rounds)
    _write_summary(output_root, summary)

    rounds_run = 0
    while not summary["converged"]:
        if args.max_rounds > 0 and rounds_run >= args.max_rounds:
            break
        next_index = len(_iter_round_dirs(output_root)) + 1
        round_dir = _round_dir(output_root, next_index)
        cmd = _build_round_command(args, round_dir)
        print(" ".join(cmd), flush=True)
        if args.dry_run:
            break
        subprocess.run(cmd, cwd=ROOT, check=True)
        rounds_run += 1
        summary = _evaluate_rounds(output_root, modes, args.improvement_threshold_pct, args.plateau_rounds)
        _write_summary(output_root, summary)
        best_round = summary.get("best_round") or {}
        print(
            json.dumps(
                {
                    "round_count": summary.get("round_count", 0),
                    "plateau_streak": summary.get("plateau_streak", 0),
                    "converged": summary.get("converged", False),
                    "best_round": best_round.get("round_id", ""),
                    "best_mode": best_round.get("best_mode", ""),
                    "best_eval_return": ((best_round.get("best_metrics") or {}).get("eval_return")),
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        if summary["converged"]:
            break
        if args.sleep_seconds > 0:
            time.sleep(args.sleep_seconds)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
