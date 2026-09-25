# Dockerfile para o rApp (Python)
FROM python:3.11-slim

WORKDIR /app
COPY src/ /app/src/
COPY apps/ /app/apps/
COPY drlexp/src/ /app/drlexp/src/
COPY drlexp/config/ /app/drlexp/config/
COPY requirements-runtime.txt /app/requirements.txt
COPY config/ /app/config/

RUN pip install --no-cache-dir -r /app/requirements.txt

# Copiar scripts e utilitários
COPY scripts/ /app/scripts/

# Variáveis de ambiente padrão
ENV GREENRAN_STATE_DIR=/tmp
ENV GREENRAN_PROJECT_DIR=/app
ENV GREENRAN_RUNS_DIR=/app/runs
ENV PYTHONPATH=/app:/app/src:/app/drlexp/src
ENV PYTHONUNBUFFERED=1

CMD ["python3", "/app/src/rapp_orchestrator.py"]
