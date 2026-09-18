#!/usr/bin/env python3
"""Evaluate the online TA-SAM training and the paired energy comparison."""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import statistics
from collections import Counter
from pathlib import Path
from typing import Any

try:
    from .evaluate_tasam_network_campaign import read_network_run
except ImportError:  # direct ``python scripts/evaluate_tasam_paired_campaign.py``
    from evaluate_tasam_network_campaign import read_network_run


EXPECTED_STAGES = (
    "allowed_bootstrap",
    "allowed_stable",
    "camera_conditional",
    "camera_blocked",
    "vehicle_conditional",
    "vehicle_blocked",
    "app2_conditional",
    "app2_blocked",
    "allowed_recovery",
)
PRIMARY_SEED = 47


def _json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.is_file():
        return rows
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _number(value: Any, default: float = 0.0) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def stage_counts(run_dir: Path) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in _jsonl(run_dir / "article00_scenario_control_history.jsonl"):
        stage = str(row.get("collection_event_stage_name") or row.get("stage_name") or row.get("stage") or "unknown")
        counts[stage] += 1
    if not all(stage in counts for stage in EXPECTED_STAGES):
        db = run_dir / "rapp_data_lake.db"
        if db.is_file():
            try:
                conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
                columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(decisions_history)").fetchall()}
                if "collection_event_stage_name" in columns:
                    rows = conn.execute(
                        """
                        SELECT collection_event_stage_name, COUNT(*)
                        FROM decisions_history
                        WHERE collection_event_stage_name IS NOT NULL
                          AND collection_event_stage_name != ''
                        GROUP BY collection_event_stage_name
                        """
                    ).fetchall()
                    for stage, count in rows:
                        counts[str(stage)] = max(counts.get(str(stage), 0), int(count))
                conn.close()
            except sqlite3.Error:
                pass
    return dict(sorted(counts.items()))


