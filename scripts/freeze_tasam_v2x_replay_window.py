#!/usr/bin/env python3
"""Freeze the r26 90-row V2X replay into an immutable 72/18 campaign pair."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from run_tasam_online_controlled import _v2x_replay_rows  # noqa: E402


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def identity(row: dict[str, Any]) -> str:
    return "|".join(str(row.get(key, "")) for key in (
        "replay_phase", "replay_episode", "replay_seed", "replay_timestamp",
    ))


def write_rows(path: Path, rows: list[dict[str, Any]]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return sha256_file(path)


def freeze(args: argparse.Namespace) -> dict[str, Any]:
    source = args.source.resolve()
    if not source.is_file():
        raise SystemExit(f"replay r26 ausente: {source}")
    source_sha256 = sha256_file(source)
    rows, rejected = _v2x_replay_rows(
        source, require_adaptive_reward=True, require_judge=False
    )
    if len(rows) != 90:
        raise SystemExit(
            f"replay r26 exige exatamente 90 transições elegíveis; obtidas {len(rows)} "
            f"(rejeitadas={rejected})"
        )
    identities = [identity(row) for row in rows]
    if len(set(identities)) != 90:
        raise SystemExit("replay r26 contém identidades duplicadas")
    if any(int(row.get("replay_seed", -1)) != args.seed for row in rows):
        raise SystemExit("replay r26 contém seed diferente da campanha")

    historical = [dict(row) for row in rows[:72]]
    recent = [dict(row) for row in rows[72:]]
    for row in historical:
        row.update({
            "frozen_replay_source": str(source),
            "frozen_replay_source_sha256": source_sha256,
            "frozen_replay_bucket": "historical",
        })
    for row in recent:
        row.update({
            "frozen_replay_source": str(source),
            "frozen_replay_source_sha256": source_sha256,
            "frozen_recent_replay": True,
            "frozen_replay_bucket": "recent",
        })
    historical_path = args.historical_output.resolve()
    recent_path = args.recent_output.resolve()
    historical_sha256 = write_rows(historical_path, historical)
    recent_sha256 = write_rows(recent_path, recent)
    manifest = {
        "schema": "greenran.tasam.v2x.frozen_replay_window90.v1",
        "status": "frozen",
        "seed": int(args.seed),
        "source": str(source),
        "source_sha256": source_sha256,
        "source_rows": 90,
        "historical_rows": 72,
        "recent_rows": 18,
        "historical_output": str(historical_path),
        "historical_output_sha256": historical_sha256,
        "recent_output": str(recent_path),
        "recent_output_sha256": recent_sha256,
        "historical_identities": [identity(row) for row in historical],
        "recent_identities": [identity(row) for row in recent],
        "source_rejected": rejected,
    }
    manifest_path = args.manifest.resolve()
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--historical-output", type=Path, required=True)
    parser.add_argument("--recent-output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=43)
    args = parser.parse_args()
    if args.manifest.exists():
        raise SystemExit(f"manifesto de replay já existe: {args.manifest}")
    print(json.dumps(freeze(args), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
