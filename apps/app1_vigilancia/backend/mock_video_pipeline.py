#!/usr/bin/env python3
"""
Gerador simples de eventos simulados para a App1-Vigilancia.
"""

from __future__ import annotations

import argparse
import json
import random
import time
from urllib import request


DEFAULT_URL = "http://127.0.0.1:5100/api/video-analyses"


def send_event(url: str, camera_id: str, location: str, confidence: float) -> None:
    duration_s = random.randint(8, 18)
    payload = {
        "camera_id": camera_id,
        "video_reference": f"sample://video/{camera_id}/{int(time.time())}",
        "clip_reference": f"sample://clip/{camera_id}/{int(time.time())}",
        "duration_s": duration_s,
        "fps": 24,
        "violence_score": confidence,
        "motion_score": round(max(0.2, confidence - 0.1), 2),
        "people_detected": random.randint(1, 4),
        "faces_anonymized": 2,
        "source": "mock_video_pipeline",
        "location": location,
        "notes": "Analise simulada gerada pelo pipeline da App1-Vigilancia.",
    }

    data = json.dumps(payload).encode("utf-8")
    req = request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with request.urlopen(req, timeout=5) as resp:
        print(resp.read().decode("utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Mock pipeline da App1-Vigilancia")
    parser.add_argument("--url", default=DEFAULT_URL, help="Endpoint da App1")
    parser.add_argument("--camera-id", default="CAM-01", help="Identificador da camera")
    parser.add_argument("--location", default="Portao Principal UFPA", help="Local do evento")
    parser.add_argument("--confidence", type=float, default=None, help="Score de violencia estimado")
    args = parser.parse_args()

    confidence = args.confidence if args.confidence is not None else round(random.uniform(0.72, 0.96), 2)
    send_event(args.url, args.camera_id, args.location, confidence)


if __name__ == "__main__":
    main()
