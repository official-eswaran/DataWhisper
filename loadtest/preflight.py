#!/usr/bin/env python3
"""Refuse a load test that would measure the wrong thing (issue #25).

    python3 loadtest/preflight.py --base-url https://staging.example.com \
        --user ceo --password '…' --vus 10 --duration 5m --queries-per-vu 5

Every latency SLO in this repo, the alert thresholds, and the E2E's 120s timeout
descend from a single dev-laptop run. The fix is a run against real staging —
and the three ways that run silently measures something else are already written
down in `README.md` as prose that has to be remembered:

  1. **The rate limiter.** k6 drives from one IP and slowapi limits are per-IP,
     so a target on production limits 429s almost every query. The k6 script
     notices *afterwards* via `dw_rate_limited`; by then the run is spent.
  2. **The LLM cache.** Keyed on model+prompt, so a repeating script stops
     reaching the model. 38.3s cold vs 61ms warm on the same stack.
  3. **The quota.** A long campaign hits the org's monthly query ceiling and
     starts measuring 429s from the quota gate instead of the app.

This checks all three against the actual target before k6 starts, and exits
non-zero with the reason. It is deliberately a separate script rather than more
README: the run costs minutes and a coordinated staging window, and prose does
not fail a build.

Exit codes: 0 ready, 1 not ready (reasons printed), 2 usage/connection error.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request

# slowapi's defaults are 10/minute for login, 30/minute for query and 20/minute
# for upload; README.md asks for 10000/minute on the target under test. 40
# requests therefore trips any production-shaped limit, provided they all land
# inside the limiter's one-minute window — hence the window below, which is
# generous rather than tight: it only has to catch a target so slow that the
# burst spread across two windows and proved nothing.
BURST_REQUESTS = 40
BURST_WINDOW_S = 30.0

_problems: list[str] = []
_notes: list[str] = []


def problem(msg: str) -> None:
    _problems.append(msg)
    print(f"  BLOCK  {msg}")


def ok(msg: str) -> None:
    print(f"  ok     {msg}")


def note(msg: str) -> None:
    _notes.append(msg)
    print(f"  note   {msg}")


def _request(url: str, *, token: str = "", data: bytes | None = None,
             timeout: float = 15) -> tuple[int, str]:
    req = urllib.request.Request(url, data=data)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001 — connection errors are a usage error
        print(f"cannot reach {url}: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


# ── Checks ────────────────────────────────────────────────────────────────────

def check_reachable(base: str) -> None:
    status, body = _request(f"{base}/health/ready")
    if status != 200:
        problem(f"/health/ready returned {status}: {body[:200]}")
        return
    try:
        payload = json.loads(body)
    except ValueError:
        payload = {}
    ok(f"target is ready ({payload.get('database', 'db state unreported')})")


def login(base: str, user: str, password: str) -> str:
    status, body = _request(
        f"{base}/api/auth/login",
        data=json.dumps({"username": user, "password": password}).encode(),
    )
    if status != 200:
        print(f"login failed ({status}): {body[:200]}", file=sys.stderr)
        raise SystemExit(2)
    token = json.loads(body).get("access_token", "")
    if not token:
        print("login returned no access_token", file=sys.stderr)
        raise SystemExit(2)
    return token


def check_rate_limits(base: str) -> None:
    """Burst the login route with a username that does not exist.

    Measured rather than read from config: what matters is what the limiter does
    to *this* source IP, which is what k6 will be, and a proxy or WAF in front of
    the app can throttle without the app knowing.

    Login is the probe because it is the cheapest route that actually carries a
    limiter — `/api/usage/` has none at all, so bursting it proves nothing (the
    first version of this check did exactly that and passed against a target
    with production limits). A **nonexistent** username keeps it safe: failed
    attempts lock the account they name, and there is no account here to lock.

    This proves `RATE_LIMIT_LOGIN` specifically. `RATE_LIMIT_QUERY` and
    `RATE_LIMIT_UPLOAD` are separate values, so a target that raised only login
    still passes this and fails during the run on `dw_rate_limited` — raise all
    three together, as README.md says.
    """
    started = time.monotonic()
    statuses: list[int] = []
    for _ in range(BURST_REQUESTS):
        status, _body = _request(
            f"{base}/api/auth/login",
            data=json.dumps(
                {"username": "preflight-probe-does-not-exist", "password": "x"}
            ).encode(),
        )
        statuses.append(status)
    elapsed = time.monotonic() - started
    throttled = sum(1 for s in statuses if s == 429)

    if elapsed > BURST_WINDOW_S:
        note(
            f"burst of {BURST_REQUESTS} took {elapsed:.1f}s — slower than the "
            f"{BURST_WINDOW_S:.0f}s window this check assumes, so a limiter just "
            "above the burst rate could still be hiding"
        )
    if throttled:
        first = statuses.index(429) + 1
        problem(
            f"rate limited after {first} requests ({throttled}/{BURST_REQUESTS} "
            "got 429). k6 drives from one IP, so the run would measure slowapi "
            "rather than the stack. Set RATE_LIMIT_LOGIN, RATE_LIMIT_QUERY and "
            "RATE_LIMIT_UPLOAD to 10000/minute on the target."
        )
    else:
        ok(f"no 429s in a {BURST_REQUESTS}-request login burst ({elapsed:.1f}s) — "
           "RATE_LIMIT_LOGIN is raised")


def _counter(metrics: str, name: str) -> float | None:
    match = re.search(rf"^{re.escape(name)}(?:\{{[^}}]*\}})?\s+([0-9.eE+-]+)$", metrics, re.M)
    return float(match.group(1)) if match else None


def check_cache_mode(base: str, metrics_url: str, cache_mode: str) -> None:
    status, body = _request(metrics_url)
    if status != 200:
        note(
            f"{metrics_url} returned {status} — cannot verify the cache state, and "
            "the k6 run's CACHE_MODE check will not work either"
        )
        return

    hits = _counter(body, "llm_cache_hits_total")
    misses = _counter(body, "llm_cache_misses_total")
    if hits is None or misses is None:
        note("cache counters absent from /metrics — is METRICS_ENABLED on?")
        return

    total = hits + misses
    ratio = hits / total if total else 0.0
    ok(f"cache counters readable: {int(hits)} hits / {int(misses)} misses"
       + (f" (ratio {ratio:.2f})" if total else " (no traffic yet)"))

    if cache_mode == "cold":
        if total and ratio > 0.05:
            problem(
                f"CACHE_MODE=cold but the target is already serving {ratio:.0%} of "
                "prompts from cache. Set LLM_CACHE_ENABLED=false on the target and "
                "restart it, or the run measures cache lookups (61ms) rather than "
                "the model (38.3s)."
            )
        else:
            ok("CACHE_MODE=cold is plausible against this target's current counters")
    elif cache_mode == "warm":
        note("CACHE_MODE=warm — the k6 run enforces the >0.5 hit ratio itself")


def check_quota(base: str, token: str, planned_queries: int) -> None:
    status, body = _request(f"{base}/api/usage/", token=token)
    if status != 200:
        problem(f"/api/usage/ returned {status} — cannot size the run against the quota")
        return
    metrics = json.loads(body).get("metrics", {})
    queries = metrics.get("queries", {})
    remaining = queries.get("remaining")

    if remaining is None:
        ok(f"query quota is unlimited on this plan ({json.loads(body).get('plan')})")
        return
    if remaining < planned_queries:
        problem(
            f"the run plans ~{planned_queries} queries and the org has {remaining} "
            "left this period. It would start measuring the quota gate's 429s "
            "part-way through. Raise the plan or reset the counter first."
        )
    else:
        headroom = remaining - planned_queries
        ok(f"quota headroom: {remaining} remaining, ~{planned_queries} planned "
           f"({headroom} spare)")


# ── Planning arithmetic ───────────────────────────────────────────────────────

def parse_duration(text: str) -> float:
    """k6-style duration ('5m', '90s', '1h30m') to seconds."""
    total = 0.0
    for value, unit in re.findall(r"(\d+(?:\.\d+)?)\s*([hms])", text):
        total += float(value) * {"h": 3600, "m": 60, "s": 1}[unit]
    if not total:
        raise argparse.ArgumentTypeError(f"cannot read duration {text!r}")
    return total


def planned_query_count(vus: int, duration_s: float, queries_per_vu: int, sleep_s: float) -> int:
    """Upper bound on queries the campaign will issue.

    Assumes think-time is the only thing between queries, which under-estimates
    iteration time and therefore over-estimates the count. Over-estimating is the
    safe direction: it fails the quota check early rather than mid-run.
    """
    per_iteration_s = max(queries_per_vu * sleep_s, 1.0)
    iterations = max(duration_s / per_iteration_s, 1.0)
    return int(vus * iterations * queries_per_vu)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--base-url", required=True)
    p.add_argument("--user", required=True)
    p.add_argument("--password", required=True)
    p.add_argument("--vus", type=int, default=10)
    p.add_argument("--duration", default="2m")
    p.add_argument("--queries-per-vu", type=int, default=5)
    p.add_argument("--sleep", type=float, default=1.0)
    p.add_argument("--cache-mode", choices=("auto", "cold", "warm"), default="auto")
    p.add_argument("--metrics-url", default="")
    args = p.parse_args()

    base = args.base_url.rstrip("/")
    metrics_url = args.metrics_url or f"{base}/metrics"
    planned = planned_query_count(
        args.vus, parse_duration(args.duration), args.queries_per_vu, args.sleep
    )

    print(f"Preflight for {base}")
    print(f"  plan: {args.vus} VUs x {args.duration}, {args.queries_per_vu} queries/VU, "
          f"CACHE_MODE={args.cache_mode} — roughly {planned} queries\n")

    check_reachable(base)
    token = login(base, args.user, args.password)
    ok("login works with the supplied credentials")
    check_rate_limits(base)
    check_cache_mode(base, metrics_url, args.cache_mode)
    check_quota(base, token, planned)

    print()
    if _problems:
        print(f"NOT READY — {len(_problems)} blocking problem(s). "
              "Fix these before spending a run:", file=sys.stderr)
        for item in _problems:
            print(f"  - {item}", file=sys.stderr)
        raise SystemExit(1)
    print(f"READY — {len(_notes)} note(s), nothing blocking.")


if __name__ == "__main__":
    main()
