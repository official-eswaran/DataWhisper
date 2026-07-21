"""Per-tenant plan limits, quota enforcement, and usage metering (issue #6).

Rate limits (slowapi) are per-IP and protect against bursts. Quotas here are
per-organization and per billing period (calendar month), enforcing plan
entitlements — the prerequisite for billing (see go-live checklist #6/#5).

Enforcement is check-then-consume: routes call ``enforce_quota`` before doing
the work and ``record`` after it succeeds, so failed requests don't burn quota.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import HTTPException, status

from app.core.config import settings
from app.core.database import get_org_plan, get_usage, record_usage

logger = logging.getLogger("datawhisper.quota")

# Metric names (also the usage_counters.metric values).
QUERIES = "queries"
UPLOADS = "uploads"
ROWS_PROCESSED = "rows_processed"

# Per-plan monthly limits. -1 means unlimited.
#
# rows_processed is the closest thing here to a compute cost — a 5M-row upload
# is far more expensive than 5M single-row queries — so it carries its own
# ceiling rather than being implied by the query/upload counts.
#
# The free row ceiling must stay comfortably above the row count of a single
# MAX_UPLOAD_SIZE_MB file, or a new user's first large upload is rejected
# outright and the product is unusable on free. ``check_limits_are_reachable``
# warns at startup if that stops being true.
PLAN_LIMITS: dict[str, dict[str, int]] = {
    "free": {QUERIES: 1_000, UPLOADS: 100, ROWS_PROCESSED: 10_000_000},
    "pro": {QUERIES: 50_000, UPLOADS: 5_000, ROWS_PROCESSED: 50_000_000},
    "enterprise": {QUERIES: -1, UPLOADS: -1, ROWS_PROCESSED: -1},
}

# Rough bytes-per-row for a typical CSV, used only to sanity-check the limits
# against each other at startup. Deliberately conservative: underestimating
# row size overestimates the row count, so the check errs toward warning.
_EST_BYTES_PER_ROW = 100

UNLIMITED = -1


def check_limits_are_reachable() -> list[str]:
    """Warn if a plan's row ceiling can't absorb one maximum-size upload.

    These two settings live in different files and are tuned by different
    people at different times, so it is easy to raise MAX_UPLOAD_SIZE_MB (or
    lower a row ceiling) and leave a plan where every large upload 429s after
    being parsed. Returns the warnings so tests can assert on them.
    """
    est_rows = (settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024) // _EST_BYTES_PER_ROW
    warnings: list[str] = []
    for plan, limits in PLAN_LIMITS.items():
        limit = limits.get(ROWS_PROCESSED, UNLIMITED)
        if limit != UNLIMITED and limit < est_rows:
            warnings.append(
                f"Plan '{plan}' allows {limit:,} rows/month but a single "
                f"{settings.MAX_UPLOAD_SIZE_MB} MB upload is roughly "
                f"{est_rows:,} rows — large uploads will be rejected."
            )
    for warning in warnings:
        logger.warning("quota: %s", warning)
    return warnings


def current_period(now: datetime | None = None) -> str:
    """Current billing period as ``YYYY-MM`` in UTC."""
    return (now or datetime.now(UTC)).strftime("%Y-%m")


def plan_limits(plan: str) -> dict[str, int]:
    return PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])


def enforce_quota(org_id: int, metric: str, amount: int = 1) -> None:
    """Raise 429 if consuming ``amount`` of ``metric`` would exceed the plan limit.

    ``amount`` defaults to 1, where this reduces to "has the org already hit the
    limit" — the check for countable actions like queries and uploads. Pass a
    real amount for metrics whose cost isn't known until the work is done
    (rows_processed), so a single large job can't blow through the whole month's
    budget in one request.
    """
    plan = get_org_plan(org_id)
    limit = plan_limits(plan).get(metric, UNLIMITED)
    if limit == UNLIMITED:
        return
    used = get_usage(org_id, current_period()).get(metric, 0)
    if used + amount > limit:
        detail = (
            f"Monthly {metric} limit reached for the '{plan}' plan ({limit:,})."
            if used >= limit
            else f"This request needs {amount:,} {metric} but only "
            f"{max(limit - used, 0):,} of the '{plan}' plan's {limit:,} remain "
            "this period."
        )
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"{detail} Upgrade your plan or wait for the next period.",
        )


def record(org_id: int, metric: str, amount: int = 1) -> None:
    """Meter usage for the current period (best-effort; never blocks a request)."""
    if amount <= 0:
        return
    record_usage(org_id, metric, current_period(), amount)


def usage_summary(org_id: int) -> dict:
    """Owner/admin-facing view: plan, period, per-metric used/limit/remaining."""
    plan = get_org_plan(org_id)
    limits = plan_limits(plan)
    period = current_period()
    used = get_usage(org_id, period)

    metrics: dict[str, dict] = {}
    for metric in (QUERIES, UPLOADS, ROWS_PROCESSED):
        limit = limits.get(metric, UNLIMITED)
        u = int(used.get(metric, 0))
        metrics[metric] = {
            "used": u,
            "limit": None if limit == UNLIMITED else limit,
            "remaining": None if limit == UNLIMITED else max(limit - u, 0),
        }
    return {"plan": plan, "period": period, "metrics": metrics}
