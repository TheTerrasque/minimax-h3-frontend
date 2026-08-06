# Build context is the repo root (see docker-compose.yml: build.context: .)
# so this image can also COPY in resources/ (workflows + prompt guides used
# by integrations/comfyui.py and integrations/llm.py).

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# Install deps into a project-local .venv first, in their own layer, so
# source changes below don't invalidate the (slow) dependency install.
COPY backend/pyproject.toml backend/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

COPY backend/ .
COPY resources/ ./resources/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"

RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /app/staticfiles /app/media \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

ENTRYPOINT ["/app/entrypoint.sh"]
# --timeout 180: gunicorn's default (30s) is well below integrations/llm.py's
# own 120s request timeout for AI-refine/chat -- a real LLM reply anywhere
# north of 30s (easy with a bigger model, multi-turn context, or vision
# images) got its worker killed before the LLM call ever had a chance to
# finish or time out on its own, surfacing to the user as a silently dropped
# connection ("Message failed to send"). 180s gives headroom above that 120s.
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "180"]
