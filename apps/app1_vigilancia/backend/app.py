#!/usr/bin/env python3
"""
App1-Vigilancia.

- API de eventos de vigilancia
- pagina de monitoramento da aplicacao
- integracao com contexto do GreenRAN
"""

from __future__ import annotations

import mimetypes
import sys
import threading
import time
from pathlib import Path

from flask import Flask, abort, jsonify, redirect, render_template, request, send_file, url_for
from werkzeug.utils import secure_filename


CURRENT_DIR = Path(__file__).resolve().parent
APP_DIR = CURRENT_DIR.parent
PROJECT_ROOT = APP_DIR.parent.parent
SRC_DIR = PROJECT_ROOT / "src"
CONFIG_DIR = PROJECT_ROOT / "config" / "core"
APP1_CAMERAS_BOOTSTRAP = CONFIG_DIR / "app1_cameras.json"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from greenran_paths import STATE_DIR  # noqa: E402
from greenran_runtime import load_runtime_config  # noqa: E402
from services import CameraRegistryStore, GreenRANContextReader, VideoAnalysisStore, VigilanceEventStore  # noqa: E402


RUNTIME_CONFIG = load_runtime_config()
APP_HOST = "0.0.0.0"
APP_PORT = 5100

app = Flask(__name__, template_folder=str(APP_DIR / "templates"))

STORE = VigilanceEventStore(STATE_DIR)
ANALYSIS_STORE = VideoAnalysisStore(STATE_DIR)
CAMERA_STORE = CameraRegistryStore(STATE_DIR, bootstrap_file=APP1_CAMERAS_BOOTSTRAP)
CONTEXT = GreenRANContextReader(STATE_DIR)
MONITORING_REFRESH_SECONDS = 5
CAMERA_INGEST_POLL_SECONDS = 10


def _state_reference_to_path(reference: str | None) -> Path | None:
    if not reference or not reference.startswith("state://"):
        return None
    relative = reference[len("state://"):]
    candidate = (STATE_DIR / relative).resolve()
    state_root = STATE_DIR.resolve()
    try:
        candidate.relative_to(state_root)
    except ValueError:
        return None
    return candidate


