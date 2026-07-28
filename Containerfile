# Containerfile for the Team Knowledge Base webapp (BFF + built SPA).
# Multi-stage: (1) build the React SPA, (2) Python runtime that serves the
# BFF (FastAPI, in-process engine) and the SPA static assets.
#
# Build:  podman build -t team-kb-webapp -f Containerfile .
# Run:    via docker-compose.yml (webapp service), or:
#         podman run --rm -p 8000:8000 --env-file .env team-kb-webapp

# ── Stage 1: build the SPA ──────────────────────────────────────────
FROM docker.io/library/node:22-slim AS spa-build
WORKDIR /spa
# Lock + manifest first for layer caching; npm ci fetches from the resolved
# URLs baked into package-lock.json (registry.npmjs.org).
COPY src/frontend/webapp/client/package.json src/frontend/webapp/client/package-lock.json ./
RUN npm ci
COPY src/frontend/webapp/client/ ./
RUN npm run build
# -> /spa/dist

# ── Stage 2: Python runtime ────────────────────────────────────────
FROM docker.io/library/python:3.12-slim AS runtime

# System libraries:
#   tesseract-ocr - OCR binary used by pytesseract for image documents
#   libgomp1      - OpenMP runtime required by torch (reranker)
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /app

# Python deps from the lock (cached unless pyproject/uv.lock change).
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Application source + config (config/app.yaml is read relative to CWD=/app).
COPY src/ ./src/
COPY config/ ./config/

# Built SPA, served by the BFF (see SPA_DIST below and app.py).
COPY --from=spa-build /spa/dist ./src/frontend/webapp/client/dist

ENV PYTHONPATH=/app \
    SPA_DIST=/app/src/frontend/webapp/client/dist \
    PYTHONUNBUFFERED=1

EXPOSE 8000

# Run the venv's uvicorn directly (PYTHONPATH=/app makes src/ + config/ importable).
CMD ["/app/.venv/bin/uvicorn", "src.frontend.webapp.server.app:app", "--host", "0.0.0.0", "--port", "8000"]
