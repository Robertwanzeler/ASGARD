#!/usr/bin/env python3
"""Build the official post-fix clean TA-SAM training dataset from an exported trace."""

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
DEFAULT_EXPORT_DIR = Path(
    os.environ.get(
        "GREENRAN_TASAM_EXPORT_DIR",
        str(ROOT / "runs" / "tasam_article_ns3_collection" / "tasam_article_export"),
    )
)

sys.path.insert(0, str(ROOT / "scripts"))

from filter_tasam_trace import POSTFIX_CLEAN_SINCE_TS  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build the official post-fix clean TA-SAM dataset")
    parser.add_argument(
        "--input-jsonl",
        default=None,
        help="Input trace JSONL; defaults to <output-dir>/tasam_article_trace.jsonl",
    )
    parser.add_argument("--output-dir", default=str(DEFAULT_EXPORT_DIR), help="Output directory")
    parser.add_argument("--output-jsonl", default=None, help="Optional explicit clean trace path")
    parser.add_argument("--summary-json", default=None, help="Optional explicit clean summary path")
    parser.add_argument("--manifest-json", default=None, help="Optional explicit manifest path")
    parser.add_argument("--python", default=sys.executable, help="Python executable for the filter step")
    parser.add_argument("--dry-run", action="store_true", help="Print the filter command only")
    return parser


def default_output_paths(output_dir: Path) -> tuple[Path, Path, Path, Path]:
    raw_trace = output_dir / "tasam_article_trace.jsonl"
    clean_trace = output_dir / "tasam_article_trace_postfix_clean.jsonl"
    summary = output_dir / "tasam_article_trace_postfix_clean_summary.json"
    manifest = output_dir / "latest_postfix_clean.json"
    return raw_trace, clean_trace, summary, manifest


def build_filter_command(
    python_bin: str,
    input_jsonl: Path,
    output_jsonl: Path,
    summary_json: Path,
) -> list[str]:
    return [
        python_bin,
        str(ROOT / "scripts" / "filter_tasam_trace.py"),
        "--input-jsonl",
        str(input_jsonl),
        "--output-jsonl",
        str(output_jsonl),
        "--summary-json",
        str(summary_json),
        "--profile",
        "postfix_clean",
    ]


def write_manifest(
    manifest_path: Path,
    *,
    input_jsonl: Path,
    output_jsonl: Path,
    summary_json: Path,
    command: list[str],
) -> None:
    payload: dict[str, Any] = {
        "schema": "greenran.tasam_article_postfix_clean_export.v1",
        "profile": "postfix_clean",
        "since_ts": int(POSTFIX_CLEAN_SINCE_TS),
        "input_jsonl": str(input_jsonl),
        "output_jsonl": str(output_jsonl),
        "summary_json": str(summary_json),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "command": command,
    }
    manifest_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    args = build_parser().parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    default_input, default_output, default_summary, default_manifest = default_output_paths(output_dir)
    input_jsonl = Path(args.input_jsonl) if args.input_jsonl else default_input
    output_jsonl = Path(args.output_jsonl) if args.output_jsonl else default_output
    summary_json = Path(args.summary_json) if args.summary_json else default_summary
    manifest_json = Path(args.manifest_json) if args.manifest_json else default_manifest

    command = build_filter_command(args.python, input_jsonl, output_jsonl, summary_json)
    print(" ".join(command), flush=True)
    if args.dry_run:
        return 0

    subprocess.run(command, cwd=ROOT, check=True)
    write_manifest(
        manifest_json,
        input_jsonl=input_jsonl,
        output_jsonl=output_jsonl,
        summary_json=summary_json,
        command=command,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
