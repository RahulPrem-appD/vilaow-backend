# Vilaow API.
#
# Multi-stage so the runtime image carries the virtualenv but not uv, the build
# toolchain or the lockfile resolution step.
FROM python:3.12-slim AS build

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# Dependencies first, in their own layer: application code changes far more
# often than the lockfile, so this layer survives most rebuilds.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY . .
RUN uv sync --frozen --no-dev


FROM python:3.12-slim

# libpq for psycopg, curl for the health check, and DejaVu because the signed
# agreement is a PDF for Greek professionals. reportlab's built-in Helvetica has
# no Greek: every accented vowel came out as a filled box, silently, on the only
# copy of a binding document. app/adapters/pdf/agreement.py looks for this file
# by path and falls back to Helvetica if it is missing, so removing this package
# degrades the document rather than breaking the build — which is exactly how it
# went unnoticed before.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libpq5 curl fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*

# Not root. A container that is compromised should not also be privileged.
RUN useradd --create-home --uid 10001 vilaow
WORKDIR /app

COPY --from=build --chown=vilaow:vilaow /app /app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER vilaow
EXPOSE 8000

# ${PORT:-8000}, matching CMD below. Hardcoding 8000 made the health check
# report unhealthy anywhere PORT is set to something else.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD sh -c 'curl -fsS "http://localhost:${PORT:-8000}/health"' || exit 1

# Migrations run at boot, before the server accepts traffic. On Render this is
# the only reliable moment to apply them — there is no separate release phase.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
