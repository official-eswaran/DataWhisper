# ── DataWhisper backend ───────────────────────────────────────────────────────
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    WEB_CONCURRENCY=1

WORKDIR /app

# `upgrade` before `install`, and it is not decoration: a base tag is a snapshot
# of the day it was built, and Debian keeps shipping security fixes to it
# afterwards. Without this the image inherits whatever was current when
# python:3.12-slim was last pushed, which is how CVE-2026-53615 (util-linux,
# nine packages, fixed in 2.41.5-0+deb13u1) turned CI red on 2026-08-17 with no
# source change involved. The frontend image has done the Alpine equivalent
# (`apk upgrade`) since it was written; this is the same reasoning, applied on
# the side that was missing it.
#
# The Trivy scan in CI is what keeps this honest — it fails the build on HIGH
# and CRITICAL, so a base image that drifts out of date cannot ship quietly.
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY alembic.ini ./alembic.ini
COPY migrations ./migrations

# Run as a non-root user; persist data under /app/data (mount a volume here).
RUN useradd --create-home appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

# Apply DB migrations, then serve. WEB_CONCURRENCY can exceed 1 when REDIS_URL is
# set (shared conversation store + rate limiter). In Kubernetes, prefer running
# `alembic upgrade head` as a Job/initContainer instead of per-replica.
CMD ["sh", "-c", "alembic upgrade head && gunicorn app.main:app -k uvicorn.workers.UvicornWorker -w ${WEB_CONCURRENCY} -b 0.0.0.0:8000 --timeout 120 --graceful-timeout 30"]
