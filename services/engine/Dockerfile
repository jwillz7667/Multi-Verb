# syntax=docker/dockerfile:1.7
# Verbio engine — Python 3.12 on a slim base. Built by uv for reproducibility.
# Targets Railway; works locally with `docker build -t verbio-engine .`.

FROM python:3.14-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONFAULTHANDLER=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:${PATH}"

# uv is statically linked — copy the binary in from the official image.
COPY --from=ghcr.io/astral-sh/uv:0.7.3 /uv /uvx /usr/local/bin/

WORKDIR /app

# Install OS packages required by uvicorn[standard] (httptools, websockets).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ca-certificates \
       libffi8 \
    && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# Dependency stage — leverage Docker layer caching against pyproject + lock.
# ---------------------------------------------------------------------------
FROM base AS deps

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# ---------------------------------------------------------------------------
# Runtime stage — install the project itself, then drop dev tooling.
# ---------------------------------------------------------------------------
FROM deps AS runtime

COPY verbio_engine ./verbio_engine
COPY README.md ./README.md

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Drop privileges. Railway honours USER directives.
RUN groupadd --system --gid 1001 verbio \
    && useradd --system --uid 1001 --gid verbio --home /app verbio \
    && chown -R verbio:verbio /app
USER verbio

EXPOSE 8000

# Healthcheck hits the FastAPI /health endpoint.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=3).status==200 else 1)"

# Default command: uvicorn workers=1 because the engine is single-process per session.
CMD ["uvicorn", "verbio_engine.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
