#!/usr/bin/env python3
"""
Simulador de fontes de câmera para a App1-Vigilancia.

Gera pequenos clipes locais que a App1 consome como se fossem fontes de câmera
vinculadas. A intenção é manter a demonstração operacional mesmo sem RTSP físico.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parents[2]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from greenran_paths import STATE_DIR  # noqa: E402


CAMERAS = [
    ("CAM-01", "Portao Principal", "testsrc2", "outdoor_gate"),
    ("CAM-02", "Biblioteca Central", "testsrc", "indoor_library"),
    ("CAM-03", "Corredor de Acesso", "testsrc2", "indoor_corridor"),
]

SCENARIOS = {
    "routine_low_flow": {
        "label": "Rotina de baixo fluxo",
        "simulated_people": 1,
        "motion_level": 0.18,
        "crowd_density": 0.10,
        "violence_probability": 0.08,
        "fight_pose_score": 0.06,
        "anomaly_score": 0.12,
        "occlusion_score": 0.05,
        "suspicious": False,
        "network_profile": {
            "min_throughput_mbps": 34.8,
            "avg_throughput_mbps": 36.4,
            "max_latency_ms": 52.0,
            "avg_latency_ms": 38.0,
            "jitter_ms": 6.0,
            "packet_loss_percent": 0.4,
        },
    },
    "crowd_transition": {
        "label": "Mudança de fluxo de pessoas",
        "simulated_people": 4,
        "motion_level": 0.42,
        "crowd_density": 0.46,
        "violence_probability": 0.24,
        "fight_pose_score": 0.18,
        "anomaly_score": 0.28,
        "occlusion_score": 0.16,
        "suspicious": False,
        "network_profile": {
            "min_throughput_mbps": 29.2,
            "avg_throughput_mbps": 31.0,
            "max_latency_ms": 68.0,
            "avg_latency_ms": 57.0,
            "jitter_ms": 11.0,
            "packet_loss_percent": 1.8,
        },
    },
    "tension_precursor": {
        "label": "Pré-incidente com comportamento tenso",
        "simulated_people": 3,
        "motion_level": 0.57,
        "crowd_density": 0.34,
        "violence_probability": 0.56,
        "fight_pose_score": 0.52,
        "anomaly_score": 0.61,
        "occlusion_score": 0.22,
        "suspicious": True,
        "network_profile": {
            "min_throughput_mbps": 26.4,
            "avg_throughput_mbps": 28.6,
            "max_latency_ms": 78.0,
            "avg_latency_ms": 71.0,
            "jitter_ms": 15.0,
            "packet_loss_percent": 3.2,
        },
    },
    "violent_incident": {
        "label": "Incidente violento simulado",
        "simulated_people": 3,
        "motion_level": 0.88,
        "crowd_density": 0.41,
        "violence_probability": 0.94,
        "fight_pose_score": 0.91,
        "anomaly_score": 0.86,
        "occlusion_score": 0.18,
        "suspicious": True,
        "network_profile": {
            "min_throughput_mbps": 23.8,
            "avg_throughput_mbps": 24.9,
            "max_latency_ms": 91.0,
            "avg_latency_ms": 84.0,
            "jitter_ms": 21.0,
            "packet_loss_percent": 7.1,
        },
    },
}

CAMERA_SCENARIO_PLANS = {
    "CAM-01": ["routine_low_flow", "crowd_transition", "tension_precursor", "violent_incident"],
    "CAM-02": ["routine_low_flow", "routine_low_flow", "crowd_transition", "tension_precursor"],
    "CAM-03": ["routine_low_flow", "crowd_transition", "routine_low_flow", "violent_incident"],
}


def _load_cycle_state(source_dir: Path) -> dict:
    state_path = source_dir / "simulator_state.json"
    if not state_path.exists():
        return {}
    try:
        with open(state_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cycle_state(source_dir: Path, state: dict) -> None:
    state_path = source_dir / "simulator_state.json"
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def _select_scenario(camera_id: str, cycle_index: int) -> dict:
    plan = CAMERA_SCENARIO_PLANS.get(camera_id, ["routine_low_flow"])
    scenario_key = plan[cycle_index % len(plan)]
    return {
        "scenario_key": scenario_key,
        **SCENARIOS[scenario_key],
    }


def _scene_filter(camera_id: str, location: str, scene_profile: str, duration_s: float) -> str:
    timestamp_text = datetime.now().strftime("%Y-%m-%d %H\\:%M\\:%S")
    overlays = {
        "outdoor_gate": (
            "eq=contrast=1.08:saturation=1.18:brightness=0.02,"
            "noise=alls=7:allf=t+u,"
            "drawbox=x='mod(t*540,iw-680)':y='ih*0.67+sin(t*1.3)*120':w=420:h=220:color=yellow@0.22:t=fill,"
            "drawbox=x='iw-620-mod(t*320,iw)':y='ih*0.38+cos(t*0.9)*80':w=280:h=150:color=red@0.18:t=fill"
        ),
        "indoor_library": (
            "eq=contrast=1.03:saturation=0.92:brightness=-0.01,"
            "noise=alls=5:allf=t+u,"
            "drawbox=x='iw*0.18+sin(t*0.7)*90':y='ih*0.58':w=310:h=170:color=blue@0.16:t=fill,"
            "drawbox=x='iw*0.64+cos(t*0.9)*60':y='ih*0.46':w=220:h=140:color=green@0.15:t=fill"
        ),
        "indoor_corridor": (
            "eq=contrast=1.06:saturation=0.98:brightness=0.00,"
            "noise=alls=6:allf=t+u,"
            "drawbox=x='iw*0.24+sin(t*1.4)*150':y='ih*0.60':w=260:h=180:color=white@0.18:t=fill,"
            "drawbox=x='iw*0.54+mod(t*210,420)':y='ih*0.33':w=180:h=110:color=orange@0.14:t=fill"
        ),
    }
    overlay_chain = overlays.get(scene_profile, overlays["outdoor_gate"])
    osd = (
        f"drawtext=text='{camera_id} | {location} | SIM 4K | {timestamp_text}':"
        "x=32:y=32:fontsize=42:fontcolor=white:box=1:boxcolor=black@0.45:boxborderw=14,"
        "drawtext=text='5G/FWA uplink target 25 Mbps':"
        "x=32:y=h-92:fontsize=36:fontcolor=white:box=1:boxcolor=black@0.35:boxborderw=10"
    )
    return f"{overlay_chain},{osd},fps=24"


def build_clip(camera_id: str, location: str, scene: str, scene_profile: str, output_path: Path, duration_s: float, resolution: str) -> bool:
    source = f"{scene}=size={resolution}:rate=24:duration={duration_s}"
    filter_chain = _scene_filter(camera_id, location, scene_profile, duration_s)
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        source,
        "-vf",
        filter_chain,
        "-an",
        "-pix_fmt",
        "yuv420p",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-g",
        "48",
        "-b:v",
        "25M",
        "-maxrate",
        "28M",
        "-bufsize",
        "50M",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=45)
    if result.returncode != 0:
        print(f"[App1CameraSim] {camera_id} erro ffmpeg: {result.stderr.strip()}")
        return False
    return output_path.exists() and output_path.stat().st_size > 0


def write_camera_sidecar(
    source_dir: Path,
    camera_id: str,
    location: str,
    resolution: str,
    duration_s: float,
    cycle_index: int,
) -> None:
    scenario = _select_scenario(camera_id, cycle_index)
    sidecar = {
        "generated_at": datetime.now().isoformat(),
        "camera_id": camera_id,
        "location": location,
        "capture_resolution": resolution,
        "duration_s": duration_s,
        "transport_profile": "4k_over_5g_fwa_simulated",
        "scenario": {
            "key": scenario["scenario_key"],
            "label": scenario["label"],
            "cycle_index": cycle_index,
            "suspicious": scenario["suspicious"],
        },
        "detector_outputs": {
            "simulated_people": scenario["simulated_people"],
            "motion_level": scenario["motion_level"],
            "crowd_density": scenario["crowd_density"],
            "violence_probability": scenario["violence_probability"],
            "fight_pose_score": scenario["fight_pose_score"],
            "anomaly_score": scenario["anomaly_score"],
            "occlusion_score": scenario["occlusion_score"],
        },
        "pipeline_profile": {
            "score_source": "simulated_multistage_video_inference",
            "faces_anonymized": scenario["simulated_people"],
            "requires_manual_review": scenario["violence_probability"] >= 0.55,
        },
        "network_profile": {
            **scenario["network_profile"],
            "transport": "5g_fwa_uplink",
            "sla_reference": {
                "throughput_min_mbps": 25.0,
                "throughput_guard_mbps": 30.0,
                "latency_target_ms": 100.0,
                "latency_guard_ms": 60.0,
                "latency_block_ms": 80.0,
            },
        },
    }
    with open(source_dir / f"{camera_id}.json", "w", encoding="utf-8") as f:
        json.dump(sidecar, f, indent=2)


def write_metadata(source_dir: Path, resolution: str, duration_s: float) -> None:
    metadata = {
        "updated_at": datetime.now().isoformat(),
        "mode": "local_simulated_camera_sources",
        "resolution": resolution,
        "duration_s": duration_s,
        "cameras": [
            {
                "camera_id": camera_id,
                "location": location,
                "source_reference": f"state://app1_vigilancia/camera_sources/{camera_id}.mp4",
                "transport": "state_file",
                "simulates": "camera_4k_over_5g_fwa",
                "video_profile": "4k_h264_high_bitrate_with_osd",
            }
            for camera_id, location, _, _ in CAMERAS
        ],
    }
    with open(source_dir / "metadata.json", "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)


def generate_once(source_dir: Path, duration_s: float, resolution: str) -> None:
    source_dir.mkdir(parents=True, exist_ok=True)
    cycle_state = _load_cycle_state(source_dir)
    camera_cycles = cycle_state.get("camera_cycles", {})
    for camera_id, location, scene, scene_profile in CAMERAS:
        output_path = source_dir / f"{camera_id}.mp4"
        ok = build_clip(camera_id, location, scene, scene_profile, output_path, duration_s, resolution)
        cycle_index = int(camera_cycles.get(camera_id, 0) or 0)
        write_camera_sidecar(source_dir, camera_id, location, resolution, duration_s, cycle_index)
        camera_cycles[camera_id] = cycle_index + 1
        status = "OK" if ok else "ERRO"
        print(f"[App1CameraSim] {status} {camera_id} -> {output_path}")
    write_metadata(source_dir, resolution, duration_s)
    _save_cycle_state(
        source_dir,
        {
            "updated_at": datetime.now().isoformat(),
            "camera_cycles": camera_cycles,
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Simula fontes locais de câmera para App1")
    parser.add_argument("--interval", type=float, default=60.0)
    parser.add_argument("--duration", type=float, default=2.0)
    parser.add_argument("--resolution", default="3840x2160")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    source_dir = STATE_DIR / "app1_vigilancia" / "camera_sources"
    while True:
        generate_once(source_dir, args.duration, args.resolution)
        if args.once:
            return
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
