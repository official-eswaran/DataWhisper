"""Shared slowapi limiter.

Uses Redis for counter storage when ``REDIS_URL`` is set, so rate limits are
enforced consistently across every worker and replica. Falls back to in-memory
storage (single-process only) otherwise. Defined in its own module so routes and
``main`` import the same instance without a circular dependency.
"""
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import settings

limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=settings.rate_limit_storage_uri,
)
