"""Health probes for load balancers / Kubernetes.

* Liveness  — is the process up? (cheap; failure → restart the pod)
* Readiness — can it serve traffic? Requires the metadata DB. Ollama is reported
  as a component but does NOT fail readiness, because login/upload still work
  while inference is degraded — the LB should keep routing so users can sign in
  and see a friendly "AI temporarily unavailable" instead of a hard outage.
"""
from __future__ import annotations

import logging

import requests

from app.core.config import settings
from app.core.database import ping_db

logger = logging.getLogger("datawhisper.health")


def check_database() -> bool:
    try:
        return ping_db()
    except Exception:
        logger.exception("DB health check failed")
        return False


def check_ollama() -> bool:
    try:
        resp = requests.get(f"{settings.OLLAMA_BASE_URL}/api/tags", timeout=3)
        return resp.status_code == 200
    except Exception:
        return False


def readiness() -> tuple[bool, dict]:
    db_ok = check_database()
    ollama_ok = check_ollama()
    components = {
        "database": "ok" if db_ok else "down",
        "ollama": "ok" if ollama_ok else "degraded",
    }
    # Ready iff the critical dependency (DB) is healthy.
    return db_ok, components
