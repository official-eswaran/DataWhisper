---
title: Data Flow Diagram
aliases:
  - DFD
  - Data Flow
tags:
  - datawhisper
  - data-flow
  - security
  - privacy
type: data-flow
updated: 2026-08-19
---

# Data Flow Diagram

Where data enters, where it is stored, who may read it, and where it is destroyed. DataWhisper's central claim is that **nothing leaves the machine** — this note is the audit of that claim.

Visual board: `Data Flow.canvas`
Related: [[03 System Architecture Flow]] · [[01 Complete System Workflow]]

## Level 0 — context diagram

```mermaid
flowchart LR
    U(("Business user"))
    A(("Admin / Owner"))
    S["DataWhisper"]
    ST[["Stripe<br/>optional, billing only"]]
    SM[["SMTP<br/>optional, verification mail"]]

    U -->|"data files, questions"| S
    S -->|"tables, charts, PDF"| U
    A -->|"user admin, plan changes"| S
    S -->|"audit logs, usage"| A
    S <-->|"plan + payment metadata only"| ST
    S -->|"verification link only"| SM
```

> [!important] The only two outbound paths
> Stripe and SMTP. **Neither ever carries uploaded data or question text** — Stripe sees plan and customer metadata, SMTP sees an address and a token. With `STRIPE_SECRET_KEY` empty the billing routes return 503 and the integration is inert. The LLM is local, so prompts never cross the network boundary.

## Level 1 — processes and stores

```mermaid
flowchart TB
    U(("User"))

    P1["1.0 Authenticate"]
    P2["2.0 Ingest dataset"]
    P3["3.0 Answer question"]
    P4["4.0 Report & export"]

    D1[("D1 Metadata DB<br/>orgs, users, sessions,<br/>audit, refresh tokens, usage")]
    D2[("D2 Upload staging<br/>UPLOAD_DIR")]
    D3[("D3 DuckDB<br/>one file per session")]
    D4[("D4 Redis<br/>history, schema cache,<br/>LLM cache, rate limits")]
    D5[("D5 Object storage<br/>S3, production only")]

    LLM["Ollama<br/>local model"]

    U -->|credentials| P1
    P1 -->|"access token + refresh cookie"| U
    P1 <--> D1

    U -->|file| P2
    P2 --> D2
    P2 -->|"cleaned DataFrame"| D3
    P2 -->|"session row"| D1
    P2 -->|"anomalies, schema"| U
    D3 <-->|"persist / materialise"| D5

    U -->|question| P3
    P3 -->|"schema + history + question"| LLM
    LLM -->|SQL| P3
    P3 -->|"SELECT only"| D3
    P3 <--> D4
    P3 -->|"who asked what"| D1
    P3 -->|"rows, chart type, summary"| U

    P4 --> D1
    P4 --> D3
    P4 -->|"PDF report"| U
```

## The five stores

| Store | Contains | Lifetime | Scope |
|---|---|---|---|
| **D1** Metadata DB | orgs, users, sessions, audit log, refresh tokens, usage counters | Permanent until deleted | Per org |
| **D2** Upload staging | the raw uploaded file | Deleted immediately on any ingestion failure | Per session |
| **D3** DuckDB | the parsed dataset, one file per `session_id` | `SESSION_TTL_HOURS`, default 24 | Per session |
| **D4** Redis | conversation history, cached schema, cached LLM responses, rate-limit counters | `CONVERSATION_TTL_MINUTES` (120) / cache TTL | Per session, per IP |
| **D5** Object storage | the DuckDB file, when `dataset_storage` is S3 | Same as the session | Per session |

> [!note] Why DuckDB is per session and not one shared database
> Isolation is structural rather than enforced by query text. A session's SQL runs against a connection that can only see that session's file, so a prompt-injected query has nothing else to reach.

## Transformations along the ingestion path

```mermaid
flowchart LR
    F["Raw file<br/>csv · xlsx · xls · json · parquet"] --> E{"Extension<br/>allowed?"}
    E -- no --> X1["400 rejected"]
    E -- yes --> M{"Magic bytes<br/>match extension?"}
    M -- no --> X2["400 rejected"]
    M -- yes --> SZ{"Under<br/>MAX_UPLOAD_SIZE_MB?"}
    SZ -- no --> X3["413 + file deleted"]
    SZ -- yes --> PR["parse_file → DataFrame"]
    PR --> Q{"Row quota<br/>available?"}
    Q -- no --> X4["429 + file and DuckDB discarded"]
    Q -- yes --> CL["detect_and_clean_schema<br/>emp_nm → employee_name"]
    CL --> LD["load_dataframe_to_duckdb"]
    LD --> AN["detect_anomalies<br/>nulls · outliers · sudden changes"]
    AN --> RS["session_id + schema + anomalies → user"]
```

