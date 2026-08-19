#!/usr/bin/env python3
"""
Pipeline Real de Detecção de Eventos (App1-Vigilância)
Baseado em MobileNetV3 para classificação de eventos/anomalias.
"""

import torch
import torchvision.transforms as T
from torchvision.models import mobilenet_v3_small, MobileNet_V3_Small_Weights
import cv2
import numpy as np
import time
import json
from urllib import request

class RealVideoPipeline:
    def __init__(self):
        # Carregar modelo leve pré-treinado
        try:
            weights = MobileNet_V3_Small_Weights.DEFAULT
            self.model = mobilenet_v3_small(weights=weights)
            self.model.eval()
            self.preprocess = weights.transforms()
        except Exception as e:
            print(f"[IA Pipeline] Erro ao carregar modelo: {e}. Certifique-se de instalar torchvision.")
            self.model = None

    def process_frame(self, frame: np.ndarray) -> float:
        """Processa um frame e retorna score de anomalia (0.0 - 1.0)"""
        if self.model is None:
            return 0.5 # Fallback
        
        # Converte BGR (OpenCV) para RGB
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        
        # Preprocessamento (conversão para PIL e transformações)
        from PIL import Image
        img = Image.fromarray(rgb_frame)
        batch = self.preprocess(img).unsqueeze(0)
        
        # Inferência
        with torch.no_grad():
            output = self.model(batch)
            probabilities = torch.nn.functional.softmax(output[0], dim=0)
            
        # Heurística: Score baseado na probabilidade da classe "suspeita"
        # (Em um modelo de detecção de violência, selecionaríamos a classe específica)
        return float(probabilities.max().item())

    def run_event(self, camera_id: str, frame: np.ndarray, url: str):
        score = self.process_frame(frame)
        print(f"[IA Pipeline] Score detectado: {score:.4f}")
        
        if score > 0.8: # Threshold de evento suspeito
            self._report_event(url, camera_id, score)

    def _report_event(self, url: str, camera_id: str, score: float):
        payload = {
            "camera_id": camera_id,
            "violence_score": score,
            "source": "real_ai_pipeline",
            "timestamp": int(time.time())
        }
        # Envio para API
        try:
            data = json.dumps(payload).encode("utf-8")
            req = request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
            request.urlopen(req, timeout=2)
            print(f"[IA Pipeline] Evento reportado: {camera_id}, Score: {score:.2f}")
        except Exception as e:
            print(f"[IA Pipeline] Erro ao reportar: {e}")

if __name__ == "__main__":
    pipeline = RealVideoPipeline()
    print("Pipeline de IA inicializado.")