def _media_url_from_path(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        relative = path.resolve().relative_to(STATE_DIR.resolve())
    except ValueError:
        return None
    return url_for("serve_media", relative_path=str(relative))


def _media_url_from_reference(reference: str | None) -> str | None:
    return _media_url_from_path(_state_reference_to_path(reference))


def _enrich_video_record(video: dict) -> dict:
    enriched = dict(video)
    file_path = (STATE_DIR / video["relative_path"]).resolve() if video.get("relative_path") else None
    enriched["media_url"] = _media_url_from_path(file_path)
    enriched["thumbnail_url"] = _media_url_from_reference(video.get("thumbnail_reference"))
    enriched["preview_url"] = _media_url_from_reference(video.get("preview_reference"))
    enriched["frames_url"] = _media_url_from_reference(video.get("frames_reference"))
    return enriched


def _enrich_analysis_record(analysis: dict) -> dict:
    enriched = dict(analysis)
    enriched["thumbnail_url"] = _media_url_from_reference(analysis.get("thumbnail_reference"))
    enriched["preview_url"] = _media_url_from_reference(analysis.get("preview_reference"))
    enriched["video_url"] = _media_url_from_reference(analysis.get("video_reference"))
    enriched["clip_url"] = _media_url_from_reference(analysis.get("clip_reference"))
    return enriched


def _enrich_upload_result(result: dict) -> dict:
    enriched = dict(result)
    if result.get("video"):
        enriched["video"] = _enrich_video_record(result["video"])
    if result.get("analysis"):
        enriched["analysis"] = _enrich_analysis_record(result["analysis"])
    return enriched


def _enrich_camera_record(camera: dict) -> dict:
    enriched = dict(camera)
    source_url = (camera.get("source_url") or "").strip()
    source_mode = (camera.get("source_mode") or "").strip()
    is_simulated_source = (
        source_url.startswith("state://camera_sources/")
        or source_url.startswith("state://app1_vigilancia/camera_sources/")
    )
    source_mode_labels = {
        "rtsp_stream": "RTSP",
        "state_file": "Arquivo local",
        "http_stream": "HTTP/HLS",
    }
    if is_simulated_source:
        enriched["source_mode_label"] = "Simulação local 4K"
        enriched["source_binding_label"] = "câmera simulada vinculada"
    else:
        enriched["source_mode_label"] = source_mode_labels.get(source_mode, source_mode or "não definido")
        enriched["source_binding_label"] = "stream vinculado" if source_url else "fonte pendente"
    enriched["is_simulated_source"] = is_simulated_source
    enriched["display_source_url"] = source_url or "não definida"
    enriched["evidence_label"] = "Evidência 4K simulada" if is_simulated_source else "Evidência original"
    enriched["preview_label"] = "Preview analítico"
    enriched["last_video_url"] = _media_url_from_reference(camera.get("last_video_reference"))
    enriched["last_preview_url"] = _media_url_from_reference(camera.get("last_preview_reference"))
    enriched["last_thumbnail_url"] = _media_url_from_reference(camera.get("last_thumbnail_reference"))
    return enriched


def ensure_monitoring_snapshot():
    snapshot = ANALYSIS_STORE.get_monitoring_snapshot()
    if snapshot:
        return snapshot
    return ANALYSIS_STORE.refresh_monitoring_snapshot(
        network_context=CONTEXT.get_context(),
        event_store=STORE,
        camera_store=CAMERA_STORE,
    )


def monitoring_refresh_loop():
    while True:
        try:
            ANALYSIS_STORE.refresh_monitoring_snapshot(
                network_context=CONTEXT.get_context(),
                event_store=STORE,
                camera_store=CAMERA_STORE,
            )
        except Exception:
            pass
        time.sleep(MONITORING_REFRESH_SECONDS)


def camera_ingest_loop():
    while True:
        try:
            CAMERA_STORE.process_due_cameras(
                analysis_store=ANALYSIS_STORE,
                network_context=CONTEXT.get_context(),
                event_store=STORE,
            )
            ANALYSIS_STORE.refresh_monitoring_snapshot(
                network_context=CONTEXT.get_context(),
                event_store=STORE,
                camera_store=CAMERA_STORE,
            )
        except Exception:
            pass
        time.sleep(CAMERA_INGEST_POLL_SECONDS)


@app.route("/")
def index():
    events = STORE.list_events(limit=20)
    analyses = [_enrich_analysis_record(item) for item in ANALYSIS_STORE.list_analyses(limit=12)]
    videos = [_enrich_video_record(item) for item in ANALYSIS_STORE.list_videos(limit=8)]
    cameras = [_enrich_camera_record(item) for item in CAMERA_STORE.refresh_camera_bindings(videos=videos, analyses=analyses)]
    summary = CONTEXT.summarize(events)
    analysis_summary = CONTEXT.summarize_analyses(analyses)
    video_summary = CONTEXT.summarize_videos(videos)
    camera_summary = CAMERA_STORE.summarize_cameras(cameras)
    return render_template(
        "index.html",
        events=events,
        analyses=analyses,
        videos=videos,
        cameras=cameras,
        summary=summary,
        analysis_summary=analysis_summary,
        video_summary=video_summary,
        camera_summary=camera_summary,
        upload_status=request.args.get("upload_status"),
        upload_message=request.args.get("upload_message"),
        camera_status=request.args.get("camera_status"),
        camera_message=request.args.get("camera_message"),
    )


@app.route("/api/health")
def health():
    return jsonify(
        {
            "status": "ok",
            "app": "app1_vigilancia",
            "state_dir": str(STATE_DIR),
            "events": len(STORE.list_events()),
            "video_analyses": len(ANALYSIS_STORE.list_analyses()),
            "uploaded_videos": len(ANALYSIS_STORE.list_videos()),
            "registered_cameras": len(CAMERA_STORE.list_cameras()),
            "camera_bootstrap_file": str(APP1_CAMERAS_BOOTSTRAP),
        }
    )


@app.route("/api/monitoring")
def monitoring_snapshot():
    return jsonify(ensure_monitoring_snapshot())


@app.route("/api/network-context")
def network_context():
    return jsonify(CONTEXT.get_context())


@app.route("/api/events")
def list_events():
    limit = request.args.get("limit", type=int)
    return jsonify({"events": STORE.list_events(limit=limit)})


@app.route("/api/video-analyses")
def list_video_analyses():
    limit = request.args.get("limit", type=int)
    analyses = [_enrich_analysis_record(item) for item in ANALYSIS_STORE.list_analyses(limit=limit)]
    return jsonify({"analyses": analyses})


@app.route("/api/videos")
def list_uploaded_videos():
    limit = request.args.get("limit", type=int)
    videos = [_enrich_video_record(item) for item in ANALYSIS_STORE.list_videos(limit=limit)]
    return jsonify({"videos": videos})


@app.route("/api/cameras")
def list_cameras():
    cameras = [_enrich_camera_record(item) for item in CAMERA_STORE.refresh_camera_bindings(
        videos=ANALYSIS_STORE.list_videos(),
        analyses=ANALYSIS_STORE.list_analyses(),
    )]
    return jsonify({"cameras": cameras})


@app.route("/api/cameras", methods=["POST"])
def upsert_camera():
    payload = request.get_json(silent=True) or {}
    camera = _enrich_camera_record(CAMERA_STORE.upsert_camera(payload))
    ANALYSIS_STORE.refresh_monitoring_snapshot(
        network_context=CONTEXT.get_context(),
        event_store=STORE,
        camera_store=CAMERA_STORE,
    )
    return jsonify(camera), 201


@app.route("/api/cameras/<camera_id>/ingest-now", methods=["POST"])
def ingest_camera_now(camera_id: str):
    result = CAMERA_STORE.process_camera_once(
        camera_id=camera_id,
        analysis_store=ANALYSIS_STORE,
        network_context=CONTEXT.get_context(),
        event_store=STORE,
        force=True,
    )
    ANALYSIS_STORE.refresh_monitoring_snapshot(
        network_context=CONTEXT.get_context(),
        event_store=STORE,
        camera_store=CAMERA_STORE,
    )
    if result.get("error") == "camera_not_found":
        return jsonify(result), 404
    return jsonify(result), 200


@app.route("/cameras/<camera_id>/ingest-now", methods=["POST"])
def ingest_camera_now_form(camera_id: str):
    result = CAMERA_STORE.process_camera_once(
        camera_id=camera_id,
        analysis_store=ANALYSIS_STORE,
        network_context=CONTEXT.get_context(),
        event_store=STORE,
        force=True,
    )
    ANALYSIS_STORE.refresh_monitoring_snapshot(
        network_context=CONTEXT.get_context(),
        event_store=STORE,
        camera_store=CAMERA_STORE,
    )
    if result.get("error") == "camera_not_found":
        return redirect(
            url_for(
                "index",
                camera_status="error",
                camera_message=f"Câmera {camera_id} não encontrada.",
            )
        )
    if result.get("error") == "source_url_missing":
        return redirect(
            url_for(
                "index",
                camera_status="error",
                camera_message=f"A câmera {camera_id} ainda não tem source_url configurada.",
            )
        )
    if result.get("ingest_result") == "capture_failed":
        return redirect(
            url_for(
                "index",
                camera_status="error",
                camera_message=f"Falha ao capturar a fonte da câmera {camera_id}: {result.get('error', 'erro_desconhecido')}.",
            )
        )
    return redirect(
        url_for(
            "index",
            camera_status="ok",
            camera_message=f"Ingestão da câmera {camera_id} executada.",
        )
    )


@app.route("/cameras/configure", methods=["POST"])
def configure_camera_form():
    payload = {
        "camera_id": request.form.get("camera_id", "").strip(),
        "name": request.form.get("name", "").strip(),
        "location": request.form.get("location", "").strip(),
        "capture_resolution": request.form.get("capture_resolution", "").strip() or "3840x2160",
        "inference_resolution": request.form.get("inference_resolution", "").strip() or "1280x720",
        "source_mode": request.form.get("source_mode", "").strip() or "rtsp_stream",
        "source_url": request.form.get("source_url", "").strip(),
        "criticality": request.form.get("criticality", "").strip() or "high",
        "notes": request.form.get("notes", "").strip(),
        "enabled": request.form.get("enabled") == "on",
    }
    camera = CAMERA_STORE.upsert_camera(payload)
    ANALYSIS_STORE.refresh_monitoring_snapshot(
        network_context=CONTEXT.get_context(),
        event_store=STORE,
        camera_store=CAMERA_STORE,
    )
    return redirect(
        url_for(
            "index",
            camera_status="ok",
            camera_message=f"Fonte da câmera {camera['camera_id']} atualizada.",
        )
    )


@app.route("/media/<path:relative_path>")
def serve_media(relative_path: str):
    candidate = (STATE_DIR / relative_path).resolve()
    state_root = STATE_DIR.resolve()
    try:
        candidate.relative_to(state_root)
    except ValueError:
        abort(404)
    if not candidate.exists() or not candidate.is_file():
        abort(404)
    mimetype, _ = mimetypes.guess_type(str(candidate))
    return send_file(candidate, mimetype=mimetype, conditional=True)


@app.route("/api/events/mock", methods=["POST"])
def create_mock_event():
    payload = request.get_json(silent=True) or {}
    event = STORE.add_event(payload, network_context=CONTEXT.get_context())
    ANALYSIS_STORE.refresh_monitoring_snapshot(
        network_context=CONTEXT.get_context(),
        event_store=STORE,
        camera_store=CAMERA_STORE,
    )
    return jsonify(event), 201


@app.route("/api/video-analyses", methods=["POST"])
def create_video_analysis():
    payload = request.get_json(silent=True) or {}
    analysis = ANALYSIS_STORE.add_analysis(
        payload,
        network_context=CONTEXT.get_context(),
        event_store=STORE,
    )
    ANALYSIS_STORE.refresh_monitoring_snapshot(
        network_context=CONTEXT.get_context(),
        event_store=STORE,
        camera_store=CAMERA_STORE,
    )
    return jsonify(analysis), 201


@app.route("/api/videos/upload", methods=["POST"])
def upload_video():
    uploaded = request.files.get("video")
    if uploaded is None or not uploaded.filename:
        return jsonify({"error": "video_required"}), 400

    filename = secure_filename(uploaded.filename) or "video_upload.bin"
    payload = dict(request.form)
    payload["content_type"] = uploaded.mimetype or "application/octet-stream"

    result = ANALYSIS_STORE.add_uploaded_video(
        filename=filename,
        content=uploaded.read(),
        payload=payload,
        network_context=CONTEXT.get_context(),
        event_store=STORE,
    )
    ANALYSIS_STORE.refresh_monitoring_snapshot(
        network_context=CONTEXT.get_context(),
        event_store=STORE,
        camera_store=CAMERA_STORE,
    )
    return jsonify(_enrich_upload_result(result)), 201


@app.route("/upload", methods=["POST"])
def upload_video_form():
    uploaded = request.files.get("video")
    if uploaded is None or not uploaded.filename:
        return redirect(url_for("index", upload_status="error", upload_message="Selecione um arquivo de vídeo."))

    filename = secure_filename(uploaded.filename) or "video_upload.bin"
    payload = dict(request.form)
    payload["content_type"] = uploaded.mimetype or "application/octet-stream"

    result = ANALYSIS_STORE.add_uploaded_video(
        filename=filename,
        content=uploaded.read(),
        payload=payload,
        network_context=CONTEXT.get_context(),
        event_store=STORE,
    )
    ANALYSIS_STORE.refresh_monitoring_snapshot(
        network_context=CONTEXT.get_context(),
        event_store=STORE,
        camera_store=CAMERA_STORE,
    )
    if result.get("duplicate_upload"):
        return redirect(
            url_for(
                "index",
                upload_status="ok",
                upload_message="Conteúdo duplicado detectado; o arquivo existente foi reaproveitado e uma nova submissão foi registrada.",
            )
        )
    return redirect(url_for("index", upload_status="ok", upload_message="Vídeo enviado e processado com sucesso."))


@app.route("/api/events/<event_id>/reveal-face", methods=["POST"])
def reveal_face(event_id: str):
    payload = request.get_json(silent=True) or {}
    updated = STORE.reveal_face(event_id, reason=payload.get("reason", "security_validation"))
    if updated is None:
        return jsonify({"error": "event_not_found", "id": event_id}), 404
    ANALYSIS_STORE.refresh_monitoring_snapshot(
        network_context=CONTEXT.get_context(),
        event_store=STORE,
        camera_store=CAMERA_STORE,
    )
    return jsonify(updated)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="App1-Vigilancia")
    parser.add_argument("--host", default=APP_HOST, help="Host para bind")
    parser.add_argument("--port", type=int, default=APP_PORT, help="Porta da App1")
    parser.add_argument("--debug", action="store_true", help="Modo debug")
    args = parser.parse_args()

    print("=" * 60)
    print("App1-Vigilancia")
    print("=" * 60)
    print(f"Host: {args.host}")
    print(f"Port: {args.port}")
    print(f"GreenRAN State Dir: {STATE_DIR}")
    print(f"Dashboard GreenRAN: http://localhost:{RUNTIME_CONFIG['dashboard']['port']}")
    print("=" * 60)

    ensure_monitoring_snapshot()
    monitoring_thread = threading.Thread(target=monitoring_refresh_loop, daemon=True)
    monitoring_thread.start()
    ingest_thread = threading.Thread(target=camera_ingest_loop, daemon=True)
    ingest_thread.start()
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
