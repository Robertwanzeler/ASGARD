#!/usr/bin/env python3
"""
Serviços do MVP da App1-Vigilancia.

- armazenamento simples de eventos
- leitura de contexto do GreenRAN
- preparação de dados para API e dashboard
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

CURRENT_DIR = Path(__file__).resolve().parent
APP_DIR = CURRENT_DIR.parent
PROJECT_ROOT = APP_DIR.parent.parent
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from greenran_paths import ARTICLE00_SCENARIO_CONTROL_PATH, STATE_DIR  # noqa: E402
from rapp_policy_consumer import summarize_armd_policy  # noqa: E402

ARTICLE00_SCENARIO_CONTROL_FILE = ARTICLE00_SCENARIO_CONTROL_PATH
CAMERA_SLA_THROUGHPUT_MIN_MBPS = 25.0
CAMERA_SLA_THROUGHPUT_GUARD_MBPS = 30.0
CAMERA_SLA_LATENCY_TARGET_MS = 100.0
CAMERA_SLA_LATENCY_GUARD_MS = 60.0
CAMERA_SLA_LATENCY_BLOCK_MS = 80.0


def _safe_read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return fallback


def _sum_unique_video_bytes(videos: List[Dict[str, Any]]) -> int:
    seen_hashes = set()
    total_bytes = 0
    for video in videos:
        content_hash = video.get("content_sha256")
        if content_hash and content_hash in seen_hashes:
            continue
        if content_hash:
            seen_hashes.add(content_hash)
        total_bytes += int(video.get("file_size_bytes", 0))
    return total_bytes


def _extract_camera_network_summary(metrics: Dict[str, Any]) -> Dict[str, Any]:
    control = _safe_read_json(ARTICLE00_SCENARIO_CONTROL_FILE, {})
    override = control.get("app1_camera_override", {}) or {}
    if override.get("enabled", False):
        min_tp = round(float(override.get("throughput_mbps", 0.0) or 0.0), 2)
        avg_tp = round(float(override.get("avg_throughput_mbps", min_tp) or min_tp), 2)
        latency_ms = round(float(override.get("latency_ms", 0.0) or 0.0), 2)
        observed = int(override.get("observed_cameras", override.get("active_cameras", 0)) or 0)
        return {
            "camera_metrics_ready": bool(override.get("throughput_ready", True)),
            "camera_latency_ready": bool(override.get("throughput_ready", True)),
            "observed_camera_metrics": observed,
            "min_camera_throughput_mbps": min_tp,
            "avg_camera_throughput_mbps": avg_tp,
            "observed_min_camera_throughput_mbps": min_tp if observed > 0 else 0.0,
            "max_camera_latency_ms": latency_ms,
            "avg_camera_latency_ms": latency_ms,
            "observed_max_camera_latency_ms": latency_ms if observed > 0 else 0.0,
        }

    ue_metrics = metrics.get("ue_metrics", {}) or {}
    camera_entries = [
        entry for entry in ue_metrics.values() if entry.get("device_type") == "camera"
    ]

    observed_entries = [
        entry for entry in camera_entries
        if (
            bool(entry.get("has_latency_samples"))
            or int(entry.get("packet_count", 0) or 0) > 0
            or float(entry.get("rx_bytes", 0) or 0) > 0
            or float(entry.get("tx_bytes", 0) or 0) > 0
            or float(entry.get("rx_throughput_kbps", entry.get("throughput_kbps", 0)) or 0) > 0
        )
    ]

    throughputs_mbps = [
        float(entry.get("rx_throughput_kbps", entry.get("throughput_kbps", 0)) or 0) / 1000.0
        for entry in camera_entries
    ]
    observed_throughputs_mbps = [
        float(entry.get("rx_throughput_kbps", entry.get("throughput_kbps", 0)) or 0) / 1000.0
        for entry in observed_entries
    ]
    latencies_ms = [
        float(entry.get("latency_us", 0) or 0) / 1000.0
        for entry in camera_entries
    ]
    observed_latencies_ms = [
        float(entry.get("latency_us", 0) or 0) / 1000.0
        for entry in observed_entries
    ]

    return {
        "camera_metrics_ready": bool(camera_entries) and len(observed_entries) == len(camera_entries),
        "camera_latency_ready": bool(camera_entries) and len(observed_entries) == len(camera_entries),
        "observed_camera_metrics": len(observed_entries),
        "min_camera_throughput_mbps": round(min(throughputs_mbps), 2) if throughputs_mbps else 0.0,
        "avg_camera_throughput_mbps": round(sum(throughputs_mbps) / len(throughputs_mbps), 2) if throughputs_mbps else 0.0,
        "observed_min_camera_throughput_mbps": round(min(observed_throughputs_mbps), 2) if observed_throughputs_mbps else 0.0,
        "max_camera_latency_ms": round(max(latencies_ms), 2) if latencies_ms else 0.0,
        "avg_camera_latency_ms": round(sum(latencies_ms) / len(latencies_ms), 2) if latencies_ms else 0.0,
        "observed_max_camera_latency_ms": round(max(observed_latencies_ms), 2) if observed_latencies_ms else 0.0,
    }


def _evaluate_camera_sla(network: Dict[str, Any]) -> Dict[str, Any]:
    active_cameras = int(network.get("active_cameras", 0) or 0)
    metrics_ready = bool(network.get("camera_metrics_ready", False))
    min_throughput_mbps = float(network.get("min_camera_throughput_mbps", 0.0) or 0.0)
    max_latency_ms = float(network.get("max_camera_latency_ms", 0.0) or 0.0)

    targets = {
        "throughput_min_mbps": CAMERA_SLA_THROUGHPUT_MIN_MBPS,
        "throughput_guard_mbps": CAMERA_SLA_THROUGHPUT_GUARD_MBPS,
        "latency_max_ms": CAMERA_SLA_LATENCY_TARGET_MS,
        "latency_guard_ms": CAMERA_SLA_LATENCY_GUARD_MS,
        "latency_block_ms": CAMERA_SLA_LATENCY_BLOCK_MS,
    }
    observed = {
        "min_throughput_mbps": round(min_throughput_mbps, 2),
        "avg_throughput_mbps": round(float(network.get("avg_camera_throughput_mbps", 0.0) or 0.0), 2),
        "max_latency_ms": round(max_latency_ms, 2),
        "avg_latency_ms": round(float(network.get("avg_camera_latency_ms", 0.0) or 0.0), 2),
        "observed_cameras": int(network.get("observed_camera_metrics", 0) or 0),
    }

    if active_cameras <= 0:
        return {
            "ready": False,
            "active_cameras": active_cameras,
            "targets": targets,
            "observed": observed,
            "proposal_status": "idle",
            "proposal_compliant": False,
            "runtime_status": "idle",
            "reason": "Nenhuma câmera ativa.",
        }

    if not metrics_ready:
        return {
            "ready": False,
            "active_cameras": active_cameras,
            "targets": targets,
            "observed": observed,
            "proposal_status": "pending",
            "proposal_compliant": False,
            "runtime_status": "pending",
            "reason": "Aguardando amostras de throughput/latência das câmeras.",
        }

    proposal_reasons = []
    if min_throughput_mbps < CAMERA_SLA_THROUGHPUT_MIN_MBPS:
        proposal_reasons.append(
            f"throughput mínimo {min_throughput_mbps:.1f} Mbps < {CAMERA_SLA_THROUGHPUT_MIN_MBPS:.0f} Mbps"
        )
    if max_latency_ms >= CAMERA_SLA_LATENCY_TARGET_MS:
        proposal_reasons.append(
            f"latência máxima {max_latency_ms:.1f} ms >= {CAMERA_SLA_LATENCY_TARGET_MS:.0f} ms"
        )

    if min_throughput_mbps < CAMERA_SLA_THROUGHPUT_MIN_MBPS or max_latency_ms >= CAMERA_SLA_LATENCY_BLOCK_MS:
        runtime_status = "blocked"
    elif min_throughput_mbps < CAMERA_SLA_THROUGHPUT_GUARD_MBPS or max_latency_ms >= CAMERA_SLA_LATENCY_GUARD_MS:
        runtime_status = "warning"
    else:
        runtime_status = "ok"

    if proposal_reasons:
        proposal_status = "violation"
        proposal_compliant = False
        reason = "; ".join(proposal_reasons)
    else:
        proposal_status = "ok"
        proposal_compliant = True
        if runtime_status == "warning":
            reason = "SLA da proposta atendido, mas o runtime já entrou na margem protegida do core."
        elif runtime_status == "blocked":
            reason = "SLA da proposta atendido parcialmente, mas o core bloqueou pela margem operacional protegida."
        else:
            reason = "SLA de câmera atendido para throughput e latência."

    return {
        "ready": True,
        "active_cameras": active_cameras,
        "targets": targets,
        "observed": observed,
        "proposal_status": proposal_status,
        "proposal_compliant": proposal_compliant,
        "runtime_status": runtime_status,
        "reason": reason,
    }


def _evaluate_simulated_camera_profile(network_profile: Dict[str, Any]) -> Dict[str, Any]:
    if not network_profile:
        return {}

    return _evaluate_camera_sla(
        {
            "active_cameras": 1,
            "camera_metrics_ready": True,
            "min_camera_throughput_mbps": float(network_profile.get("min_throughput_mbps", 0.0) or 0.0),
            "avg_camera_throughput_mbps": float(network_profile.get("avg_throughput_mbps", 0.0) or 0.0),
            "max_camera_latency_ms": float(network_profile.get("max_latency_ms", 0.0) or 0.0),
            "avg_camera_latency_ms": float(network_profile.get("avg_latency_ms", 0.0) or 0.0),
            "observed_camera_metrics": 1,
        }
    )


class VigilanceEventStore:
    """Persistência simples de eventos da App1."""

    MAX_EVENTS = 5000
    PRUNE_TO_EVENTS = 4000

    def __init__(self, state_dir: Path):
        self.state_dir = state_dir
        self.app_state_dir = self.state_dir / "app1_vigilancia"
        self.events_file = self.app_state_dir / "events.json"
        self.app_state_dir.mkdir(parents=True, exist_ok=True)
        if not self.events_file.exists():
            self._write_events([])
        else:
            self._prune_if_needed()

    def _read_events(self) -> List[Dict[str, Any]]:
        return _safe_read_json(self.events_file, [])

    def _write_events(self, events: List[Dict[str, Any]]) -> None:
        with open(self.events_file, "w", encoding="utf-8") as f:
            json.dump(events, f, indent=2)

    def _prune_events(self, events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if len(events) <= self.MAX_EVENTS:
            return events
        events_sorted = sorted(events, key=lambda item: item.get("timestamp", 0), reverse=True)
        pruned = events_sorted[:self.PRUNE_TO_EVENTS]
        return sorted(pruned, key=lambda item: item.get("timestamp", 0))

    def _prune_if_needed(self) -> None:
        events = self._read_events()
        pruned = self._prune_events(events)
        if len(pruned) != len(events):
            self._write_events(pruned)

    def list_events(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        events = sorted(self._read_events(), key=lambda item: item.get("timestamp", 0), reverse=True)
        if limit is not None:
            return events[:limit]
        return events

    def add_event(self, payload: Dict[str, Any], network_context: Dict[str, Any]) -> Dict[str, Any]:
        events = self._read_events()
        event = {
            "id": payload.get("id") or f"evt_{uuid.uuid4().hex[:12]}",
            "timestamp": int(payload.get("timestamp") or time.time()),
            "camera_id": payload.get("camera_id", "CAM-01"),
            "event_type": payload.get("event_type", "suspected_violence"),
            "confidence": float(payload.get("confidence", 0.82)),
            "source": payload.get("source", "mock_video_pipeline"),
            "location": payload.get("location", "UFPA-Campus-Belem"),
            "status": payload.get("status", "detected"),
            "anonymized": bool(payload.get("anonymized", True)),
            "face_revealed": bool(payload.get("face_revealed", False)),
            "clip_reference": payload.get("clip_reference", "sample://clip"),
            "description": payload.get(
                "description",
                "Evento suspeito detectado pela App1-Vigilancia."
            ),
            "network_context": network_context,
        }
        events.append(event)
        self._write_events(self._prune_events(events))
        return event

    def reveal_face(self, event_id: str, reason: str = "security_validation") -> Optional[Dict[str, Any]]:
        events = self._read_events()
        updated = None
        for event in events:
            if event.get("id") != event_id:
                continue
            event["face_revealed"] = True
            event["anonymized"] = False
            event["status"] = "validated"
            event["reveal_reason"] = reason
            event["revealed_at"] = int(time.time())
            updated = event
            break

        if updated is not None:
            self._write_events(self._prune_events(events))
        return updated


class CameraRegistryStore:
    """Cadastro simples das câmeras 4K acompanhadas pela App1."""

    DEFAULT_CAMERAS = [
        {
            "camera_id": "CAM-01",
            "name": "Camera 4K Portao Principal",
            "location": "UFPA-Campus-Belem / Portao Principal",
            "capture_resolution": "3840x2160",
            "inference_resolution": "1280x720",
            "source_mode": "rtsp_stream",
            "source_url": "",
            "enabled": True,
            "criticality": "high",
            "status": "unbound",
            "notes": "Camera 4K de vigilância patrimonial com ingestão automática prevista.",
        },
        {
            "camera_id": "CAM-02",
            "name": "Camera 4K Biblioteca",
            "location": "UFPA-Campus-Belem / Biblioteca Central",
            "capture_resolution": "3840x2160",
            "inference_resolution": "1280x720",
            "source_mode": "rtsp_stream",
            "source_url": "",
            "enabled": True,
            "criticality": "high",
            "status": "unbound",
            "notes": "Camera 4K de vigilância com foco em fluxo de pessoas.",
        },
        {
            "camera_id": "CAM-03",
            "name": "Camera 4K Corredor de Acesso",
            "location": "UFPA-Campus-Belem / Corredor de Acesso",
            "capture_resolution": "3840x2160",
            "inference_resolution": "1280x720",
            "source_mode": "rtsp_stream",
            "source_url": "",
            "enabled": True,
            "criticality": "medium",
            "status": "unbound",
            "notes": "Camera 4K de apoio à vigilância e correlação com o estado da rede.",
        },
    ]

    def __init__(self, state_dir: Path, bootstrap_file: Path | None = None):
        self.state_dir = state_dir
        self.app_state_dir = self.state_dir / "app1_vigilancia"
        self.cameras_file = self.app_state_dir / "cameras.json"
        self.bootstrap_file = bootstrap_file
        self.app_state_dir.mkdir(parents=True, exist_ok=True)
        self.bootstrap_cameras = self._load_bootstrap_cameras()
        if not self.cameras_file.exists():
            self._write_cameras(self.bootstrap_cameras)
        else:
            self.sync_bootstrap_definitions()

    def _load_bootstrap_cameras(self) -> List[Dict[str, Any]]:
        if self.bootstrap_file and self.bootstrap_file.exists():
            cameras = _safe_read_json(self.bootstrap_file, self.DEFAULT_CAMERAS)
            if isinstance(cameras, list) and cameras:
                return cameras
        return self.DEFAULT_CAMERAS

    def _read_cameras(self) -> List[Dict[str, Any]]:
        return _safe_read_json(self.cameras_file, self.bootstrap_cameras)

    def _write_cameras(self, cameras: List[Dict[str, Any]]) -> None:
        with open(self.cameras_file, "w", encoding="utf-8") as f:
            json.dump(cameras, f, indent=2)

    def list_cameras(self) -> List[Dict[str, Any]]:
        return sorted(self._read_cameras(), key=lambda item: item.get("camera_id", ""))

    def sync_bootstrap_definitions(self) -> List[Dict[str, Any]]:
        persisted = self._read_cameras()
        persisted_by_id = {camera.get("camera_id"): camera for camera in persisted if camera.get("camera_id")}
        merged = []

        for camera in self.bootstrap_cameras:
            camera_id = camera.get("camera_id")
            if not camera_id:
                continue
            current = persisted_by_id.pop(camera_id, {})
            merged_camera = {**camera, **current}
            if not current.get("source_url") and camera.get("source_url"):
                for key in [
                    "source_mode",
                    "source_url",
                    "status",
                    "capture_duration_seconds",
                    "ingest_interval_seconds",
                    "notes",
                ]:
                    if key in camera:
                        merged_camera[key] = camera[key]
            merged.append(merged_camera)

        for leftover in persisted_by_id.values():
            merged.append(leftover)

        self._write_cameras(merged)
        return sorted(merged, key=lambda item: item.get("camera_id", ""))

    def upsert_camera(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        cameras = self._read_cameras()
        camera_id = payload.get("camera_id", "").strip() or f"CAM-{len(cameras) + 1:02d}"

        camera_record = {
            "camera_id": camera_id,
            "name": payload.get("name") or f"Camera 4K {camera_id}",
            "location": payload.get("location", "UFPA-Campus-Belem"),
            "capture_resolution": payload.get("capture_resolution", "3840x2160"),
            "inference_resolution": payload.get("inference_resolution", "1280x720"),
            "source_mode": payload.get("source_mode", "rtsp_stream"),
            "source_url": payload.get("source_url", ""),
            "enabled": bool(payload.get("enabled", True)),
            "criticality": payload.get("criticality", "high"),
            "status": payload.get("status", "configured" if payload.get("source_url") else "unbound"),
            "notes": payload.get("notes", ""),
            "updated_at": int(time.time()),
        }

        replaced = False
        for idx, camera in enumerate(cameras):
            if camera.get("camera_id") == camera_id:
                cameras[idx] = {**camera, **camera_record}
                replaced = True
                break
        if not replaced:
            cameras.append(camera_record)

        self._write_cameras(cameras)
        return camera_record

    def refresh_camera_bindings(
        self,
        videos: List[Dict[str, Any]],
        analyses: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        cameras = self._read_cameras()
        refreshed = []

        for camera in cameras:
            camera_id = camera.get("camera_id")
            latest_video = next((item for item in videos if item.get("camera_id") == camera_id), None)
            latest_analysis = next((item for item in analyses if item.get("camera_id") == camera_id), None)

            source_url = camera.get("source_url", "")
            if source_url.startswith("state://"):
                candidate = self.state_dir / source_url[len("state://"):]
                source_status = "online" if candidate.exists() else "offline"
            elif source_url:
                source_status = "configured"
            elif latest_video is not None:
                source_status = "evidence_only"
            else:
                source_status = "unbound"

            latest_timestamp = max(
                int(camera.get("updated_at", 0) or 0),
                int((latest_video or {}).get("timestamp", 0) or 0),
                int((latest_analysis or {}).get("timestamp", 0) or 0),
            )

            refreshed.append(
                {
                    **camera,
                    "status": source_status,
                    "updated_at": latest_timestamp or int(time.time()),
                    "last_video_reference": (latest_video or {}).get("video_reference"),
                    "last_preview_reference": (latest_video or {}).get("preview_reference"),
                    "last_thumbnail_reference": (latest_video or {}).get("thumbnail_reference"),
                    "last_analysis_id": (latest_analysis or {}).get("id"),
                    "last_event_generated": bool((latest_analysis or {}).get("event_generated")),
                    "last_risk_score": float((latest_analysis or {}).get("decision_score", 0.0) or 0.0),
                    "last_pipeline_status": (latest_analysis or {}).get("pipeline_status"),
                    "last_probe_status": (latest_video or {}).get("probe_status"),
                    "fallback_upload_available": latest_video is not None,
                    "can_ingest_now": bool(source_url),
                    "last_auto_ingest_status": camera.get("last_auto_ingest_status"),
                    "last_auto_ingest_error": camera.get("last_auto_ingest_error"),
                    "last_auto_ingest_at": camera.get("last_auto_ingest_at"),
                }
            )

        self._write_cameras(refreshed)
        return sorted(refreshed, key=lambda item: item.get("camera_id", ""))

    def summarize_cameras(self, cameras: List[Dict[str, Any]]) -> Dict[str, Any]:
        enabled = [camera for camera in cameras if camera.get("enabled", True)]
        return {
            "total_cameras": len(cameras),
            "enabled_cameras": len(enabled),
            "bound_sources": sum(1 for camera in cameras if camera.get("status") in {"configured", "online"}),
            "evidence_ready": sum(1 for camera in cameras if camera.get("fallback_upload_available")),
            "critical_active": sum(1 for camera in cameras if camera.get("last_event_generated")),
        }

    def _resolve_source_input(self, source_url: str) -> Optional[str]:
        if not source_url:
            return None
        if source_url.startswith("state://"):
            return str((self.state_dir / source_url[len("state://"):]).resolve())
        return source_url

    def _build_input_args(self, source_input: str) -> List[str]:
        if source_input.startswith("rtsp://"):
            return ["-rtsp_transport", "tcp", "-i", source_input]
        return ["-i", source_input]

    def _load_source_sidecar(self, source_input: str) -> Dict[str, Any]:
        candidate = Path(source_input)
        if not candidate.exists():
            return {}
        sidecar_path = candidate.with_suffix(".json")
        if not sidecar_path.exists():
            return {}
        sidecar = _safe_read_json(sidecar_path, {})
        return sidecar if isinstance(sidecar, dict) else {}

    def _capture_source_clip(self, camera: Dict[str, Any]) -> Dict[str, Any]:
        source_input = self._resolve_source_input(camera.get("source_url", ""))
        if not source_input:
            return {"ok": False, "error": "source_url_missing"}

        capture_dir = self.app_state_dir / "camera_captures"
        capture_dir.mkdir(parents=True, exist_ok=True)
        capture_path = capture_dir / f"{camera['camera_id']}_live_capture.mp4"

        cmd = [
            "ffmpeg",
            "-y",
            *self._build_input_args(source_input),
            "-t",
            str(int(camera.get("capture_duration_seconds", 3) or 3)),
            "-vf",
            f"scale={camera.get('inference_resolution', '1280x720').split('x')[0]}:-2",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            str(capture_path),
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        if result.returncode != 0 or not capture_path.exists():
            return {
                "ok": False,
                "error": result.stderr.strip() or "capture_failed",
            }

        with open(capture_path, "rb") as f:
            content = f.read()

        return {
            "ok": True,
            "filename": f"{camera['camera_id']}_auto_ingest.mp4",
            "content": content,
            "content_type": "video/mp4",
            "source_input": source_input,
            "simulated_metadata": self._load_source_sidecar(source_input),
        }

    def process_camera_once(
        self,
        camera_id: str,
        analysis_store: "VideoAnalysisStore",
        network_context: Dict[str, Any],
        event_store: VigilanceEventStore,
        force: bool = False,
    ) -> Dict[str, Any]:
        cameras = self._read_cameras()
        now = int(time.time())

        for idx, camera in enumerate(cameras):
            if camera.get("camera_id") != camera_id:
                continue

            interval = int(camera.get("ingest_interval_seconds", 30) or 30)
            last_ingest_at = int(camera.get("last_auto_ingest_at", 0) or 0)
            if not force and last_ingest_at and (now - last_ingest_at) < interval:
                return {
                    "camera": camera,
                    "ingest_result": "skipped_interval_guard",
                    "seconds_until_next_ingest": interval - (now - last_ingest_at),
                }

            if not camera.get("enabled", True):
                camera["status"] = "disabled"
                camera["last_auto_ingest_status"] = "disabled"
                camera["last_auto_ingest_at"] = now
                cameras[idx] = camera
                self._write_cameras(cameras)
                return {"camera": camera, "ingest_result": "disabled"}

            capture = self._capture_source_clip(camera)
            if not capture.get("ok"):
                camera["status"] = "offline"
                camera["last_auto_ingest_status"] = "capture_failed"
                camera["last_auto_ingest_error"] = capture.get("error")
                camera["last_auto_ingest_at"] = now
                cameras[idx] = camera
                self._write_cameras(cameras)
                return {"camera": camera, "ingest_result": "capture_failed", "error": capture.get("error")}

            result = analysis_store.add_uploaded_video(
                filename=str(capture["filename"]),
                content=capture["content"],
                payload={
                    "camera_id": camera["camera_id"],
                    "location": camera.get("location", "UFPA-Campus-Belem"),
                    "content_type": capture["content_type"],
                    "source": "camera_auto_ingest",
                    "simulated_metadata": capture.get("simulated_metadata", {}),
                    "notes": f"Ingestão automática da fonte {camera['camera_id']} pela App1-Vigilancia.",
                },
                network_context=network_context,
                event_store=event_store,
            )

            camera["status"] = "online"
            camera["last_auto_ingest_status"] = result.get("ingest_result", "stored_new")
            camera["last_auto_ingest_at"] = now
            camera["last_auto_ingest_error"] = None
            camera["last_video_reference"] = result["video"].get("video_reference")
            camera["last_preview_reference"] = result["video"].get("preview_reference")
            camera["last_thumbnail_reference"] = result["video"].get("thumbnail_reference")
            camera["last_analysis_id"] = result["analysis"].get("id")
            camera["last_event_generated"] = bool(result["analysis"].get("event_generated"))
            camera["last_risk_score"] = float(result["analysis"].get("decision_score", 0.0) or 0.0)
            camera["last_pipeline_status"] = result["analysis"].get("pipeline_status")
            camera["last_probe_status"] = result["video"].get("probe_status")
            camera["fallback_upload_available"] = True
            cameras[idx] = camera
            self._write_cameras(cameras)
            return {"camera": camera, "ingest_result": result.get("ingest_result"), "result": result}

        return {"error": "camera_not_found", "camera_id": camera_id}

    def process_due_cameras(
        self,
        analysis_store: "VideoAnalysisStore",
        network_context: Dict[str, Any],
        event_store: VigilanceEventStore,
    ) -> List[Dict[str, Any]]:
        results = []
        for camera in self._read_cameras():
            if not camera.get("source_url") or not camera.get("enabled", True):
                continue
            results.append(
                self.process_camera_once(
                    camera_id=camera["camera_id"],
                    analysis_store=analysis_store,
                    network_context=network_context,
                    event_store=event_store,
                    force=False,
                )
            )
        return results


class VideoAnalysisStore:
    """Persistência simples de análises de vídeo da App1."""

    DETECTION_THRESHOLD = 0.78
    MIN_THRESHOLD = 0.64
    MAX_THRESHOLD = 0.90

    def __init__(self, state_dir: Path):
        self.state_dir = state_dir
        self.app_state_dir = self.state_dir / "app1_vigilancia"
        self.analyses_file = self.app_state_dir / "video_analyses.json"
        self.videos_file = self.app_state_dir / "uploaded_videos.json"
        self.monitoring_file = self.app_state_dir / "monitoring_snapshot.json"
        self.uploads_dir = self.app_state_dir / "uploads"
        self.artifacts_dir = self.app_state_dir / "artifacts"
        self.app_state_dir.mkdir(parents=True, exist_ok=True)
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        if not self.analyses_file.exists():
            self._write_analyses([])
        if not self.videos_file.exists():
            self._write_videos([])
        if not self.monitoring_file.exists():
            self._write_monitoring_snapshot({})

    def _read_analyses(self) -> List[Dict[str, Any]]:
        return _safe_read_json(self.analyses_file, [])

    def _write_analyses(self, analyses: List[Dict[str, Any]]) -> None:
        with open(self.analyses_file, "w", encoding="utf-8") as f:
            json.dump(analyses, f, indent=2)

    def _read_videos(self) -> List[Dict[str, Any]]:
        return _safe_read_json(self.videos_file, [])

    def _write_videos(self, videos: List[Dict[str, Any]]) -> None:
        with open(self.videos_file, "w", encoding="utf-8") as f:
            json.dump(videos, f, indent=2)

    def _write_monitoring_snapshot(self, snapshot: Dict[str, Any]) -> None:
        with open(self.monitoring_file, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, indent=2)

    def list_analyses(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        analyses = sorted(self._read_analyses(), key=lambda item: item.get("timestamp", 0), reverse=True)
        if limit is not None:
            return analyses[:limit]
        return analyses

    def list_videos(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        videos = sorted(self._read_videos(), key=lambda item: item.get("timestamp", 0), reverse=True)
        if limit is not None:
            return videos[:limit]
        return videos

    def get_monitoring_snapshot(self) -> Dict[str, Any]:
        return _safe_read_json(self.monitoring_file, {})

    def _find_analysis_by_id(self, analysis_id: str | None) -> Optional[Dict[str, Any]]:
        if not analysis_id:
            return None
        for analysis in self._read_analyses():
            if analysis.get("id") == analysis_id:
                return analysis
        return None

    def _find_video_by_content_hash(self, content_hash: str) -> Optional[Dict[str, Any]]:
        for video in self._read_videos():
            if video.get("content_sha256") == content_hash:
                return video
        return None

    def _state_reference(self, path: Path) -> str:
        return f"state://{path.relative_to(self.state_dir)}"

    def _parse_frame_rate(self, frame_rate: str) -> Optional[float]:
        if not frame_rate or frame_rate == "0/0":
            return None
        if "/" in frame_rate:
            numerator, denominator = frame_rate.split("/", 1)
            try:
                denominator_value = float(denominator)
                if denominator_value == 0:
                    return None
                return round(float(numerator) / denominator_value, 3)
            except ValueError:
                return None
        try:
            return round(float(frame_rate), 3)
        except ValueError:
            return None

    def _probe_video(self, path: Path) -> Dict[str, Any]:
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_streams",
            "-show_format",
            str(path),
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=8)
        except Exception as exc:
            return {"valid_video": False, "probe_status": f"probe_failed: {exc}"}

        if result.returncode != 0:
            return {
                "valid_video": False,
                "probe_status": (result.stderr.strip() or "probe_failed"),
            }

        try:
            payload = json.loads(result.stdout or "{}")
        except json.JSONDecodeError:
            return {"valid_video": False, "probe_status": "probe_invalid_json"}

        video_stream = next(
            (stream for stream in payload.get("streams", []) if stream.get("codec_type") == "video"),
            None,
        )
        if video_stream is None:
            return {"valid_video": False, "probe_status": "video_stream_not_found"}

        format_info = payload.get("format", {})
        duration = format_info.get("duration") or video_stream.get("duration")
        frame_rate = self._parse_frame_rate(
            str(video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate") or "0/0")
        )
        return {
            "valid_video": True,
            "probe_status": "ok",
            "duration_s": round(float(duration), 3) if duration is not None else None,
            "fps": frame_rate,
            "width": int(video_stream.get("width") or 0),
            "height": int(video_stream.get("height") or 0),
            "codec_name": video_stream.get("codec_name", "unknown"),
        }

    def _extract_video_artifacts(self, source_path: Path, video_id: str) -> Dict[str, Any]:
        thumbnail_path = self.artifacts_dir / f"{video_id}_thumbnail.jpg"
        preview_path = self.artifacts_dir / f"{video_id}_preview.mp4"

        thumbnail_cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(source_path),
            "-vf",
            "thumbnail,scale=640:-1",
            "-frames:v",
            "1",
            str(thumbnail_path),
        ]
        preview_cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            "0",
            "-i",
            str(source_path),
            "-t",
            "3",
            "-vf",
            "scale=640:-2",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            str(preview_path),
        ]

        artifact_status = "ready"
        error_message = None

        thumb_result = subprocess.run(
            thumbnail_cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
        if thumb_result.returncode != 0:
            artifact_status = "thumbnail_failed"
            error_message = thumb_result.stderr.strip() or "thumbnail_failed"

        preview_result = subprocess.run(
            preview_cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=25,
        )
        if preview_result.returncode != 0:
            artifact_status = "preview_failed" if artifact_status == "ready" else "partial"
            if error_message is None:
                error_message = preview_result.stderr.strip() or "preview_failed"

        return {
            "artifact_status": artifact_status,
            "thumbnail_reference": self._state_reference(thumbnail_path) if thumbnail_path.exists() else None,
            "preview_reference": self._state_reference(preview_path) if preview_path.exists() else None,
            "artifact_error": error_message,
        }

    def _read_pgm_frame(self, frame_path: Path) -> Dict[str, Any]:
        with open(frame_path, "rb") as f:
            header_tokens = []
            while len(header_tokens) < 4:
                line = f.readline()
                if not line:
                    raise ValueError("invalid_pgm_header")
                if line.startswith(b"#"):
                    continue
                header_tokens.extend(line.split())

            magic = header_tokens[0]
            if magic != b"P5":
                raise ValueError("unsupported_pgm_format")

            width = int(header_tokens[1])
            height = int(header_tokens[2])
            max_value = int(header_tokens[3])
            if max_value > 255:
                raise ValueError("unsupported_pgm_depth")

            pixels = f.read(width * height)
            if len(pixels) < width * height:
                raise ValueError("truncated_pgm")

        brightness = sum(pixels) / len(pixels)
        return {
            "width": width,
            "height": height,
            "pixels": pixels,
            "brightness": brightness,
        }

    def _calculate_adaptive_threshold(self, duration_s: int, useful_duration_ratio: float, scene_changes: int) -> float:
        threshold = self.DETECTION_THRESHOLD

        if duration_s <= 2:
            threshold += 0.08
        elif duration_s >= 10:
            threshold -= 0.03

        if useful_duration_ratio >= 0.65:
            threshold -= 0.08
        elif useful_duration_ratio >= 0.40:
            threshold -= 0.03
        elif useful_duration_ratio <= 0.20:
            threshold += 0.08

        if scene_changes >= 3:
            threshold -= 0.05
        elif scene_changes == 0:
            threshold += 0.03

        return round(max(self.MIN_THRESHOLD, min(self.MAX_THRESHOLD, threshold)), 3)

    def _extract_frame_heuristics(
        self,
        source_path: Path,
        video_id: str,
        duration_s: float | None = None,
    ) -> Dict[str, Any]:
        frames_dir = self.artifacts_dir / f"{video_id}_frames"
        frames_dir.mkdir(parents=True, exist_ok=True)
        frame_pattern = frames_dir / "frame_%02d.pgm"

        frame_cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(source_path),
            "-vf",
            "fps=2,scale=64:64,format=gray",
            "-frames:v",
            "6",
            str(frame_pattern),
        ]
        try:
            result = subprocess.run(
                frame_cmd,
                capture_output=True,
                text=True,
                check=False,
                timeout=25,
            )
        except Exception as exc:
            return {
                "heuristic_status": f"frame_extract_failed: {exc}",
                "frames_sampled": 0,
                "motion_intensity": 0.0,
                "brightness_mean": 0.0,
                "brightness_variance": 0.0,
                "scene_changes": 0,
                "visual_risk_score": 0.0,
                "useful_duration_ratio": 0.0,
                "useful_duration_s": 0.0,
                "event_threshold": self.DETECTION_THRESHOLD,
            }

        if result.returncode != 0:
            return {
                "heuristic_status": "frame_extract_failed",
                "frames_sampled": 0,
                "motion_intensity": 0.0,
                "brightness_mean": 0.0,
                "brightness_variance": 0.0,
                "scene_changes": 0,
                "visual_risk_score": 0.0,
                "useful_duration_ratio": 0.0,
                "useful_duration_s": 0.0,
                "event_threshold": self.DETECTION_THRESHOLD,
            }

        frame_paths = sorted(frames_dir.glob("frame_*.pgm"))
        if len(frame_paths) < 2:
            return {
                "heuristic_status": "insufficient_frames",
                "frames_sampled": len(frame_paths),
                "motion_intensity": 0.0,
                "brightness_mean": 0.0,
                "brightness_variance": 0.0,
                "scene_changes": 0,
                "visual_risk_score": 0.0,
                "useful_duration_ratio": 0.0,
                "useful_duration_s": 0.0,
                "event_threshold": self.DETECTION_THRESHOLD,
            }

        frames = [self._read_pgm_frame(path) for path in frame_paths]
        brightness_values = [frame["brightness"] for frame in frames]
        frame_diffs = []
        for current, nxt in zip(frames, frames[1:]):
            diff_sum = sum(abs(a - b) for a, b in zip(current["pixels"], nxt["pixels"]))
            frame_diffs.append(diff_sum / (len(current["pixels"]) * 255.0))

        motion_intensity = sum(frame_diffs) / len(frame_diffs)
        brightness_mean = sum(brightness_values) / len(brightness_values)
        brightness_variance = (
            sum((value - brightness_mean) ** 2 for value in brightness_values) / len(brightness_values)
        )
        scene_changes = sum(1 for diff in frame_diffs if diff >= 0.18)
        active_motion_frames = sum(1 for diff in frame_diffs if diff >= 0.035)
        active_motion_ratio = active_motion_frames / max(1, len(frame_diffs))
        scene_ratio = scene_changes / max(1, len(frame_diffs))
        normalized_brightness_variance = min(1.0, brightness_variance / (255.0 * 255.0))
        scaled_motion = min(1.0, motion_intensity * 8.0)
        scaled_scene = min(1.0, scene_ratio)
        scaled_variance = min(1.0, normalized_brightness_variance * 4.0)
        clip_duration_s = max(1.0, float(duration_s or 0.0))
        duration_factor = min(1.0, clip_duration_s / 8.0)
        useful_duration_ratio = min(1.0, (active_motion_ratio * 0.75) + (duration_factor * 0.25))
        useful_duration_s = round(clip_duration_s * useful_duration_ratio, 3)
        visual_risk_score = min(
            1.0,
            (scaled_motion * 0.45)
            + (scaled_scene * 0.20)
            + (useful_duration_ratio * 0.25)
            + (scaled_variance * 0.10),
        )
        event_threshold = self._calculate_adaptive_threshold(
            duration_s=int(round(clip_duration_s)),
            useful_duration_ratio=useful_duration_ratio,
            scene_changes=scene_changes,
        )

        return {
            "heuristic_status": "ok",
            "frames_sampled": len(frames),
            "motion_intensity": round(motion_intensity, 3),
            "brightness_mean": round(brightness_mean, 3),
            "brightness_variance": round(brightness_variance, 3),
            "scene_changes": scene_changes,
            "visual_risk_score": round(visual_risk_score, 3),
            "useful_duration_ratio": round(useful_duration_ratio, 3),
            "useful_duration_s": useful_duration_s,
            "event_threshold": event_threshold,
            "frames_reference": self._state_reference(frames_dir),
        }

    def _apply_simulated_inference(
        self,
        frame_heuristics: Dict[str, Any],
        simulated_metadata: Dict[str, Any],
    ) -> Dict[str, Any]:
        if not simulated_metadata:
            return {}

        detector_outputs = simulated_metadata.get("detector_outputs", {}) or {}
        pipeline_profile = simulated_metadata.get("pipeline_profile", {}) or {}
        scenario = simulated_metadata.get("scenario", {}) or {}
        network_profile = simulated_metadata.get("network_profile", {}) or {}

        heuristic_visual = float(frame_heuristics.get("visual_risk_score", 0.0) or 0.0)
        heuristic_motion = float(frame_heuristics.get("motion_intensity", 0.0) or 0.0)
        useful_duration_ratio = float(frame_heuristics.get("useful_duration_ratio", 0.0) or 0.0)

        violence_probability = float(detector_outputs.get("violence_probability", heuristic_visual) or heuristic_visual)
        fight_pose_score = float(detector_outputs.get("fight_pose_score", 0.0) or 0.0)
        anomaly_score = float(detector_outputs.get("anomaly_score", 0.0) or 0.0)
        crowd_density = float(detector_outputs.get("crowd_density", 0.0) or 0.0)
        occlusion_score = float(detector_outputs.get("occlusion_score", 0.0) or 0.0)
        motion_level = float(detector_outputs.get("motion_level", heuristic_motion) or heuristic_motion)
        simulated_people = int(detector_outputs.get("simulated_people", 1) or 1)

        decision_score = min(
            1.0,
            (violence_probability * 0.42)
            + (fight_pose_score * 0.26)
            + (anomaly_score * 0.16)
            + (motion_level * 0.10)
            + (crowd_density * 0.04)
            + (useful_duration_ratio * 0.02),
        )
        decision_score = max(decision_score - (occlusion_score * 0.05), 0.0)

        event_threshold = 0.72 if scenario.get("suspicious") else 0.80
        if crowd_density > 0.4 and not scenario.get("suspicious"):
            event_threshold += 0.03
        event_threshold = round(max(self.MIN_THRESHOLD, min(self.MAX_THRESHOLD, event_threshold)), 3)
        simulated_camera_sla = _evaluate_simulated_camera_profile(network_profile)

        return {
            "score_source": pipeline_profile.get("score_source", "simulated_multistage_video_inference"),
            "pipeline_status": "simulated_detector_pipeline",
            "scenario_label": scenario.get("label"),
            "scenario_key": scenario.get("key"),
            "scenario_suspicious": bool(scenario.get("suspicious", False)),
            "violence_score": round(violence_probability, 3),
            "decision_score": round(decision_score, 3),
            "visual_risk_score": round(max(heuristic_visual, decision_score), 3),
            "motion_intensity": round(max(heuristic_motion, motion_level), 3),
            "people_detected": simulated_people,
            "faces_anonymized": int(pipeline_profile.get("faces_anonymized", simulated_people) or simulated_people),
            "event_threshold": event_threshold,
            "requires_manual_review": bool(pipeline_profile.get("requires_manual_review", False)),
            "detector_outputs": detector_outputs,
            "network_profile": network_profile,
            "simulated_camera_sla": simulated_camera_sla,
        }

    def refresh_monitoring_snapshot(
        self,
        network_context: Dict[str, Any],
        event_store: VigilanceEventStore,
        camera_store: CameraRegistryStore | None = None,
    ) -> Dict[str, Any]:
        events = event_store.list_events()
        analyses = self.list_analyses()
        videos = self.list_videos()
        cameras = camera_store.refresh_camera_bindings(videos=videos, analyses=analyses) if camera_store else []

        latest_analysis = analyses[0] if analyses else {}
        latest_video = videos[0] if videos else {}
        camera_mode = str(os.environ.get("GREENRAN_APP1_CAMERA_SOURCE_MODE", "real") or "real").strip().lower()
        network = dict(network_context.get("network", {}) or {})
        camera_sla = dict(network.get("camera_sla", {}) or {})

        observed_camera_metrics = int(network.get("observed_camera_metrics", 0) or 0)
        configured_camera_count = len(cameras)
        if observed_camera_metrics > 0 and int(network.get("active_cameras", 0) or 0) <= 0:
            network["active_cameras"] = max(configured_camera_count, observed_camera_metrics)
        if observed_camera_metrics > 0 and int(network.get("active_ues", 0) or 0) <= 0:
            network["active_ues"] = max(int(network.get("active_cameras", 0) or 0), observed_camera_metrics)

        if camera_mode == "simulated":
            simulated_profile = latest_analysis.get("network_profile", {}) or {}
            simulated_camera_sla = latest_analysis.get("simulated_camera_sla", {}) or {}
            if simulated_profile and simulated_camera_sla:
                total_cameras = len(cameras)
                network.update(
                    {
                        "active_cameras": total_cameras,
                        "active_ues": total_cameras,
                        "throughput_mbps": round(float(simulated_profile.get("avg_throughput_mbps", 0.0) or 0.0), 2),
                        "total_throughput_mbps": round(float(simulated_profile.get("avg_throughput_mbps", 0.0) or 0.0), 2),
                        "min_camera_throughput_mbps": round(float(simulated_profile.get("min_throughput_mbps", 0.0) or 0.0), 2),
                        "avg_camera_throughput_mbps": round(float(simulated_profile.get("avg_throughput_mbps", 0.0) or 0.0), 2),
                        "observed_min_camera_throughput_mbps": round(float(simulated_profile.get("min_throughput_mbps", 0.0) or 0.0), 2),
                        "max_camera_latency_ms": round(float(simulated_profile.get("max_latency_ms", 0.0) or 0.0), 2),
                        "avg_camera_latency_ms": round(float(simulated_profile.get("avg_latency_ms", 0.0) or 0.0), 2),
                        "observed_max_camera_latency_ms": round(float(simulated_profile.get("max_latency_ms", 0.0) or 0.0), 2),
                        "camera_metrics_ready": True,
                        "camera_latency_ready": True,
                        "observed_camera_metrics": total_cameras,
                    }
                )
                camera_sla = simulated_camera_sla

        if not camera_sla and (int(network.get("active_cameras", 0) or 0) > 0 or int(network.get("observed_camera_metrics", 0) or 0) > 0):
            camera_sla = _evaluate_camera_sla(network)
        elif camera_sla and int(camera_sla.get("active_cameras", 0) or 0) <= 0 and int(network.get("active_cameras", 0) or 0) > 0:
            camera_sla = _evaluate_camera_sla(network)

        snapshot = {
            "app": "app1_vigilancia",
            "updated_at": int(time.time()),
            "events": {
                "total": len(events),
                "open": sum(1 for event in events if event.get("status") != "validated"),
                "validated": sum(1 for event in events if event.get("status") == "validated"),
            },
            "analyses": {
                "total": len(analyses),
                "critical": sum(1 for analysis in analyses if analysis.get("event_generated")),
                "latest_camera_id": latest_analysis.get("camera_id"),
                "latest_visual_risk_score": float(latest_analysis.get("visual_risk_score", 0.0) or 0.0),
                "latest_motion_intensity": float(latest_analysis.get("motion_intensity", 0.0) or 0.0),
                "latest_pipeline_status": latest_analysis.get("pipeline_status", "unknown"),
                "latest_camera_runtime_status": ((latest_analysis.get("simulated_camera_sla") or {}).get("runtime_status")),
                "latest_camera_proposal_status": ((latest_analysis.get("simulated_camera_sla") or {}).get("proposal_status")),
            },
            "videos": {
                "uploaded": len(videos),
                "valid_videos": sum(1 for video in videos if video.get("valid_video")),
                "with_artifacts": sum(1 for video in videos if video.get("thumbnail_reference") or video.get("preview_reference")),
                "stored_megabytes": round(_sum_unique_video_bytes(videos) / (1024 * 1024), 3),
                "latest_artifact_status": latest_video.get("artifact_status", "unknown"),
                "latest_probe_status": latest_video.get("probe_status", "unknown"),
            },
            "cameras": {
                "total": len(cameras),
                "enabled": sum(1 for camera in cameras if camera.get("enabled", True)),
                "bound_sources": sum(1 for camera in cameras if camera.get("status") in {"configured", "online"}),
                "evidence_ready": sum(1 for camera in cameras if camera.get("fallback_upload_available")),
                "critical_active": sum(1 for camera in cameras if camera.get("last_event_generated")),
            },
            "network": network,
            "camera_sla": camera_sla,
            "policies": network_context.get("policies", {}),
            "links": {
                "app1_api": "http://localhost:5100/api/monitoring",
                "app1_ui": "http://localhost:5100",
            },
        }
        self._write_monitoring_snapshot(snapshot)
        return snapshot

    def add_analysis(
        self,
        payload: Dict[str, Any],
        network_context: Dict[str, Any],
        event_store: VigilanceEventStore,
    ) -> Dict[str, Any]:
        analyses = self._read_analyses()

        timestamp = int(payload.get("timestamp") or time.time())
        duration_s = int(payload.get("duration_s", 12))
        fps = int(payload.get("fps", 24))
        violence_score = float(payload.get("violence_score", payload.get("confidence", 0.35)))
        motion_score = float(payload.get("motion_score", max(0.2, violence_score - 0.12)))
        decision_score = float(payload.get("decision_score", payload.get("visual_risk_score", violence_score)))
        event_threshold = float(payload.get("event_threshold", self.DETECTION_THRESHOLD))
        people_detected = int(payload.get("people_detected", 1 if violence_score < 0.55 else 2))
        faces_anonymized = int(payload.get("faces_anonymized", people_detected))

        analysis = {
            "id": payload.get("id") or f"analysis_{uuid.uuid4().hex[:12]}",
            "timestamp": timestamp,
            "camera_id": payload.get("camera_id", "CAM-01"),
            "location": payload.get("location", "UFPA-Campus-Belem"),
            "video_reference": payload.get("video_reference", f"sample://video/{timestamp}"),
            "clip_reference": payload.get("clip_reference", f"sample://clip/{timestamp}"),
            "duration_s": duration_s,
            "fps": fps,
            "frames_estimated": int(payload.get("frames_estimated", duration_s * fps)),
            "people_detected": people_detected,
            "faces_anonymized": faces_anonymized,
            "violence_score": round(violence_score, 3),
            "decision_score": round(decision_score, 3),
            "event_threshold": round(event_threshold, 3),
            "motion_score": round(motion_score, 3),
            "visual_risk_score": round(float(payload.get("visual_risk_score", decision_score)), 3),
            "motion_intensity": round(float(payload.get("motion_intensity", motion_score)), 3),
            "scene_changes": int(payload.get("scene_changes", 0)),
            "frames_sampled": int(payload.get("frames_sampled", 0)),
            "useful_duration_ratio": round(float(payload.get("useful_duration_ratio", 0.0)), 3),
            "useful_duration_s": round(float(payload.get("useful_duration_s", 0.0)), 3),
            "source": payload.get("source", "video_analysis_pipeline"),
            "score_source": payload.get("score_source", "external_analysis"),
            "pipeline_status": payload.get("pipeline_status", "processed"),
            "scenario_label": payload.get("scenario_label"),
            "scenario_key": payload.get("scenario_key"),
            "scenario_suspicious": bool(payload.get("scenario_suspicious", False)),
            "requires_manual_review": bool(payload.get("requires_manual_review", False)),
            "detector_outputs": payload.get("detector_outputs", {}),
            "network_profile": payload.get("network_profile", {}),
            "simulated_camera_sla": payload.get("simulated_camera_sla", {}),
            "simulated_metadata": payload.get("simulated_metadata", {}),
            "thumbnail_reference": payload.get("thumbnail_reference"),
            "preview_reference": payload.get("preview_reference"),
            "notes": payload.get(
                "notes",
                "Analise registrada pela App1-Vigilancia a partir de metadados do video.",
            ),
            "network_context": network_context,
            "event_generated": False,
            "generated_event_id": None,
        }

        if decision_score >= event_threshold:
            event = event_store.add_event(
                {
                    "camera_id": analysis["camera_id"],
                    "timestamp": timestamp,
                    "event_type": "suspected_violence",
                    "confidence": decision_score,
                    "source": "video_analysis_pipeline",
                    "location": analysis["location"],
                    "status": "detected",
                    "anonymized": True,
                    "clip_reference": analysis["clip_reference"],
                    "description": (
                        f"Analise de video acima do limiar de risco inferido "
                        f"({round(decision_score * 100)}%, limiar {round(event_threshold * 100)}%)."
                    ),
                },
                network_context=network_context,
            )
            analysis["event_generated"] = True
            analysis["generated_event_id"] = event["id"]

        analyses.append(analysis)
        self._write_analyses(analyses)
        self.refresh_monitoring_snapshot(network_context=network_context, event_store=event_store)
        return analysis

    def add_uploaded_video(
        self,
        filename: str,
        content: bytes,
        payload: Dict[str, Any],
        network_context: Dict[str, Any],
        event_store: VigilanceEventStore,
    ) -> Dict[str, Any]:
        videos = self._read_videos()
        content_sha256 = hashlib.sha256(content).hexdigest()
        existing_video = self._find_video_by_content_hash(content_sha256)
        simulated_metadata = payload.get("simulated_metadata") if isinstance(payload.get("simulated_metadata"), dict) else {}

        timestamp = int(payload.get("timestamp") or time.time())
        safe_name = Path(filename or "video_upload.bin").name
        suffix = Path(safe_name).suffix or ".bin"
        video_id = payload.get("video_id") or f"video_{uuid.uuid4().hex[:12]}"
        stored_name = f"{video_id}{suffix.lower()}"
        duplicate_upload = existing_video is not None
        duplicate_of_video_id = existing_video.get("id") if existing_video is not None else None

        if existing_video is not None:
            stored_name = existing_video.get("stored_filename", stored_name)
            stored_path = self.state_dir / existing_video["relative_path"]
            file_size_bytes = int(existing_video.get("file_size_bytes", len(content)))
            probe = {
                "valid_video": bool(existing_video.get("valid_video")),
                "probe_status": existing_video.get("probe_status", "unknown"),
                "duration_s": existing_video.get("duration_s"),
                "fps": existing_video.get("fps"),
                "width": existing_video.get("width"),
                "height": existing_video.get("height"),
                "codec_name": existing_video.get("codec_name", "unknown"),
            }
            probe_duration = probe.get("duration_s")
            frame_heuristics = {
                "heuristic_status": existing_video.get("heuristic_status", "reused_asset"),
                "frames_reference": existing_video.get("frames_reference"),
                "frames_sampled": int(existing_video.get("frames_sampled", 0)),
                "motion_intensity": float(existing_video.get("motion_intensity", 0.0)),
                "brightness_mean": float(existing_video.get("brightness_mean", 0.0)),
                "brightness_variance": float(existing_video.get("brightness_variance", 0.0)),
                "scene_changes": int(existing_video.get("scene_changes", 0)),
                "visual_risk_score": float(existing_video.get("visual_risk_score", 0.0)),
                "useful_duration_ratio": float(existing_video.get("useful_duration_ratio", 0.0)),
                "useful_duration_s": float(existing_video.get("useful_duration_s", 0.0)),
                "event_threshold": float(existing_video.get("event_threshold", self.DETECTION_THRESHOLD)),
            }
            artifacts = {
                "artifact_status": existing_video.get("artifact_status", "reused_asset"),
                "artifact_error": existing_video.get("artifact_error"),
                "thumbnail_reference": existing_video.get("thumbnail_reference"),
                "preview_reference": existing_video.get("preview_reference"),
            }
        else:
            stored_path = self.uploads_dir / stored_name

            with open(stored_path, "wb") as f:
                f.write(content)

            file_size_bytes = stored_path.stat().st_size
            probe = self._probe_video(stored_path)
            probe_duration = probe.get("duration_s")
            frame_heuristics = (
                self._extract_frame_heuristics(stored_path, video_id, duration_s=probe_duration)
                if probe.get("valid_video")
                else {
                    "heuristic_status": "skipped_invalid_video",
                    "frames_sampled": 0,
                    "motion_intensity": 0.0,
                    "brightness_mean": 0.0,
                    "brightness_variance": 0.0,
                    "scene_changes": 0,
                    "visual_risk_score": 0.0,
                    "useful_duration_ratio": 0.0,
                    "useful_duration_s": 0.0,
                    "event_threshold": self.DETECTION_THRESHOLD,
                    "frames_reference": None,
                }
            )
            artifacts = (
                self._extract_video_artifacts(stored_path, video_id)
                if probe.get("valid_video")
                else {
                    "artifact_status": "skipped_invalid_video",
                    "thumbnail_reference": None,
                    "preview_reference": None,
                    "artifact_error": probe.get("probe_status"),
                }
            )

        duration_s = int(
            payload.get(
                "duration_s",
                round(probe_duration) if probe_duration is not None else max(6, min(30, file_size_bytes // 120_000 or 8)),
            )
        )
        fps = int(payload.get("fps", round(probe.get("fps") or 24)))
        simulated_inference = self._apply_simulated_inference(frame_heuristics, simulated_metadata)
        visual_risk_score = float(
            simulated_inference.get("visual_risk_score", frame_heuristics.get("visual_risk_score", 0.0))
            if probe.get("valid_video")
            else 0.0
        )
        violence_score = float(simulated_inference.get("violence_score", visual_risk_score))
        event_threshold = float(simulated_inference.get("event_threshold", frame_heuristics.get("event_threshold", self.DETECTION_THRESHOLD)))

        video_record = {
            "id": video_id,
            "timestamp": timestamp,
            "original_filename": safe_name,
            "stored_filename": stored_name,
            "content_type": payload.get("content_type", "application/octet-stream"),
            "content_sha256": content_sha256,
            "asset_reused": duplicate_upload,
            "asset_source_video_id": duplicate_of_video_id,
            "file_size_bytes": file_size_bytes,
            "camera_id": payload.get("camera_id", "CAM-01"),
            "location": payload.get("location", "UFPA-Campus-Belem"),
            "duration_s": duration_s,
            "fps": fps,
            "width": int(probe.get("width") or 0),
            "height": int(probe.get("height") or 0),
            "codec_name": probe.get("codec_name", "unknown"),
            "valid_video": bool(probe.get("valid_video")),
            "probe_status": probe.get("probe_status", "unknown"),
            "relative_path": str(stored_path.relative_to(self.state_dir)),
            "video_reference": self._state_reference(stored_path),
            "source": payload.get("source", "uploaded_video_pipeline"),
            "ingest_status": "reused_asset" if duplicate_upload else "stored",
            "heuristic_status": frame_heuristics["heuristic_status"],
            "frames_reference": frame_heuristics.get("frames_reference"),
            "frames_sampled": frame_heuristics["frames_sampled"],
            "motion_intensity": simulated_inference.get("motion_intensity", frame_heuristics["motion_intensity"]),
            "brightness_mean": frame_heuristics["brightness_mean"],
            "brightness_variance": frame_heuristics["brightness_variance"],
            "scene_changes": frame_heuristics["scene_changes"],
            "visual_risk_score": visual_risk_score,
            "useful_duration_ratio": frame_heuristics["useful_duration_ratio"],
            "useful_duration_s": frame_heuristics["useful_duration_s"],
            "event_threshold": event_threshold,
            "artifact_status": artifacts["artifact_status"],
            "artifact_error": artifacts["artifact_error"],
            "thumbnail_reference": artifacts["thumbnail_reference"],
            "preview_reference": artifacts["preview_reference"],
            "score_source": simulated_inference.get("score_source", "video_frame_inference"),
            "scenario_label": simulated_inference.get("scenario_label"),
            "scenario_key": simulated_inference.get("scenario_key"),
            "scenario_suspicious": simulated_inference.get("scenario_suspicious", False),
            "requires_manual_review": simulated_inference.get("requires_manual_review", False),
            "network_profile": simulated_inference.get("network_profile", {}),
            "simulated_camera_sla": simulated_inference.get("simulated_camera_sla", {}),
            "simulated_metadata": simulated_metadata,
        }
        videos.append(video_record)
        self._write_videos(videos)

        analysis = self.add_analysis(
            {
                "timestamp": timestamp,
                "camera_id": video_record["camera_id"],
                "location": video_record["location"],
                "video_reference": video_record["video_reference"],
                "clip_reference": payload.get(
                    "clip_reference",
                    video_record["preview_reference"] or f"{video_record['video_reference']}#clip-start",
                ),
                "duration_s": duration_s,
                "fps": fps,
                "frames_estimated": duration_s * fps,
                "violence_score": violence_score,
                "decision_score": float(simulated_inference.get("decision_score", visual_risk_score)),
                "motion_score": float(max(0.0, simulated_inference.get("motion_intensity", frame_heuristics.get("motion_intensity", 0.0)))),
                "visual_risk_score": visual_risk_score,
                "motion_intensity": simulated_inference.get("motion_intensity", frame_heuristics["motion_intensity"]),
                "scene_changes": frame_heuristics["scene_changes"],
                "frames_sampled": frame_heuristics["frames_sampled"],
                "useful_duration_ratio": frame_heuristics["useful_duration_ratio"],
                "useful_duration_s": frame_heuristics["useful_duration_s"],
                "event_threshold": event_threshold,
                "people_detected": int(payload.get("people_detected", simulated_inference.get("people_detected", 2 if violence_score >= 0.6 else 1))),
                "faces_anonymized": int(payload.get("faces_anonymized", simulated_inference.get("faces_anonymized", 2))),
                "source": video_record["source"],
                "score_source": video_record["score_source"],
                "pipeline_status": simulated_inference.get(
                    "pipeline_status",
                    "artifacts_ready" if video_record["artifact_status"] == "ready" else video_record["artifact_status"],
                ),
                "scenario_label": video_record.get("scenario_label"),
                "scenario_key": video_record.get("scenario_key"),
                "scenario_suspicious": video_record.get("scenario_suspicious", False),
                "requires_manual_review": video_record.get("requires_manual_review", False),
                "detector_outputs": simulated_inference.get("detector_outputs", {}),
                "network_profile": video_record.get("network_profile", {}),
                "simulated_camera_sla": video_record.get("simulated_camera_sla", {}),
                "thumbnail_reference": video_record["thumbnail_reference"],
                "preview_reference": video_record["preview_reference"],
                "simulated_metadata": simulated_metadata,
                "notes": payload.get(
                    "notes",
                    f"Upload {safe_name} armazenado e analisado pela App1-Vigilancia.",
                ),
            },
            network_context=network_context,
            event_store=event_store,
        )

        video_record["analysis_id"] = analysis["id"]
        video_record["event_generated"] = analysis["event_generated"]
        videos[-1] = video_record
        self._write_videos(videos)
        self.refresh_monitoring_snapshot(network_context=network_context, event_store=event_store)
        return {
            "video": video_record,
            "analysis": analysis,
            "duplicate_upload": duplicate_upload,
            "duplicate_of_video_id": duplicate_of_video_id,
            "ingest_result": "duplicate_reused_with_new_submission" if duplicate_upload else "stored_new",
        }


class GreenRANContextReader:
    """Leitura do estado mínimo do GreenRAN para a App1."""

    def __init__(self, state_dir: Path):
        self.state_dir = state_dir
        self.metrics_file = self.state_dir / "xapp_metrics" / "extended_metrics.json"
        self.energy_policy_file = self.state_dir / "rapp_policies" / "energy_policy.json"
        self.slice_policy_file = self.state_dir / "rapp_policies" / "slice_policy.json"
        self.health_file = self.state_dir / "xapp_health.json"
        self.energy_command_file = self.state_dir / "xapp_intents" / "energy_command.json"

    def get_context(self) -> Dict[str, Any]:
        metrics = _safe_read_json(self.metrics_file, {})
        energy_policy = _safe_read_json(self.energy_policy_file, {})
        slice_policy = _safe_read_json(self.slice_policy_file, {})
        health = _safe_read_json(self.health_file, {})
        energy_command = _safe_read_json(self.energy_command_file, {})

        global_metrics = metrics.get("global_metrics", {})
        camera_summary = _extract_camera_network_summary(metrics)
        network = {
            "avg_latency_ms": round(float(global_metrics.get("global_avg_latency_us", 0)) / 1000.0, 2),
            "worst_latency_ms": round(float(global_metrics.get("global_worst_latency_us", 0)) / 1000.0, 2),
            "cvar_ms": round(float(global_metrics.get("cvar_per_ue_us", 0)) / 1000.0, 2),
            "active_cameras": int(global_metrics.get("total_active_cameras", 0)),
            "active_ues": int(global_metrics.get("total_active_ues", 0)),
            "throughput_mbps": round(float(global_metrics.get("throughput_kbps", 0)) / 1000.0, 2),
            "total_throughput_mbps": round(float(global_metrics.get("throughput_kbps", 0)) / 1000.0, 2),
            "min_camera_throughput_mbps": camera_summary["min_camera_throughput_mbps"],
            "avg_camera_throughput_mbps": camera_summary["avg_camera_throughput_mbps"],
            "observed_min_camera_throughput_mbps": camera_summary["observed_min_camera_throughput_mbps"],
            "max_camera_latency_ms": camera_summary["max_camera_latency_ms"],
            "avg_camera_latency_ms": camera_summary["avg_camera_latency_ms"],
            "observed_max_camera_latency_ms": camera_summary["observed_max_camera_latency_ms"],
            "camera_metrics_ready": camera_summary["camera_metrics_ready"],
            "camera_latency_ready": camera_summary["camera_latency_ready"],
            "observed_camera_metrics": camera_summary["observed_camera_metrics"],
        }
        network["camera_sla"] = _evaluate_camera_sla(network)
        return {
            "captured_at": int(time.time()),
            "network": network,
            "policies": {
                "energy_status": energy_policy.get("status", "UNKNOWN"),
                "slice_state": slice_policy.get("slicer_state", "UNKNOWN"),
                "energy_action": energy_command.get("action", "UNKNOWN"),
                "armd": summarize_armd_policy(energy_policy, slice_policy),
            },
            "xapps": health,
        }

    def summarize(self, events: List[Dict[str, Any]]) -> Dict[str, Any]:
        context = self.get_context()
        total = len(events)
        validated = sum(1 for event in events if event.get("status") == "validated")
        open_events = sum(1 for event in events if event.get("status") != "validated")
        return {
            "total_events": total,
            "validated_events": validated,
            "open_events": open_events,
            "network": context["network"],
            "policies": context["policies"],
        }

    def summarize_analyses(self, analyses: List[Dict[str, Any]]) -> Dict[str, Any]:
        total = len(analyses)
        critical = sum(1 for analysis in analyses if analysis.get("event_generated"))
        anonymized_faces = sum(int(analysis.get("faces_anonymized", 0)) for analysis in analyses)
        return {
            "total_analyses": total,
            "critical_analyses": critical,
            "faces_anonymized": anonymized_faces,
        }

    def summarize_videos(self, videos: List[Dict[str, Any]]) -> Dict[str, Any]:
        total = len(videos)
        total_bytes = _sum_unique_video_bytes(videos)
        generated_events = sum(1 for video in videos if video.get("event_generated"))
        return {
            "uploaded_videos": total,
            "stored_megabytes": round(total_bytes / (1024 * 1024), 3),
            "uploads_with_events": generated_events,
        }
