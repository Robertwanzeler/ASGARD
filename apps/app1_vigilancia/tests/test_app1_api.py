#!/usr/bin/env python3

import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from io import BytesIO
from pathlib import Path


class App1ApiTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="greenran_app1_test_")
        os.environ["GREENRAN_STATE_DIR"] = self.temp_dir

        project_root = Path(__file__).resolve().parents[3]
        backend_dir = project_root / "apps" / "app1_vigilancia" / "backend"
        src_dir = project_root / "src"

        if str(backend_dir) not in sys.path:
            sys.path.insert(0, str(backend_dir))
        if str(src_dir) not in sys.path:
            sys.path.insert(0, str(src_dir))

        for module_name in [
            "greenran_paths",
            "greenran_runtime",
            "services",
            "app",
        ]:
            if module_name in sys.modules:
                del sys.modules[module_name]

        self.app_module = importlib.import_module("app")
        self.client = self.app_module.app.test_client()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        os.environ.pop("GREENRAN_STATE_DIR", None)

    def _build_sample_video(self, path: Path) -> None:
        cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=320x240:d=1.2",
            "-vf",
            "format=yuv420p",
            str(path),
        ]
        subprocess.run(cmd, capture_output=True, check=True)

    def _write_extended_metrics(self, payload: dict) -> None:
        metrics_dir = Path(self.temp_dir) / "xapp_metrics"
        metrics_dir.mkdir(parents=True, exist_ok=True)
        with open(metrics_dir / "extended_metrics.json", "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

    def test_health_endpoint(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["app"], "app1_vigilancia")
        self.assertEqual(data["video_analyses"], 0)
        self.assertEqual(data["uploaded_videos"], 0)

    def test_index_page_renders(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"App1-Vigilancia", response.data)
        self.assertIn(b"C\xc3\xa2meras 4K", response.data)
        self.assertIn(b"disabled", response.data)

    def test_simulated_camera_source_is_labeled_in_api_and_index(self):
        source_dir = Path(self.temp_dir) / "camera_sources"
        source_dir.mkdir(parents=True, exist_ok=True)
        sample_video = source_dir / "camera01.mp4"
        self._build_sample_video(sample_video)

        response = self.client.post(
            "/api/cameras",
            json={
                "camera_id": "CAM-01",
                "name": "Camera 4K Simulada Portao Principal",
                "location": "Portao Principal",
                "source_mode": "rtsp_stream",
                "source_url": "state://camera_sources/camera01.mp4",
                "criticality": "high",
            },
        )
        self.assertEqual(response.status_code, 201)
        camera = response.get_json()
        self.assertTrue(camera["is_simulated_source"])
        self.assertEqual(camera["source_mode_label"], "Simulação local 4K")
        self.assertEqual(camera["source_binding_label"], "câmera simulada vinculada")

        index_response = self.client.get("/")
        self.assertEqual(index_response.status_code, 200)
        self.assertIn(b"4K simulada", index_response.data)
        self.assertIn(b"Simula\xc3\xa7\xc3\xa3o local 4K", index_response.data)

    def test_default_cameras_are_exposed(self):
        response = self.client.get("/api/cameras")
        self.assertEqual(response.status_code, 200)
        cameras = response.get_json()["cameras"]
        self.assertEqual(len(cameras), 3)
        self.assertEqual(cameras[0]["capture_resolution"], "3840x2160")
        self.assertEqual(cameras[0]["inference_resolution"], "1280x720")

    def test_can_register_camera_source(self):
        response = self.client.post(
            "/api/cameras",
            json={
                "camera_id": "CAM-10",
                "name": "Camera 4K Reitoria",
                "location": "Reitoria",
                "source_mode": "rtsp_stream",
                "source_url": "rtsp://10.0.0.10/live",
                "criticality": "high",
            },
        )
        self.assertEqual(response.status_code, 201)
        camera = response.get_json()
        self.assertEqual(camera["camera_id"], "CAM-10")
        self.assertEqual(camera["status"], "configured")

    def test_can_configure_camera_source_via_form(self):
        response = self.client.post(
            "/cameras/configure",
            data={
                "camera_id": "CAM-02",
                "name": "Camera 4K Biblioteca",
                "location": "Biblioteca Central",
                "capture_resolution": "3840x2160",
                "inference_resolution": "1920x1080",
                "source_mode": "rtsp_stream",
                "source_url": "rtsp://camera-biblioteca/live",
                "criticality": "high",
                "notes": "Fonte principal da biblioteca",
                "enabled": "on",
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("camera_status=ok", response.headers["Location"])

        cameras_response = self.client.get("/api/cameras")
        self.assertEqual(cameras_response.status_code, 200)
        cameras = {camera["camera_id"]: camera for camera in cameras_response.get_json()["cameras"]}
        self.assertEqual(cameras["CAM-02"]["source_url"], "rtsp://camera-biblioteca/live")
        self.assertEqual(cameras["CAM-02"]["status"], "configured")

    def test_can_ingest_from_configured_state_source(self):
        source_dir = Path(self.temp_dir) / "camera_sources"
        source_dir.mkdir(parents=True, exist_ok=True)
        sample_video = source_dir / "camera01.mp4"
        self._build_sample_video(sample_video)

        configure_response = self.client.post(
            "/api/cameras",
            json={
                "camera_id": "CAM-01",
                "name": "Camera 4K Portao Principal",
                "location": "Portao Principal",
                "source_mode": "state_file",
                "source_url": "state://camera_sources/camera01.mp4",
                "criticality": "high",
            },
        )
        self.assertEqual(configure_response.status_code, 201)

        ingest_response = self.client.post("/api/cameras/CAM-01/ingest-now")
        self.assertEqual(ingest_response.status_code, 200)
        payload = ingest_response.get_json()
        self.assertEqual(payload["camera"]["camera_id"], "CAM-01")
        self.assertEqual(payload["camera"]["status"], "online")
        self.assertEqual(payload["ingest_result"], "stored_new")

        videos_response = self.client.get("/api/videos")
        self.assertEqual(videos_response.status_code, 200)
        videos = videos_response.get_json()["videos"]
        self.assertEqual(len(videos), 1)
        self.assertEqual(videos[0]["camera_id"], "CAM-01")

    def test_simulated_camera_sidecar_influences_analysis_pipeline(self):
        source_dir = Path(self.temp_dir) / "camera_sources"
        source_dir.mkdir(parents=True, exist_ok=True)
        sample_video = source_dir / "camera01.mp4"
        self._build_sample_video(sample_video)
        sidecar = {
            "camera_id": "CAM-01",
            "scenario": {
                "key": "violent_incident",
                "label": "Incidente violento simulado",
                "cycle_index": 3,
                "suspicious": True,
            },
            "detector_outputs": {
                "simulated_people": 3,
                "motion_level": 0.88,
                "crowd_density": 0.41,
                "violence_probability": 0.94,
                "fight_pose_score": 0.91,
                "anomaly_score": 0.86,
                "occlusion_score": 0.18,
            },
            "pipeline_profile": {
                "score_source": "simulated_multistage_video_inference",
                "faces_anonymized": 3,
                "requires_manual_review": True,
            },
            "network_profile": {
                "min_throughput_mbps": 23.6,
                "avg_throughput_mbps": 24.8,
                "max_latency_ms": 92.0,
                "avg_latency_ms": 85.0,
                "jitter_ms": 19.0,
                "packet_loss_percent": 6.4,
            },
        }
        with open(source_dir / "camera01.json", "w", encoding="utf-8") as f:
            json.dump(sidecar, f, indent=2)

        configure_response = self.client.post(
            "/api/cameras",
            json={
                "camera_id": "CAM-01",
                "name": "Camera 4K Portao Principal",
                "location": "Portao Principal",
                "source_mode": "state_file",
                "source_url": "state://camera_sources/camera01.mp4",
                "criticality": "high",
            },
        )
        self.assertEqual(configure_response.status_code, 201)

        ingest_response = self.client.post("/api/cameras/CAM-01/ingest-now")
        self.assertEqual(ingest_response.status_code, 200)
        payload = ingest_response.get_json()
        analysis = payload["result"]["analysis"]
        self.assertEqual(analysis["score_source"], "simulated_multistage_video_inference")
        self.assertEqual(analysis["scenario_key"], "violent_incident")
        self.assertTrue(analysis["requires_manual_review"])
        self.assertTrue(analysis["event_generated"])
        self.assertEqual(analysis["simulated_camera_sla"]["runtime_status"], "blocked")
        self.assertEqual(analysis["simulated_camera_sla"]["proposal_status"], "violation")
        self.assertEqual(analysis["network_profile"]["max_latency_ms"], 92.0)

        monitoring_response = self.client.get("/api/monitoring")
        self.assertEqual(monitoring_response.status_code, 200)
        snapshot = monitoring_response.get_json()
        self.assertEqual(snapshot["analyses"]["latest_camera_runtime_status"], "blocked")

    def test_ingest_now_form_reports_missing_source_url(self):
        self.client.post(
            "/api/cameras",
            json={
                "camera_id": "CAM-03",
                "name": "Camera 4K Corredor de Acesso",
                "location": "Corredor de Acesso",
                "source_mode": "rtsp_stream",
                "source_url": "",
                "criticality": "medium",
            },
        )
        response = self.client.post(
            "/cameras/CAM-03/ingest-now",
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("camera_status=error", response.headers["Location"])
        self.assertIn("source_url", response.headers["Location"])

    def test_create_and_reveal_event(self):
        create_response = self.client.post(
            "/api/events/mock",
            json={
                "camera_id": "CAM-99",
                "location": "Biblioteca Central",
                "confidence": 0.91,
            },
        )
        self.assertEqual(create_response.status_code, 201)
        event = create_response.get_json()
        self.assertEqual(event["camera_id"], "CAM-99")
        self.assertTrue(event["anonymized"])

        reveal_response = self.client.post(
            f"/api/events/{event['id']}/reveal-face",
            json={"reason": "manual_review"},
        )
        self.assertEqual(reveal_response.status_code, 200)
        updated = reveal_response.get_json()
        self.assertFalse(updated["anonymized"])
        self.assertTrue(updated["face_revealed"])
        self.assertEqual(updated["status"], "validated")

    def test_video_analysis_generates_event_when_score_is_high(self):
        create_response = self.client.post(
            "/api/video-analyses",
            json={
                "camera_id": "CAM-07",
                "location": "Setor Basico",
                "video_reference": "sample://video/cam07",
                "duration_s": 16,
                "fps": 20,
                "violence_score": 0.88,
                "motion_score": 0.79,
                "people_detected": 3,
                "faces_anonymized": 3,
            },
        )
        self.assertEqual(create_response.status_code, 201)
        analysis = create_response.get_json()
        self.assertEqual(analysis["camera_id"], "CAM-07")
        self.assertTrue(analysis["event_generated"])
        self.assertIsNotNone(analysis["generated_event_id"])

        analyses_response = self.client.get("/api/video-analyses")
        self.assertEqual(analyses_response.status_code, 200)
        analyses = analyses_response.get_json()["analyses"]
        self.assertEqual(len(analyses), 1)

        events_response = self.client.get("/api/events")
        self.assertEqual(events_response.status_code, 200)
        events = events_response.get_json()["events"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["source"], "video_analysis_pipeline")

    def test_video_analysis_below_threshold_does_not_generate_event(self):
        create_response = self.client.post(
            "/api/video-analyses",
            json={
                "camera_id": "CAM-11",
                "location": "Laboratorio",
                "video_reference": "sample://video/cam11",
                "violence_score": 0.41,
                "motion_score": 0.38,
                "people_detected": 1,
            },
        )
        self.assertEqual(create_response.status_code, 201)
        analysis = create_response.get_json()
        self.assertFalse(analysis["event_generated"])
        self.assertIsNone(analysis["generated_event_id"])

        events_response = self.client.get("/api/events")
        self.assertEqual(events_response.status_code, 200)
        self.assertEqual(events_response.get_json()["events"], [])

    def test_video_upload_persists_file_and_generates_analysis(self):
        upload_response = self.client.post(
            "/api/videos/upload",
            data={
                "camera_id": "CAM-12",
                "location": "Portao 3",
                "people_detected": "3",
                "video": (BytesIO(b"fake-video-data-" * 512), "camera12.mp4"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(upload_response.status_code, 201)
        payload = upload_response.get_json()
        self.assertEqual(payload["video"]["original_filename"], "camera12.mp4")
        self.assertFalse(payload["video"]["valid_video"])
        self.assertFalse(payload["analysis"]["event_generated"])
        self.assertIn("visual_risk_score", payload["analysis"])
        self.assertEqual(payload["analysis"]["decision_score"], 0.0)

        stored_path = Path(self.temp_dir) / payload["video"]["relative_path"]
        self.assertTrue(stored_path.exists())
        self.assertGreater(stored_path.stat().st_size, 0)

        videos_response = self.client.get("/api/videos")
        self.assertEqual(videos_response.status_code, 200)
        videos = videos_response.get_json()["videos"]
        self.assertEqual(len(videos), 1)
        self.assertEqual(videos[0]["camera_id"], "CAM-12")

    def test_valid_video_upload_generates_artifacts(self):
        sample_video = Path(self.temp_dir) / "sample.mp4"
        self._build_sample_video(sample_video)

        with open(sample_video, "rb") as video_file:
            upload_response = self.client.post(
                "/api/videos/upload",
                data={
                    "camera_id": "CAM-15",
                    "location": "Ginásio",
                    "video": (BytesIO(video_file.read()), "sample.mp4"),
                },
                content_type="multipart/form-data",
            )

        self.assertEqual(upload_response.status_code, 201)
        payload = upload_response.get_json()
        self.assertTrue(payload["video"]["valid_video"])
        self.assertEqual(payload["video"]["probe_status"], "ok")
        self.assertIsNotNone(payload["video"]["thumbnail_reference"])
        self.assertIsNotNone(payload["video"]["preview_reference"])
        self.assertGreaterEqual(payload["video"]["frames_sampled"], 2)
        self.assertIn("visual_risk_score", payload["analysis"])
        self.assertEqual(payload["analysis"]["score_source"], "video_frame_inference")
        self.assertIn("event_threshold", payload["analysis"])
        self.assertIn("useful_duration_s", payload["analysis"])
        self.assertIn(payload["video"]["artifact_status"], {"ready", "partial"})
        self.assertIsNotNone(payload["video"]["media_url"])

    def test_monitoring_snapshot_reflects_upload_state(self):
        sample_video = Path(self.temp_dir) / "snapshot.mp4"
        self._build_sample_video(sample_video)

        with open(sample_video, "rb") as video_file:
            upload_response = self.client.post(
                "/api/videos/upload",
                data={
                    "camera_id": "CAM-20",
                    "location": "Reitoria",
                    "video": (BytesIO(video_file.read()), "snapshot.mp4"),
                },
                content_type="multipart/form-data",
            )

        self.assertEqual(upload_response.status_code, 201)
        monitoring_response = self.client.get("/api/monitoring")
        self.assertEqual(monitoring_response.status_code, 200)
        snapshot = monitoring_response.get_json()
        self.assertEqual(snapshot["app"], "app1_vigilancia")
        self.assertEqual(snapshot["videos"]["uploaded"], 1)
        self.assertEqual(snapshot["analyses"]["total"], 1)

    def test_network_context_exposes_camera_throughput_and_latency_sla(self):
        self._write_extended_metrics(
            {
                "global_metrics": {
                    "global_avg_latency_us": 42100,
                    "global_worst_latency_us": 73400,
                    "cvar_per_ue_us": 38800,
                    "total_active_cameras": 2,
                    "total_active_ues": 6,
                    "throughput_kbps": 81200,
                },
                "ue_metrics": {
                    "ue_cam_01": {
                        "device_type": "camera",
                        "latency_us": 62000,
                        "throughput_kbps": 31000,
                        "rx_throughput_kbps": 31000,
                        "has_latency_samples": True,
                        "packet_count": 180,
                    },
                    "ue_cam_02": {
                        "device_type": "camera",
                        "latency_us": 58000,
                        "throughput_kbps": 28000,
                        "rx_throughput_kbps": 28000,
                        "has_latency_samples": True,
                        "packet_count": 172,
                    },
                },
            }
        )

        context_response = self.client.get("/api/network-context")
        self.assertEqual(context_response.status_code, 200)
        context = context_response.get_json()
        network = context["network"]
        camera_sla = network["camera_sla"]

        self.assertEqual(network["min_camera_throughput_mbps"], 28.0)
        self.assertEqual(network["max_camera_latency_ms"], 62.0)
        self.assertTrue(network["camera_metrics_ready"])
        self.assertEqual(camera_sla["targets"]["throughput_min_mbps"], 25.0)
        self.assertEqual(camera_sla["targets"]["latency_max_ms"], 100.0)
        self.assertEqual(camera_sla["proposal_status"], "ok")
        self.assertTrue(camera_sla["proposal_compliant"])
        self.assertEqual(camera_sla["runtime_status"], "warning")

        monitoring_response = self.client.get("/api/monitoring")
        self.assertEqual(monitoring_response.status_code, 200)
        snapshot = monitoring_response.get_json()
        self.assertEqual(snapshot["camera_sla"]["runtime_status"], "warning")
        self.assertIn("margem protegida", snapshot["camera_sla"]["reason"])

    def test_media_route_serves_uploaded_video_and_preview(self):
        sample_video = Path(self.temp_dir) / "media.mp4"
        self._build_sample_video(sample_video)

        with open(sample_video, "rb") as video_file:
            upload_response = self.client.post(
                "/api/videos/upload",
                data={
                    "camera_id": "CAM-MEDIA",
                    "location": "PCT",
                    "video": (BytesIO(video_file.read()), "media.mp4"),
                },
                content_type="multipart/form-data",
            )

        self.assertEqual(upload_response.status_code, 201)
        payload = upload_response.get_json()
        original_media_response = self.client.get(payload["video"]["media_url"])
        self.assertEqual(original_media_response.status_code, 200)
        self.assertTrue(original_media_response.content_type.startswith("video/"))
        original_media_response.close()

        preview_media_response = self.client.get(payload["video"]["preview_url"])
        self.assertEqual(preview_media_response.status_code, 200)
        self.assertTrue(preview_media_response.content_type.startswith("video/"))
        preview_media_response.close()

    def test_index_page_includes_media_links_after_upload(self):
        sample_video = Path(self.temp_dir) / "index_media.mp4"
        self._build_sample_video(sample_video)

        with open(sample_video, "rb") as video_file:
            self.client.post(
                "/api/videos/upload",
                data={
                    "camera_id": "CAM-INDEX",
                    "location": "Portal",
                    "video": (BytesIO(video_file.read()), "index_media.mp4"),
                },
                content_type="multipart/form-data",
            )

        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Abrir v\xc3\xaddeo enviado", response.data)
        self.assertIn(b"<video controls", response.data)

    def test_browser_upload_route_redirects_back_to_index(self):
        sample_video = Path(self.temp_dir) / "browser.mp4"
        self._build_sample_video(sample_video)

        with open(sample_video, "rb") as video_file:
            response = self.client.post(
                "/upload",
                data={
                    "camera_id": "CAM-BROWSER",
                    "location": "Portal",
                    "video": (BytesIO(video_file.read()), "browser.mp4"),
                },
                content_type="multipart/form-data",
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 302)
        self.assertIn("upload_status=ok", response.headers["Location"])

    def test_duplicate_video_upload_reuses_existing_record(self):
        sample_video = Path(self.temp_dir) / "duplicate.mp4"
        self._build_sample_video(sample_video)

        with open(sample_video, "rb") as video_file:
            content = video_file.read()

        first_response = self.client.post(
            "/api/videos/upload",
            data={
                "camera_id": "CAM-DUP",
                "location": "ICEN",
                "video": (BytesIO(content), "duplicate.mp4"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(first_response.status_code, 201)
        first_payload = first_response.get_json()
        self.assertFalse(first_payload["duplicate_upload"])

        duplicate_response = self.client.post(
            "/api/videos/upload",
            data={
                "camera_id": "CAM-DUP-2",
                "location": "CTIC",
                "video": (BytesIO(content), "duplicate-again.mp4"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(duplicate_response.status_code, 201)
        duplicate_payload = duplicate_response.get_json()
        self.assertTrue(duplicate_payload["duplicate_upload"])
        self.assertEqual(duplicate_payload["ingest_result"], "duplicate_reused_with_new_submission")
        self.assertNotEqual(duplicate_payload["video"]["id"], first_payload["video"]["id"])
        self.assertNotEqual(duplicate_payload["analysis"]["id"], first_payload["analysis"]["id"])
        self.assertTrue(duplicate_payload["video"]["asset_reused"])
        self.assertEqual(duplicate_payload["video"]["asset_source_video_id"], first_payload["video"]["id"])
        self.assertEqual(duplicate_payload["video"]["content_sha256"], first_payload["video"]["content_sha256"])
        self.assertEqual(duplicate_payload["video"]["relative_path"], first_payload["video"]["relative_path"])
        self.assertEqual(duplicate_payload["video"]["camera_id"], "CAM-DUP-2")
        self.assertEqual(duplicate_payload["analysis"]["camera_id"], "CAM-DUP-2")

        videos_response = self.client.get("/api/videos")
        self.assertEqual(videos_response.status_code, 200)
        videos = videos_response.get_json()["videos"]
        self.assertEqual(len(videos), 2)

        analyses_response = self.client.get("/api/video-analyses")
        self.assertEqual(analyses_response.status_code, 200)
        analyses = analyses_response.get_json()["analyses"]
        self.assertEqual(len(analyses), 2)

        monitoring_response = self.client.get("/api/monitoring")
        self.assertEqual(monitoring_response.status_code, 200)
        snapshot = monitoring_response.get_json()
        self.assertEqual(snapshot["videos"]["uploaded"], 2)
        self.assertLessEqual(snapshot["videos"]["stored_megabytes"], 0.1)


if __name__ == "__main__":
    unittest.main()
