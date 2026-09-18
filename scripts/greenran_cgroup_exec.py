#!/usr/bin/env python3
"""Execute one GreenRAN process tree inside its cgroup-v2 budget."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from greenran_infra_budget import CgroupV2Controller, GROUPS, build_physical_budget  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", choices=GROUPS, required=True)
    parser.add_argument("--r-ai", type=float, default=1.0)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = list(args.command)
    if command and command[0] == "--":
        command.pop(0)
    if not command:
        parser.error("a command is required after --")
    budget = build_physical_budget(args.r_ai)
    controller = CgroupV2Controller()
    controller.apply(budget, {args.group: os.getpid()})
    os.execvp(command[0], command)
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
