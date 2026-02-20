import logging
import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import upload, query, auth, audit, export
from app.core.config import settings
from app.core.database import init_audit_db

# ── Structured logging ────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("datawhisper")

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="DataWhisper",
    description="Private AI Data Assistant — NL to SQL with Zero Data Leakage",
    version="1.0.0",
    # Hide docs in production
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url="/redoc" if settings.DEBUG else None,
)

# ── CORS ──────────────────────────────────────────────────────────────────────
origins = [o.strip() for o in settings.ALLOWED_ORIGINS.split(",")]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Security headers middleware ───────────────────────────────────────────────
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    # Don't set HSTS — this is HTTP on LAN, not HTTPS
    return response


# ── Request logging middleware ────────────────────────────────────────────────
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - start) * 1000
    logger.info(
        "%s %s — %d (%.0fms) — %s",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
        request.client.host if request.client else "unknown",
    )
    return response


# ── Routes ────────────────────────────────────────────────────────────────────
app.include_router(auth.router,   prefix="/api/auth",   tags=["Authentication"])
app.include_router(upload.router, prefix="/api/upload", tags=["Data Upload"])
app.include_router(query.router,  prefix="/api/query",  tags=["NL Query"])
app.include_router(audit.router,  prefix="/api/audit",  tags=["Audit Logs"])
app.include_router(export.router, prefix="/api/export", tags=["Export Reports"])


# ── Startup ───────────────────────────────────────────────────────────────────
@app.on_event("startup")
async def startup():
    init_audit_db()
    logger.info("DataWhisper started — model: %s", settings.LLM_MODEL)


# ── Health check (public) ─────────────────────────────────────────────────────
@app.get("/health")
def health_check():
    return {"status": "running", "model": settings.LLM_MODEL}
