FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TENAOS_KB_HOST=127.0.0.1 \
    TENAOS_KB_PORT=4276 \
    TENAOS_KB_COLLECTION=who_msf_guidelines \
    TENAOS_QDRANT_URL=http://tenaos-qdrant:6333

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential curl git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY kb_common        /app/kb_common
COPY kb_guidelines    /app/kb_guidelines

ENV PYTHONPATH=/app

EXPOSE 4276

HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=5 \
    CMD curl -fsS http://localhost:${TENAOS_KB_PORT}/health || exit 1

CMD ["python3", "-m", "kb_guidelines.daemon"]
