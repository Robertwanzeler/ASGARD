#!/usr/bin/env python3
"""Print the joint ARMD + TA-SAM canary and rollback status."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    parser = argparse.ArgumentParser(description="Show the joint ARMD + TA-SAM control trial status")
    parser.add_argument("--state", type=Path, default=PROJECT_ROOT / "runs/tasam_greenran_control_trial_20260809/control_trial_state.json")
    parser.add_argument("--gate", type=Path, default=PROJECT_ROOT / "runs/tasam_greenran_control_trial_20260809/marl_control_gate.json")
    args = parser.parse_args()
    state = json.loads(args.state.read_text(encoding="utf-8")) if args.state.exists() else {"status": "not_started"}
    gate = json.loads(args.gate.read_text(encoding="utf-8")) if args.gate.exists() else {}
    payload = {
        "trial": state,
        "gate": (gate.get("gate") or {}),
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
