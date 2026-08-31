#!/usr/bin/env python3
"""Create a run-local, evaluation-only ARMD + TA-SAM assistant-only gate."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--target-decisions", type=int, default=65)
    args = parser.parse_args()
    target_decisions = max(1, int(args.target_decisions))
    checkpoint = args.checkpoint_dir.resolve()
    required = [checkpoint / "tasam_marl_actors.pt", checkpoint / "tasam_marl_checkpoint_meta.json"]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise SystemExit("checkpoint incompleto: " + ", ".join(missing))
    now = datetime.now(timezone.utc).isoformat()
    gate = {
        "status": "evaluation_only",
        "allow_shadow": True,
        "allow_control_trial": True,
        "manual_approval_required": False,
        "manual_approval_valid": True,
        "training_readiness": "shadow_ready",
        "runtime_readiness": "evaluation_only",
        "policy_id": f"ta_sam_marl_evaluation:{checkpoint.name}",
        "run_dir": str(checkpoint),
        "reasons": ["explicit paired network evaluation; no promotion or deployment implied"],
        "scope": {
            "mode": "assistant_only_control",
            "fraction": 1.0,
            "target_decisions": target_decisions,
            "eligible_only_when": ["armd_valid", "tasam_valid", "tasam_resource_advice_enabled"],
            "critical_states": "allowed_inside_armd_safety_envelope",
            "fallback": "invalid_run_stop",
            "proxy": False,
            "created_at": now,
        },
    }
    payload = {
        "schema": "greenran.marl_control_gate.evaluation_only.v1",
        "tasam_eval_path": "evaluation-only",
        "runtime_eval_path": "evaluation-only",
        "manual_approval_path": "evaluation-only",
        "gate": gate,
    }
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "status": gate["status"], "policy_id": gate["policy_id"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
