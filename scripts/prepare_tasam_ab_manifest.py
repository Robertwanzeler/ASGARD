#!/usr/bin/env python3
"""Create a local TA-SAM checkpoint manifest for an isolated A/B round."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint-dir", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--label", default="tasam_ab_candidate")
    parser.add_argument("--disabled", action="store_true", help="Write an explicitly disabled baseline manifest")
    args = parser.parse_args()

    if args.disabled:
        output = args.output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(
                {
                    "schema": "greenran.tasam_ab_checkpoint_manifest.v1",
                    "best_run": {"promote_shadow": False, "run_dir": "", "ab_label": args.label},
                    "evaluated_runs": [],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(json.dumps({"output": str(output), "disabled": True}, indent=2))
        return 0

    if args.checkpoint_dir is None:
        parser.error("--checkpoint-dir e obrigatorio quando --disabled nao foi informado")

    checkpoint_dir = args.checkpoint_dir.resolve()
    required = ["tasam_marl_actors.pt", "tasam_marl_checkpoint_meta.json", "tasam_marl_summary.json"]
    missing = [name for name in required if not (checkpoint_dir / name).is_file()]
    if missing:
        raise SystemExit(f"checkpoint incompleto: {', '.join(missing)}")

    try:
        summary = json.loads((checkpoint_dir / "tasam_marl_summary.json").read_text(encoding="utf-8"))
        meta = json.loads((checkpoint_dir / "tasam_marl_checkpoint_meta.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"checkpoint metadata invalida: {exc}") from exc

    completed = int(summary.get("completed_epochs", 0) or 0)
    target = int(summary.get("target_epochs", summary.get("epochs", 0)) or 0)
    final = summary.get("final_metrics") or {}
    if completed <= 0 or target <= 0 or completed < target:
        raise SystemExit(f"treino incompleto: completed_epochs={completed}, target_epochs={target}")

    entry = {
        "trace_jsonl": str(summary.get("trace_jsonl", "") or ""),
        "epochs": completed,
        "du_count": int(meta.get("du_count", 0) or 0),
        "final_metrics": final,
        "readiness": "shadow_ready",
        "promote_shadow": True,
        "promote_control_candidate": False,
        "run_dir": str(checkpoint_dir),
        "summary_path": str((checkpoint_dir / "tasam_marl_summary.json").resolve()),
        "ab_label": args.label,
    }
    if entry["du_count"] <= 0:
        raise SystemExit("checkpoint metadata sem du_count valido")

    payload = {
        "schema": "greenran.tasam_ab_checkpoint_manifest.v1",
        "best_run": entry,
        "evaluated_runs": [entry],
    }
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"checkpoint_dir": str(checkpoint_dir), "output": str(output), "epochs": completed}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
