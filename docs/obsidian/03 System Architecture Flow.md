---
title: System Architecture Flow
aliases:
  - Architecture
  - System Architecture
tags:
  - datawhisper
  - architecture
  - infrastructure
type: architecture
updated: 2026-08-19
---

# System Architecture Flow

The components DataWhisper is made of, how a request moves through them, and what changes between a laptop and a Kubernetes cluster.

Visual board: `System Architecture.canvas`
Related: [[01 Complete System Workflow]] · [[04 Data Flow Diagram]] · [[02 Frontend Application Workflow]]

## Layered view

```mermaid
flowchart TB
    subgraph client["Client tier"]
        BROWSER["Browser / PWA<br/>React 19 + Vite"]
    end

    subgraph edge["Edge tier"]
        NGINX["nginx<br/>static build + /api proxy + SSE passthrough"]
    end

    subgraph app["Application tier"]
        API["FastAPI<br/>Gunicorn + Uvicorn workers"]
        MW["Middleware chain<br/>CORS → security headers → request id + metrics"]
        ROUTES["Routers<br/>auth · users · upload · query · usage<br/>billing · audit · export · gdpr"]
        NL["nl2sql package<br/>classifier → prompt → validator → repairs → formatter"]
    end

    subgraph state["State tier"]
        META[("Metadata DB<br/>SQLite dev / Postgres prod")]
        DUCK[("DuckDB<br/>one file per session")]
        REDIS[("Redis<br/>conversations · LLM cache · rate limits")]
        OBJ[("Object storage<br/>S3 for dataset files")]
    end

    subgraph ai["Inference tier"]
        OLLAMA["Ollama<br/>llama3.2:3b"]
    end

    BROWSER --> NGINX --> API
    API --> MW --> ROUTES --> NL
    ROUTES --> META
    ROUTES --> DUCK
    ROUTES --> REDIS
    DUCK <--> OBJ
    NL --> OLLAMA
```

## What each tier is responsible for

> [!info] Client
> React 19 SPA. Holds the access token in memory, consumes SSE with `fetch` + a manual reader, lazily loads Recharts. See [[02 Frontend Application Workflow]].

> [!info] Edge
> In production nginx serves the static Vite build and reverse-proxies `/api`, including **SSE passthrough** — buffering must stay off or the streaming stages arrive all at once. In development Vite's own proxy plays the same role.

> [!info] Application
> FastAPI. Every request passes the same middleware chain, every route is prefixed under `/api`, and `/health/live`, `/health/ready`, `/metrics` sit outside it.

> [!info] State
> Four distinct stores with different lifetimes and blast radius — this split is the core of the design, and [[04 Data Flow Diagram]] covers the rules.

> [!info] Inference
> Ollama, reached over HTTP at `OLLAMA_BASE_URL`. Nothing else in the system talks to a model.

## Request path through the middleware chain

```mermaid
sequenceDiagram
    autonumber
    participant C as Client
    participant CORS as CORSMiddleware
    participant SEC as add_security_headers
    participant LOG as log_and_measure
    participant RL as slowapi limiter
    participant DEP as Auth dependency
    participant H as Route handler

    C->>CORS: HTTP request
    CORS->>SEC: origin allowed
    SEC->>LOG: headers queued for response
    LOG->>LOG: request_id = uuid4[:12]
    LOG->>RL: timer started
    RL->>DEP: under the per-IP limit
    DEP->>DEP: decode JWT, check role + email verified
    DEP->>H: current_user
    H-->>LOG: result
    LOG-->>C: response + X-Request-ID, metrics recorded
```

> [!warning] Order is load-bearing
> Security headers are applied to the response on the way out, and the metrics label uses the **matched route template** rather than the raw URL — labelling by raw path would give Prometheus unbounded cardinality, one series per session id.

## Authorization model

```mermaid
erDiagram
    ORGANIZATIONS ||--o{ USERS : has
    ORGANIZATIONS ||--o{ SESSIONS : owns
    ORGANIZATIONS ||--o{ AUDIT_LOGS : records
    USERS ||--o{ SESSIONS : uploaded
    SESSIONS ||--|| DUCKDB_FILE : "one file each"
```

Every user, session, and audit row carries an `org_id`. An authorization check requires **both** ownership/role **and** a matching org, which is what isolates tenants.

| Role | Can do |
|---|---|
| `owner` | Everything in the org, including billing and deletion of the organization |
| `admin` | Manage users, read audit logs |
| `member` | Upload, query, export, manage own account |

