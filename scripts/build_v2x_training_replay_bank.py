#!/usr/bin/env python3
"""Seal a real-only V2X historical replay bank for SAC-L2 training.

The input files are derived, read-only exports from the two collection arms.
This tool never synthesizes rows, repairs incomplete telemetry, or borrows
evaluation evidence.  It produces one immutable historical bank; online arms
write their recent 20%% bucket elsewhere.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from greenran_v2x_adaptive_reward import REWARD_CONTRACT  # noqa: E402


REPLAY_SCHEMA = "greenran.tasam.v2x.replay_80_20.v1"
PROFILE = "tasam_training_balanced_v6_v2x_gbr_deadline_mc_fallback"
TRAINING_SEEDS = (43, 44)
EXPECTED_STAGES = {
    "allowed_bootstrap", "allowed_stable", "camera_conditional", "camera_blocked",
    "vehicle_conditional", "vehicle_blocked", "app2_conditional", "app2_blocked",
    "allowed_recovery",
}


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _identity(row: dict[str, Any]) -> tuple[str, str, int, str] | None:
    try:
        seed = int(row.get("replay_seed", row.get("seed")))
    except (TypeError, ValueError):
        return None
    phase = str(row.get("replay_phase") or row.get("phase") or "").strip()
    episode = str(row.get("replay_episode") or row.get("episode") or "").strip()
    timestamp = str(row.get("replay_timestamp", row.get("timestamp", "")) or "").strip()
    if not phase or not episode or not timestamp:
        return None
    return phase, episode, seed, timestamp


def _valid_quality(row: dict[str, Any]) -> bool:
    quality = row.get("collection_quality") if isinstance(row.get("collection_quality"), dict) else {}
    return bool(
        quality.get("valid_for_training") is True
        and quality.get("collector_mode") == "pdcp_real"
        and quality.get("pdcp_real") is True
        and float(quality.get("proxy_latency_sample_count", 0) or 0) == 0.0
        and quality.get("metric_alignment_valid") is True
        and not quality.get("sim_reset")
        and float(quality.get("current_metric_skew_s", 0) or 0) <= 6.0
        and float(quality.get("next_metric_skew_s", 0) or 0) <= 6.0
    )


def _stage(row: dict[str, Any]) -> str:
    decision = row.get("decision") if isinstance(row.get("decision"), dict) else {}
    return str(
        row.get("decision_stage_name")
        or row.get("scenario_stage")
        or decision.get("collection_event_stage_name")
        or ""
    ).strip()


def _parse_source(raw: str) -> tuple[int, Path]:
    try:
        seed_text, path_text = raw.split(":", 1)
        seed = int(seed_text)
    except (ValueError, TypeError) as exc:
        raise argparse.ArgumentTypeError("--source usa SEED:ARQUIVO.jsonl") from exc
    return seed, Path(path_text).resolve()


def validate_source(seed: int, path: Path, *, profile: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Normalize one source export and return only fully evidenced records."""
    report: dict[str, Any] = {
        "seed": seed,
        "source": str(path),
        "source_sha256": sha256(path) if path.is_file() else "",
        "accepted": 0,
        "rejected": {},
        "stages": {},
    }
    if seed not in TRAINING_SEEDS:
        raise ValueError(f"seed de coleta não permitida: {seed}")
    if not path.is_file():
        raise ValueError(f"export de coleta ausente: {path}")
    arm_manifest = _json(path.parent / "arm_manifest.json")
    if (
        arm_manifest.get("mode") != "rapp_only_actuating"
        or arm_manifest.get("profile") != profile
        or int(arm_manifest.get("seed", -1) or -1) != seed
        or arm_manifest.get("real_only_collection") is not True
        or arm_manifest.get("scenario_control_override_allowed") is not False
    ):
        raise ValueError(f"manifesto da coleta seed {seed} não prova rApp real-only")
    report["arm_manifest"] = str(path.parent / "arm_manifest.json")
    report["arm_manifest_sha256"] = sha256(path.parent / "arm_manifest.json")
    accepted: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int, str]] = set()
    episode = f"historical_collection_seed_{seed}"
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                report["rejected"]["invalid_json"] = report["rejected"].get("invalid_json", 0) + 1
                continue
            if not isinstance(row, dict):
                report["rejected"]["invalid_row"] = report["rejected"].get("invalid_row", 0) + 1
                continue
            if row.get("scenario_control_override") is not False:
                report["rejected"]["scenario_control_override"] = report["rejected"].get("scenario_control_override", 0) + 1
                continue
            if not _valid_quality(row):
                report["rejected"]["native_pdcp_or_quality"] = report["rejected"].get("native_pdcp_or_quality", 0) + 1
                continue
            decision = row.get("decision") if isinstance(row.get("decision"), dict) else {}
            if not row.get("decision_id") or not decision.get("action_correlation_id"):
                report["rejected"]["e2_or_correlation_missing"] = report["rejected"].get("e2_or_correlation_missing", 0) + 1
                continue
            feedback = row.get("judge_feedback") if isinstance(row.get("judge_feedback"), dict) else {}
            if (
                decision.get("e2_ack_complete") is not True
                or decision.get("feedback_integrity_valid") is not True
                or feedback.get("e2_ack_complete") is False
            ):
                report["rejected"]["e2_ack_missing"] = report["rejected"].get("e2_ack_missing", 0) + 1
                continue
            snapshot = row.get("adaptive_reward") if isinstance(row.get("adaptive_reward"), dict) else {}
            if (
                row.get("reward_contract") != REWARD_CONTRACT
                or snapshot.get("reward_contract") != REWARD_CONTRACT
                or snapshot.get("evidence_valid") is not True
            ):
                report["rejected"]["adaptive_reward_invalid"] = report["rejected"].get("adaptive_reward_invalid", 0) + 1
                continue
            stage = _stage(row)
            if stage not in EXPECTED_STAGES:
                report["rejected"]["stage_missing"] = report["rejected"].get("stage_missing", 0) + 1
                continue
            normalized = dict(row)
            normalized.update({
                "replay_contract": REPLAY_SCHEMA,
                "replay_phase": "historical_collection",
                "replay_episode": episode,
                "replay_seed": seed,
                "replay_timestamp": str(row.get("timestamp", "")),
                "replay_partition": "training",
                "scenario_control_override": False,
                "collection_stage": stage,
                "source_export_sha256": report["source_sha256"],
                "source_arm_manifest_sha256": report["arm_manifest_sha256"],
            })
            identity = _identity(normalized)
            if identity is None or identity in seen:
                report["rejected"]["duplicate_or_missing_identity"] = report["rejected"].get("duplicate_or_missing_identity", 0) + 1
                continue
            seen.add(identity)
            normalized["tasam_experience_id"] = "v2x|" + "|".join(map(str, identity))
            accepted.append(normalized)
            report["stages"][stage] = report["stages"].get(stage, 0) + 1
    report["accepted"] = len(accepted)
    return accepted, report


