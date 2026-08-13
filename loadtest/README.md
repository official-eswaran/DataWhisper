# DataWhisper load testing (k6)

Capacity-baseline suite for the core user flow: **login → upload → query**.
Closes the k6 half of issue #2 (M11). The suite doubles as a CI performance
gate — thresholds are encoded in the script, so a breaching run exits non-zero.

## Prerequisites

- [k6](https://k6.io/docs/get-started/installation/) installed locally, and
- a running DataWhisper stack (`docker compose up` from the repo root, or a
  deployed environment) with Ollama reachable and the model pulled, and
- **the target stack's rate limits raised** — see below. This is not optional;
  a stack on production limits cannot be load-tested at all.

### Rate limits will throttle the generator (read this first)

k6 drives the API from a **single source IP**. slowapi's limits are **per-IP**
burst protection, defaulting to `RATE_LIMIT_QUERY=30/minute`. A run at any
meaningful concurrency therefore 429s almost every query, and what you measure
is the rate limiter — not the stack.

Raise the limits on the environment under test:

```bash
RATE_LIMIT_LOGIN=10000/minute \
RATE_LIMIT_QUERY=10000/minute \
RATE_LIMIT_UPLOAD=10000/minute
```

The script counts 429s in a dedicated `dw_rate_limited` metric with a
`count<1` threshold, so a throttled run fails by name instead of looking like
a capacity or error-rate problem. **If `dw_rate_limited` is non-zero, the rest
of the numbers are meaningless.**

### The LLM cache decides what your latency numbers mean (`CACHE_MODE`)

The LLM cache is keyed on `LLM_MODEL + prompt` (`app/nl2sql/cache.py`), so a run
that repeats questions stops reaching the model and starts measuring cache
lookups. The effect is not subtle — measured on the same stack on 2026-07-21:

| Condition | Query p95 |
|---|---|
| Cold cache, 3 VUs | **38.3s** |
| Warm cache, 2 VUs | **61ms** |

Both are "correct"; they answer different questions. Warm cache is the realistic
steady state (real users repeat questions). Cold cache is the worst case that
sizes your LLM capacity.

Declare which one you are measuring with `CACHE_MODE`, and the run **verifies
it** against the server's own `llm_cache_{hits,misses}_total` counters rather
than taking your word for it (issue #26):

| `CACHE_MODE` | Means | Enforced by |
|---|---|---|
| `auto` (default) | No claim. Reports the hit ratio it observed. | nothing — reporting only |
| `cold` | "This measures LLM capacity." | `dw_cache_hit_ratio < 0.05` |
| `warm` | "This measures cached steady state." | `dw_cache_hit_ratio > 0.5` |

k6 cannot turn the cache off from the outside, so **`CACHE_MODE=cold` also needs
`LLM_CACHE_ENABLED=false` on the target stack**. If you forget, the run fails on
`dw_cache_hit_ratio` and says so by name instead of publishing a fantasy number:

```
CACHE MODE: cold — 36.0% of 25 LLM lookups served from cache (16 reached the model).
THIS RUN DID NOT MEASURE WHAT IT CLAIMED. …
```

Every run ends with that `CACHE MODE:` line whatever the mode. **Record it with
the baseline** — a warm number and a cold number are not comparable.

The question pool is 20 questions and each VU starts at a different offset, so a
short run no longer goes warm within seconds by accident. That widens the window;
it does not replace `LLM_CACHE_ENABLED=false` for a true cold measurement.

Verification requires `/metrics` on the target (`METRICS_ENABLED=true`, the
default). Without it the run still works, prints a warning, and asserts nothing.

### Quotas will also stop a long campaign

Per-tenant quotas (`app/core/quota.py`) are per-org and per calendar month, and
the seeded `ceo` account is on the **free** plan: 100 uploads and 1,000 queries
a month. Each VU uploads once per run, and every query counts. Repeated runs
will eventually 429 on quota rather than rate limits — a different failure with
an identical status code.

Use an org on the `enterprise` plan (unlimited) as the load-test tenant, or
reset the counters between campaigns.

## Files

| File | Purpose |
|------|---------|
| `k6-login-upload-query.js` | Ramping-VU scenario over the full flow with SLO thresholds. |
| `sample.csv` | Self-contained dataset each VU uploads (copy of `sample_data/sales_data.csv`). |

## Preflight — run this first (#25)

```bash
python3 loadtest/preflight.py --base-url https://staging.example.com \
    --user ceo --password '…' --vus 10 --duration 5m --cache-mode cold
```

Exits non-zero, with the fix, if the target would make the run measure something
other than the stack:

| Check | Blocks when |
|---|---|
| reachable | `/health/ready` is not 200 |
| rate limits | a 40-request login burst gets any 429 — see below |
| cache | `CACHE_MODE=cold` while the target is already serving >5% from cache |
| quota | the planned query count exceeds the org's remaining monthly allowance |

Everything it checks is written in prose above, and prose does not fail a build.
A staging run costs minutes and a coordinated window; finding out afterwards
that `dw_rate_limited` was non-zero wastes both.

**The rate-limit probe uses `POST /api/auth/login` with a username that does not
exist.** Login is the cheapest route that actually carries a limiter —
`/api/usage/` has none, so bursting it proves nothing, which the first version of
this check did. The nonexistent username matters too: failed attempts lock the
account they name, and there is no account here to lock. Note this proves
`RATE_LIMIT_LOGIN` only; `QUERY` and `UPLOAD` are separate values, so raise all
three together and let `dw_rate_limited` catch the rest.

## Run

```bash
BASE_URL=http://localhost:8000 \
DW_USER=ceo DW_PASS='Admin@2024' \
k6 run loadtest/k6-login-upload-query.js
```

### Tunables (environment variables)

| Var | Default | Meaning |
|-----|---------|---------|
| `BASE_URL` | `http://localhost:8000` | API base URL. |
| `DW_USER` / `DW_PASS` | `ceo` / `Admin@2024` | Login credentials. |
| `VUS` | `10` | Peak virtual users (concurrent sessions). |
| `DURATION` | `2m` | Steady-state hold time (excludes 30s ramp-up + 20s ramp-down). |
| `QUERIES_PER_VU` | `5` | Questions each VU asks per iteration. |
| `SLEEP` | `1` | Think-time (seconds) between requests. |
| `CACHE_MODE` | `auto` | `auto` \| `cold` \| `warm` — see above. Verified against the server's cache counters. |
| `METRICS_URL` | `$BASE_URL/metrics` | Where to read the cache counters, if `/metrics` is elsewhere. |

### Quick smoke (CI / pre-merge)

A short, low-concurrency run to catch regressions without a big cluster:

```bash
VUS=3 DURATION=30s QUERIES_PER_VU=2 \
k6 run loadtest/k6-login-upload-query.js
```

### Capacity run (what sizes your LLM hardware)

```bash
# On the target stack: LLM_CACHE_ENABLED=false
CACHE_MODE=cold VUS=10 DURATION=5m \
k6 run loadtest/k6-login-upload-query.js
```

## Thresholds (SLOs)

Encoded in `options.thresholds`; the run fails if any is breached:

| Metric | Budget |
|--------|--------|
| `dw_rate_limited` | `count < 1` — any 429 invalidates the run |
| `http_req_failed` | error rate `< 1%` |
| `dw_business_errors` | app-level (non-2xx / `type=="error"`) rate `< 1%` |
| `dw_login_duration` | p95 `< 1.5s` |
| `dw_upload_duration` | p95 `< 3s` |
| `dw_query_duration` | p95 `< 8s` (LLM-bound; tune to your GPU/CPU) |
| `dw_cache_hit_ratio` | only when `CACHE_MODE` is `cold` (`< 0.05`) or `warm` (`> 0.5`) — the run must have measured what it claimed |

The query threshold is deliberately hardware-dependent — the LLM call dominates.
Record the observed numbers as your **baseline** (below) and tighten from there.

## Recording a baseline

After a representative run, capture the k6 summary and note the environment:

```
Date:          <yyyy-mm-dd>
Commit:        <sha>
Environment:   <e.g. 1 replica, 4 vCPU, Ollama on RTX 4090, llama3.2:3b>
Peak VUs:      <N>
Query p95:     <ms>       Query p99: <ms>
Upload p95:    <ms>
Login p95:     <ms>
Error rate:    <%>
Throughput:    <queries/s>  (dw_queries_run / duration)
```

Commit the recorded baseline so future runs have something to regress against.

## Runs so far

### 2026-07-21 — first end-to-end execution (NOT a baseline)

The script had never actually been run before this date; it was syntax-checked
only. Running it surfaced two script bugs, now fixed (per-VU session reuse and
429 accounting), plus the cache-dominance issue documented above. The numbers
below are recorded for provenance, **not** as a capacity baseline — a developer laptop sharing a CPU with the LLM is not
representative of anything you should set an SLO from.

```
Date:          2026-07-21
Commit:        9dceca8 (script fixes applied on top)
Environment:   dev laptop, single uvicorn worker, SQLite,
               Ollama llama3.2:3b on CPU, same machine as the generator
Cache:         cold (first run against a fresh stack — asserted by hand;
               CACHE_MODE did not exist yet)
Peak VUs:      3
Query p95:     38.3s      (median 10.2s, max 43.6s)
Upload p95:    373ms
Login p95:     575ms
Error rate:    0%         (dw_rate_limited: 0, dw_business_errors: 0)
Throughput:    0.13 queries/s
```

What this run does and doesn't establish:

- **Does:** the flow works end to end, the script is correct, and login/upload
  are comfortably inside their thresholds even on weak hardware.
- **Doesn't:** say anything about query latency in production. p95 of 38s
  against the committed 8s threshold is entirely CPU-bound LLM inference with
  three concurrent requests against a 3B model. On the GPU node the k8s
  manifests assume, this number is not comparable.

The 8s query threshold remains **unvalidated**. Do not tune it to match numbers
from hardware like the above — run against staging and set it from there.

### 2026-07-23 — `CACHE_MODE` verification (NOT a baseline either)

Short runs on the same dev laptop, purely to prove the cache-mode machinery
does what it claims. No capacity conclusions:

| Run | Target | Observed hit ratio | `dw_cache_hit_ratio` |
|---|---|---|---|
| `CACHE_MODE=auto` | cache on, fresh | 0% (1 lookup) | not asserted |
| `CACHE_MODE=cold` | cache **on** (the mistake) | 36% → 97% | ✗ **run failed, exit 99** |
| `CACHE_MODE=cold` | `LLM_CACHE_ENABLED=false` | 0% (7 lookups) | ✓ passed |

So the failure mode the issue describes — publishing a warm-cache number as if
it were capacity — now stops the run instead of producing a plausible-looking
figure. `dw_query_duration` still breached on this hardware, as expected and as
recorded above.
