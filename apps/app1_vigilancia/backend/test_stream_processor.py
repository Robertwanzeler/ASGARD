import time
import argparse
from stream_processor import CameraStreamProcessor

def run_test(source):
    print(f"--- Iniciando teste do CameraStreamProcessor (Fonte: {source}) ---")
    
    # API_URL precisa estar acessível
    API_URL = "http://127.0.0.1:5100/api/video-analyses"
    
    processor = CameraStreamProcessor("TESTE-CAM", source, API_URL)
    processor.start()
    
    try:
        time.sleep(10)
    except KeyboardInterrupt:
        print("Teste interrompido.")
    finally:
        processor.stop()
        print("--- Teste finalizado ---")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", help="Caminho para arquivo de vídeo ou índice da câmera")
    args = parser.parse_args()
    
    # Tenta converter para inteiro se for um índice de câmera
    source = int(args.source) if args.source.isdigit() else args.source
    run_test(source)