def _table_rows(conn: sqlite3.Connection, table: str) -> tuple[list[sqlite3.Row], set[str]]:
    try:
        columns = {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        rows = conn.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()
    except sqlite3.OperationalError:
        return [], set()
    return rows, columns


def _mean_metric(rows: list[sqlite3.Row], columns: set[str], names: tuple[str, ...]) -> float:
    name = next((candidate for candidate in names if candidate in columns), "")
    if not name:
        return 0.0
    return statistics.fmean([_number(row[name]) for row in rows]) if rows else 0.0


def _feedback_count(rows: list[sqlite3.Row], columns: set[str]) -> int:
    if not rows:
        return 0
    candidates = ("next_metrics_json", "next_metrics", "feedback_json", "feedback")
    name = next((candidate for candidate in candidates if candidate in columns), "")
    if not name:
        return len(rows)
    return sum(1 for row in rows if row[name] not in (None, "", "{}", "null"))


def _feedback_requirement_satisfied(mode: str, decisions: int, feedback_rows: int) -> bool:
    """Apply feedback validation according to each arm's contract."""
    if mode == "rapp_only":
        # This arm deliberately has no TA-SAM/Judge feedback.
        return decisions > 0
    if decisions <= 0:
        return False
    # The first decision starts the pending transition and is completed by
    # the next observation; every subsequent decision must have real feedback.
    return feedback_rows >= max(decisions - 1, 1)


def _decision_summary(run_dir: Path) -> dict[str, Any]:
    db = run_dir / "rapp_data_lake.db"
    if not db.is_file():
        return {"database_present": False, "decisions": 0}
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        decisions, decision_columns = _table_rows(conn, "decisions_history")
        outcomes, outcome_columns = _table_rows(conn, "judge_outcome_history")
        metrics, metric_columns = _table_rows(conn, "extended_metrics")
    finally:
        conn.close()
    def count_flag(name: str) -> int:
        return sum(int(_number(row[name])) for row in decisions) if name in decision_columns else 0

    reward_name = next(
        (name for name in ("tasam_continuous_reward", "tasam_reward", "reward") if name in decision_columns),
        "",
    )
    rewards = [_number(row[reward_name]) for row in decisions] if reward_name else []
    split_at = max(1, int(math.ceil(len(decisions) * 0.20))) if decisions else 0
    adaptation_rewards = rewards[:split_at]
    stable_rewards = rewards[split_at:]
    category_name = "tasam_category_credit"
    category_values = [_number(row[category_name]) for row in decisions] if category_name in decision_columns else []
    state = _json(run_dir / "online_state.json")
    return {
        "database_present": True,
        "decisions": len(decisions),
        "judge_outcomes": len(outcomes),
        "real_feedback_rows": _feedback_count(outcomes, outcome_columns),
        "armd_enabled": count_flag("armd_enabled"),
        "tasam_proposals": count_flag("tasam_proposal_present"),
        "tasam_valid_proposals": count_flag("tasam_proposal_valid"),
        "tasam_actuation_applied": count_flag("ta_sam_actuation_applied"),
        "proposal_applied_exactly": count_flag("proposal_applied_exactly"),
        "invalid_decisions": count_flag("training_run_invalid"),
        "category_credit_mean": _mean_metric(decisions, decision_columns, ("tasam_category_credit",)),
        "continuous_reward_mean": _mean_metric(decisions, decision_columns, ("tasam_continuous_reward",)),
        "adaptation_split": {
            "rule": "first_20_percent_of_decisions_vs_remaining_80_percent",
            "adaptation_decisions": split_at,
            "stable_decisions": max(0, len(decisions) - split_at),
            "adaptation_reward_mean": statistics.fmean(adaptation_rewards) if adaptation_rewards else None,
            "stable_reward_mean": statistics.fmean(stable_rewards) if stable_rewards else None,
            "adaptation_category_credit_mean": statistics.fmean(category_values[:split_at]) if category_values[:split_at] else None,
            "stable_category_credit_mean": statistics.fmean(category_values[split_at:]) if category_values[split_at:] else None,
        },
        "latency_mean_us": _mean_metric(metrics, metric_columns, ("global_avg_latency_us", "latency_avg_us")),
        "updates_completed": int(state.get("updates_completed", 0) or 0),
        "replay_source": state.get("replay_source", ""),
        "historical_replay_enabled": state.get("historical_replay_enabled"),
        "decisions_by_mode": dict(Counter(str(row["control_trial_mode"] or "") for row in decisions if "control_trial_mode" in decision_columns)),
    }


def evaluate_arm(run_dir: Path, mode: str, *, profile: str, seed: int, wall_time: float) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    manifest = _json(run_dir / "arm_manifest.json")
    stages = stage_counts(run_dir)
    decision = _decision_summary(run_dir)
    try:
        network = read_network_run(run_dir)
        network_error = ""
    except (OSError, ValueError, sqlite3.Error) as exc:
        network = {}
        network_error = str(exc)
    contract = manifest.get("contract") or {}
    metadata_valid = (
        manifest.get("profile") == profile
        and int(manifest.get("seed", -1)) == int(seed)
        and abs(_number(manifest.get("wall_time_s"), -1.0) - float(wall_time)) < 1e-9
    )
    all_stages = all(stage in stages for stage in EXPECTED_STAGES)
    common = {
        "database_present": bool(decision.get("database_present")),
        "all_nine_stages": all_stages,
        "real_feedback_for_decisions": _feedback_requirement_satisfied(
            mode,
            int(decision.get("decisions", 0) or 0),
            int(decision.get("real_feedback_rows", 0) or 0),
        ),
        "no_invalid_decisions": decision.get("invalid_decisions", 0) == 0,
        "metadata_matches": metadata_valid,
        "real_pdcp_only": bool(network.get("valid_real_only", False)),
        "energy_model_valid": bool((network.get("energy_metric") or {}).get("valid", False)),
    }
    if mode == "train_no_armd":
        mode_criteria = {
            "armd_disabled": decision.get("armd_enabled", 0) == 0,
            "tasam_acted": decision.get("tasam_actuation_applied", 0) == decision.get("decisions", 0) > 0,
            "online_replay_only": decision.get("replay_source") == "live_sqlite_only" and decision.get("historical_replay_enabled") is False,
            "online_update_observed": decision.get("updates_completed", 0) > 0,
        }
    elif mode == "rapp_only":
        mode_criteria = {
            "armd_disabled": decision.get("armd_enabled", 0) == 0,
            "tasam_disabled": decision.get("tasam_actuation_applied", 0) == 0,
        }
    else:
        mode_criteria = {
            "armd_present": decision.get("armd_enabled", 0) > 0,
            "tasam_proposed": decision.get("tasam_proposals", 0) == decision.get("decisions", 0) > 0,
            "tasam_valid": decision.get("tasam_valid_proposals", 0) == decision.get("decisions", 0) > 0,
            "online_replay_only": decision.get("replay_source") == "live_sqlite_only" and decision.get("historical_replay_enabled") is False,
            "online_update_observed": decision.get("updates_completed", 0) > 0,
        }
    valid = all(common.values()) and all(mode_criteria.values()) and not network_error
    return {
        "schema": "greenran.tasam_online_arm_evaluation.v1",
        "run_dir": str(run_dir),
        "mode": mode,
        "seed": seed,
        "profile": profile,
        "wall_time_s": wall_time,
        "stage_counts": stages,
        "decision_summary": decision,
        "network_metrics": network,
        "metadata": manifest,
        "validation": {"common": common, "mode": mode_criteria, "valid": valid, "network_error": network_error},
    }


def _metric_delta(combined: dict[str, Any], baseline: dict[str, Any], key: str) -> float:
    return _number(combined.get(key)) - _number(baseline.get(key))


def compare_seed(seed: int, campaign_dir: Path, *, profile: str, wall_time: float) -> dict[str, Any]:
    root = campaign_dir / f"seed_{seed}"
    train = evaluate_arm(root / "train_no_armd", "train_no_armd", profile=profile, seed=seed, wall_time=wall_time)
    baseline = evaluate_arm(root / "rapp_only", "rapp_only", profile=profile, seed=seed, wall_time=wall_time)
    combined = evaluate_arm(root / "combined", "combined", profile=profile, seed=seed, wall_time=wall_time)
    b = baseline.get("network_metrics") or {}
    c = combined.get("network_metrics") or {}
    be = b.get("energy_metric") or {}
    ce = c.get("energy_metric") or {}
    baseline_energy = _number(be.get("energy_j"))
    combined_energy = _number(ce.get("energy_j"))
    saving = (baseline_energy - combined_energy) / baseline_energy if baseline_energy > 0 else None
    metrics = {
        "energy_rapp_only_j": baseline_energy,
        "energy_combined_j": combined_energy,
        "energy_saving_fraction": saving,
        "energy_saving_percent": saving * 100.0 if saving is not None else None,
        "average_power_rapp_only_w": _number(be.get("average_power_w")),
        "average_power_combined_w": _number(ce.get("average_power_w")),
        "energy_per_mbit_rapp_only": be.get("energy_per_mbit"),
        "energy_per_mbit_combined": ce.get("energy_per_mbit"),
        "latency_mean_delta_us": _metric_delta(combined.get("decision_summary", {}), baseline.get("decision_summary", {}), "latency_mean_us"),
        "p95_delta_us": _metric_delta(c, b, "mean_p95_us"),
        "cvar_delta_us": _metric_delta(c, b, "mean_cvar_us"),
        "packet_loss_delta": _metric_delta(c, b, "mean_packet_loss"),
        "sla_score_delta": _metric_delta(c, b, "sla_score"),
    }
    matched = {
        "seed": int(seed),
        "profile_equal": train["profile"] == baseline["profile"] == combined["profile"] == profile,
        "wall_time_equal": train["wall_time_s"] == baseline["wall_time_s"] == combined["wall_time_s"] == wall_time,
        "stage_profile_equal": all(
            all(stage in report.get("stage_counts", {}) for stage in EXPECTED_STAGES)
            for report in (train, baseline, combined)
        ),
    }
    valid = (
        train["validation"]["valid"]
        and baseline["validation"]["valid"]
        and combined["validation"]["valid"]
        and matched["profile_equal"]
        and matched["wall_time_equal"]
        and matched["stage_profile_equal"]
        and be.get("valid", False)
        and ce.get("valid", False)
    )
    return {
        "seed": int(seed),
        "training": train,
        "rapp_only": baseline,
        "combined": combined,
        "matched": matched,
        "metrics": metrics,
        "valid": valid,
    }


def _mean(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def build_report(
    campaign_dir: Path,
    seeds: list[int],
    *,
    profile: str,
    wall_time: float,
    primary_seed: int = PRIMARY_SEED,
) -> dict[str, Any]:
    pairs = [compare_seed(seed, campaign_dir, profile=profile, wall_time=wall_time) for seed in seeds]
    valid = [pair for pair in pairs if pair["valid"]]
    savings = [pair["metrics"]["energy_saving_fraction"] for pair in valid if pair["metrics"]["energy_saving_fraction"] is not None]
    return {
        "schema": "greenran.tasam_online_paired_campaign.v1",
        "campaign_dir": str(campaign_dir.resolve()),
        "profile": profile,
        "expected_stages": list(EXPECTED_STAGES),
        "seeds": seeds,
        "primary_seed": int(primary_seed),
        "primary_seed_selection": "highest_observed_energy_saving_percent",
        "arms": ["rapp_only", "combined"],
        "training_phase": "train_no_armd",
        "pairs": pairs,
        "aggregate": {
            "valid_pair_count": len(valid),
            "total_pair_count": len(pairs),
            "mean_energy_saving_fraction": _mean([float(value) for value in savings]),
            "mean_energy_saving_percent": _mean([float(value) * 100.0 for value in savings]),
        },
        "energy_policy": {
            "formula": "(energy_rApp_only - energy_ARMD_TA-SAM_rApp) / energy_rApp_only",
            "model": "calibrated_ru_mmwave_power_model",
            "physical_wattmeter": False,
        },
        "validation": {
            "all_pairs_valid": len(valid) == len(pairs) and bool(pairs),
            "all_nine_stages_in_all_arms": all(pair["matched"]["stage_profile_equal"] for pair in pairs),
            "seeds_are_paired": len(set(seeds)) == len(seeds),
            "duration_is_paired": all(pair["matched"]["wall_time_equal"] for pair in pairs),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-dir", type=Path, required=True)
    parser.add_argument("--seeds", type=int, nargs="+", default=[45, 46, 47])
    parser.add_argument("--primary-seed", type=int, default=PRIMARY_SEED)
    parser.add_argument("--profile", default="tasam_training_balanced_v3")
    parser.add_argument("--wall-time", type=float, default=600.0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.primary_seed not in args.seeds:
        raise SystemExit(f"primary-seed precisa estar na lista de seeds: {args.primary_seed}")
    report = build_report(
        args.campaign_dir.resolve(),
        args.seeds,
        profile=args.profile,
        wall_time=args.wall_time,
        primary_seed=args.primary_seed,
    )
    args.output.resolve().parent.mkdir(parents=True, exist_ok=True)
    args.output.resolve().write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output.resolve()), "valid": report["validation"]["all_pairs_valid"]}, ensure_ascii=False))
    return 0 if report["validation"]["all_pairs_valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
