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

### The LLM cache dominates any long run

`QUESTIONS` holds **5 fixed questions** asked against **one fixed dataset**, and
the LLM cache is keyed on `LLM_MODEL + prompt` (`app/nl2sql/cache.py`). After
the first handful of iterations every query is a cache hit, so a long run
measures cache lookups rather than inference.

The effect is not subtle. Measured on the same stack on 2026-07-21:

| Condition | Query p95 |
|---|---|
| Cold cache, 3 VUs | **38.3s** |
| Warm cache, 2 VUs | **61ms** |

Both are "correct" — they just answer different questions:

- **Warm cache** is a realistic steady state (real users do repeat questions)
  and is what you get by default.
- **Cold cache** is the worst case that actually sizes your LLM capacity. To
  measure it, run the target stack with `LLM_CACHE_ENABLED=false`.

Always record which mode a baseline was taken in — a warm-cache number compared
against a cold-cache number is meaningless. Cross-check with the
`llm_cache_hits_total` Prometheus counter if you're unsure what you measured.

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

### Quick smoke (CI / pre-merge)

A short, low-concurrency run to catch regressions without a big cluster:

```bash
VUS=3 DURATION=30s QUERIES_PER_VU=2 \
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
Cache:         cold (first run against a fresh stack)
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
