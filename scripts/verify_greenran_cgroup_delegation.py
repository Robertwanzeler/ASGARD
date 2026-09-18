#!/usr/bin/env python3
"""Read-only verification for the GreenRAN cgroup-v2 user delegation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from greenran_infra_budget import cgroup_delegation_status, probe_cgroup_attach  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=None,
        help="override the root; otherwise use the bootstrapped per-user path",
    )
    parser.add_argument("--json", action="store_true", help="emit the complete status as JSON")
    parser.add_argument(
        "--require-attach", action="store_true",
        help="run a short-lived cgroup attach probe after the read-only checks",
    )
    args = parser.parse_args()
    status = cgroup_delegation_status(args.root)
    if status["valid"] and args.require_attach:
        status["attach_probe"] = probe_cgroup_attach(args.root)
        status["valid"] = bool(status["attach_probe"].get("valid"))
        if not status["valid"]:
            status["failures"].append(
                "attach real no cgroup delegado falhou: "
                + str(status["attach_probe"].get("reason", "desconhecido"))
            )
    if args.json or not status["valid"]:
        print(json.dumps(status, indent=2, ensure_ascii=False))
    else:
        print(f"delegação cgroup GreenRAN válida: {status['root']}")
    return 0 if status["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
