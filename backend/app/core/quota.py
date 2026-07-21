"""Per-tenant plan limits, quota enforcement, and usage metering (issue #6).

Rate limits (slowapi) are per-IP and protect against bursts. Quotas here are
per-organization and per billing period (calendar month), enforcing plan
entitlements — the prerequisite for billing (see go-live checklist #6/#5).

Enforcement is check-then-consume: routes call ``enforce_quota`` before doing
the work and ``record`` after it succeeds, so failed requests don't burn quota.
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import HTTPException, status

from app.core.database import get_org_plan, get_usage, record_usage

# Metric names (also the usage_counters.metric values).
QUERIES = "queries"
UPLOADS = "uploads"
ROWS_PROCESSED = "rows_processed"

# Per-plan monthly limits. -1 means unlimited. rows_processed/storage are
# metered for visibility but not hard-enforced by default.
PLAN_LIMITS: dict[str, dict[str, int]] = {
    "free": {QUERIES: 1_000, UPLOADS: 100},
    "pro": {QUERIES: 50_000, UPLOADS: 5_000},
    "enterprise": {QUERIES: -1, UPLOADS: -1},
}

UNLIMITED = -1


def current_period(now: datetime | None = None) -> str:
    """Current billing period as ``YYYY-MM`` in UTC."""
    return (now or datetime.now(UTC)).strftime("%Y-%m")


def plan_limits(plan: str) -> dict[str, int]:
    return PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])


def enforce_quota(org_id: int, metric: str) -> None:
    """Raise 429 if the org has reached its plan limit for ``metric`` this period."""
    plan = get_org_plan(org_id)
    limit = plan_limits(plan).get(metric, UNLIMITED)
    if limit == UNLIMITED:
        return
    used = get_usage(org_id, current_period()).get(metric, 0)
    if used >= limit:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Monthly {metric} limit reached for the '{plan}' plan "
            f"({limit}). Upgrade your plan or wait for the next period.",
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