def seal(
    sources: dict[int, Path], output: Path, manifest_path: Path, *, profile: str = PROFILE,
    min_total: int = 600, min_per_seed: int = 240,
) -> dict[str, Any]:
    if set(sources) != set(TRAINING_SEEDS):
        raise ValueError("o banco histórico exige exatamente as seeds 43 e 44")
    if output.exists() or manifest_path.exists():
        raise ValueError("banco ou manifesto de replay já existe; artefatos selados não são sobrescritos")
    rows: list[dict[str, Any]] = []
    sources_report: dict[str, Any] = {}
    all_ids: set[str] = set()
    for seed in TRAINING_SEEDS:
        accepted, report = validate_source(seed, sources[seed], profile=profile)
        for row in accepted:
            identity = str(row["tasam_experience_id"])
            if identity in all_ids:
                raise ValueError(f"identificador duplicado entre fontes: {identity}")
            all_ids.add(identity)
        rows.extend(accepted)
        sources_report[str(seed)] = report
    stage_counts: dict[str, int] = {}
    for row in rows:
        stage = str(row["collection_stage"])
        stage_counts[stage] = stage_counts.get(stage, 0) + 1
    valid = bool(
        len(rows) >= int(min_total)
        and all(sources_report[str(seed)]["accepted"] >= int(min_per_seed) for seed in TRAINING_SEEDS)
        and EXPECTED_STAGES.issubset(stage_counts)
    )
    manifest = {
        "schema": REPLAY_SCHEMA,
        "kind": "sealed_historical_v2x_training_bank",
        "status": "sealed" if valid else "blocked",
        "immutable": True,
        "profile": profile,
        "training_seeds": list(TRAINING_SEEDS),
        "evaluation_seeds_excluded": [45, 46, 47],
        "scenario_control_override": False,
        "collector_mode": "pdcp_real",
        "proxy_allowed": False,
        "reward_contract": REWARD_CONTRACT,
        "minimums": {"total": int(min_total), "per_seed": int(min_per_seed)},
        "transitions": len(rows),
        "per_seed": {seed: sources_report[str(seed)]["accepted"] for seed in TRAINING_SEEDS},
        "stage_counts": dict(sorted(stage_counts.items())),
        "required_stages": sorted(EXPECTED_STAGES),
        "sources": sources_report,
        "output": str(output),
        "sealed_at": int(time.time()),
    }
    if not valid:
        manifest["rejection_reasons"] = [
            *([] if len(rows) >= int(min_total) else ["minimum_total_not_reached"]),
            *([] if all(sources_report[str(seed)]["accepted"] >= int(min_per_seed) for seed in TRAINING_SEEDS) else ["minimum_per_seed_not_reached"]),
            *([] if EXPECTED_STAGES.issubset(stage_counts) else ["curriculum_stage_coverage_incomplete"]),
        ]
        return manifest
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    temporary.replace(output)
    manifest["sha256"] = sha256(output)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_manifest = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
    temporary_manifest.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary_manifest.replace(manifest_path)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", action="append", required=True, type=_parse_source, metavar="SEED:JSONL")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--profile", default=PROFILE)
    parser.add_argument("--min-total", type=int, default=600)
    parser.add_argument("--min-per-seed", type=int, default=240)
    args = parser.parse_args()
    sources = dict(args.source)
    result = seal(
        sources, args.output.resolve(), args.manifest.resolve(), profile=args.profile,
        min_total=args.min_total, min_per_seed=args.min_per_seed,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["status"] == "sealed" else 2


if __name__ == "__main__":  # pragma: no cover - CLI boundary
    raise SystemExit(main())
