#!/usr/bin/env python3
"""
Standalone DRL sequence smoke test.

Exercises the predictor over a short synthetic sequence so we can inspect
warmup, stabilization and hysteresis behavior without touching the rApp.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.rapp_drl_predictor import DRLPredictor


def make_state(
    *,
    cvar_ms: float,
    cvar_trend: float,
    latency_p95_ms: float,
    throughput_mbps: float,
    active_ues: int,
    active_cameras: int,
    critical_ues: int,
    jitter_ms: float = 2.0,
    packet_loss_pct: float = 0.1,
    variance_ms2: float = 10.0,
) -> dict:
    return {
        "cvar_ms": cvar_ms,
        "cvar_trend": cvar_trend,
        "cvar_acceleration": 0.0,
        "latency_p95_ms": latency_p95_ms,
        "jitter_ms": jitter_ms,
        "packet_loss_pct": packet_loss_pct,
        "throughput_mbps": throughput_mbps,
        "active_ues": active_ues,
        "active_cameras": active_cameras,
        "critical_ues": critical_ues,
        "camera_ratio": active_cameras / max(active_ues, 1),
        "critical_ue_ratio": critical_ues / max(active_ues, 1),
        "allocated_rbs": active_ues * 10,
        "current_power": 20,
        "power_budget": 30,
        "hour_sin": 0.0,
        "hour_cos": 1.0,
        "variance_ms2": variance_ms2,
    }


def demo_sequence() -> list[dict]:
    return [
        make_state(cvar_ms=28, cvar_trend=0, latency_p95_ms=38, throughput_mbps=90, active_ues=18, active_cameras=3, critical_ues=0),
        make_state(cvar_ms=31, cvar_trend=3, latency_p95_ms=41, throughput_mbps=95, active_ues=18, active_cameras=3, critical_ues=0),
        make_state(cvar_ms=36, cvar_trend=5, latency_p95_ms=48, throughput_mbps=110, active_ues=20, active_cameras=3, critical_ues=0),
        make_state(cvar_ms=44, cvar_trend=8, latency_p95_ms=58, throughput_mbps=120, active_ues=20, active_cameras=3, critical_ues=1),
        make_state(cvar_ms=52, cvar_trend=8, latency_p95_ms=66, throughput_mbps=130, active_ues=22, active_cameras=4, critical_ues=2),
        make_state(cvar_ms=59, cvar_trend=7, latency_p95_ms=71, throughput_mbps=140, active_ues=22, active_cameras=4, critical_ues=2),
        make_state(cvar_ms=63, cvar_trend=4, latency_p95_ms=77, throughput_mbps=145, active_ues=22, active_cameras=4, critical_ues=2),
        make_state(cvar_ms=61, cvar_trend=-2, latency_p95_ms=73, throughput_mbps=142, active_ues=22, active_cameras=4, critical_ues=1),
        make_state(cvar_ms=56, cvar_trend=-5, latency_p95_ms=67, throughput_mbps=135, active_ues=20, active_cameras=4, critical_ues=1),
        make_state(cvar_ms=48, cvar_trend=-8, latency_p95_ms=60, throughput_mbps=125, active_ues=20, active_cameras=4, critical_ues=0),
        make_state(cvar_ms=39, cvar_trend=-9, latency_p95_ms=50, throughput_mbps=110, active_ues=18, active_cameras=3, critical_ues=0),
        make_state(cvar_ms=33, cvar_trend=-6, latency_p95_ms=43, throughput_mbps=100, active_ues=18, active_cameras=3, critical_ues=0),
    ]


def main() -> int:
    predictor = DRLPredictor()
    predictor.load_models()
    predictor.reset_history()

    rows = []
    print(
        "cycle  cvar  pred   risk  final        power     policy               conf  warmup  hyst  calib"
    )
    print(
        "-----  ----  -----  ----  -----------  --------  -------------------  ----  ------  ----  -----"
    )
    for idx, state in enumerate(demo_sequence(), start=1):
        result = predictor.predict(state)
        rows.append(
            {
                "cycle": idx,
                "input_cvar_ms": state["cvar_ms"],
                **result,
            }
        )
        print(
            f"{idx:>5}  "
            f"{state['cvar_ms']:>4.0f}  "
            f"{result['predicted_cvar_ms']:>5.1f}  "
            f"{result.get('risk_score', 0.0):>4.2f}  "
            f"{result['final_decision']:<11}  "
            f"{result['power']:<8}  "
            f"{result['policy_action']:<19}  "
            f"{result['confidence']:.2f}  "
            f"{result['warmup_factor']:.2f}    "
            f"{'Y' if result['hysteresis_applied'] else 'N'}     "
            f"{'Y' if result['calibrated'] else 'N'}"
        )

    output_path = Path("/tmp/drl_sequence_smoke.json")
    output_path.write_text(json.dumps(rows, indent=2, ensure_ascii=False))
    print(f"\nSaved detailed trace to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
