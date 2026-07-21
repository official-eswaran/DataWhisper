"""FastAPI application entrypoint."""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded

from app.api.routes import (
    audit,
    auth,
    billing,
    export,
    gdpr,
    query,
    upload,
    usage,
    users,
)
from app.core.config import settings
from app.core.database import (
    cleanup_stale_sessions,
    init_db,
    purge_expired_refresh_tokens,
)
from app.core.health import readiness
from app.core.observability import (
    metrics_response,
    record_request,
    request_id_var,
    setup_logging,
)
from app.core.quota import check_limits_are_reachable
from app.core.ratelimit import limiter
from app.core.telemetry import init_sentry, init_tracing

setup_logging(debug=settings.DEBUG, json_logs=settings.LOG_JSON)
logger = logging.getLogger("datawhisper")

# Error tracking is initialised as early as possible; both calls are no-ops
# unless their env vars are set.
init_sentry()

_CLEANUP_INTERVAL_SECONDS = 3600


async def _cleanup_loop():
    while True:
        await asyncio.sleep(_CLEANUP_INTERVAL_SECONDS)
        try:
            purged = cleanup_stale_sessions()
            purge_expired_refresh_tokens()
            if purged:
                logger.info("Cleanup removed %d stale session(s)", purged)
        except Exception:
            logger.exception("Background cleanup failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    check_limits_are_reachable()  # warns if a plan's row cap rejects big uploads
    logger.info("DataWhisper started — model: %s", settings.LLM_MODEL)
    task = asyncio.create_task(_cleanup_loop())
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(
    title="DataWhisper",
    description="Private AI Data Assistant — NL to SQL with Zero Data Leakage",
    version="2.0.0",
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url="/redoc" if settings.DEBUG else None,
    lifespan=lifespan,
)

# Distributed tracing (no-op unless OTEL_EXPORTER_OTLP_ENDPOINT is set).
init_tracing(app)

# ── Rate limiting ─────────────────────────────────────────────────────────────
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def _rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(status_code=429, content={"detail": "Too many requests. Please slow down."})


# ── Consistent error envelopes (never leak internals) ─────────────────────────
@app.exception_handler(RequestValidationError)
async def _validation_handler(request: Request, exc: RequestValidationError):
    # jsonable_encoder makes validator ctx (e.g. ValueError) JSON-safe.
    return JSONResponse(
        status_code=422,
        content=jsonable_encoder({"detail": "Invalid request.", "errors": exc.errors()}),
    )


@app.exception_handler(Exception)
async def _unhandled_handler(request: Request, exc: Exception):
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error."})


# ── CORS ──────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    response.headers["Content-Security-Policy"] = "default-src 'self'; frame-ancestors 'none'"
    return response


@app.middleware("http")
async def log_and_measure(request: Request, call_next):
    request_id = uuid.uuid4().hex[:12]
    token = request_id_var.set(request_id)
    start = time.perf_counter()
    try:
        response = await call_next(request)
        duration = time.perf_counter() - start
        response.headers["X-Request-ID"] = request_id

        # Matched route template (not the raw URL) to bound metric cardinality.
        route = request.scope.get("route")
        path_label = getattr(route, "path", request.url.path)

        if settings.METRICS_ENABLED and path_label != "/metrics":
            record_request(request.method, path_label, response.status_code, duration)

        logger.info(
            "%s %s — %d (%.0fms) — %s — rid=%s",
            request.method,
            request.url.path,
            response.status_code,
            duration * 1000,
            request.client.host if request.client else "unknown",
            request_id,
        )
        return response
    finally:
        request_id_var.reset(token)


# ── Routes ────────────────────────────────────────────────────────────────────
app.include_router(auth.router, prefix="/api/auth", tags=["Authentication"])
app.include_router(users.router, prefix="/api/users", tags=["User Management"])
app.include_router(upload.router, prefix="/api/upload", tags=["Data Upload"])
app.include_router(query.router, prefix="/api/query", tags=["NL Query"])
app.include_router(usage.router, prefix="/api/usage", tags=["Usage & Quotas"])
app.include_router(billing.router, prefix="/api/billing", tags=["Billing"])
app.include_router(audit.router, prefix="/api/audit", tags=["Audit Logs"])
app.include_router(export.router, prefix="/api/export", tags=["Export Reports"])
app.include_router(gdpr.router, prefix="/api", tags=["Privacy / GDPR"])


@app.get("/health")
def health_check():
    """Backward-compatible liveness alias."""
    return {"status": "running", "model": settings.LLM_MODEL, "version": app.version}


@app.get("/health/live")
def liveness():
    """Cheap check — the process is up and serving. Failure → restart the pod."""
    return {"status": "alive", "version": app.version}


@app.get("/health/ready")
def readiness_probe(response: Response):
    """Deep check — safe to route traffic? Fails (503) only if the critical
    dependency (metadata DB) is down; Ollama is reported but non-fatal."""
    ready, components = readiness()
    if not ready:
        response.status_code = 503
    return {"status": "ready" if ready else "not_ready", "components": components}


@app.get("/metrics")
def metrics():
    if not settings.METRICS_ENABLED:
        return Response(status_code=404)
    body, content_type = metrics_response()
    return Response(content=body, media_type=content_type)
