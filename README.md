# DataWhisper — Private AI Data Assistant

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
| Auth | JWT (PyJWT), bcrypt (password hashing) |
| Security | Account lockout, security headers, magic-byte file validation |
| Streaming | Server-Sent Events (SSE) |
| PDF Export | ReportLab |
| HTTPS | mkcert (local trusted certificates) |

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
│   ├── public/
│   │   ├── index.html                 # PWA meta tags
│   │   └── manifest.json              # PWA manifest (dark theme)
│   └── src/
│       ├── components/
│       │   ├── Auth/Login.js           # Login page
│       │   ├── Chat/ChatWindow.js      # Chat interface + SSE streaming
│       │   ├── Dashboard/              # Sidebar, Dashboard, AuditLogs
│       │   ├── Upload/FileUpload.js    # Drag-drop upload
│       │   └── Visualization/ResultView.js  # Tables, charts, numbers
│       └── services/api.js             # Axios + fetch API client
└── sample_data/
    ├── sales_data.csv
    └── employees.csv
```

---

## Getting Started

### Prerequisites

- Python 3.10+
- Node.js 18+
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

```env
SECRET_KEY=your-random-secret-key-here
ADMIN_PASSWORD=Admin@2024
MANAGER_PASSWORD=Manager@2024
OLLAMA_BASE_URL=http://localhost:11434
LLM_MODEL=llama3.2:3b
MAX_UPLOAD_SIZE_MB=500
MAX_LOGIN_ATTEMPTS=5
LOCKOUT_MINUTES=15
ALLOWED_ORIGINS=*
DEBUG=false
```

> **Important:** Change `SECRET_KEY`, `ADMIN_PASSWORD`, and `MANAGER_PASSWORD` before first run.
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

**Step 3 — Update `frontend/src/services/api.js`:**
```js
// Change http to https on line 5
const API_BASE = `https://${API_HOST}:8000/api`;
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
| POST | `/api/auth/login` | Public | Login, returns JWT |
| POST | `/api/upload/` | Required | Upload data file |
| POST | `/api/query/` | Required | Ask NL question |
| POST | `/api/query/stream` | Required | Ask with SSE streaming |
| GET | `/api/audit/logs` | Admin only | Get audit trail |
| GET | `/api/export/pdf/{session_id}` | Required | Export session as PDF |
| GET | `/health` | Public | Health check |

---

## Security

| Control | Implementation |
|---------|---------------|
| Password hashing | bcrypt (rounds=12) |
| Authentication | JWT Bearer tokens (HS256) |
| Account lockout | 5 failed attempts → 15 min lock |
| Route protection | FastAPI `Depends(get_current_user)` on all routes |
| Admin-only routes | `Depends(require_admin)` on audit logs |
| SQL injection | Only SELECT allowed, dangerous commands blocked |
| File validation | Magic-byte check (content must match extension) |
| Security headers | X-Content-Type-Options, X-Frame-Options, X-XSS-Protection, Referrer-Policy |
| CORS | Configurable via `ALLOWED_ORIGINS` in `.env` |
| Data privacy | All data stays local — no external API calls |

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
