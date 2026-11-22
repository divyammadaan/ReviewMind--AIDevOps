# One small image shared by every microservice and the ops control plane.
# What differs is the command each container runs (see docker-compose.yml).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

RUN pip install --no-cache-dir "fastapi==0.120.1" "uvicorn==0.38.0" "watchfiles==1.1.0" \
    "httpx==0.28.1" "groq==0.33.0" "python-dotenv==1.1.1" \
    "opentelemetry-sdk==1.45.1" "opentelemetry-exporter-otlp-proto-http==1.45.1" \
    "opentelemetry-instrumentation-fastapi==0.66b1" "opentelemetry-instrumentation-httpx==0.66b1" \
    "pywebpush==2.5.0"

# Known-good source of every service. Also the restore point for "Restore known-good".
COPY golden/ /golden/
COPY ops/ /ops/
COPY metrics/ /metrics/
COPY otel/ /otel/
COPY serve.sh /serve.sh
