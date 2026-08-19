#!/usr/bin/env python3
"""Monitor multiple parallel online TA-SAM runs in one terminal view."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from statistics import mean
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = ROOT / "runs" / "sac_bootstrap" / "online_tasam_parallel"


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _format_age(path: Path | None) -> str:
    if path is None or not path.exists():
        return "--"
    age = max(0.0, time.time() - path.stat().st_mtime)
    if age < 60:
        return f"{age:.0f}s"
    if age < 3600:
        return f"{age / 60:.1f}m"
    return f"{age / 3600:.1f}h"


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _load_history(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    except Exception:
        return []
    return rows


def _recent_mean(values: list[float], window: int) -> float:
    if not values:
        return 0.0
    return mean(values[-window:])


def _discover_seed_dirs(root: Path) -> list[Path]:
    manifest = root / "parallel_launch_manifest.json"
    payload = _read_json(manifest) or {}
    plan = payload.get("plan") or []
    if plan:
        dirs = []
        for item in plan:
            output_dir = Path(str(item.get("output_dir", "")).strip())
            if output_dir:
                dirs.append(output_dir)
        if dirs:
            return sorted(dirs)
    return sorted([path for path in root.glob("seed_*") if path.is_dir()])


def _seed_snapshot(seed_dir: Path) -> dict[str, Any]:
    history_path = seed_dir / "online_tasam_marl_history.jsonl"
    resume_path = seed_dir / "online_tasam_marl_resume.pt"
    summary_path = seed_dir / "online_tasam_marl_summary.json"
    rows = _load_history(history_path) if history_path.exists() else []

    if not rows:
        return {
            "seed": seed_dir.name,
            "status": "waiting",
            "history_age": _format_age(history_path if history_path.exists() else None),
            "checkpoint": resume_path.exists(),
            "summary": summary_path.exists(),
            "recent": [],
        }

    returns = [_safe_float(row.get("episode_return")) for row in rows]
    last = rows[-1]
    train_rows = [row for row in rows if "critic_loss" in row]
    train_last = train_rows[-1] if train_rows else {}
    recent = rows[-3:]

    return {
        "seed": seed_dir.name,
        "status": "active" if history_path.exists() and (time.time() - history_path.stat().st_mtime) < 180 else "stalled",
        "history_age": _format_age(history_path),
        "checkpoint": resume_path.exists(),
        "summary": summary_path.exists(),
        "episode": int(last.get("episode", 0) or 0),
        "step": int(last.get("global_step", 0) or 0),
        "ret": _safe_float(last.get("episode_return")),
        "avg5": _recent_mean(returns, 5),
        "avg10": _recent_mean(returns, 10),
        "critic": _safe_float(train_last.get("critic_loss")),
        "alpha": _safe_float(train_last.get("alpha")),
        "rho": _safe_float(train_last.get("rho_actor")),
        "td": _safe_float(train_last.get("td_var_mean")),
        "eval_proxy": _safe_float(train_last.get("eval_return")),
        "recent": recent,
    }


def _render(root: Path, started_at: float) -> None:
    seed_dirs = _discover_seed_dirs(root)
    os.system("clear" if os.name == "posix" else "cls")
    print("=" * 110)
    print("  GREENRAN TA-SAM - MONITOR PARALELO")
    print("=" * 110)
    print(f"  Root: {root}")
    print(f"  Tempo monitorando: {_format_age(Path('/tmp') if started_at else None) if False else _format_elapsed(started_at)}")
    print("-" * 110)
    print("  seed       status   age   ep    step   ret     avg5    avg10   critic   alpha   rho     td_var  ckpt")
    print("-" * 110)

    if not seed_dirs:
        print("  nenhum seed_* encontrado ainda")
        print("=" * 110)
        return

    snapshots = [_seed_snapshot(seed_dir) for seed_dir in seed_dirs]
    for snap in snapshots:
        if snap["status"] == "waiting":
            print(
                "  {seed:<10} {status:<8} {age:<5} {ep:>4}  {step:>6}  {ret:>6}  {avg5:>6}  {avg10:>6}  {critic:>7}  {alpha:>6}  {rho:>6}  {td:>7}  {ckpt}".format(
                    seed=snap["seed"],
                    status=snap["status"],
                    age=snap["history_age"],
                    ep="-",
                    step="-",
                    ret="-",
                    avg5="-",
                    avg10="-",
                    critic="-",
                    alpha="-",
                    rho="-",
                    td="-",
                    ckpt="yes" if snap["checkpoint"] else "no",
                )
            )
            continue
        print(
            "  {seed:<10} {status:<8} {age:<5} {ep:>4}  {step:>6}  {ret:>6.2f}  {avg5:>6.2f}  {avg10:>6.2f}  {critic:>7.3f}  {alpha:>6.3f}  {rho:>6.3f}  {td:>7.3f}  {ckpt}".format(
                seed=snap["seed"],
                status=snap["status"],
                age=snap["history_age"],
                ep=snap["episode"],
                step=snap["step"],
                ret=snap["ret"],
                avg5=snap["avg5"],
                avg10=snap["avg10"],
                critic=snap["critic"],
                alpha=snap["alpha"],
                rho=snap["rho"],
                td=snap["td"],
                ckpt="yes" if snap["checkpoint"] else "no",
            )
        )

    print("-" * 110)
    for snap in snapshots:
        print(f"  [{snap['seed']}] recentes")
        recent = snap.get("recent") or []
        if not recent:
            print("    sem histórico ainda")
            continue
        for row in recent:
            print(
                "    ep={episode:>4} step={step:>6} ret={ret:>6.2f} critic={critic:>7.3f} alpha={alpha:>6.3f} rho={rho:>6.3f}".format(
                    episode=int(row.get("episode", 0) or 0),
                    step=int(row.get("global_step", 0) or 0),
                    ret=_safe_float(row.get("episode_return")),
                    critic=_safe_float(row.get("critic_loss")),
                    alpha=_safe_float(row.get("alpha")),
                    rho=_safe_float(row.get("rho_actor")),
                )
            )
    print("=" * 110)


def _format_elapsed(started_at: float) -> str:
    elapsed = max(0, int(time.time() - started_at))
    minutes, seconds = divmod(elapsed, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h{minutes:02d}m{seconds:02d}s"
    return f"{minutes}m{seconds:02d}s"


def main() -> int:
    parser = argparse.ArgumentParser(description="Monitor multiple online TA-SAM seeds")
    parser.add_argument("--root", default=str(DEFAULT_ROOT), help="Parallel output root")
    parser.add_argument("--refresh", type=int, default=5, help="Refresh interval in seconds")
    parser.add_argument("--once", action="store_true", help="Render one snapshot and exit")
    args = parser.parse_args()

    root = Path(args.root)
    started_at = time.time()
    try:
        while True:
            _render(root, started_at)
            if args.once:
                return 0
            time.sleep(max(1, int(args.refresh)))
    except KeyboardInterrupt:
        print("\nMonitor encerrado.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
