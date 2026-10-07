from __future__ import annotations

import json
from pathlib import Path

from scripts.freeze_tasam_v2x_replay_window import freeze
from scripts.run_tasam_online_controlled import build_v2x_replay_window90


def _row(index: int) -> dict:
    return {
        "replay_phase": "r26",
        "replay_episode": "window90_seed43",
        "replay_seed": 43,
        "replay_timestamp": index,
        "timestamp": index,
        "scenario_control_override": False,
        "collection_quality": {
            "valid_for_training": True,
            "collector_mode": "pdcp_real",
            "pdcp_real": True,
            "proxy_latency_sample_count": 0,
            "metric_alignment_valid": True,
            "current_metric_skew_s": 0,
            "next_metric_skew_s": 0,
        },
        "next_metrics": {"packet_loss_percent": 0.0},
        "decision": {
            "e2_ack_complete": True,
            "native_readback_observed": True,
        },
        "action_correlation_valid": True,
        "judge_feedback": {},
        "adaptive_reward": {
            "reward_contract": "greenran.tasam.v2x.reward_adaptive.v1",
            "reward": 1.0,
        },
        "reward_contract": "greenran.tasam.v2x.reward_adaptive.v1",
    }


def test_freeze_splits_exact_72_18_and_preserves_source(tmp_path: Path):
    source = tmp_path / "replay_90.jsonl"
    source.write_text(
        "".join(json.dumps(_row(index), sort_keys=True) + "\n" for index in range(90)),
        encoding="utf-8",
    )
    historical = tmp_path / "campaign" / "historical.jsonl"
    recent = tmp_path / "campaign" / "recent.jsonl"
    manifest = tmp_path / "campaign" / "freeze.json"
    before = source.read_bytes()
    payload = freeze(
        type("Args", (), {
            "source": source,
            "historical_output": historical,
            "recent_output": recent,
            "manifest": manifest,
            "seed": 43,
        })()
    )
    assert payload["historical_rows"] == 72
    assert payload["recent_rows"] == 18
    assert source.read_bytes() == before
    assert len(recent.read_text(encoding="utf-8").splitlines()) == 18
    rows = [json.loads(line) for line in recent.read_text(encoding="utf-8").splitlines()]
    assert all(row["frozen_recent_replay"] is True for row in rows)

    replay = build_v2x_replay_window90(
        historical, recent, tmp_path / "campaign" / "replay.jsonl",
        seed=43, run_seed=43, strict_recent_evidence=True,
    )
    assert replay["status"] == "ready"
    assert replay["historical_used"] == 72
    assert replay["recent_used"] == 18
