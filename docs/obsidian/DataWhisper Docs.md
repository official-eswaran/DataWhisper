---
title: DataWhisper Docs
aliases:
  - DataWhisper Home
  - Docs MOC
tags:
  - datawhisper
  - moc
type: index
updated: 2026-08-19
---

# DataWhisper Docs

Map of content for the **DataWhisper** codebase — a private, offline NL-to-SQL data assistant. Every note here is written against the code in this repo, not against an idealised design.

> [!abstract] What DataWhisper does
> A user uploads a spreadsheet, asks a question in plain English, and gets back a table, a chart, or a single number. The language model runs locally via **Ollama**, the data lives in a per-session **DuckDB** file, and nothing leaves the machine.

## The four notes

| # | Note | Answers |
|---|------|---------|
| 1 | [[01 Complete System Workflow]] | What happens end to end, from login to rendered answer |
| 2 | [[02 Frontend Application Workflow]] | How the React app boots, holds tokens, streams, and renders |
| 3 | [[03 System Architecture Flow]] | What the components are, and how a request moves through them |
| 4 | [[04 Data Flow Diagram]] | Where data comes from, where it is stored, and where it stops |

## Visual boards

- `System Architecture.canvas` — the deployment topology as a pan/zoom board
- `Data Flow.canvas` — the upload → query → answer data path as a board

Open either from the file explorer; they are [JSON Canvas](https://jsoncanvas.org/) files that Obsidian renders natively.

## Stack at a glance

> [!info] Runtime pieces
> - **Frontend** — React 19 + Vite, Recharts, react-router, axios
> - **Backend** — FastAPI on Python 3.12, Gunicorn/Uvicorn in production
> - **Data** — DuckDB per upload session; SQLite or Postgres for metadata
> - **LLM** — Ollama serving `llama3.2:3b` on `localhost:11434`
> - **Shared state** — Redis for conversation history, LLM cache, and rate limits
> - **Delivery** — Docker Compose locally, Kubernetes manifests in `deploy/k8s`

## Reading order

If you are new to the codebase, read [[03 System Architecture Flow]] first to learn the vocabulary, then [[01 Complete System Workflow]] for the sequence, then [[04 Data Flow Diagram]] for the storage rules. [[02 Frontend Application Workflow]] stands on its own if you only touch the UI.

## Related repo docs

These are plain Markdown, outside this folder, and stay authoritative for operations:

- `docs/DEPLOYMENT.md` — Kubernetes and Terraform deployment
- `docs/DISASTER_RECOVERY.md` — backup/restore drills, RPO and RTO
- `docs/BILLING.md` — Stripe integration and plan mechanics
- `docs/GO_LIVE_CHECKLIST.md` — production readiness gates
