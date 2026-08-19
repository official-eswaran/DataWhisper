---
title: Complete System Workflow
aliases:
  - End-to-End Workflow
  - System Workflow
tags:
  - datawhisper
  - workflow
  - backend
  - frontend
type: workflow
updated: 2026-08-19
---

# Complete System Workflow

The full path a user takes: **sign in → upload a dataset → ask a question → read the answer**. Every stage below names the file that owns it, so a reader can jump from the diagram into the code.

Related: [[03 System Architecture Flow]] for the components, [[04 Data Flow Diagram]] for where the bytes live, [[02 Frontend Application Workflow]] for the browser half.

## The whole thing in one diagram

```mermaid
flowchart TD
    A([User opens the app]) --> B{Refresh cookie<br/>still valid?}
    B -- yes --> D[Dashboard]
    B -- no --> C[Login screen]
    C -->|POST /api/auth/login| D

    D --> E[Upload CSV / Excel / JSON / Parquet]
    E -->|POST /api/upload/| F[Parse, clean schema,<br/>load into DuckDB]
    F --> G[Anomalies reported,<br/>session_id returned]

    G --> H[Ask a question in English]
    H -->|POST /api/query/stream| I{Intent?}
    I -- chitchat --> J[Canned friendly reply]
    I -- off_topic --> K[Refusal]
    I -- data_query --> L[Build prompt with schema<br/>+ conversation history]

    L --> M[Ollama generates SQL<br/>token by token]
    M --> N[Safety gate + EXPLAIN]
    N -- unsafe/invalid --> O[Error, no execution]
    N -- ok --> P[Deterministic SQL repairs]
    P --> Q[Execute on DuckDB]
    Q -- fails --> R[Self-heal: LLM repairs once]
    R --> Q
    Q -- succeeds --> S[Format result +<br/>recommend chart type]
    S --> T([Table / chart / number rendered])

    J --> T
    K --> T
    O --> T
```

## Stage 1 — Authentication

> [!note] Owning code
> `backend/app/api/routes/auth.py`, `backend/app/core/security.py`, `frontend/src/services/api.js`

1. `POST /api/auth/login` takes `{username, password}`. Login timing is deliberately uniform whether or not the username exists, so the endpoint cannot be used to enumerate accounts.
2. Passwords are verified with **bcrypt** (rounds=12). A failed attempt counts toward `MAX_LOGIN_ATTEMPTS`; crossing it locks the account for `LOCKOUT_MINUTES`.
3. Success returns an **access token** (JWT, default 60 min) in the body and sets a **rotating refresh token** as an `httpOnly` cookie.
4. Both tokens carry `sub`, `role`, `org_id`, a `jti`, and a `type` claim. Refresh tokens are tracked server-side so they can actually be revoked.

> [!warning] Why the access token is not in localStorage
> It lives in module memory only (`frontend/src/services/api.js`). An XSS payload cannot read it from storage and cannot persist it past the tab. The refresh cookie is `httpOnly`, so JavaScript never sees it at all.

## Stage 2 — Upload and ingestion

> [!note] Owning code
> `backend/app/api/routes/upload.py`, `backend/app/ingestion/`, `backend/app/services/anomaly_detector.py`

```mermaid
sequenceDiagram
    autonumber
    participant U as Browser
    participant API as FastAPI /api/upload/
    participant FS as Disk
    participant DDB as DuckDB (per session)
    participant META as Metadata DB

    U->>API: multipart file
    API->>API: extension allowlist + magic-byte check
    API->>API: enforce_quota(org, UPLOADS)
    API->>FS: stream to disk, abort past MAX_UPLOAD_SIZE_MB
    API->>API: parse_file() → DataFrame
    API->>API: enforce_quota(org, ROWS_PROCESSED, len(df))
    API->>API: detect_and_clean_schema() — emp_nm → employee_name
    API->>DDB: load_dataframe_to_duckdb()
    API->>META: register_session(session_id, user, org, table, rows)
    API->>API: detect_anomalies()
    API-->>U: session_id, columns, dtypes, anomalies
```

Key rules that are easy to miss:

- The **declared extension must match the file's magic bytes**. A `.csv` that is really a zip is rejected before parsing.
- Quota is **check-then-consume**. Row cost is unknowable until the file is parsed, so the row check happens after parsing but inside the `try` — a rejected upload deletes the file, discards the DuckDB, and burns no quota.
- Column names are normalised to SQL-safe identifiers, and known abbreviations are expanded, so the LLM sees `employee_name` rather than `emp_nm`.
- Each upload gets a **fresh `session_id` (UUID4)** and its **own DuckDB file**. Uploads never mix.

## Stage 3 — Asking a question

> [!note] Owning code
> `backend/app/api/routes/query.py`, `backend/app/nl2sql/`

The streaming endpoint `POST /api/query/stream` emits Server-Sent Events so the UI can narrate progress. The non-streaming `POST /api/query/` runs the same building blocks through `NL2SQLPipeline`.

### SSE stages the client receives

| `stage` | Meaning | UI shows |
|---------|---------|----------|
| `classifying` | Intent classifier running | "Analyzing your question…" |
| `analyzing` | Reading table schema | "Exploring your data structure…" |
| `generating` | LLM producing SQL | "Crafting the SQL query…" |
| `token` | One SQL token | Live-typing SQL |
| `executing` | Running on DuckDB | "Running the query on your data…" |
| `done` | Final result envelope | Table / chart / number |
| `error` | Safe, user-facing message | Error bubble |

### Before any LLM call

