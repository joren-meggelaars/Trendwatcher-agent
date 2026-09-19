# syntax=docker/dockerfile:1

# ---- builder: resolve + install dependencies with uv ----
FROM python:3.12-slim-bookworm AS builder

RUN pip install --no-cache-dir uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY . .
RUN uv sync --frozen --no-dev

# ---- runtime: only the venv + app source ----
FROM python:3.12-slim-bookworm AS runtime

RUN groupadd --system app && useradd --system --gid app --home-dir /app --shell /usr/sbin/nologin app

WORKDIR /app
COPY --from=builder --chown=app:app /app /app

# Mount point for the seen-items cache volume (see docker-compose.yml). Must
# exist and be owned by app in the image: a fresh named volume inherits the
# ownership of the directory it is mounted over, and the process is not root.
RUN mkdir -p /app/data && chown app:app /app/data

USER app
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1

# No HTTP server here: main.py starts an APScheduler BlockingScheduler that
# manages daily_digest/weekly_discovery on their own cron schedules and keeps
# the process (and container) alive for as long as it runs.
CMD ["python", "main.py"]
