#!/usr/bin/env python3
"""Fail-closed evaluator for the frozen rApp-only/ASGARD campaign.

The evaluator is intentionally independent from the launcher.  It accepts
only a complete 3-seed x 5-repetition tree and reports ``approved`` only when
the strict SLA, native E2 acknowledgement, frozen-checkpoint and paired
energy gates all pass.  Missing evidence is never treated as success.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
from pathlib import Path
from typing import Any

try:
    from .evaluate_tasam_strict_pair import evaluate_pair
except ImportError:  # direct execution from scripts/
    from evaluate_tasam_strict_pair import evaluate_pair


EXPECTED_SEEDS = (45, 46, 47)
EXPECTED_REPETITIONS = 5
DEFAULT_PROFILE = "tasam_training_balanced_v6_v2x_gbr_deadline_mc_fallback"

# Student-t two-sided 95% critical values for df 1..30.  The campaign gate
# uses n=15, but keeping the table here makes the statistic deterministic and
# avoids making scipy a runtime dependency.
_T95 = {
    1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571,
    6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228,
    11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145, 15: 2.131,
    16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093, 20: 2.086,
    21: 2.080, 22: 2.074, 23: 2.069, 24: 2.064, 25: 2.060,
    26: 2.056, 27: 2.052, 28: 2.048, 29: 2.045, 30: 2.042,
}


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _number(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def paired_ci95(values: list[float]) -> dict[str, Any]:
    """Return a deterministic two-sided paired t interval for ``values``."""
    clean: list[float] = []
    for value in values:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(parsed):
            clean.append(parsed)
    count = len(clean)
    if not clean:
        return {"n": 0, "mean": None, "median": None, "low": None, "high": None}
    mean = statistics.fmean(clean)
    median = statistics.median(clean)
    if count < 2:
        return {"n": count, "mean": mean, "median": median, "low": None, "high": None}
    standard_error = statistics.stdev(clean) / math.sqrt(count)
    critical = _T95.get(count - 1, 1.96)
    margin = critical * standard_error
    return {
        "n": count,
        "mean": mean,
        "median": median,
        "sample_std": statistics.stdev(clean),
        "low": mean - margin,
        "high": mean + margin,
        "confidence": 0.95,
        "method": "paired_student_t_two_sided",
    }


def _readiness(
    baseline_manifest: Path,
    checkpoint: Path,
    *,
    profile: str,
) -> tuple[str, list[str], dict[str, Any], str | None]:
    baseline = _json(baseline_manifest)
    reasons: list[str] = []
    decision = str(baseline.get("scientific_decision") or "")
    if baseline.get("schema") != "greenran.autonomous_vehicle_feasibility.v4":
        reasons.append("vehicle_manifest_v4_multi_seed_required")
    if baseline.get("status") != "passed" or decision != "approved":
        reasons.append(f"baseline_not_approved:{decision or 'missing'}")
    if baseline.get("profile") != profile:
        reasons.append("baseline_profile_mismatch")
    provenance = baseline.get("provenance") if isinstance(baseline.get("provenance"), dict) else {}
    contract = provenance.get("metric_contract") if isinstance(provenance.get("metric_contract"), dict) else {}
    if contract.get("collector_mode") != "pdcp_real":
        reasons.append("collector_mode_not_pdcp_real")
    if contract.get("pdcp_source") != "native_pdcp_pdu_tx_rx":
        reasons.append("pdcp_source_not_native_pdu_tx_rx")
    if contract.get("proxy_allowed") is not False:
        reasons.append("proxy_allowed")
    matrix = baseline.get("multi_seed_validation") if isinstance(baseline.get("multi_seed_validation"), dict) else {}
    if (
        baseline.get("metric_contract") != "per_pdu_cohort_v1"
        or baseline.get("promotion_eligible") is not True
        or matrix.get("valid") is not True
        or tuple(matrix.get("required_seeds") or []) != EXPECTED_SEEDS
        or tuple(matrix.get("complete_seeds") or []) != EXPECTED_SEEDS
        or matrix.get("seed47_reused_from_phase1") is not True
        or matrix.get("provenance_compatible") is not True
    ):
        reasons.append("baseline_multi_seed_contract_invalid")

    meta_path = checkpoint / "tasam_marl_checkpoint_meta.json"
    actors_path = checkpoint / "tasam_marl_actors.pt"
    metadata = _json(meta_path)
    if not metadata:
        reasons.append("checkpoint_metadata_missing")
    if not actors_path.is_file():
        reasons.append("checkpoint_actors_missing")
    if not isinstance(metadata.get("final_metrics"), dict) or not metadata.get("final_metrics"):
        reasons.append("checkpoint_final_metrics_missing")
    if metadata.get("parent_was_promoted") is not True:
        reasons.append("checkpoint_parent_not_promoted")
    if metadata.get("replay_imported") is not True:
        reasons.append("checkpoint_replay_not_imported")
    return ("blocked" if reasons else "ready", reasons, baseline, _sha256(actors_path))


def _rollback_free(run_dir: Path) -> dict[str, Any]:
    state = _json(run_dir / "control_trial_state.json")
    rollback_count = int(_number(state.get("rollback_count"), 0))
    return {
        "valid": state.get("rollback") is not True and rollback_count == 0,
        "rollback": state.get("rollback"),
        "rollback_count": rollback_count,
        "source": str(run_dir / "control_trial_state.json"),
    }


def evaluate_one_pair(
    baseline_dir: Path,
    asgard_dir: Path,
    *,
    seed: int,
    repetition: int,
    profile: str,
    checkpoint_sha256: str,
    warmup_s: int,
    duration_s: float,
) -> dict[str, Any]:
    baseline_manifest = _json(baseline_dir / "arm_manifest.json")
    asgard_manifest = _json(asgard_dir / "arm_manifest.json")
    pair = evaluate_pair(
        baseline_dir,
        asgard_dir,
        warmup_s=warmup_s,
        duration_s=duration_s,
        expected_seed=seed,
        expected_profile=profile,
        expected_checkpoint_sha256=checkpoint_sha256,
        expected_baseline_mode="rapp_only_actuating",
    )
    asgard_e2 = pair.get("combined", {}).get("e2", {})
    baseline_e2 = pair.get("baseline", {}).get("e2", {})
    asgard_sla = pair.get("combined", {}).get("sla", {})
    baseline_sla = pair.get("baseline", {}).get("sla", {})
    rollback = _rollback_free(asgard_dir)
    energy_b = _number((pair.get("baseline", {}).get("energy") or {}).get("energy_j"), -1.0)
    energy_a = _number((pair.get("combined", {}).get("energy") or {}).get("energy_j"), -1.0)
    contract_checks = pair.get("experiment_contract", {}).get("checks", {})
    pair_criteria = {
        "pair_contract_valid": pair.get("experiment_contract", {}).get("valid") is True,
        "arms_finished": baseline_manifest.get("status") == "finished" and asgard_manifest.get("status") == "finished",
        "same_pairing_schedule": bool(contract_checks.get("same_schedule")),
        "strict_sla_baseline": baseline_sla.get("valid") is True,
        "strict_sla_asgard": asgard_sla.get("valid") is True,
        "native_e2_ack_for_every_control_action": baseline_e2.get("valid") is True,
        "native_e2_ack_for_every_action": asgard_e2.get("valid") is True,
        "no_rollback": rollback["valid"],
        "no_starvation": not any(
            "disconnected_or_unserved" in reason or "missing_ue_window" in reason
            for report in (baseline_sla, asgard_sla)
            for violation in report.get("violations", [])
            for reason in violation.get("reasons", [])
        ),
        "energy_complete": energy_b >= 0.0 and energy_a >= 0.0,
        "energy_asgard_lower": energy_a < energy_b if energy_b >= 0.0 and energy_a >= 0.0 else False,
    }
    return {
        "seed": int(seed),
        "repetition": int(repetition),
        "baseline_dir": str(baseline_dir.resolve()),
        "asgard_dir": str(asgard_dir.resolve()),
        "baseline_manifest": baseline_manifest,
        "asgard_manifest": asgard_manifest,
        "strict_pair": pair,
        "rollback": rollback,
        "actuations": {
            "proposed": int(asgard_e2.get("transactions", 0) or 0),
            "vetoed": int(asgard_e2.get("fallbacks", 0) or 0),
            "applied": int(asgard_e2.get("confirmed", 0) or 0),
            "confirmed": int(asgard_e2.get("scheduler_phy_confirmed", 0) or 0),
        },
        "energy_delta_j": energy_a - energy_b if energy_b >= 0.0 and energy_a >= 0.0 else None,
        "criteria": pair_criteria,
        "valid": all(pair_criteria.values()),
    }


def build_report(
    campaign_root: Path,
    *,
    baseline_manifest: Path,
    checkpoint: Path,
    profile: str = DEFAULT_PROFILE,
    seeds: tuple[int, ...] = EXPECTED_SEEDS,
    repetitions: int = EXPECTED_REPETITIONS,
    warmup_s: int = 30,
    duration_s: float = 331.5,
) -> dict[str, Any]:
    readiness, readiness_reasons, baseline, checkpoint_sha256 = _readiness(
        baseline_manifest.resolve(), checkpoint.resolve(), profile=profile
    )
    pairs: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    if readiness == "ready" and tuple(seeds) == EXPECTED_SEEDS and repetitions == EXPECTED_REPETITIONS:
        for seed in seeds:
            for repetition in range(1, repetitions + 1):
                root = campaign_root / f"seed_{seed}" / f"rep_{repetition}"
                baseline_dir = root / "rapp_only"
                asgard_dir = root / "combined"
                if not (baseline_dir / "arm_manifest.json").is_file() or not (asgard_dir / "arm_manifest.json").is_file():
                    missing.append({"seed": seed, "repetition": repetition, "root": str(root)})
                    continue
                pairs.append(evaluate_one_pair(
                    baseline_dir,
                    asgard_dir,
                    seed=seed,
                    repetition=repetition,
                    profile=profile,
                    checkpoint_sha256=str(checkpoint_sha256 or ""),
                    warmup_s=warmup_s,
                    duration_s=duration_s,
                ))
    else:
        if tuple(seeds) != EXPECTED_SEEDS:
            readiness_reasons.append("seed_matrix_must_be_45_46_47")
        if repetitions != EXPECTED_REPETITIONS:
            readiness_reasons.append("repetitions_must_be_5")

    energy_deltas = [float(pair["energy_delta_j"]) for pair in pairs if pair.get("energy_delta_j") is not None]
    energy_ci = paired_ci95(energy_deltas)
    baseline_energies = [
        _number((pair.get("strict_pair", {}).get("baseline", {}).get("energy") or {}).get("energy_j"), -1.0)
        for pair in pairs
    ]
    asgard_energies = [
        _number((pair.get("strict_pair", {}).get("combined", {}).get("energy") or {}).get("energy_j"), -1.0)
        for pair in pairs
    ]
    baseline_energies = [value for value in baseline_energies if value >= 0.0]
    asgard_energies = [value for value in asgard_energies if value >= 0.0]
    criteria = {
        "baseline_approved": readiness == "ready",
        "complete_15_pairs": len(pairs) == len(EXPECTED_SEEDS) * EXPECTED_REPETITIONS and not missing,
        "all_pairs_valid": bool(pairs) and len(pairs) == len(energy_deltas) and all(pair["valid"] for pair in pairs),
        "all_strict_slas": bool(pairs) and all(
            pair["criteria"]["strict_sla_baseline"] and pair["criteria"]["strict_sla_asgard"]
            for pair in pairs
        ),
        "all_e2_acks": bool(pairs) and all(pair["criteria"]["native_e2_ack_for_every_action"] for pair in pairs),
        "no_rollbacks_or_starvation": bool(pairs) and all(
            pair["criteria"]["no_rollback"] and pair["criteria"]["no_starvation"] for pair in pairs
        ),
        "mean_energy_lower": bool(baseline_energies) and statistics.fmean(asgard_energies) < statistics.fmean(baseline_energies),
        "median_energy_lower": bool(baseline_energies) and statistics.median(asgard_energies) < statistics.median(baseline_energies),
        "paired_energy_ci95_strictly_below_zero": energy_ci.get("high") is not None and energy_ci["high"] < 0.0,
        "checkpoint_frozen_for_all_pairs": bool(pairs) and all(
            pair["strict_pair"].get("experiment_contract", {}).get("checks", {}).get("checkpoint_frozen") is True
            for pair in pairs
        ),
    }
    approved = all(criteria.values())
    if approved:
        decision = "approved"
    elif readiness != "ready" or missing or len(pairs) != len(EXPECTED_SEEDS) * EXPECTED_REPETITIONS:
        decision = "blocked"
    else:
        decision = "rejected"
    actuation_totals = {
        key: sum(int(pair.get("actuations", {}).get(key, 0) or 0) for pair in pairs)
        for key in ("proposed", "vetoed", "applied", "confirmed")
    }
    return {
        "schema": "greenran.asgard.paired_campaign.v2",
        "scientific_decision": decision,
        "campaign_root": str(campaign_root.resolve()),
        "profile": profile,
        "seeds": list(seeds),
        "repetitions": repetitions,
        "warmup_s": warmup_s,
        "duration_s": duration_s,
        "baseline_manifest": str(baseline_manifest.resolve()),
        "checkpoint": {
            "path": str(checkpoint.resolve()),
            "actors_sha256": checkpoint_sha256,
            "readiness": readiness,
            "readiness_reasons": readiness_reasons,
        },
        "baseline": {
            "selected_interval_us": baseline.get("selected_interval_us"),
            "scientific_decision": baseline.get("scientific_decision"),
        },
        "pairs": pairs,
        "missing_pairs": missing,
        "sla_by_pair": [
            {
                "seed": pair["seed"],
                "repetition": pair["repetition"],
                "baseline": pair["strict_pair"].get("baseline", {}).get("sla", {}),
                "asgard": pair["strict_pair"].get("combined", {}).get("sla", {}),
            }
            for pair in pairs
        ],
        "actuations": actuation_totals,
        "energy": {
            "delta_definition": "ASGARD - rApp-only, joules",
            "baseline_mean_j": statistics.fmean(baseline_energies) if baseline_energies else None,
            "asgard_mean_j": statistics.fmean(asgard_energies) if asgard_energies else None,
            "baseline_median_j": statistics.median(baseline_energies) if baseline_energies else None,
            "asgard_median_j": statistics.median(asgard_energies) if asgard_energies else None,
            "paired_delta_ci95_j": energy_ci,
            "integrated_energy_source": "native_ns3_energy_trace",
        },
        "criteria": criteria,
        "approved": approved,
        "rejection_reasons": [name for name, passed in criteria.items() if not passed],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-root", type=Path, required=True)
    parser.add_argument("--baseline-manifest", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--profile", default=DEFAULT_PROFILE)
    parser.add_argument("--seeds", type=int, nargs="+", default=list(EXPECTED_SEEDS))
    parser.add_argument("--repetitions", type=int, default=EXPECTED_REPETITIONS)
    parser.add_argument("--warmup-s", type=int, default=30)
    parser.add_argument("--duration-s", type=float, default=331.5)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = build_report(
        args.campaign_root.resolve(),
        baseline_manifest=args.baseline_manifest.resolve(),
        checkpoint=args.checkpoint.resolve(),
        profile=args.profile,
        seeds=tuple(args.seeds),
        repetitions=args.repetitions,
        warmup_s=args.warmup_s,
        duration_s=args.duration_s,
    )
    args.output.resolve().parent.mkdir(parents=True, exist_ok=True)
    args.output.resolve().write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output.resolve()), "scientific_decision": report["scientific_decision"]}, ensure_ascii=False))
    return 0 if report["scientific_decision"] == "approved" else 2


if __name__ == "__main__":
    raise SystemExit(main())