> [!important] 404, not 403
> `_authorize()` in `query.py` raises **404** when a caller does not own a session. A 403 would confirm the session exists, which turns the endpoint into an id oracle.

## Reliability around the model

```mermaid
stateDiagram-v2
    [*] --> Closed
    Closed --> Open: N consecutive failures
    Open --> HalfOpen: after LLM_CIRCUIT_RESET_SECONDS
    HalfOpen --> Closed: probe succeeds
    HalfOpen --> Open: probe fails
    Closed --> Closed: retry with backoff on transient error
```

- **Bounded concurrency** — `LLM_MAX_CONCURRENCY` semaphore, so a burst of users queues instead of overwhelming one model server.
- **Retry** — `LLM_RETRY_ATTEMPTS` with exponential backoff.
- **Circuit breaker** — after `LLM_CIRCUIT_FAIL_THRESHOLD` consecutive failures, calls fail fast for `LLM_CIRCUIT_RESET_SECONDS`, so a dead Ollama does not tie up every worker.
- **Cache** — identical prompts reuse a prior response. Safe because temperature is 0.1 and the prompt fully encodes schema + history + question. Keyed by `hash(model + prompt)`, so changing `LLM_MODEL` invalidates it.

## Scaling: what shared state unlocks

```mermaid
flowchart LR
    subgraph single["REDIS_URL empty"]
        W1["Worker 1<br/>in-process history,<br/>cache, rate limits"]
    end
    subgraph shared["REDIS_URL set"]
        W2["Worker A"] --> R[("Redis")]
        W3["Worker B"] --> R
        W4["Worker C"] --> R
    end
    single -. "WEB_CONCURRENCY must be 1" .-> shared
```

`build_conversation_store()` and `build_llm_cache()` pick their backend from `REDIS_URL` alone — swapping is pure configuration, every call site is unchanged.

> [!danger] REQUIRE_SHARED_STATE
> Set `true` on any deployment that can run more than one worker or replica. The process then **refuses to start** without `REDIS_URL` instead of silently falling back to per-pod conversation history and per-pod rate limits — which look fine in staging and lose a user's follow-up question in production.

## Deployment topologies

### Local development

```mermaid
flowchart LR
    V["Vite dev :3000"] -->|proxy /api| U["uvicorn --reload :8000"]
    U --> S[("SQLite file")]
    U --> D[("DuckDB files on disk")]
    U --> O["ollama serve :11434"]
```

Single worker, in-process state, `DEBUG=true`, `/docs` exposed.

### Docker Compose

Defined in `docker-compose.yml` — five services on one network:

| Service | Image / build | Port | Notes |
|---|---|---|---|
| `frontend` | `./frontend` | `8080:80` | nginx serving the production build |
| `backend` | `./backend` | `expose 8000` | Gunicorn/Uvicorn, not `--reload` |
| `ollama` | `ollama/ollama:latest` | `11434` | models in the `ollama_models` volume |
| `redis` | `redis:7-alpine` | internal | `allkeys-lru`, 256 MB, no persistence |
| `postgres` | `postgres:16-alpine` | internal | `postgres_data` volume |

The backend waits on `redis` and `postgres` **health**, not merely start, and reaches every dependency by service name.

### Kubernetes

Manifests in `deploy/k8s`, managed infra in `deploy/terraform`:

- backend Deployment with **2+ replicas**, HPA **2–10**, and a PodDisruptionBudget
- health-gated rollout using `/health/live` and `/health/ready`
- a migration `Job` that runs before the new version takes traffic
- ingress configured for **SSE** (no response buffering)
- `backup-cronjob.yaml` for the scheduled backups behind the RPO ≤ 15 min / RTO ≤ 1 hr targets in `docs/DISASTER_RECOVERY.md`

## Configuration fail-fast

At startup `lifespan` runs `init_db()`, `check_limits_are_reachable()`, and `check_shared_state()`. With `DEBUG=false` the app **refuses to start** when:

- `SECRET_KEY` is unset, default, or weak — a known signing key means forgeable admin tokens
- `ALLOWED_ORIGINS` is `*`
- `REQUIRE_SHARED_STATE=true` but `REDIS_URL` is empty

> [!tip] This is a feature
> A boot that fails loudly on a laptop is cheaper than a deployment that silently accepts forged tokens.
