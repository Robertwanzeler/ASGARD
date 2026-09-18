#!/usr/bin/env python3
"""Replay the same real-PDCP states with multiple TA-SAM checkpoints.

The recorded action is the live/canary allocation for each state.  Each
candidate is evaluated on those identical states, without actuating GreenRAN
and without using proxy latency samples.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import tempfile
import sqlite3
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return ""
    return digest.hexdigest()


def validate_sqlite_sample(rows: list[dict[str, Any]]) -> list[str]:
    """Reject a replay whose active/candidate pairing can change silently."""
    reasons: list[str] = []
    decision_ids = [row.get("decision_id") for row in rows]
    if any(value is None for value in decision_ids):
        reasons.append("decision_id_missing")
    if len(set(decision_ids)) != len(decision_ids):
        reasons.append("decision_id_duplicate")
    for row in rows:
        if row.get("source_metric_snapshot_id") is None:
            reasons.append("source_snapshot_missing")
            break
        if row.get("observed_metric_snapshot_id") is None:
            reasons.append("observed_snapshot_missing")
            break
        du_states = row.get("du_states") or []
        if len(du_states) != 3:
            reasons.append("du_state_count_invalid")
            break
        for key in ("global_state",):
            if not isinstance(row.get(key), dict) or not row.get(key):
                reasons.append(f"{key}_missing")
                break
        if reasons:
            break
    return sorted(set(reasons))


def parse_candidate(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("candidate deve estar no formato label=checkpoint_dir")
    label, path = value.split("=", 1)
    label = label.strip()
    if not label or not path.strip():
        raise argparse.ArgumentTypeError("label e checkpoint_dir nao podem ser vazios")
    return label, Path(path).expanduser().resolve()


def load_real_rows(trace: Path, limit: int | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with trace.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            metrics = record.get("metrics") or {}
            if metrics.get("collector_mode") != "pdcp_real":
                continue
            if safe_float(metrics.get("proxy_latency_sample_count"), 0.0) != 0.0:
                continue
            if not record.get("du_states") or not record.get("action"):
                continue
            rows.append(record)
            if limit is not None and len(rows) >= limit:
                break
    return rows


def load_sqlite_rows(database: Path, limit: int | None) -> list[dict[str, Any]]:
    """Build the same compact replay used by training, from SQLite only."""
    import sys

    sys.path.insert(0, str(ROOT / "scripts"))
    from run_tasam_online_controlled import build_sqlite_economic_replay

    max_rows = int(limit) if limit is not None and int(limit) > 0 else 10_000_000
    with tempfile.NamedTemporaryFile(prefix="greenran_candidate_replay_", suffix=".jsonl", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        result = build_sqlite_economic_replay(database, temporary, 0, max_rows)
        if result.get("status") != "ok":
            raise SystemExit(f"replay SQLite inválido: {result}")
        rows = [json.loads(line) for line in temporary.read_text(encoding="utf-8").splitlines() if line.strip()]
    finally:
        temporary.unlink(missing_ok=True)
    with sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        for row in rows:
            timestamp = int(row.get("timestamp", 0) or 0)
            slice_rows = conn.execute(
                """
                SELECT slice_id, ue_count, demand, allocation, qos_pressure,
                       completion_ratio, min_qos_met, budget_share
                  FROM marl_slice_state_history
                 WHERE timestamp = ?
                 ORDER BY slice_id
                """,
                (timestamp,),
            ).fetchall()
            row["slice_state"] = {
                str(item["slice_id"]): {
                    "ue_count": item["ue_count"], "demand": item["demand"],
                    "allocation": item["allocation"], "qos_pressure": item["qos_pressure"],
                    "completion_ratio": item["completion_ratio"],
                    "min_qos_met": item["min_qos_met"], "budget_share": item["budget_share"],
                }
                for item in slice_rows
            }
    return rows


def resource_snapshot(record: dict[str, Any]) -> dict[str, Any]:
    """Rebuild the complete runtime snapshot used by the shadow policy.

    The first version of the replay kept only the aggregate RAN/AI budgets.
    That silently removed camera, vehicle and sensor pressure signals from the
    recorded real-PDCP state.  Both candidates were therefore evaluated under
    an incomplete ``mixed`` context, which could make their final allocation
    look identical even when their slice action vectors differed.
    """
    action = record.get("action") or {}
    economic_action = record.get("economic_action") or {}
    live_candidate = economic_action.get("live_candidate") or {}
    snapshot = action.get("snapshot") or {}
    resource: dict[str, Any] = {
        key: safe_float(action.get(key))
        for key in ("resource_budget", "usable_budget", "d_ran", "d_ai", "r_ran", "r_ai")
    }
    for key in ("ran_components", "ai_components"):
        value = snapshot.get(key) or action.get(key)
        if isinstance(value, dict):
            resource[key] = dict(value)
    resource["scenario_stage"] = record.get("scenario_stage") or record.get("decision_stage_name") or "unknown"
    resource["allocation_state"] = action.get("allocation_state", record.get("allocation_state", "ALLOWED"))
    resource["floor_total_ran"] = safe_float(action.get("floor_total_ran"), 0.0)
    resource["floor_total_ai"] = safe_float(action.get("floor_total_ai"), 0.0)
    resource["floor_feasible"] = action.get("floor_feasible", True)
    resource["floor_verified"] = action.get("floor_verified", True)
    # Energy provenance is compactly retained in the applied-action contract.
    # Reconstruct the live reference without copying the original decision.
    for target, source in (
        ("live_power_percent", "power_percent"),
        ("live_power_w", "power_w"),
        ("live_ru_count", "ru_count"),
        ("live_mmwave_count", "mmwave_count"),
    ):
        if target not in resource and source in live_candidate:
            resource[target] = live_candidate[source]
    resource["live_energy_observation"] = {
        "power_percent": resource.get("live_power_percent"),
        "power_w": resource.get("live_power_w"),
        "ru_count": resource.get("live_ru_count"),
        "mmwave_count": resource.get("live_mmwave_count"),
    }
    return resource


def summarize(results: list[dict[str, Any]], *, include_by_stage: bool = True) -> dict[str, Any]:
    deltas = [safe_float(row.get("score_delta")) for row in results]
    causal = [safe_float(row.get("causal_score_delta")) for row in results]
    ran = [safe_float(row.get("shadow_ran_completion_est")) - safe_float(row.get("live_ran_completion_est")) for row in results]
    ai = [safe_float(row.get("shadow_ai_completion_est")) - safe_float(row.get("live_ai_completion_est")) for row in results]
    positive = [delta > 0.01 for delta in deltas]
    summary = {
        "samples": len(results),
        "avg_score_delta": statistics.fmean(deltas) if deltas else 0.0,
        "avg_causal_score_delta_vs_rapp": statistics.fmean(causal) if causal else 0.0,
        "median_score_delta": statistics.median(deltas) if deltas else 0.0,
        "positive_rate_delta_gt_001": (sum(positive) / len(positive)) if positive else 0.0,
        "recommend_rate": (sum(bool(row.get("recommend_shadow")) for row in results) / len(results)) if results else 0.0,
        "avg_ran_completion_delta": statistics.fmean(ran) if ran else 0.0,
        "avg_ai_completion_delta": statistics.fmean(ai) if ai else 0.0,
        "min_score_delta": min(deltas) if deltas else 0.0,
        "max_score_delta": max(deltas) if deltas else 0.0,
    }
    if include_by_stage:
        summary["by_stage"] = {
            stage: summarize(
                [row for row in results if str(row.get("scenario_stage") or "unknown") == stage],
                include_by_stage=False,
            )
            for stage in sorted({str(row.get("scenario_stage") or "unknown") for row in results})
        }
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace", default=None)
    parser.add_argument("--sqlite-db", default=None, help="SQLite econômico canônico, modo somente leitura")
    parser.add_argument("--candidate", action="append", type=parse_candidate, required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    if not args.trace and not args.sqlite_db:
        raise SystemExit("informe --sqlite-db ou --trace")
    trace = Path(args.trace).resolve() if args.trace else None
    database = Path(args.sqlite_db).resolve() if args.sqlite_db else None
    output = Path(args.output).resolve()
    rows = load_sqlite_rows(database, args.limit) if database else load_real_rows(trace, args.limit)
    if not rows:
        raise SystemExit("nenhum estado pdcp_real sem proxy encontrado")
    sample_errors = validate_sqlite_sample(rows) if database else []

    replay_state = output.parent / ".same_trace_shadow_state"
    replay_state.mkdir(parents=True, exist_ok=True)
    os.environ["GREENRAN_STATE_DIR"] = str(replay_state)
    os.environ["GREENRAN_TASAM_ADVISOR_ENABLED"] = "1"
    os.environ["GREENRAN_TASAM_ADVISOR_MODE"] = "shadow"
    os.environ["GREENRAN_TASAM_GLOBAL_RAN_GUARD"] = "1"
    # This replay selects each checkpoint through its temporary manifest.  Do
    # not let the live runtime bootstrap override point at every candidate.
    os.environ.pop("GREENRAN_TASAM_CHECKPOINT", None)
    os.environ["GREENRAN_TASAM_REQUIRE_CHECKPOINT"] = "0"

    import sys

    sys.path.insert(0, str(ROOT / "src"))
    from rapp_marl_shadow import MARLShadowRuntimeEvaluator

    payload: dict[str, Any] = {
        "evaluation": "same_real_pdcp_trace_shadow_replay",
        "trace": str(trace) if trace else "",
        "sqlite_db": str(database) if database else "",
        "source": "sqlite_economic_replay_v2" if database else "jsonl_real_pdcp",
        "samples": len(rows),
        "proxy_latency_samples": 0,
        "baseline": "recorded_live_allocator_action",
        "runtime_actuation": False,
        "evaluation_status": "candidate_evaluation_blocked" if sample_errors else "ready",
        "sample_validation_errors": sample_errors,
        "sample_sha256": hashlib.sha256(
            json.dumps(rows, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "candidates": {},
    }
    if sample_errors:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        raise SystemExit("candidate_evaluation_blocked: " + ",".join(sample_errors))

    candidate_results: dict[str, list[dict[str, Any]]] = {}
    for label, checkpoint_dir in args.candidate:
        required = checkpoint_dir / "tasam_marl_actors.pt"
        if not required.exists():
            raise SystemExit(f"checkpoint ausente para {label}: {required}")
        manifest = output.parent / f".tasam_manifest_{label}.json"
        manifest.write_text(
            json.dumps(
                {
                    "best_run": {
                        "run_dir": str(checkpoint_dir),
                        "readiness": "shadow_ready",
                        "promote_shadow": True,
                    }
                }
            )
            + "\n",
            encoding="utf-8",
        )
        os.environ["GREENRAN_TASAM_EVAL_MANIFEST"] = str(manifest)
        os.environ["GREENRAN_MARL_SHADOW_POLICY_ID"] = f"ta_sam_same_trace:{label}"
        evaluator = MARLShadowRuntimeEvaluator({"mode": "shadow", "stability_window": 2})
        results: list[dict[str, Any]] = []
        for record in rows:
            state = {
                "topology_id": record.get("topology_id", "greenran_fixed_marl_v1"),
                "du_states": record.get("du_states") or [],
                "global_state": record.get("global_state") or {},
                "slice_state": record.get("slice_state") or {},
                "slice_state": record.get("slice_state") or {},
                # Replay must use the stage captured with this exact PDCP
                # observation, rather than a possibly stale live control file.
                "scenario_stage": record.get("scenario_stage", "unknown"),
            }
            result = evaluator.evaluate(state, resource_snapshot(record))
            comparison = result.get("comparison") or {}
            if comparison:
                results.append({
                    **comparison,
                    "decision_id": record.get("decision_id"),
                    "source_metric_snapshot_id": record.get("source_metric_snapshot_id"),
                    "observed_metric_snapshot_id": record.get("observed_metric_snapshot_id"),
                    "scenario_stage": record.get("scenario_stage", "unknown"),
                })
        candidate_results[label] = results
        payload["candidates"][label] = {
            "checkpoint_dir": str(checkpoint_dir),
            "checkpoint_sha256": sha256_file(required),
            "policy_id": evaluator.policy_id,
            "checkpoint_loaded": evaluator.checkpoint_source == "checkpoint",
            "summary": summarize(results),
        }

    active_rows = candidate_results.get("active", [])
    if active_rows:
        active_by_index = list(active_rows)
        for label, rows_for_candidate in candidate_results.items():
            if label == "active":
                continue
            active_by_id = {
                row.get("decision_id"): row for row in active_rows
                if row.get("decision_id") is not None
            }
            candidate_by_id = {
                row.get("decision_id"): row for row in rows_for_candidate
                if row.get("decision_id") is not None
            }
            common_ids = sorted(set(active_by_id) & set(candidate_by_id))
            paired = [(active_by_id[key], candidate_by_id[key]) for key in common_ids]
            score_deltas = [
                safe_float(candidate_row.get("causal_score_delta"))
                - safe_float(active_row.get("causal_score_delta"))
                for active_row, candidate_row in paired
            ]
            ran_deltas = [
                (safe_float(candidate_row.get("shadow_ran_completion_est"))
                 - safe_float(candidate_row.get("live_ran_completion_est")))
                - (safe_float(active_row.get("shadow_ran_completion_est"))
                   - safe_float(active_row.get("live_ran_completion_est")))
                for active_row, candidate_row in paired
            ]
            ai_deltas = [
                (safe_float(candidate_row.get("shadow_ai_completion_est"))
                 - safe_float(candidate_row.get("live_ai_completion_est")))
                - (safe_float(active_row.get("shadow_ai_completion_est"))
                   - safe_float(active_row.get("live_ai_completion_est")))
                for active_row, candidate_row in paired
            ]
            payload["candidates"][label]["candidate_vs_active"] = {
                "samples": len(paired),
                "decision_ids": common_ids,
                "avg_causal_score_delta": statistics.fmean(score_deltas) if score_deltas else 0.0,
                "avg_ran_completion_delta": statistics.fmean(ran_deltas) if ran_deltas else 0.0,
                "avg_ai_completion_delta": statistics.fmean(ai_deltas) if ai_deltas else 0.0,
                "positive_rate": (
                    sum(value > 0.0 for value in score_deltas) / len(score_deltas)
                    if score_deltas else 0.0
                ),
                "active_policy": "active",
                "candidate_policy": label,
            }

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"salvo em: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
