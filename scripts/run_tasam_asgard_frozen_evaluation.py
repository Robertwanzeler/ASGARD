#!/usr/bin/env python3
"""Run the post-promotion shadow gate and paired rApp/ASGARD evaluation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARM = ROOT / "scripts" / "run_tasam_online_arm.py"
GATE = ROOT / "scripts" / "prepare_tasam_control_trial.py"
PAIR = ROOT / "scripts" / "run_tasam_causal_pilot.py"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run(command: list[str]) -> int:
    print("$ " + " ".join(str(item) for item in command), flush=True)
    return int(subprocess.run(command, cwd=ROOT, check=False).returncode)


def _complete(checkpoint: Path) -> bool:
    return all((checkpoint / name).is_file() for name in (
        "tasam_marl_actors.pt", "tasam_marl_checkpoint_meta.json", "tasam_marl_summary.json",
    ))


def _validate_promoted(adaptation: Path, checkpoint: Path) -> dict:
    state_path = adaptation / "online_state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"estado de adaptação ausente ou inválido: {state_path}: {exc}") from exc
    if state.get("active_checkpoint_promoted") is not True:
        raise SystemExit("avaliação congelada recusada: não há checkpoint ativo promovido")
    if int(state.get("promotion_count", 0) or 0) < 1:
        raise SystemExit("avaliação congelada recusada: promotion_count=0")
    promoted = Path(str(state.get("last_promoted_checkpoint") or "")).resolve()
    if promoted != checkpoint.resolve():
        raise SystemExit(f"checkpoint informado não é o último promovido: {checkpoint} != {promoted}")
    if not _complete(checkpoint):
        raise SystemExit(f"checkpoint promovido incompleto: {checkpoint}")
    actor = checkpoint / "tasam_marl_actors.pt"
    meta = json.loads((checkpoint / "tasam_marl_checkpoint_meta.json").read_text(encoding="utf-8"))
    return {
        "checkpoint": str(checkpoint.resolve()),
        "checkpoint_sha256": _sha256(actor),
        "promotion_count": int(state.get("promotion_count", 0) or 0),
        "last_promoted_update_id": int(state.get("last_promoted_update_id", 0) or 0),
        "model_version": meta.get("model_version"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-dir", type=Path, required=True)
    parser.add_argument("--adaptation-dir", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--calibration", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=47)
    parser.add_argument("--profile", default="tasam_training_balanced_v3")
    parser.add_argument("--sim-time", type=float, default=600.0)
    parser.add_argument("--decisions", type=int, default=300)
    parser.add_argument("--min-free-gib", type=float, default=10.0)
    args = parser.parse_args()

    campaign = args.campaign_dir.resolve()
    if campaign.exists() and any(campaign.iterdir()):
        raise SystemExit(f"diretório da avaliação não está vazio: {campaign}")
    campaign.mkdir(parents=True, exist_ok=True)
    for label, path in (("campaign", campaign), ("adaptation", args.adaptation_dir),
                        ("checkpoint", args.checkpoint), ("calibration", args.calibration)):
        try:
            path.resolve().relative_to(ROOT)
        except ValueError as exc:
            raise SystemExit(f"{label} fora do projeto local: {path}") from exc
    promoted = _validate_promoted(args.adaptation_dir.resolve(), args.checkpoint.resolve())
    os.environ.update({
        "GREENRAN_LOCAL_ONLY": "1", "GREENRAN_CGROUP_ENFORCE": "1",
        "GREENRAN_CGROUP_ALLOW_UNENFORCED": "0", "GREENRAN_REQUIRE_REAL_PDCP": "1",
        "GREENRAN_REAL_ONLY": "1", "GREENRAN_TASAM_EXPORT_ALLOW_PROXY": "0",
        "GREENRAN_ENERGY_CALIBRATION_PATH": str(args.calibration.resolve()),
    })
    (campaign / "frozen_evaluation_manifest.json").write_text(json.dumps({
        "schema": "greenran.tasam.asgard.frozen_evaluation.v1",
        "system_name": "ASGARD", "seed": args.seed, "profile": args.profile,
        "sim_time_s": args.sim_time, "decisions": args.decisions,
        "adaptation_dir": str(args.adaptation_dir.resolve()), "promoted": promoted,
        "energy_interpretation": "estimativa relativa da simulação ns-3; não é consumo físico",
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    shadow = campaign / "shadow_300"
    shadow_cmd = [sys.executable, str(ARM), "--mode", "combined_shadow", "--run-dir", str(shadow),
                  "--seed", str(args.seed), "--profile", args.profile, "--wall-time", "900",
                  "--sim-time", str(args.sim_time), "--checkpoint", str(args.checkpoint),
                  "--energy-calibration", str(args.calibration), "--decision-target", str(args.decisions),
                  "--min-free-gib", str(args.min_free_gib), "--shadow-min-decisions", str(args.decisions),
                  "--stage-window-decisions", str(args.decisions), "--max-rollout-fraction", "0.50",
                  "--min-economic-transitions", "180", "--economic-update-min-transitions", "64"]
    code = _run(shadow_cmd)
    if code != 0:
        return code
    gate = campaign / "control_gate"
    gate_cmd = [sys.executable, str(GATE), "--checkpoint", str(args.checkpoint),
                "--shadow-db", str(shadow / "rapp_data_lake.db"), "--output-dir", str(gate),
                "--window", str(args.decisions), "--min-samples", str(args.decisions),
                "--min-positive-rate", "0.80", "--require-economic-head"]
    code = _run(gate_cmd)
    if code != 0:
        return code
    pair = campaign / "paired_comparison"
    pair_cmd = [sys.executable, str(PAIR), "--execute", "--campaign-dir", str(pair),
                "--seed", str(args.seed), "--profile", args.profile, "--wall-time", "900",
                "--sim-time", str(args.sim_time), "--canary-wall-time", "120",
                "--checkpoint", str(args.checkpoint), "--shadow-db", str(shadow / "rapp_data_lake.db"),
                "--control-gate", str(gate / "marl_control_gate.json"), "--calibration", str(args.calibration),
                "--min-free-gib", str(args.min_free_gib)]
    return _run(pair_cmd)


if __name__ == "__main__":
    raise SystemExit(main())
