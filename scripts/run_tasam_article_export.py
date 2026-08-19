#!/usr/bin/env python3
"""Export the live line-2 TA-SAM dataset from the official ns-3 SQLite DB."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = Path(
    os.environ.get(
        "GREENRAN_DB_PATH",
        str(ROOT / "runs" / "tasam_article_ns3_collection" / "rapp_data_lake.db"),
    )
)
DEFAULT_OUTPUT_DIR = Path(
    os.environ.get(
        "GREENRAN_TASAM_EXPORT_DIR",
        str(ROOT / "runs" / "tasam_article_ns3_collection" / "tasam_article_export"),
    )
)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export the live TA-SAM article dataset")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="Official ns-3 collection SQLite DB")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Export directory")
    parser.add_argument("--trace-jsonl", default=None, help="Optional explicit trace output path")
    parser.add_argument("--summary-json", default=None, help="Optional explicit summary output path")
    parser.add_argument(
        "--limit",
        type=int,
        default=_env_int("GREENRAN_TASAM_EXPORT_LIMIT", 20000),
        help="Optional transition cap",
    )
    parser.add_argument(
        "--allow-proxy",
        action="store_true",
        default=_env_bool("GREENRAN_TASAM_EXPORT_ALLOW_PROXY", False),
        help="Legacy compatibility option; final GreenRAN training still requires the quality gate",
    )
    parser.add_argument(
        "--trainable-profile",
        default=os.environ.get("GREENRAN_TASAM_TRAINABLE_PROFILE", "rapp_online_trainable"),
        help="Filter profile for the trainable subset",
    )
    parser.add_argument(
        "--trainable-trace-jsonl",
        default=None,
        help="Optional explicit trainable trace output path",
    )
    parser.add_argument(
        "--trainable-summary-json",
        default=None,
        help="Optional explicit trainable summary output path",
    )
    parser.add_argument(
        "--trainable-max-p95-ms",
        type=float,
        default=None,
        help="Optional latency cap applied only to the trainable subset",
    )
    parser.add_argument(
        "--trainable-max-cvar-ms",
        type=float,
        default=None,
        help="Optional CVaR cap applied only to the trainable subset",
    )
    parser.add_argument(
        "--skip-trainable",
        action="store_true",
        help="Export only the raw trace and skip trainable subset generation",
    )
    parser.add_argument("--python", default=sys.executable, help="Python executable for the exporter")
    parser.add_argument("--dry-run", action="store_true", help="Print the exporter command only")
    return parser


def default_output_paths(output_dir: Path) -> tuple[Path, Path, Path]:
    trace = output_dir / "tasam_article_trace.jsonl"
    summary = output_dir / "tasam_article_export_summary.json"
    manifest = output_dir / "latest_export.json"
    return trace, summary, manifest


def default_trainable_paths(output_dir: Path) -> tuple[Path, Path]:
    trace = output_dir / "rapp_online_trainable_trace.jsonl"
    summary = output_dir / "rapp_online_trainable_summary.json"
    return trace, summary


def build_export_command(
    python_bin: str,
    db_path: Path,
    trace_path: Path,
    summary_path: Path,
    limit: int = 0,
    allow_proxy: bool = False,
    include_invalid: bool = False,
) -> list[str]:
    cmd = [
        python_bin,
        str(ROOT / "scripts" / "export_tasam_article_dataset.py"),
        "--db",
        str(db_path),
        "--output-jsonl",
        str(trace_path),
        "--summary-json",
        str(summary_path),
    ]
    if limit > 0:
        cmd.extend(["--limit", str(limit)])
    if allow_proxy:
        cmd.append("--allow-proxy")
    if include_invalid:
        cmd.append("--include-invalid")
    return cmd


def build_filter_command(
    python_bin: str,
    input_path: Path,
    output_path: Path,
    summary_path: Path,
    profile: str,
    max_p95_ms: float | None = None,
    max_cvar_ms: float | None = None,
) -> list[str]:
    cmd = [
        python_bin,
        str(ROOT / "scripts" / "filter_tasam_trace.py"),
        "--input-jsonl",
        str(input_path),
        "--output-jsonl",
        str(output_path),
        "--summary-json",
        str(summary_path),
        "--profile",
        profile,
    ]
    if max_p95_ms is not None:
        cmd.extend(["--max-p95-ms", str(max_p95_ms)])
    if max_cvar_ms is not None:
        cmd.extend(["--max-cvar-ms", str(max_cvar_ms)])
    return cmd


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def write_manifest(
    manifest_path: Path,
    db_path: Path,
    raw_trace_path: Path,
    raw_summary_path: Path,
    raw_command: list[str],
    trainable_trace_path: Path | None = None,
    trainable_summary_path: Path | None = None,
    trainable_command: list[str] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "schema": "greenran.tasam_article_live_export.v1",
        "db": str(db_path),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "raw_export": {
            "trace_jsonl": str(raw_trace_path),
            "summary_json": str(raw_summary_path),
            "command": raw_command,
            "summary": _load_json(raw_summary_path),
        },
    }
    if trainable_trace_path and trainable_summary_path and trainable_command:
        payload["trainable_export"] = {
            "trace_jsonl": str(trainable_trace_path),
            "summary_json": str(trainable_summary_path),
            "command": trainable_command,
            "summary": _load_json(trainable_summary_path),
        }
    manifest_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    args = build_parser().parse_args()
    db_path = Path(args.db)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    trace_path, summary_path, manifest_path = default_output_paths(output_dir)
    trainable_trace_path, trainable_summary_path = default_trainable_paths(output_dir)
    if args.trace_jsonl:
        trace_path = Path(args.trace_jsonl)
    if args.summary_json:
        summary_path = Path(args.summary_json)
    if args.trainable_trace_jsonl:
        trainable_trace_path = Path(args.trainable_trace_jsonl)
    if args.trainable_summary_json:
        trainable_summary_path = Path(args.trainable_summary_json)

    raw_cmd = build_export_command(
        python_bin=args.python,
        db_path=db_path,
        trace_path=trace_path,
        summary_path=summary_path,
        limit=args.limit or 0,
        allow_proxy=args.allow_proxy,
        include_invalid=True,
    )
    print(" ".join(raw_cmd), flush=True)
    trainable_cmd: list[str] | None = None
    if not args.skip_trainable:
        trainable_cmd = build_filter_command(
            python_bin=args.python,
            input_path=trace_path,
            output_path=trainable_trace_path,
            summary_path=trainable_summary_path,
            profile=args.trainable_profile,
            max_p95_ms=args.trainable_max_p95_ms,
            max_cvar_ms=args.trainable_max_cvar_ms,
        )
        print(" ".join(trainable_cmd), flush=True)
    if args.dry_run:
        return 0

    subprocess.run(raw_cmd, cwd=ROOT, check=True)
    if trainable_cmd is not None:
        subprocess.run(trainable_cmd, cwd=ROOT, check=True)
    write_manifest(
        manifest_path,
        db_path,
        trace_path,
        summary_path,
        raw_cmd,
        trainable_trace_path if trainable_cmd is not None else None,
        trainable_summary_path if trainable_cmd is not None else None,
        trainable_cmd,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
