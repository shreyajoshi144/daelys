# syntax=docker/dockerfile:1
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=120 PIP_RETRIES=10 HF_HOME=/app/.hf
# Production-safe defaults: no test tooling, no audio stack. docker-compose turns both on for local work.
ARG INSTALL_DEV=false
# Audio meetings need ffmpeg + faster-whisper (~1.5 GB RAM at runtime). Transcript meetings work without them.
ARG INSTALL_MEETINGS=false

RUN if [ "$INSTALL_MEETINGS" = "true" ]; then \
        apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*; \
    fi

WORKDIR /app
COPY requirements.lock requirements-dev.lock requirements-meetings.lock ./
# The cache mount keeps downloaded wheels across builds (and across retries on a flaky network); not in the image.
RUN --mount=type=cache,target=/root/.cache/pip \
    if [ "$INSTALL_DEV" = "true" ]; then pip install -r requirements-dev.lock; else pip install -r requirements.lock; fi \
    && if [ "$INSTALL_MEETINGS" = "true" ]; then pip install -r requirements-meetings.lock; fi

COPY . .
RUN useradd --system --uid 10001 daelys && chown -R daelys /app
USER daelys

EXPOSE 8000
# PORT is set by hosts like Render. Which proxies are trusted for X-Forwarded-For comes from FORWARDED_ALLOW_IPS
# (uvicorn's default is 127.0.0.1 only), so per-IP login rate limits see the real client behind the load balancer.
CMD ["sh", "-c", "exec uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers"]