Every rejection path deletes what it created. A failed upload leaves no file, no DuckDB, and no consumed quota.

## What reaches the model

> [!question] Does my data go into the prompt?
> Some of it does — and it stays on the machine. `get_schema_info()` sends **column names, column types, and three sample rows per table**. Sample rows are what let a small model produce correct SQL; without them it guesses at value formats. The prompt travels to `OLLAMA_BASE_URL`, which is local, so this never crosses a network boundary you do not control.

```mermaid
flowchart LR
    subgraph prompt["Prompt sent to Ollama"]
        SC["Table names, columns, types"]
        SR["3 sample rows per table"]
        HI["Conversation history<br/>question + SQL pairs"]
        QU["The current question"]
    end
    prompt --> O["Ollama, localhost"]
    O --> SQL["Generated SQL"]
    SQL --> G{"Safety gate"}
    G -- rejected --> ERR["Error, nothing runs"]
    G -- accepted --> EX["Executes on the session's DuckDB"]
```

Full results are **never** sent to the model. Only the schema, the samples, and the question shape the prompt.

## Trust boundaries

```mermaid
flowchart TB
    subgraph B1["Untrusted — the browser"]
        UI["React app"]
    end
    subgraph B2["Semi-trusted — LLM output"]
        GEN["Generated SQL"]
    end
    subgraph B3["Trusted — server process"]
        VAL["Validator + repairs"]
        CONN["DuckDB connection<br/>enable_external_access=false"]
    end

    UI -->|"JWT verified every request"| VAL
    GEN -->|"single statement · SELECT/WITH only<br/>denylist · EXPLAIN must bind"| VAL
    VAL --> CONN
```

> [!danger] Treat generated SQL as hostile input
> It is written by a model that just read user-controlled text. Two independent controls apply:
> 1. **`enable_external_access=false`** on every query connection — file and URL access is blocked by the database itself. This is the primary control.
> 2. **The validator** — one statement, `SELECT`/`WITH` only, denylist for DDL/DML and file-reading table functions, and a binding `EXPLAIN`.
>
> The self-heal path re-runs both. A denylist alone would be brittle; the connection-level lockdown is what makes the guarantee hold.

## Prompt injection

Injection patterns are matched **before any data signal**, in `classify_intent()`. A question containing "ignore previous instructions" is classified `off_topic` and never reaches prompt construction — the check is not a filter applied to the model's output, it is a gate in front of the model.

## Retention and deletion

```mermaid
flowchart TD
    UP["Upload"] --> LIVE["Session live"]
    LIVE -->|"SESSION_TTL_HOURS elapses"| CLEAN["Hourly cleanup loop"]
    LIVE -->|"user deletes account"| GDPR["GDPR delete"]
    LIVE -->|"org deleted"| GDPR
    CLEAN --> GONE(["DuckDB + staging file removed"])
    GDPR --> GONE
    GDPR --> PURGE(["User rows, sessions, tokens purged"])
```

- A background loop runs **hourly**: `cleanup_stale_sessions()` and `purge_expired_refresh_tokens()`.
- Conversation history expires after `CONVERSATION_TTL_MINUTES` independently of the dataset.
- `GET /api/me/export` returns a user's data; `DELETE /api/me` and `DELETE /api/org` remove it. These are the GDPR routes in `backend/app/api/routes/gdpr.py`.

## Audit trail

Every query writes one row: **user, org, session, question, the SQL that ran, the summary, the outcome**. Entries are **hash-chained** — each row's hash covers the previous one, and a checkpoint signature covers the run — so a deleted or edited row breaks the chain and is detectable. Only admins and owners can read the log, and only within their own org.

## Privacy claim, restated precisely

> [!success] What "100% offline" means here
> - The model runs locally; **prompts and results never leave the host**.
> - Uploaded data reaches disk, DuckDB, and — in production only — your own object storage.
> - The only outbound calls are Stripe and SMTP, both optional, and neither carries dataset content or question text.
> - With `STRIPE_SECRET_KEY` and the mailer unset, the process makes **no outbound connections at all** beyond `OLLAMA_BASE_URL`.
