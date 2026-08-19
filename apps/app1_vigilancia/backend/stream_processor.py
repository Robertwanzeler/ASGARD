import cv2
import threading
import time
from real_video_pipeline import RealVideoPipeline

class CameraStreamProcessor:
    def __init__(self, camera_id, rtsp_url, api_url):
        self.camera_id = camera_id
        self.rtsp_url = rtsp_url
        self.api_url = api_url
        self.pipeline = RealVideoPipeline()
        self.running = False
        self.thread = None

    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self._capture_loop, daemon=True)
        self.thread.start()
        print(f"[StreamProcessor] Iniciado: {self.camera_id}")

    def _capture_loop(self):
        cap = cv2.VideoCapture(self.rtsp_url)
        while self.running:
            ret, frame = cap.read()
            if not ret:
                time.sleep(1)
                cap = cv2.VideoCapture(self.rtsp_url)
                continue
            
            # Processar apenas 1 a cada 10 frames para economizar CPU
            if time.time() % 0.5 < 0.1:
                self.pipeline.run_event(self.camera_id, frame, self.api_url)
            
            time.sleep(0.03) # ~30 FPS
        cap.release()

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join()
