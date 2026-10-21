# voice-notes-backend
# Build:  docker build -t voice-notes-backend .
# Run:    docker run --env-file .env -p 8000:8000 voice-notes-backend
# Migrations run on every start (docker-entrypoint.sh), then uvicorn.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    LOG_FORMAT=json

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY alembic ./alembic
COPY alembic.ini docker-entrypoint.sh ./
RUN useradd --system --uid 1001 --no-create-home appuser \
    && chmod +x docker-entrypoint.sh
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).status == 200 else 1)"

ENTRYPOINT ["./docker-entrypoint.sh"]
