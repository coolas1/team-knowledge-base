# Single-stage build for the Team Knowledge Base webapp (BFF + built SPA).
#
# Deliberately single-stage: a multi-stage `COPY --from=spa-build /spa/dist`
# failed under podman/buildah on this storage backend ("io: read/write on
# closed pipe", "operation not permitted" - buildah's inter-stage tar pipe).
# Node 22 is installed into the Python image so the SPA is built in-place -
# no cross-stage copy.
#
# Layers are ordered by change frequency: system deps -> toolchain -> Python
# deps -> SPA build -> app source. A Python-only edit re-runs only the final
# COPY layers. uv comes digest-pinned from the official image (one-time
# pull, cached in local storage) - the old astral.sh installer curl was the
# 78%-of-wall-clock cost of a build (flaky through the proxy: one run spent
# ~113 min on it, another failed with curl exit 35).
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

# uv from the official image, digest-pinned (NOT an inter-stage copy - an
# external image ref in --from pulls from local storage, which is warm).
COPY --from=ghcr.io/astral-sh/uv:0.12.5@sha256:e85be844203885286c60ffad8a858d48afb6c5a5c237ca0e67f12e74b8f174b1 \
     /uv /uvx /usr/local/bin/

WORKDIR /app

# Python deps (the optional reranker extra, and therefore torch, is omitted).
# Copying only the lock inputs keeps this layer cached across code edits.
COPY pyproject.toml uv.lock ./
# Package index: upstream PyPI by default. A regional mirror is opt-in via
# --build-arg PYPI_MIRROR=<simple-index-url> (the CICD build passes Aliyun for
# the LAN deployment), so a default build never depends on a mirror host. The
# frozen lock fixes the dependency graph and artifact hashes either way; the
# lock records absolute PyPI artifact URLs, so a mirror also needs those hosts
# remapped inside the image.
ARG PYPI_MIRROR=""
RUN if [ -n "$PYPI_MIRROR" ]; then \
      sed -i \
        -e "s#https://pypi.org/simple#${PYPI_MIRROR}#g" \
        -e "s#https://files.pythonhosted.org/packages#${PYPI_MIRROR%/simple}/packages#g" \
        uv.lock; \
    fi
RUN --mount=type=cache,target=/root/.cache/uv \
    UV_CONCURRENT_DOWNLOADS=1 \
    UV_HTTP_TIMEOUT=600 \
    sh -c 'uv sync --frozen --no-dev --no-install-project \
      ${PYPI_MIRROR:+--default-index "$PYPI_MIRROR"}'

# SPA: client source only, so a Python edit never invalidates this layer.
# node_modules is installed and removed in the SAME layer, so it is not in
# the final image (only dist/ is). The later `COPY src/` overlay cannot
# clobber dist because the context excludes **/dist (see .dockerignore).
COPY src/frontend/webapp/client/ ./src/frontend/webapp/client/
RUN cd src/frontend/webapp/client \
 && npm ci \
 && npm run security \
 && npm run build \
 && rm -rf node_modules

# App source + config: the most frequently changed inputs, last.
COPY src/ ./src/
COPY config/ ./config/

ENV PYTHONPATH=/app \
    SPA_DIST=/app/src/frontend/webapp/client/dist \
    PYTHONUNBUFFERED=1

EXPOSE 8000

# Run the venv's uvicorn directly (PYTHONPATH=/app makes src/ + config/ importable).
CMD ["/app/.venv/bin/uvicorn", "src.frontend.webapp.server.app:app", "--host", "0.0.0.0", "--port", "8000"]
