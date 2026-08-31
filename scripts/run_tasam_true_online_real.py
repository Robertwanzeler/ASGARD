#!/usr/bin/env python3
"""Continuously update TA-SAM from the real GreenRAN Data Lake.

This runner is intentionally 100% real-data:
  - reads the live SQLite Data Lake
  - exports an article-aligned MARL transition trace from real runtime history
  - performs small resumed TA-SAM updates whenever enough new snapshots arrive

It does NOT use any synthetic online environment or Markov generator.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "runs" / "tasam_article_ns3_collection" / "rapp_data_lake.db"
DEFAULT_OUTPUT_ROOT = ROOT / "runs" / "tasam_article_ns3_collection" / "tasam_true_online_real"
LEGACY_OUTPUT_ROOT = ROOT / "runs" / "tasam_true_online_real"
DEFAULT_TRAIN_PYTHON = ROOT / "drlexp" / ".venv" / "bin" / "python"


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run true online TA-SAM updates from the real GreenRAN Data Lake")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="SQLite Data Lake path")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT), help="Output root directory")
    parser.add_argument("--trace-jsonl", default=None, help="Exported real transition trace path")
    parser.add_argument("--export-summary-json", default=None, help="Export summary JSON path")
    parser.add_argument("--model-dir", default=None, help="TA-SAM output/checkpoint directory")
    parser.add_argument("--status-json", default=None, help="Status JSON path")
    parser.add_argument("--state-json", default=None, help="Persistent runner state JSON path")
    parser.add_argument("--train-python", default=None, help="Python executable used to run train_tasam_marl.py")
    parser.add_argument("--init-checkpoint-dir", default=os.environ.get("GREENRAN_TASAM_TRUE_ONLINE_INIT_CHECKPOINT"), help="Validated actor checkpoint used to initialize the first online update")
    parser.add_argument("--seed", type=int, default=_env_int("GREENRAN_TASAM_TRUE_ONLINE_SEED", 45), help="Seed for online updates")
    parser.add_argument("--min-new-snapshots", type=int, default=500, help="Minimum new marl_global_state_history rows required to trigger another update")
    parser.add_argument("--min-trainable-transitions", type=int, default=1500, help="Minimum exported valid transitions required before training")
    parser.add_argument("--bootstrap-epochs", type=int, default=5, help="Target epochs for the first real-data update")
    parser.add_argument("--epochs-per-update", type=int, default=2, help="How many extra epochs to add on each subsequent real-data update")
    parser.add_argument("--poll-seconds", type=float, default=60.0, help="Polling interval in seconds when running continuously")
    parser.add_argument(
        "--limit",
        type=int,
        default=_env_int("GREENRAN_TASAM_EXPORT_LIMIT", 20000),
        help="Optional export transition cap",
    )
    parser.add_argument("--since-ts", type=int, default=0, help="Optional lower timestamp bound for exports")
    parser.add_argument("--until-ts", type=int, default=0, help="Optional upper timestamp bound for exports")
    parser.add_argument("--include-invalid", action="store_true", help="Include invalid transitions in the export (disabled by default)")
    parser.add_argument("--max-step-gap-s", type=int, default=20, help="Maximum wall-clock timestamp gap for s->s' pairing")
    parser.add_argument("--max-sim-reset-gap-s", type=float, default=1.0, help="Maximum sim-time reset gap before transition invalidation")
    parser.add_argument("--once", action="store_true", help="Run a single poll/update cycle and exit")
    parser.add_argument("--dry-run", action="store_true", help="Print commands/state transitions without executing them")
    return parser


def load_json(path: Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {} if default is None else default
    return payload if isinstance(payload, dict) else ({} if default is None else default)


def save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _move_file_if_newer(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        src_mtime = src.stat().st_mtime
        dst_mtime = dst.stat().st_mtime
        if src_mtime <= dst_mtime:
            return
        if dst.is_dir():
            shutil.rmtree(dst)
        else:
            dst.unlink()
    shutil.move(str(src), str(dst))


def _merge_tree_if_newer(src: Path, dst: Path) -> None:
    if not src.exists():
        return
    if src.is_file():
        _move_file_if_newer(src, dst)
        return
    dst.mkdir(parents=True, exist_ok=True)
    for child in sorted(src.iterdir()):
        target = dst / child.name
        if child.is_dir():
            _merge_tree_if_newer(child, target)
            try:
                child.rmdir()
            except OSError:
                pass
            continue
        _move_file_if_newer(child, target)
    try:
        src.rmdir()
    except OSError:
        pass


def migrate_legacy_output_root(output_root: Path) -> None:
    if output_root != DEFAULT_OUTPUT_ROOT:
        return
    if not LEGACY_OUTPUT_ROOT.exists() or LEGACY_OUTPUT_ROOT == output_root:
        return
    for relative in (
        "true_online_status.json",
        "true_online_state.json",
        "tasam_true_online_real_export_summary.json",
        "tasam_true_online_real_trace.jsonl",
        "tasam_selective",
    ):
        src = LEGACY_OUTPUT_ROOT / relative
        dst = output_root / relative
        if src.exists():
            _merge_tree_if_newer(src, dst)
    try:
        LEGACY_OUTPUT_ROOT.rmdir()
    except OSError:
        pass


def count_runtime_rows(db_path: Path) -> dict[str, int]:
    counts = {
        "marl_global_state_history": 0,
        "marl_shadow_comparison_history": 0,
        "decisions_history": 0,
    }
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        for table in counts:
            try:
                counts[table] = int(cur.execute(f"select count(*) from {table}").fetchone()[0] or 0)
            except sqlite3.OperationalError:
                counts[table] = 0
    finally:
        conn.close()
    return counts


def should_train(
    *,
    current_snapshot_count: int,
    previous_snapshot_count: int,
    min_new_snapshots: int,
    target_epochs: int,
) -> tuple[bool, str]:
    if current_snapshot_count <= 0:
        return False, "no runtime snapshots available"
    if target_epochs <= 0:
        return False, "target epochs must be positive"
    if previous_snapshot_count <= 0:
        return True, "bootstrap update"
    delta = current_snapshot_count - previous_snapshot_count
    if delta < min_new_snapshots:
        return False, f"waiting for more real snapshots: +{delta} < +{min_new_snapshots}"
    return True, f"new snapshots ready: +{delta}"


def compute_target_epochs(previous_target_epochs: int, bootstrap_epochs: int, epochs_per_update: int) -> int:
    previous_target_epochs = max(int(previous_target_epochs or 0), 0)
    if previous_target_epochs <= 0:
        return max(int(bootstrap_epochs), 1)
    return previous_target_epochs + max(int(epochs_per_update), 1)


def build_export_command(args: argparse.Namespace) -> list[str]:
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "export_tasam_article_dataset.py"),
        "--db",
        str(args.db),
        "--output-jsonl",
        str(args.trace_jsonl),
        "--summary-json",
        str(args.export_summary_json),
        "--max-step-gap-s",
        str(args.max_step_gap_s),
        "--max-sim-reset-gap-s",
        str(args.max_sim_reset_gap_s),
    ]
    if args.limit:
        cmd.extend(["--limit", str(args.limit)])
    if args.since_ts:
        cmd.extend(["--since-ts", str(args.since_ts)])
    if args.until_ts:
        cmd.extend(["--until-ts", str(args.until_ts)])
    if args.include_invalid:
        cmd.append("--include-invalid")
    return cmd


def build_train_command(args: argparse.Namespace, target_epochs: int) -> list[str]:
    train_python = args.train_python or str(DEFAULT_TRAIN_PYTHON if DEFAULT_TRAIN_PYTHON.exists() else sys.executable)
    return [
        train_python,
        str(ROOT / "drlexp" / "training" / "train_tasam_marl.py"),
        "--trace-jsonl",
        str(args.trace_jsonl),
        "--output-dir",
        str(args.model_dir),
        "--epochs",
        str(target_epochs),
        "--trainer-backend",
        "article_sac",
        "--lr",
        "0.0001",
        "--alpha-lr",
        "0.0001",
        "--sam-mode",
        "tasam_selective",
        "--actor-sam-rho",
        "0.5",
        "--actor-sam-rho-final",
        "0.01",
        "--critic-sam-rho",
        "0.5",
        "--critic-sam-rho-final",
        "0.01",
        "--td-var-threshold",
        "0.01",
        "--warmup-epochs",
        "2",
        "--bc-weight",
        "0.0",
        "--value-weight",
        "0.0",
        "--gamma",
        "0.99",
        "--tau",
        "0.01",
        "--alpha-init",
        "0.03",
        "--target-entropy-scale",
        "1.0",
        "--batch-size",
        "128",
        "--seed",
        str(args.seed),
        "--article-hidden",
        "--activation",
        "tanh",
        "--resume",
        "--resume-ignore-early-stop",
    ]
    if args.init_checkpoint_dir:
        cmd.extend(["--init-checkpoint-dir", str(args.init_checkpoint_dir)])
    return cmd


def run_command(cmd: list[str], *, cwd: Path, dry_run: bool) -> None:
    print(" ".join(cmd), flush=True)
    if dry_run:
        return
    subprocess.run(cmd, cwd=cwd, check=True)


def load_export_summary(path: Path) -> dict[str, Any]:
    return load_json(path, default={})


def write_status(args: argparse.Namespace, payload: dict[str, Any]) -> None:
    status = dict(payload)
    status.setdefault("training_mode", "online")
    status.setdefault("replay_source", "live_sqlite")
    status.setdefault("min_trainable_transitions", int(args.min_trainable_transitions))
    status["updated_at"] = int(time.time())
    save_json(args.status_json, status)


def run_once(args: argparse.Namespace) -> dict[str, Any]:
    counts = count_runtime_rows(args.db)
    state = load_json(args.state_json, default={})
    previous_snapshot_count = int(state.get("last_snapshot_count", 0) or 0)
    previous_target_epochs = int(state.get("target_epochs", 0) or 0)
    target_epochs = compute_target_epochs(previous_target_epochs, args.bootstrap_epochs, args.epochs_per_update)

    ready, reason = should_train(
        current_snapshot_count=counts["marl_global_state_history"],
        previous_snapshot_count=previous_snapshot_count,
        min_new_snapshots=args.min_new_snapshots,
        target_epochs=target_epochs,
    )
    if not ready:
        payload = {
            "status": "idle",
            "reason": reason,
            "db": str(args.db),
            "counts": counts,
            "previous_snapshot_count": previous_snapshot_count,
            "target_epochs_next": target_epochs,
            "model_dir": str(args.model_dir),
        }
        write_status(args, payload)
        return payload

    args.output_root.mkdir(parents=True, exist_ok=True)
    export_cmd = build_export_command(args)
    run_command(export_cmd, cwd=ROOT, dry_run=args.dry_run)
    export_summary = load_export_summary(args.export_summary_json) if not args.dry_run else {}
    written_transitions = int(export_summary.get("written_transitions", 0) or 0)
    if not args.dry_run and written_transitions < int(args.min_trainable_transitions):
        payload = {
            "status": "waiting_dataset",
            "reason": f"written transitions below threshold: {written_transitions} < {args.min_trainable_transitions}",
            "db": str(args.db),
            "counts": counts,
            "written_transitions": written_transitions,
            "target_epochs_next": target_epochs,
            "trace_jsonl": str(args.trace_jsonl),
        }
        write_status(args, payload)
        return payload

    write_status(
        args,
        {
            "status": "training",
            "reason": reason,
            "db": str(args.db),
            "counts": counts,
            "written_transitions": written_transitions,
            "target_epochs": target_epochs,
            "trace_jsonl": str(args.trace_jsonl),
            "model_dir": str(args.model_dir),
            "dry_run": bool(args.dry_run),
        },
    )

    train_cmd = build_train_command(args, target_epochs)
    run_command(train_cmd, cwd=ROOT, dry_run=args.dry_run)

    new_state = {
        "db": str(args.db),
        "last_snapshot_count": counts["marl_global_state_history"],
        "last_shadow_count": counts["marl_shadow_comparison_history"],
        "last_decision_count": counts["decisions_history"],
        "target_epochs": target_epochs,
        "last_trace_jsonl": str(args.trace_jsonl),
        "last_export_summary_json": str(args.export_summary_json),
        "last_written_transitions": written_transitions,
        "updates_completed": int(state.get("updates_completed", 0) or 0) + 1,
        "updated_at": int(time.time()),
    }
    if not args.dry_run:
        save_json(args.state_json, new_state)

    payload = {
        "status": "trained" if not args.dry_run else "dry_run_ready",
        "reason": reason,
        "db": str(args.db),
        "counts": counts,
        "written_transitions": written_transitions,
        "target_epochs": target_epochs,
        "trace_jsonl": str(args.trace_jsonl),
        "model_dir": str(args.model_dir),
        "state_json": str(args.state_json),
        "updates_completed": new_state["updates_completed"],
        "dry_run": bool(args.dry_run),
    }
    write_status(args, payload)
    return payload


def normalize_args(args: argparse.Namespace) -> argparse.Namespace:
    args.db = Path(args.db)
    args.output_root = Path(args.output_root)
    migrate_legacy_output_root(args.output_root)
    args.trace_jsonl = Path(args.trace_jsonl) if args.trace_jsonl else args.output_root / "tasam_true_online_real_trace.jsonl"
    args.export_summary_json = Path(args.export_summary_json) if args.export_summary_json else args.output_root / "tasam_true_online_real_export_summary.json"
    args.model_dir = Path(args.model_dir) if args.model_dir else args.output_root / "tasam_selective"
    args.status_json = Path(args.status_json) if args.status_json else args.output_root / "true_online_status.json"
    args.state_json = Path(args.state_json) if args.state_json else args.output_root / "true_online_state.json"
    return args


def main() -> int:
    args = normalize_args(build_parser().parse_args())

    if args.once:
        payload = run_once(args)
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    while True:
        payload = run_once(args)
        print(json.dumps(payload, ensure_ascii=False))
        time.sleep(max(float(args.poll_seconds), 1.0))


if __name__ == "__main__":
    raise SystemExit(main())
