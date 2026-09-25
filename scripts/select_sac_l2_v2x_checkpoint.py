#!/usr/bin/env python3
"""Select and freeze one internally validated SAC-L2 V2X checkpoint."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REPLAY_SCHEMA = "greenran.tasam.v2x.replay_80_20.v1"
REWARD_CONTRACT = "greenran.tasam.v2x.reward_adaptive.v1"
TRAINING_SEEDS = (43, 44)
REQUIRED_COMPONENTS = (
    "tasam_marl_actors.pt", "tasam_marl_global_actor.pt", "tasam_marl_critic1.pt",
    "tasam_marl_critic2.pt", "tasam_marl_target_critic1.pt", "tasam_marl_target_critic2.pt",
    "tasam_marl_category_head.pt",
)


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    files = sorted(item for item in path.rglob("*") if item.is_file())
    for item in files:
        relative = item.relative_to(path).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(item.read_bytes())
    return digest.hexdigest()


def _parse_candidate(raw: str) -> tuple[int, Path]:
    try:
        seed_text, path_text = raw.split(":", 1)
        return int(seed_text), Path(path_text).resolve()
    except (ValueError, TypeError) as exc:
        raise argparse.ArgumentTypeError("--candidate usa SEED:DIRETORIO") from exc


def validate_candidate(seed: int, checkpoint: Path) -> tuple[bool, str, dict[str, Any]]:
    meta = _json(checkpoint / "tasam_marl_checkpoint_meta.json")
    summary = _json(checkpoint / "tasam_marl_summary.json")
    final = meta.get("final_metrics") or summary.get("final_metrics") or {}
    if seed not in TRAINING_SEEDS:
        return False, "training_seed_invalid", {}
    if not checkpoint.is_dir() or any(not (checkpoint / name).is_file() for name in REQUIRED_COMPONENTS):
        return False, "required_checkpoint_component_missing", {}
    expected = {"du_count": 3, "du_state_dim": 13, "global_state_dim": 13, "joint_action_dim": 12}
    if {key: meta.get(key) for key in expected} != expected:
        return False, "state_or_action_contract_invalid", {}
    heads = ("category_head_path", "power_head_path", "allocation_head_path")
    if not meta.get("uses_global_energy_infra_actor") or any(
        not str(meta.get(name) or "") or not (checkpoint / str(meta[name])).is_file()
        for name in heads
    ):
        return False, "current_head_or_global_actor_missing", {}
    if (
        meta.get("article_method") != "sac_l2"
        or meta.get("sam_mode") != "l2"
        or float(meta.get("l2_weight", 0.0) or 0.0) != 0.0001
        or meta.get("replay_contract") != REPLAY_SCHEMA
        or meta.get("reward_contract") != REWARD_CONTRACT
    ):
        return False, "sac_l2_contract_invalid", {}
    try:
        score = float(final.get("eval_return"))
    except (TypeError, ValueError):
        return False, "final_metrics_eval_return_missing", {}
    if not math.isfinite(score):
        return False, "final_metrics_eval_return_invalid", {}
    return True, "ok", {"score": score, "final_metrics": final, "fingerprint": fingerprint(checkpoint)}


def select(candidates: dict[int, Path], output: Path, manifest_path: Path) -> dict[str, Any]:
    if set(candidates) != set(TRAINING_SEEDS):
        raise ValueError("a seleção interna exige candidatos exatamente das seeds 43 e 44")
    if output.exists() or manifest_path.exists():
        raise ValueError("checkpoint congelado ou manifesto de seleção já existe")
    report: dict[str, Any] = {"schema": "greenran.tasam.v2x.sac_l2_selection.v1", "candidates": {}}
    valid: list[tuple[float, int, Path, dict[str, Any]]] = []
    for seed in TRAINING_SEEDS:
        checkpoint = candidates[seed]
        ok, reason, evidence = validate_candidate(seed, checkpoint)
        report["candidates"][str(seed)] = {
            "checkpoint": str(checkpoint), "valid": ok, "reason": reason, **evidence,
        }
        if ok:
            valid.append((float(evidence["score"]), seed, checkpoint, evidence))
    if len(valid) != len(TRAINING_SEEDS):
        report.update({"status": "blocked", "reason": "invalid_training_candidate"})
        return report
    # Maximize internal training score; lower seed provides deterministic tie-break.
    score, seed, checkpoint, evidence = sorted(valid, key=lambda item: (-item[0], item[1]))[0]
    shutil.copytree(checkpoint, output)
    meta_path = output / "tasam_marl_checkpoint_meta.json"
    meta = _json(meta_path)
    meta.update({
        "evaluation_eligible": True,
        "promotion_eligible": False,
        "frozen_for_evaluation": True,
        "selection_seed": seed,
        "selection_metric": "final_metrics.eval_return",
        "selection_score": score,
        "selection_tie_break": "lower_training_seed",
        "selection_source_checkpoint": str(checkpoint),
        "selection_source_fingerprint": evidence["fingerprint"],
    })
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    frozen_fingerprint = fingerprint(output)
    report.update({
        "status": "selected",
        "selection_metric": "final_metrics.eval_return",
        "selection_tie_break": "seed_43",
        "selected_training_seed": seed,
        "selected_checkpoint_source": str(checkpoint),
        "selected_checkpoint": str(output),
        "selected_score": score,
        "source_fingerprint": evidence["fingerprint"],
        "frozen_fingerprint": frozen_fingerprint,
        "evaluation_frozen": True,
        "selected_at": int(time.time()),
    })
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", action="append", required=True, type=_parse_candidate, metavar="SEED:DIR")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    result = select(dict(args.candidate), args.output.resolve(), args.manifest.resolve())
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["status"] == "selected" else 2


if __name__ == "__main__":  # pragma: no cover - CLI boundary
    raise SystemExit(main())
