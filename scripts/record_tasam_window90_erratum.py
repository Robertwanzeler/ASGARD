#!/usr/bin/env python3
"""Write a hash-only erratum for a preserved V2X window90 pilot."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _inventory(campaign: Path, relative: str) -> dict[str, object]:
    path = campaign / relative
    if not path.is_file():
        return {"relative": relative, "exists": False}
    return {
        "relative": relative,
        "exists": True,
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-dir", type=Path, required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument(
        "--status",
        default="pilot_training_incomplete",
        choices=("pilot_training_incomplete", "no_economic_headroom", "cancelled", "metric_invalid"),
        help="Classificação terminal da campanha preservada.",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--relative", action="append", required=True)
    args = parser.parse_args()

    campaign = args.campaign_dir.resolve()
    payload = {
        "schema": "greenran.tasam.v2x.window90.erratum.v1",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "campaign_dir": str(campaign),
        "status": args.status,
        "scientific_decision": "not_promotable",
        "promotion_eligible": False,
        "reason": args.reason,
        "original_artifacts_preserved": True,
        "traces_edited": False,
        "artifacts": [_inventory(campaign, relative) for relative in args.relative],
    }
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
