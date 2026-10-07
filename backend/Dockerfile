# Multi-stage build — keeps final image minimal (no build tools, no pip cache)
# Build from the REPOSITORY ROOT (rag/ and safeguarding/ live outside backend/):
#   docker build -f backend/Dockerfile -t rusty-api .
# One image serves the API, the rusty-migrate job (alembic) and the rusty-ingest job.
FROM python:3.11-slim AS builder

WORKDIR /build

# Install deps into a prefix we can copy cleanly
COPY backend/requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Final stage ──────────────────────────────────────────────────────────────
FROM python:3.11-slim

# Non-root user — Cloud Run security requirement
RUN useradd --no-create-home --shell /bin/false rusty

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application source (backend/rag and backend/safeguarding are dev symlinks — copy the real dirs)
COPY backend/app/ ./app/
COPY backend/migrations/ ./migrations/
COPY backend/alembic.ini .
COPY rag/ ./rag/
COPY safeguarding/ ./safeguarding/

# PYTHONPATH makes both `app` and `rag` importable. No env file is baked in: Cloud Run
# supplies configuration as env vars + Secret Manager. FORWARDED_ALLOW_IPS lets the
# app see the real client address behind Cloud Run's front end (only ingress path).
ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    APP_ENV=production \
    FORWARDED_ALLOW_IPS="*"

# No root at runtime
USER rusty

EXPOSE 8080

# Cloud Run sets $PORT. WEB_CONCURRENCY = gunicorn workers (each has its own DB pool).
CMD ["sh", "-c", "exec gunicorn app.main:app --worker-class uvicorn.workers.UvicornWorker --workers \"${WEB_CONCURRENCY:-2}\" --bind \"0.0.0.0:${PORT:-8080}\" --timeout 120 --access-logfile - --error-logfile -"]
