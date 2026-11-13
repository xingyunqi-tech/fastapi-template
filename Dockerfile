FROM ghcr.io/astral-sh/uv:0.13.7@sha256:96f40d3904aad8d0bd010539001d36c8e2188b981e1334471a90c4a1d93bb490 AS uv

FROM python:3.15.2-slim@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928 AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MALLOC_ARENA_MAX=2 \
    MALLOC_MMAP_THRESHOLD_=131072 \
    PATH="/app/.venv/bin:${PATH}"
WORKDIR /app

FROM base AS builder
COPY --from=uv /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    uv sync --frozen --no-dev --no-install-project
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev \
    && python -m compileall -q --invalidation-mode checked-hash src

FROM builder AS development
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen
COPY . .
ENV APP_ENV=development
EXPOSE 3000
CMD ["sh", "-c", "exec fastapi dev --host 0.0.0.0 --port \"${APP_PORT:-3000}\""]

FROM base AS production
ENV APP_ENV=production
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip uninstall -y --root-user-action=ignore pip setuptools \
    && rm -f /usr/local/bin/pip* \
    && groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app --no-create-home --shell /usr/sbin/nologin app \
    && mkdir -p /app/files /tmp/prometheus \
    && chown app:app /app/files /tmp/prometheus
COPY --from=builder /app/.venv ./.venv
COPY --from=builder /app/src ./src
COPY alembic.ini ./
COPY migrations ./migrations
COPY openapi ./openapi
USER 10001:10001
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 --start-interval=2s \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen(f\"http://localhost:{os.environ.get('APP_PORT') or '3000'}/{os.environ.get('API_PREFIX', 'api')}/v1/health/live\", timeout=5)"]
CMD ["python", "-m", "app"]
