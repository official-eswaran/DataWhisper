# DataWhisper load testing (k6)

Capacity-baseline suite for the core user flow: **login → upload → query**.
Closes the k6 half of issue #2 (M11). The suite doubles as a CI performance
gate — thresholds are encoded in the script, so a breaching run exits non-zero.

## Prerequisites

- [k6](https://k6.io/docs/get-started/installation/) installed locally, and
- a running DataWhisper stack (`docker compose up` from the repo root, or a
  deployed environment) with Ollama reachable and the model pulled.

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
