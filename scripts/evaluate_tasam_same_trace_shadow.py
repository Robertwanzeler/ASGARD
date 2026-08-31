#!/usr/bin/env python3
"""Replay the same real-PDCP states with multiple TA-SAM checkpoints.

The recorded action is the live/canary allocation for each state.  Each
candidate is evaluated on those identical states, without actuating GreenRAN
and without using proxy latency samples.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


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


def resource_snapshot(record: dict[str, Any]) -> dict[str, Any]:
    """Rebuild the complete runtime snapshot used by the shadow policy.

    The first version of the replay kept only the aggregate RAN/AI budgets.
    That silently removed camera, vehicle and sensor pressure signals from the
    recorded real-PDCP state.  Both candidates were therefore evaluated under
    an incomplete ``mixed`` context, which could make their final allocation
    look identical even when their slice action vectors differed.
    """
    action = record.get("action") or {}
    snapshot = action.get("snapshot") or {}
    resource: dict[str, Any] = {
        key: safe_float(action.get(key))
        for key in ("resource_budget", "usable_budget", "d_ran", "d_ai", "r_ran", "r_ai")
    }
    for key in ("ran_components", "ai_components"):
        value = snapshot.get(key)
        if isinstance(value, dict):
            resource[key] = dict(value)
    return resource


def summarize(results: list[dict[str, Any]], *, include_by_stage: bool = True) -> dict[str, Any]:
    deltas = [safe_float(row.get("score_delta")) for row in results]
    ran = [safe_float(row.get("shadow_ran_completion_est")) - safe_float(row.get("live_ran_completion_est")) for row in results]
    ai = [safe_float(row.get("shadow_ai_completion_est")) - safe_float(row.get("live_ai_completion_est")) for row in results]
    positive = [delta > 0.01 for delta in deltas]
    summary = {
        "samples": len(results),
        "avg_score_delta": statistics.fmean(deltas) if deltas else 0.0,
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
    parser.add_argument("--trace", required=True)
    parser.add_argument("--candidate", action="append", type=parse_candidate, required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    trace = Path(args.trace).resolve()
    output = Path(args.output).resolve()
    rows = load_real_rows(trace, args.limit)
    if not rows:
        raise SystemExit("nenhum estado pdcp_real sem proxy encontrado")

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
        "trace": str(trace),
        "samples": len(rows),
        "proxy_latency_samples": 0,
        "baseline": "recorded_live_allocator_action",
        "runtime_actuation": False,
        "candidates": {},
    }

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
                "slice_state": record.get("slice_state") or {},
                # Replay must use the stage captured with this exact PDCP
                # observation, rather than a possibly stale live control file.
                "scenario_stage": record.get("scenario_stage", "unknown"),
            }
            result = evaluator.evaluate(state, resource_snapshot(record))
            comparison = result.get("comparison") or {}
            if comparison:
                results.append({**comparison, "scenario_stage": record.get("scenario_stage", "unknown")})
        payload["candidates"][label] = {
            "checkpoint_dir": str(checkpoint_dir),
            "policy_id": evaluator.policy_id,
            "checkpoint_loaded": evaluator.checkpoint_source == "checkpoint",
            "summary": summarize(results),
        }

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"salvo em: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
