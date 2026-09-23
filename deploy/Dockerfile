# syntax=docker/dockerfile:1
#
# One image for every tester: Person A's capture package (src/longevity) and
# the pipeline backend (backend/pipeline), installed into one virtualenv.
# Build from the repository root (docker-compose.yml does this for you):
#
#     docker build -f deploy/Dockerfile -t hackrice-backend:local .
#
# Two stages so the image carries no compiler, no uv and no source tree:
# the build stage resolves the locked dependencies, the runtime stage gets
# only the finished virtualenv.

ARG PYTHON_IMAGE=python:3.11-slim-bookworm

# --- build -------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS build

# Pinned to the uv that wrote backend/uv.lock, so --frozen reads it as-is.
COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /bin/uv

# Compile bytecode now so the first request does not pay for it; keep no uv
# cache, it would only bloat the layer (and cache mounts need BuildKit, which
# this file does not assume); never download a Python, the base image's 3.11
# is the one the lock was made for.
ENV UV_COMPILE_BYTECODE=1 \
    UV_NO_CACHE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /src

# Dependencies first, on their own layer: they change far less often than
# the code, so a code edit rebuilds in seconds instead of re-downloading
# numpy and OpenCV.
COPY pyproject.toml ./
COPY backend/pyproject.toml backend/uv.lock backend/README.md ./backend/
RUN cd backend && uv sync --frozen --no-dev --no-install-project \
        --no-install-package longevity

# Then the two packages themselves. Explicit paths, never `COPY . .`:
# backend/.env is a symlink to real keys and must never enter an image.
COPY src ./src
COPY backend/pipeline ./backend/pipeline
# --no-editable installs both as ordinary packages inside the venv, so the
# runtime stage needs the venv and nothing else.
RUN cd backend && uv sync --frozen --no-dev --no-editable

# --- runtime -----------------------------------------------------------------
FROM ${PYTHON_IMAGE}

# A fixed, unprivileged uid: deploy/new_tester.sh chowns each tester's data
# directory to it, so the bind mount is writable without running as root.
RUN useradd --create-home --uid 10001 --user-group app \
    && mkdir -p /data && chown app:app /data

COPY --from=build /app/.venv /app/.venv

# Everything that must survive a restart lives under /data, which compose
# bind-mounts per tester: the database and any wearable tokens. The defaults
# below are the hosted ones; every one can still be overridden per tester.
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PORT=8010 \
    DEMO_MODE=1 \
    DB_PATH=/data/pipeline.db \
    FITBIT_TOKEN_PATH=/data/fitbit_token.json \
    GOOGLE_HEALTH_TOKEN_PATH=/data/google_health_token.json \
    FORWARDED_ALLOW_IPS=*

WORKDIR /app
USER app
VOLUME ["/data"]
EXPOSE 8010

# /healthz is the one route that answers without the access token, so the
# probe needs no secret. start-period covers seeding a fresh database.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('PORT', '8010'), timeout=4)" || exit 1

# Shell form only to expand ${PORT}; exec so python is PID 1 and gets the
# SIGTERM from `docker stop` (a clean stop flushes the day's scores).
CMD ["sh", "-c", "exec python -m pipeline.main --source glasses --vlm gemini --reasoner openai --port \"${PORT:-8010}\""]