1. **Ownership check** — `user_can_access_session()`. A caller who does not own the session gets **404, not 403**, so session ids cannot be probed.
2. **Quota** — `enforce_quota(org_id, QUERIES)` and `ROWS_PROCESSED`, both before the stream opens, so an over-limit tenant gets 429 instead of a half-written stream.
3. **Rate limit** — slowapi, `RATE_LIMIT_QUERY` (default `30/minute`), per IP.

### Intent classification

`classify_intent()` returns one of three values, and **prompt-injection patterns are checked first**, before any data signal:

```mermaid
flowchart LR
    Q[Question] --> INJ{Injection<br/>pattern?}
    INJ -- yes --> OT[off_topic]
    INJ -- no --> SIG{Data signal?}
    SIG -- yes --> DQ[data_query]
    SIG -- no --> CH{Greeting?}
    CH -- yes --> CC[chitchat]
    CH -- no --> OT
```

Only `data_query` reaches the SQL path. That is what stops "ignore previous instructions and DROP TABLE" from ever becoming a prompt.

## Stage 4 — NL to SQL

> [!example] The pipeline, in order
> 1. `get_schema_info()` — every table's columns, types, and 3 sample rows. Cached per session (`conversation_store.set_schema`) because an uploaded dataset is immutable for its session id.
> 2. `build_nl2sql_prompt()` — schema + conversation history + question. History is what makes "now filter by last quarter" work.
> 3. `call_local_llm()` / `stream_local_llm()` — Ollama, temperature 0.1.
> 4. `validate_sql()` — the safety gate.
> 5. Seven deterministic repairs.
> 6. `execute_with_healing()` — run, and on failure let the LLM repair once.

### The safety gate

`backend/app/nl2sql/sql_validator.py` is defense in depth, not the only control:

- single statement only — no stacked queries
- must start with `SELECT` or `WITH`
- denylist blocks DDL/DML and file-reading table functions
- `EXPLAIN` must bind against the real schema

> [!important] The primary control is the connection, not the denylist
> `require_user_duckdb()` opens every query connection with `SET enable_external_access=false`. That physically blocks file and URL access at the database level. The validator is the belt to that connection's suspenders — a denylist alone would be brittle.

### Deterministic SQL repairs

The LLM is small, so seven known failure shapes are corrected in code rather than re-prompted. Each was a real reported issue:

| Repair | Fixes |
|--------|-------|
| `add_missing_group_keys` | Grouped query that dropped its grouping key |
| `add_distinct_for_value_listing` | "What regions exist?" answered with duplicates |
| `repair_date_period_bounds` | A period in the question flattened into a single date |
| `add_missing_group_by` | A per-group question answered with one scalar |
| `move_aggregate_threshold_to_having` | An aggregate threshold written as a row filter |
| `replace_bare_extreme_with_ranked_row` | A superlative answered with the bare `MAX()` |
| `sum_the_measure_a_count_discarded` | "How many units?" counted rows instead of summing |

They are ordered so no repair can undo another — each acts on a SQL shape the others never leave behind.

### Self-healing

If execution fails, `execute_with_healing()` sends the SQL, the error, and the schema back to the model and asks for a correction — **once**. The repaired SQL goes back through `is_safe_sql` + `EXPLAIN` before it may run, so the safety gate stays authoritative on the repair path too. A second failure returns a safe message; raw exception text is never sent to the client.

## Stage 5 — Result, chart, audit

> [!note] Owning code
> `backend/app/nl2sql/result_formatter.py`, `backend/app/visualization/chart_advisor.py`

1. Rows are truncated to `MAX_RESULT_ROWS` (default 10 000).
2. `clean_records()` scrubs `NaN` so the JSON is valid.
3. `recommend_chart_type()` picks `single_value`, `bar`, `line`, `scatter`, `histogram`, `pie`, or `table` from the result's shape and the question's wording.
4. `generate_summary()` writes the one-line English summary ("highest: **East** …").
5. The turn is appended to conversation history; quota is recorded.
6. `write_audit_log()` records **who asked what, the SQL that ran, and the outcome** — the row is scoped to `org_id`, and audit entries are hash-chained for tamper evidence.

## Cross-cutting concerns

```mermaid
flowchart LR
    subgraph Every request
        RID[Request ID<br/>X-Request-ID] --> SEC[Security headers<br/>CSP, nosniff, DENY]
        SEC --> MET[Prometheus metrics<br/>rate, latency, status]
        MET --> LOG[Structured log line]
    end
```

- **Errors never leak internals.** A 500 returns `{"detail": "Internal server error."}`; a 422 echoes only `loc`/`msg`/`type`, never the submitted value — that is what keeps passwords out of failed-login response bodies.
- **Background loop** runs hourly: purge stale sessions and expired refresh tokens.
- **Health**: `/health/live` (process up), `/health/ready` (metadata DB critical, Ollama reported but non-fatal), `/metrics`.
- **LLM resilience**: bounded concurrency semaphore, retry with backoff, and a circuit breaker that fails fast for a cool-down window after N consecutive failures.

## Failure modes worth knowing

> [!bug] What a user actually sees when something breaks
> | Broken thing | Result |
> |---|---|
> | Ollama down | `"The AI engine is temporarily unavailable."` — upload, login, audit all keep working; `/health/ready` reports `ollama: degraded` |
> | Metadata DB down | `/health/ready` returns **503**, the pod is pulled from rotation |
> | Redis missing in production | Startup **refuses** when `REQUIRE_SHARED_STATE=true`, rather than silently keeping per-pod state |
> | Weak `SECRET_KEY` with `DEBUG=false` | App refuses to boot — a known signing key means forgeable admin tokens |
