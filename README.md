# DataWhisper — Private AI Data Assistant

[![CI](https://github.com/official-eswaran/DataWhisper/actions/workflows/ci.yml/badge.svg)](https://github.com/official-eswaran/DataWhisper/actions/workflows/ci.yml)
[![E2E](https://github.com/official-eswaran/DataWhisper/actions/workflows/e2e.yml/badge.svg)](https://github.com/official-eswaran/DataWhisper/actions/workflows/e2e.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-22D3EE.svg?style=flat-square)](LICENSE)
![Python](https://img.shields.io/badge/Python-3.12-3776AB?style=flat-square&logo=python&logoColor=white)
![React](https://img.shields.io/badge/React-19-61DAFB?style=flat-square&logo=react&logoColor=black)
![DuckDB](https://img.shields.io/badge/DuckDB-FFF000?style=flat-square&logo=duckdb&logoColor=black)
![Ollama](https://img.shields.io/badge/Ollama-local_LLM-000000?style=flat-square&logo=ollama&logoColor=white)
![Kubernetes](https://img.shields.io/badge/Kubernetes-ready-326CE5?style=flat-square&logo=kubernetes&logoColor=white)

> Upload your data. Ask in plain English. Get answers as tables, charts & numbers — **100% offline, zero data leakage.**

DataWhisper is a full-stack application that lets CEOs, managers, and business users query their data using natural language. It converts plain English questions into SQL, executes them on your private data, and returns results with auto-visualizations — all running locally on your machine using Ollama.

---

## Why DataWhisper?

Most AI tools (ChatGPT, Gemini) require uploading sensitive business data to external servers. DataWhisper solves this by running everything locally:

- **No data leaves your machine** — Ollama LLM runs 100% offline
- **No API keys needed** — no OpenAI, no cloud dependency
- **CEO-friendly** — ask questions in plain English, get instant answers
- **Mobile-ready** — access from any phone on the same WiFi, no app install needed
- **Production secure** — bcrypt passwords, JWT auth, account lockout, security headers
- **Handles messy data** — auto-cleans column names, detects types
- **Smart enough to refuse** — blocks off-topic questions, only answers data queries

---

## Features

| Feature | Description |
|---------|-------------|
| **NL-to-SQL Pipeline** | Converts natural language to SQL using local LLM |
| **Live Streaming** | Real-time stage updates while AI thinks (classifying → generating → executing) |
| **Multi-format Upload** | Supports CSV, Excel (.xlsx/.xls), JSON, Parquet |
| **Auto Schema Detection** | Cleans messy headers (`emp_nm` → `employee_name`) |
| **Self-Healing SQL** | If a query fails, LLM auto-corrects and retries |
| **Conversational Memory** | Follow-up questions like "now filter by last quarter" |
| **Auto Visualization** | Detects if result is best as table, chart, or number |
| **Anomaly Detection** | Flags outliers, missing data, and sudden changes on upload |
| **Intent Classifier** | Separates data queries vs greetings vs off-topic questions |
| **Audit Trail** | Logs every query — who asked what and when (admin only) |
| **PDF Export** | One-click session report for board meetings |
| **Role-Based Access** | Admin sees audit logs, department users see their data |
| **HTTPS Support** | mkcert-based SSL for secure LAN access on all devices |
| **PWA Ready** | Install on phone home screen like a native app |
| **100% Offline** | Ollama + DuckDB — nothing touches the internet |

---

## Architecture

```
┌─────────────────────────────────────────────────┐
│                   FRONTEND (React)               │
│  ┌───────────┐ ┌──────────┐ ┌────────────────┐  │
│  │ Chat UI   │ │ Data     │ │ Visualization  │  │
│  │ (NL Input)│ │ Upload   │ │ (Charts/Tables)│  │
│  └─────┬─────┘ └────┬─────┘ └───────┬────────┘  │
└────────┼─────────────┼───────────────┼───────────┘
         │             │               │
         ▼             ▼               ▼
┌─────────────────────────────────────────────────┐
│              BACKEND (FastAPI)                    │
│                                                   │
│  ┌─────────────────────────────────────────────┐ │
│  │          NL-to-SQL Pipeline                  │ │
│  │                                               │ │
│  │  User Query → Intent Classifier              │ │
│  │      → Schema Mapper → Prompt Builder        │ │
│  │      → LLM (Ollama) → SQL Generation        │ │
│  │      → SQL Validator → Execute on DuckDB     │ │
│  │      → Result Formatter → SSE Response       │ │
│  └─────────────────────────────────────────────┘ │
│                                                   │
│  ┌──────────────┐  ┌─────────────────────────┐   │
│  │ Anomaly      │  │ Conversation Memory     │   │
│  │ Detection    │  │ (Context Manager)       │   │
│  └──────────────┘  └─────────────────────────┘   │
└──────────────────────┬──────────────────────────┘
                       │
         ┌─────────────┼─────────────┐
         ▼             ▼             ▼
   ┌──────────┐  ┌──────────┐  ┌──────────┐
   │ DuckDB   │  │ Ollama   │  │ SQLite   │
   │ (User    │  │ (Local   │  │ (Audit + │
   │  Data)   │  │  LLM)    │  │  Users)  │
   └──────────┘  └──────────┘  └──────────┘
```

---

## Tech Stack

| Layer | Technology |
|-------|------------|
| Frontend | React 19, Recharts, React Dropzone, Axios |
| Backend | Python 3.12, FastAPI, Pydantic |
| Database | DuckDB (user data), SQLite (audit + users) |
| AI/LLM | Ollama (local), Llama 3.2 3B |
| Data Processing | Pandas |
| Auth | Access + rotating refresh JWT (PyJWT), bcrypt (rounds=12) |
| Security | Session-ownership authz, rate limiting (slowapi), config fail-fast, DuckDB external-access lockdown |
| Streaming | Server-Sent Events (SSE) |
| PDF Export | ReportLab |
| Deploy | Docker + docker-compose, nginx, Gunicorn/Uvicorn workers, GitHub Actions CI |
| Testing | pytest (unit + integration), ruff, React Testing Library |
| HTTPS | mkcert (local) / reverse-proxy TLS (production) |

---

## Project Structure

```
DataWhisper/
├── backend/
│   ├── app/
│   │   ├── main.py                    # FastAPI entry point + middlewares
│   │   ├── core/
│   │   │   ├── config.py              # App settings (env-driven)
│   │   │   ├── database.py            # DuckDB + SQLite + users
│   │   │   └── security.py            # bcrypt hashing + JWT dependency
│   │   ├── api/routes/
│   │   │   ├── upload.py              # File upload (auth required)
│   │   │   ├── query.py               # NL question + SSE streaming
│   │   │   ├── auth.py                # Login with lockout
│   │   │   ├── audit.py               # Audit logs (admin only)
│   │   │   └── export.py              # PDF report generation
│   │   ├── ingestion/
│   │   │   ├── file_parser.py         # CSV/Excel/JSON/Parquet parser
│   │   │   └── schema_detector.py     # Auto-clean column names
│   │   ├── nl2sql/
│   │   │   ├── pipeline.py            # Full NL → SQL → Result pipeline
│   │   │   ├── intent_classifier.py   # Data query vs chitchat vs off-topic
│   │   │   ├── prompt_builder.py      # LLM prompt construction
│   │   │   ├── sql_validator.py       # SQL safety & syntax check
│   │   │   └── llm_client.py          # Ollama API client
│   │   └── services/
│   │       └── anomaly_detector.py    # Auto-detect data anomalies
│   ├── .env                           # Environment config (not committed)
│   └── requirements.txt
├── frontend/
│   ├── index.html                     # Vite entry point + PWA meta tags
│   ├── vite.config.js                 # Build config (outputs to build/)
│   ├── public/
│   │   └── manifest.json              # PWA manifest (dark theme)
│   └── src/
│       ├── components/
│       │   ├── Auth/                   # Login, Signup
│       │   ├── Chat/ChatWindow.jsx     # Chat interface + SSE streaming
│       │   ├── Dashboard/              # Sidebar, Dashboard, AuditLogs,
│       │   │                           #   AdminConsole, AccountSettings
│       │   ├── Upload/FileUpload.jsx   # Drag-drop upload
│       │   └── Visualization/ResultView.jsx  # Tables, charts (lazy-loaded)
│       └── services/api.js             # Axios + fetch API client
└── sample_data/
    ├── sales_data.csv
    └── employees.csv
```

---

## Getting Started

### Option 0 — Docker (recommended)

The whole stack (Ollama + backend + frontend) runs with one command:

```bash
cp backend/.env.example backend/.env   # set a real SECRET_KEY
docker compose up -d --build
docker compose exec ollama ollama pull llama3.2:3b
# open http://localhost:8080
```

The backend runs under Gunicorn/Uvicorn (not the dev `--reload` server), the
frontend is a static production build served by nginx (which also proxies
`/api` and streams SSE), and data persists in named volumes.

> **Scaling:** the compose stack includes Redis, so the conversation store and
> rate limiter share state across workers — `WEB_CONCURRENCY` can safely be >1
> and the backend can run as multiple replicas. Set `REDIS_URL=""` to fall back
> to single-worker in-process state.

**Production deployment (Kubernetes):** see [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md)
— manifests in [`deploy/k8s`](deploy/k8s) (2+ replica backend, HPA 2–10, PDB,
health-gated rollout, migration Job, ingress with SSE support) and managed
infra (RDS/ElastiCache/S3) in [`deploy/terraform`](deploy/terraform).

**Backups & disaster recovery:** [`docs/DISASTER_RECOVERY.md`](docs/DISASTER_RECOVERY.md)
with `scripts/backup.sh` / `scripts/restore.sh` (RPO ≤ 15 min, RTO ≤ 1 hr).

**Operational endpoints:**

| Endpoint | Purpose |
|---|---|
| `GET /health/live` | Liveness probe (process up) — for k8s `livenessProbe` |
| `GET /health/ready` | Readiness probe (DB up; Ollama reported) — for k8s `readinessProbe`, `503` when not ready |
| `GET /metrics` | Prometheus metrics (request rate/latency/status, LLM calls/failures) |

---

### Prerequisites (manual setup)

- Python 3.10+ (3.12 recommended)
- Node.js 18+ (20 recommended)
- Ollama

### 1. Install Ollama & pull model

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull llama3.2:3b
```

### 2. Clone the repo

```bash
git clone https://github.com/official-eswaran/DataWhisper.git
cd DataWhisper
```

### 3. Configure backend

```bash
cd backend
pip install -r requirements.txt
```

Create a `.env` file:

```bash
cp .env.example .env    # then edit .env
```

The full, documented list of settings lives in [`backend/.env.example`](backend/.env.example). At minimum set:

```env
DEBUG=false
SECRET_KEY=            # REQUIRED — see command below
ADMIN_PASSWORD=change-me-admin
MANAGER_PASSWORD=change-me-manager
OLLAMA_BASE_URL=http://localhost:11434
ALLOWED_ORIGINS=https://data.yourcompany.com
```

> **The app refuses to start in production (`DEBUG=false`) if `SECRET_KEY` is
> missing/default/weak or `ALLOWED_ORIGINS` is `*`.** This is intentional — a
> known signing key means anyone can forge admin tokens.
>
> Generate a secure key with: `python3 -c "import secrets; print(secrets.token_hex(32))"`

---

## Running the App

### Option A — HTTP (Quick start)

**Terminal 1 — Ollama:**
```bash
ollama serve
```

**Terminal 2 — Backend:**
```bash
cd backend
python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

**Terminal 3 — Frontend:**
```bash
cd frontend
npm install   # first time only
npm start
```

Open: **http://localhost:3000**
Mobile (same WiFi): **http://YOUR_LOCAL_IP:3000**

Find your local IP:
```bash
hostname -I | awk '{print $1}'
```

---

### Option B — HTTPS (Recommended for mobile)

HTTPS enables secure access on phones without browser warnings.

**Step 1 — Install mkcert:**
```bash
sudo apt install mkcert libnss3-tools -y
mkcert -install
```

**Step 2 — Generate certificates:**
```bash
cd backend
mkcert localhost YOUR_LOCAL_IP 127.0.0.1
# Example: mkcert localhost 192.168.1.241 127.0.0.1
# Creates: localhost+2.pem and localhost+2-key.pem
```

**Step 3 — (no code edit needed)** The frontend calls the API at the same origin
(`/api`) by default, so it inherits HTTPS automatically. For a split-origin dev
setup, point it at the backend explicitly:
```bash
# frontend/.env.local
VITE_API_URL=https://YOUR_LOCAL_IP:8000/api
```

**Step 4 — Start backend with HTTPS:**
```bash
cd backend
python3 -m uvicorn app.main:app \
  --host 0.0.0.0 \
  --port 8000 \
  --ssl-certfile localhost+2.pem \
  --ssl-keyfile localhost+2-key.pem \
  --reload
```

**Step 5 — Start frontend with HTTPS:**
```bash
cd frontend
HTTPS=true \
SSL_CRT_FILE=../backend/localhost+2.pem \
SSL_KEY_FILE=../backend/localhost+2-key.pem \
npm start
```

Open: **https://localhost:3000**
Mobile: **https://YOUR_LOCAL_IP:3000**

---

### Trust certificate on mobile

**Android:**
1. Run `mkcert -CAROOT` to find the CA folder
2. Copy `rootCA.pem` to your phone
3. Settings → Security → Install certificate → CA Certificate

**iPhone:**
1. AirDrop or email `rootCA.pem` to yourself
2. Tap the file → Settings → Profile Downloaded → Install
3. Settings → General → About → Certificate Trust Settings → Enable mkcert

---

### Install as PWA on Phone (Home Screen App)

**Android (Chrome):** Tap ⋮ menu → "Add to Home screen"
**iPhone (Safari):** Tap Share → "Add to Home Screen"

DataWhisper installs as a dark-themed app icon — works like a native app.

---

## Login Credentials

| Role | Username | Password | Access |
|------|----------|----------|--------|
| Admin (CEO) | `ceo` | `Admin@2024` | Full access + audit logs |
| Department | `manager` | `Manager@2024` | Upload + query only |

> Passwords are bcrypt-hashed and stored in SQLite. Change defaults in `.env` before first run.
> After 5 failed login attempts, account is locked for 15 minutes.

---

## Usage

### Upload Data
1. Login and click **Upload Data** in the sidebar
2. Drag & drop a CSV, Excel, JSON, or Parquet file
3. System auto-detects schema and flags anomalies

### Ask Questions
Navigate to **Ask Questions** and type in plain English:

```
"What is the total revenue?"                     → Single number
"Show revenue by region"                         → Bar/Pie chart
"Top 5 products by sales"                        → Table
"Revenue trend by month"                         → Line chart
"Orders where quantity > 10 and region is South" → Filtered table
```

While the AI thinks, you'll see live updates:
```
Analyzing your question...
Exploring your data structure...
Crafting the SQL query...
Running the query on your data...
```

### Follow-up Questions
```
User: "Show total revenue by category"
AI:   Electronics: 25.5L, Furniture: 4.6L

User: "Now break that down by region"
AI:   (Uses context from previous question)
```

### Off-topic Questions
```
User: "Who is Modi?"
AI:   "I can only answer questions about your uploaded data."

User: "Write me a poem"
AI:   "I can only answer questions about your uploaded data."
```

---

## API Endpoints

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| POST | `/api/auth/register` | Public | Self-service signup — creates an organization + owner user |
| POST | `/api/auth/login` | Public | Login, returns access + refresh token |
| POST | `/api/auth/refresh` | Public | Exchange a refresh token for a new pair (rotates) |
| POST | `/api/auth/logout` | Required | Revoke all of the caller's refresh tokens |
| GET | `/api/users/` | Admin/Owner | List users in the caller's organization |
| POST | `/api/users/` | Admin/Owner | Create a user in the organization |
| PATCH | `/api/users/{username}/status` | Admin/Owner | Activate / deactivate an org user |
| POST | `/api/upload/` | Required | Upload data file (bound to the uploading user) |
| POST | `/api/query/` | Required + owner | Ask NL question |
| POST | `/api/query/stream` | Required + owner | Ask with SSE streaming |
| GET | `/api/audit/logs?limit=&offset=` | Admin only | Get paginated audit trail (with per-entry hash) |
| GET | `/api/audit/verify` | Admin only | Verify the org's tamper-evident audit chain |
| GET | `/api/export/pdf/{session_id}` | Required + owner | Export session as PDF |
| GET | `/api/me/export` | Required | GDPR: export all of the caller's data (JSON) |
| DELETE | `/api/me` | Required | GDPR: delete the caller's account + datasets |
| DELETE | `/api/org` | Owner only | Delete the whole organization and all its data |
| GET | `/health` | Public | Health check |

> **Authorization:** query, stream, and export enforce session ownership — a user
> can only access sessions they uploaded (admins can access any). Requests for
> another user's `session_id` return `404` (no existence leak).

> **Rate limits:** login/refresh, query, and upload are IP rate-limited
> (configurable via `RATE_LIMIT_*` in `.env`).

### Multi-tenancy & database

DataWhisper is multi-tenant: every user, uploaded session, and audit record is
scoped to an **organization**. Signup (`/api/auth/register`) creates a new org
with an **owner**; owners/admins manage their org's users via `/api/users`.
Roles are `owner` > `admin` > `member`. Cross-org access is impossible — auth
checks require a matching `org_id`.

**Compliance & integrity:**
- **Tamper-evident audit** — each audit entry is hash-chained to the previous one
  (per org). `GET /api/audit/verify` recomputes the chain and detects any edit,
  reorder, insertion, or deletion.
- **GDPR** — `GET /api/me/export` (Article 15 access) returns everything held
  about a user; `DELETE /api/me` (Article 17 erasure) deletes their account and
  datasets; `DELETE /api/org` erases an entire organization. Stale sessions/data
  auto-expire after `SESSION_TTL_HOURS`.

The metadata store runs on **SQLAlchemy**, so it works on **SQLite** (dev,
default) or **Postgres** (production, via `DATABASE_URL`). Schema is managed by
**Alembic**:

```bash
alembic upgrade head      # apply migrations (the backend container runs this on start)
alembic revision --autogenerate -m "describe change"   # after editing models
```

---

## Security

| Control | Implementation |
|---------|---------------|
| Password hashing | bcrypt (rounds=12), SHA-256 pre-hash (no 72-byte truncation) |
| Authentication | Short-lived access JWT + revocable refresh token (rotating) |
| Token revocation | Refresh `jti` tracked in SQLite; `/logout` revokes all |
| Config safety | App refuses to start if `SECRET_KEY` is default/weak or CORS is `*` (production) |
| Account lockout | 5 failed attempts → 15 min lock |
| Login timing | Constant-time (dummy bcrypt on unknown user) — no username enumeration |
| Authorization | Session ownership enforced (IDOR-safe); admin-only audit logs |
| SQL injection | SELECT-only allowlist **and** DuckDB `enable_external_access=false` — generated SQL cannot read files/URLs |
| Rate limiting | Per-IP limits on login, query, upload (slowapi) |
| Upload limits | Size enforced while streaming to disk (no OOM); magic-byte check |
| Security headers | X-Content-Type-Options, X-Frame-Options, X-XSS-Protection, Referrer-Policy, CSP |
| CORS | Explicit origins in production (`ALLOWED_ORIGINS`); `*` rejected when `DEBUG=false` |
| Data privacy | All data stays local — no external API calls |
| Data lifecycle | Stale sessions + uploads auto-purged after `SESSION_TTL_HOURS` |

---

## Performance Benchmarks

| Dataset Size | Upload Time | Query Time |
|-------------|-------------|------------|
| 25 rows | ~100ms | ~4s |
| 100K rows | ~525ms | ~5-9s |
| 1M rows | ~2.9s | ~5s |

> Query time is dominated by LLM response (~3-5s). DuckDB executes SQL in milliseconds even on millions of rows.

---

## How NL-to-SQL Works

```
"Show total revenue by region where category is Electronics"
                    │
                    ▼
        ┌─── Intent Classifier ───┐
        │   data_query ✓          │
        └─────────┬───────────────┘
                  ▼
        ┌─── Prompt Builder ──────┐
        │   Schema + History +    │
        │   Question → Prompt     │
        └─────────┬───────────────┘
                  ▼
        ┌─── Ollama LLM ─────────┐
        │   Generates SQL query   │
        └─────────┬───────────────┘
                  ▼
        ┌─── SQL Validator ───────┐
        │   SELECT-only enforced  │
        │   Dangerous cmds blocked│
        └─────────┬───────────────┘
                  ▼
        ┌─── DuckDB Execute ──────┐
        │   Run query on data     │
        │   If fails → self-heal  │
        └─────────┬───────────────┘
                  ▼
        ┌─── Result Formatter ────┐
        │   Auto-detect: table,   │
        │   chart, or number      │
        └─────────────────────────┘
```

---

## Team

| Member | Responsibility |
|--------|---------------|
| Person A | Backend, NL-to-SQL pipeline, anomaly detection, Ollama integration |
| Person B | Frontend, chat UI, visualizations, upload, PDF export |

---

## License

This project is developed as a final year academic project.
