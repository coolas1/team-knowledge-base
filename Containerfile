# Single-stage build for the Team Knowledge Base webapp (BFF + built SPA).
#
# Deliberately single-stage: a multi-stage `COPY --from=spa-build /spa/dist`
# failed under podman/buildah on this storage backend ("io: read/write on
# closed pipe", "operation not permitted" - buildah's inter-stage tar pipe).
# Node 22 is installed into the Python image so the SPA is built in-place -
# no cross-stage copy.
#
# Build:  podman build -t team-kb-webapp -f Containerfile .
# Run:    via docker-compose.yml (webapp service), or:
#         podman run --rm -p 8000:8000 --env-file .env team-kb-webapp

FROM docker.io/library/python:3.12-slim

# System libraries:
#   tesseract-ocr          - OCR binary for image documents (pytesseract)
#   libgomp1               - OpenMP runtime for torch (reranker)
#   ca-certificates, curl  - to fetch the Node 22 binary
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr libgomp1 ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

# Node 22 (Vite 6 requires Node >=20; Debian's nodejs is v18, too old).
# Installed to /usr/local so node/npm are on PATH alongside the Python toolchain.
# Arch is detected so this works on x86_64 and arm64 hosts.
ARG NODE_VERSION=22.23.2
RUN set -eux; \
    case "$(uname -m)" in \
      x86_64)  NODE_ARCH=x64  ;; \
      aarch64) NODE_ARCH=arm64 ;; \
      *) echo "unsupported arch: $(uname -m)"; exit 1 ;; \
    esac; \
    curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-${NODE_ARCH}.tar.gz" \
      | tar -xz --strip-components=1 -C /usr/local

RUN pip install --no-cache-dir uv

WORKDIR /app

# Python deps (heavy: torch) - cached unless pyproject/uv.lock change.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# App source + config.
COPY src/ ./src/
COPY config/ ./config/

# Build the SPA in-place -> src/frontend/webapp/client/dist (served by the BFF).
# node_modules is installed and removed in the SAME layer, so it is not in the
# final image (only dist/ is).
RUN cd src/frontend/webapp/client \
 && npm ci \
 && npm run build \
 && rm -rf node_modules

ENV PYTHONPATH=/app \
    SPA_DIST=/app/src/frontend/webapp/client/dist \
    PYTHONUNBUFFERED=1

EXPOSE 8000

# Run the venv's uvicorn directly (PYTHONPATH=/app makes src/ + config/ importable).
CMD ["/app/.venv/bin/uvicorn", "src.frontend.webapp.server.app:app", "--host", "0.0.0.0", "--port", "8000"]
