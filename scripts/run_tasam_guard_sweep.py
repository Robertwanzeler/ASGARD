#!/usr/bin/env python3
"""Run a sequential offline sweep of the TA-SAM RAN guard strength."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TRACE = ROOT / "runs" / "greenran_tasam_3du_real_only_shadow_20260803" / "tasam_article_export" / "rapp_online_trainable_trace.jsonl"
DEFAULT_OUTPUT = ROOT / "runs" / "tasam_seed45_guard_sweep_20260806"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace-jsonl", default=str(DEFAULT_TRACE))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--epochs", type=int, default=150)
    parser.add_argument("--seed", type=int, default=45)
    parser.add_argument("--train-python", default="/usr/bin/python3")
    parser.add_argument("--skip-current", action="store_true", help="Do not include the already completed current variant")
    return parser


def configs(output_root: Path) -> list[dict[str, object]]:
    return [
        {
            "name": "current",
            "label": "guard atual",
            "weight": 0.25,
            "min_embb_share": 0.45,
            "output_dir": str((ROOT / "runs" / "tasam_seed45_ran_guard_20260806").resolve()),
            "existing": True,
        },
        {
            "name": "strong",
            "label": "guard forte",
            "weight": 0.40,
            "min_embb_share": 0.50,
            "output_dir": str((output_root / "strong").resolve()),
            "existing": False,
        },
        {
            "name": "soft",
            "label": "guard suave",
            "weight": 0.15,
            "min_embb_share": 0.45,
            "output_dir": str((output_root / "soft").resolve()),
            "existing": False,
        },
    ]


def train_command(args: argparse.Namespace, item: dict[str, object]) -> list[str]:
    return [
        str(args.train_python),
        str(ROOT / "drlexp" / "training" / "train_tasam_marl.py"),
        "--trace-jsonl", str(Path(args.trace_jsonl).resolve()),
        "--output-dir", str(item["output_dir"]),
        "--epochs", str(args.epochs), "--max-epochs", str(args.epochs),
        "--lr", "0.0001", "--alpha-lr", "0.0001",
        "--trainer-backend", "article_sac", "--sam-mode", "tasam_selective",
        "--actor-sam-rho", "0.01", "--actor-sam-rho-final", "0.001",
        "--critic-sam-rho", "0.01", "--critic-sam-rho-final", "0.001",
        "--td-var-threshold", "0.01", "--min-selected-fraction", "0.20",
        "--warmup-epochs", "10", "--bc-weight", "0",
        "--ran-guard-weight", str(item["weight"]),
        "--ran-guard-pressure-threshold", "0.35",
        "--ran-guard-min-embb-share", str(item["min_embb_share"]),
        "--actor-hidden-dims", "300,400,400", "--critic-hidden-dims", "300,400,400",
        "--activation", "tanh", "--batch-size", "128", "--seed", str(args.seed),
        "--checkpoint-every", "10", "--milestone-epochs", "50,100,150",
    ]


def main() -> int:
    args = build_parser().parse_args()
    trace = Path(args.trace_jsonl).resolve()
    output_root = Path(args.output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    items = configs(output_root)
    if args.skip_current:
        items = [item for item in items if item["name"] != "current"]
    manifest = output_root / "guard_sweep_manifest.json"
    manifest.write_text(json.dumps({
        "schema": "greenran.tasam_guard_sweep.v1",
        "trace_jsonl": str(trace),
        "epochs": args.epochs,
        "seed": args.seed,
        "configs": items,
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if not trace.exists():
        raise SystemExit(f"Trace não encontrado: {trace}")

    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    statuses: list[dict[str, object]] = []
    for item in items:
        output_dir = Path(str(item["output_dir"]))
        summary_path = output_dir / "tasam_marl_summary.json"
        if summary_path.exists():
            try:
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
                if int(summary.get("completed_epochs", 0) or 0) >= args.epochs:
                    statuses.append({"name": item["name"], "status": "already_complete", "output_dir": str(output_dir)})
                    continue
            except (OSError, json.JSONDecodeError):
                pass
        output_dir.mkdir(parents=True, exist_ok=True)
        log_path = output_dir / "guard_sweep_train.log"
        command = train_command(args, item)
        with log_path.open("w", encoding="utf-8", buffering=1) as log:
            log.write("$ " + " ".join(command) + "\n")
            log.flush()
            status = subprocess.run(command, cwd=str(ROOT), env=env, stdout=log, stderr=subprocess.STDOUT, check=False).returncode
        statuses.append({"name": item["name"], "status": "completed" if status == 0 else "failed", "returncode": status, "output_dir": str(output_dir)})
        if status != 0:
            print(json.dumps(statuses, indent=2, ensure_ascii=False), flush=True)
            return int(status)
    print(json.dumps({"manifest": str(manifest), "statuses": statuses}, indent=2, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
