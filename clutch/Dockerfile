# Clutch — Cloud Run container (FastAPI UI + ADK agent tools).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    GOOGLE_GENAI_USE_VERTEXAI=FALSE

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY clutch ./clutch
COPY server ./server
COPY web ./web

# Cloud Run injects $PORT (default 8080); bind to it.
EXPOSE 8080
CMD exec uvicorn server.app:app --host 0.0.0.0 --port ${PORT:-8080}
