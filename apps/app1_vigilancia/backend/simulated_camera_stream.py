import numpy as np
import cv2
import time
from real_video_pipeline import RealVideoPipeline

class SimulatedCameraStream:
    def __init__(self, camera_id, api_url):
        self.camera_id = camera_id
        self.api_url = api_url
        self.pipeline = RealVideoPipeline()
        self.running = False

    def start(self):
        self.running = True
        print(f"[Simulador] Iniciado: {self.camera_id}")
        
        # Gera frames sintéticos (ruído colorido e formas móveis)
        frame_counter = 0
        while self.running:
            # Criar frame sintético: ruído + círculo móvel
            frame = np.random.randint(0, 255, (480, 640, 3), dtype=np.uint8)
            cv2.circle(frame, ( (frame_counter * 5) % 640, 240), 50, (0, 255, 0), -1)
            
            # Processar o frame sintético
            self.pipeline.run_event(self.camera_id, frame, self.api_url)
            
            frame_counter += 1
            time.sleep(0.5) # Simula 2 FPS para economizar CPU

    def stop(self):
        self.running = False

if __name__ == "__main__":
    # Teste rápido do simulador
    sim = SimulatedCameraStream("SIM-CAM-01", "http://127.0.0.1:5100/api/video-analyses")
    try:
        sim.start()
    except KeyboardInterrupt:
        sim.stop()
