# syntax=docker/dockerfile:1.7
# One image, three roles (architecture v0.6 §2):
#   research serve api     - REST, SSE and the static UI
#   research serve agent   - executes runs (scale with --scale agent=N)
#   research migrate       - schema to head + encryption key, then exits
# Build context is the repository root: the image needs config/, contracts/ and migrations/.

ARG PYTHON_VERSION=3.13

FROM python:${PYTHON_VERSION}-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0
WORKDIR /app

# Dependencies first, for layer caching. `--frozen` because uv cannot check the lock without
# every workspace member's pyproject; the second sync below re-checks it with `--locked`.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=agent/pyproject.toml,target=agent/pyproject.toml \
    uv sync --frozen --no-install-workspace --no-dev --no-editable

COPY pyproject.toml uv.lock ./
COPY agent/pyproject.toml agent/pyproject.toml
COPY agent/src agent/src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable


# `docker compose --profile test run --rm tests` - the whole suite, including the Postgres
# integration tests, against a throwaway database on the compose server.
FROM builder AS test
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-editable
COPY config ./config
COPY contracts ./contracts
COPY skills ./skills
COPY migrations ./migrations
COPY alembic.ini ./alembic.ini
COPY agent/tests ./agent/tests
ENV PATH="/app/.venv/bin:${PATH}" \
    APP_ROOT=/app \
    APP_CONFIG_DIR=/app/config \
    APP_CONTRACTS_DIR=/app/contracts
ENTRYPOINT ["pytest"]
CMD ["-q"]


FROM python:${PYTHON_VERSION}-slim AS runtime
# curl is only for the container healthchecks.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app \
    && mkdir -p /run/app-secrets \
    && chown app:app /run/app-secrets

WORKDIR /app
COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --chown=app:app config ./config
COPY --chown=app:app contracts ./contracts
COPY --chown=app:app skills ./skills
COPY --chown=app:app migrations ./migrations
COPY --chown=app:app alembic.ini ./alembic.ini

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    APP_ROOT=/app \
    APP_CONFIG_DIR=/app/config \
    APP_CONTRACTS_DIR=/app/contracts \
    APP_SECRET_KEY_FILE=/run/app-secrets/fernet.key

USER app
EXPOSE 8000 8081
ENTRYPOINT ["research"]
CMD ["serve", "api"]
