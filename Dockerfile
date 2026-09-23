# Brian backend (backend/pipeline + the `longevity` package at the repo root).
# Build context is the repo root: backend/pyproject.toml pulls `longevity` from "..".
#   docker build -t brian-backend .        (fly deploy runs this; docs/DEPLOY.md)
# All config is env (docs/DEPLOY.md); no .env file is copied or needed.
FROM python:3.11-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.12.1 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app/backend

# Third-party dependencies first, so a code edit does not reinstall numpy/opencv.
COPY pyproject.toml /app/pyproject.toml
COPY backend/pyproject.toml backend/uv.lock backend/README.md backend/.python-version /app/backend/
RUN uv sync --frozen --no-dev --no-install-project --no-install-local

COPY src /app/src
COPY backend/pipeline /app/backend/pipeline
RUN uv sync --frozen --no-dev

# Everything the service writes (SQLite + WAL, OAuth token files) lives under
# DATA_DIR, which fly.toml mounts as a volume.
ENV PATH="/app/backend/.venv/bin:$PATH" \
    PORT=8010 \
    DATA_DIR=/data \
    DB_PATH=/data/pipeline.db \
    FITBIT_TOKEN_PATH=/data/fitbit_token.json \
    GOOGLE_HEALTH_TOKEN_PATH=/data/google_health_token.json

RUN useradd --create-home --uid 10001 app && mkdir -p /data && chown app:app /data

EXPOSE 8010

# The server runs as `app`, never root. A fresh Fly volume mounts root-owned, so
# when started as root this chowns DATA_DIR first and then drops to `app`
# (setpriv execs, so python is PID 1 and gets SIGTERM directly).
ENTRYPOINT ["/bin/sh", "-c", "if [ \"$(id -u)\" = 0 ]; then mkdir -p \"$DATA_DIR\" && chown -R app:app \"$DATA_DIR\" && exec setpriv --reuid=app --regid=app --init-groups -- \"$@\"; fi; exec \"$@\"", "entrypoint"]

# Binds 0.0.0.0:$PORT; SOURCE / REASONER / VLM pick the mode (see fly.toml).
CMD ["python", "-m", "pipeline.main"]
