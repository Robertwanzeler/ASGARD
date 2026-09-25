#!/usr/bin/env python3
"""Record a hash-only cancellation erratum for a preserved V2X matrix."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def inventory(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"path": str(path), "exists": False}
    return {"path": str(path), "exists": True, "sha256": sha256(path), "bytes": path.stat().st_size}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign-dir", type=Path, required=True)
    parser.add_argument("--relative", action="append", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    campaign = args.campaign_dir.resolve()
    files: list[dict[str, Any]] = []
    for relative in args.relative:
        candidate = campaign / relative
        files.append({"relative": relative, **inventory(candidate)})
    payload = {
        "schema": "greenran.v2x.matrix_cancellation_erratum.v1",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "campaign_dir": str(campaign),
        "status": "cancelled",
        "scientific_decision": "not_promotable",
        "promotion_eligible": False,
        "reason": args.reason,
        "original_artifacts_preserved": True,
        "traces_edited": False,
        "dispatcher_preserved": True,
        "original_artifacts": files,
    }
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
