from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import upload, query, auth, audit, export
from app.core.config import settings
from app.core.database import init_audit_db

app = FastAPI(
    title="DataWhisper",
    description="Private AI Data Assistant — NL to SQL with Zero Data Leakage",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include route modules
app.include_router(auth.router, prefix="/api/auth", tags=["Authentication"])
app.include_router(upload.router, prefix="/api/upload", tags=["Data Upload"])
app.include_router(query.router, prefix="/api/query", tags=["NL Query"])
app.include_router(audit.router, prefix="/api/audit", tags=["Audit Logs"])
app.include_router(export.router, prefix="/api/export", tags=["Export Reports"])


@app.on_event("startup")
async def startup():
    init_audit_db()


@app.get("/health")
def health_check():
    return {"status": "running", "model": settings.LLM_MODEL}
