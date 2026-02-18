# DataWhisper — Private AI Data Assistant

> Upload your data. Ask in plain English. Get answers as tables, charts & numbers — **100% offline, zero data leakage.**

DataWhisper is a full-stack application that lets CEOs, managers, and business users query their data using natural language. It converts plain English questions into SQL, executes them on your private data, and returns results with auto-visualizations — all running locally on your machine using Ollama.

---

## Why DataWhisper?

Most AI tools (ChatGPT, Gemini) require uploading sensitive business data to external servers. DataWhisper solves this by running everything locally:

- **No data leaves your machine** — Ollama LLM runs 100% offline
- **No API keys needed** — no OpenAI, no cloud dependency
- **CEO-friendly** — ask questions in plain English, get instant answers
- **Handles messy data** — auto-cleans column names, detects types
- **Smart enough to refuse** — blocks off-topic questions, only answers data queries

---

## Features

| Feature | Description |
|---------|-------------|
| **NL-to-SQL Pipeline** | Converts natural language to SQL using local LLM |
| **Multi-format Upload** | Supports CSV, Excel (.xlsx/.xls), JSON, Parquet |
| **Auto Schema Detection** | Cleans messy headers (`emp_nm` → `employee_name`) |
| **Self-Healing SQL** | If a query fails, LLM auto-corrects and retries |
| **Conversational Memory** | Follow-up questions like "now filter by last quarter" |
| **Auto Visualization** | Detects if result is best as table, chart, or number |
| **Anomaly Detection** | Flags outliers, missing data, and sudden changes on upload |
| **Intent Classifier** | Separates data queries vs greetings vs off-topic questions |
| **Audit Trail** | Logs every query — who asked what and when |
| **PDF Export** | One-click session report for board meetings |
| **Role-Based Access** | CEO sees everything, managers see limited data |
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
│  │      → Result Formatter → Response           │ │
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
   │ (User    │  │ (Local   │  │ (Audit   │
   │  Data)   │  │  LLM)    │  │  Logs)   │
   └──────────┘  └──────────┘  └──────────┘
```

---

## Tech Stack

| Layer | Technology |
|-------|------------|
| Frontend | React, Recharts, React Dropzone, Axios |
| Backend | Python, FastAPI, Pydantic |
| Database | DuckDB (user data), SQLite (audit logs) |
| AI/LLM | Ollama (local), Llama 3.2 3B |
| Data Processing | Pandas, NumPy |
| Auth | JWT (PyJWT) |
| PDF Export | ReportLab |

---

## Project Structure

```
DataWhisper/
├── backend/
│   ├── app/
│   │   ├── main.py                    # FastAPI entry point
│   │   ├── core/
│   │   │   ├── config.py              # App settings
│   │   │   └── database.py            # DuckDB + SQLite connections
│   │   ├── api/routes/
│   │   │   ├── upload.py              # File upload endpoint
│   │   │   ├── query.py               # NL question endpoint
│   │   │   ├── auth.py                # JWT login
│   │   │   ├── audit.py               # Audit log retrieval
│   │   │   └── export.py              # PDF report generation
│   │   ├── ingestion/
│   │   │   ├── file_parser.py         # CSV/Excel/JSON parser
│   │   │   └── schema_detector.py     # Auto-clean column names
│   │   ├── nl2sql/
│   │   │   ├── pipeline.py            # Full NL → SQL → Result pipeline
│   │   │   ├── intent_classifier.py   # Data query vs chitchat vs off-topic
│   │   │   ├── prompt_builder.py      # LLM prompt construction
│   │   │   ├── sql_validator.py       # SQL safety & syntax check
│   │   │   └── llm_client.py          # Ollama API client
│   │   └── services/
│   │       └── anomaly_detector.py    # Auto-detect data anomalies
│   └── requirements.txt
├── frontend/
│   └── src/
│       ├── components/
│       │   ├── Auth/Login.js           # Login page
│       │   ├── Chat/ChatWindow.js      # Chat interface
│       │   ├── Dashboard/              # Sidebar, Dashboard, AuditLogs
│       │   ├── Upload/FileUpload.js    # Drag-drop upload
│       │   └── Visualization/ResultView.js  # Tables, charts, numbers
│       └── services/api.js             # Axios API client
└── sample_data/
    ├── sales_data.csv                  # 25 rows sample
    └── employees.csv                   # 20 rows sample
```

---

## Getting Started

### Prerequisites

- Python 3.10+
- Node.js 18+
- Ollama

### 1. Install Ollama

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull llama3.2:3b
```

### 2. Start Backend

```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Create .env file
echo 'SECRET_KEY=your-secret-key-change-in-production
OLLAMA_BASE_URL=http://localhost:11434
LLM_MODEL=llama3.2:3b' > .env

uvicorn app.main:app --reload --port 8000
```

### 3. Start Frontend

```bash
cd frontend
npm install
npm start
```

### 4. Open Browser

Go to **http://localhost:3000**

Login credentials:
| Role | Username | Password |
|------|----------|----------|
| CEO (Admin) | `ceo` | `admin123` |
| Manager | `manager` | `manager123` |

---

## Usage

### Upload Data
1. Click **Upload Data** in the sidebar
2. Drag & drop a CSV, Excel, or JSON file
3. System auto-detects schema and flags anomalies

### Ask Questions
Navigate to **Ask Questions** and type in plain English:

```
"What is the total revenue?"                    → Single number
"Show revenue by region"                        → Bar/Pie chart
"Top 5 products by sales"                       → Table
"Revenue trend by month"                        → Line chart
"Orders where quantity > 10 and region is South" → Filtered table
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
AI:   "Sorry, I can only answer questions about your uploaded data."

User: "Write me a poem"
AI:   "Sorry, I can only answer questions about your uploaded data."
```

---

## Performance Benchmarks

| Dataset Size | Upload Time | Query Time |
|-------------|-------------|------------|
| 25 rows | ~100ms | ~4s |
| 100K rows | 525ms | ~5-9s |
| 1M rows | 2.9s | ~5s |

> Query time is dominated by LLM response (~3-5s). DuckDB executes SQL in milliseconds even for millions of rows.

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/auth/login` | JWT login |
| POST | `/api/upload/` | Upload data file |
| POST | `/api/query/` | Ask natural language question |
| GET | `/api/audit/logs` | Get audit trail |
| GET | `/api/export/pdf/{session_id}` | Export session as PDF |
| GET | `/health` | Health check |

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
        │   Safety check (no      │
        │   DROP/DELETE/UPDATE)    │
        │   Syntax check (EXPLAIN)│
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

## Security

- All data stays on your local machine
- Ollama runs offline — no API calls to external services
- SQL injection prevented — only SELECT queries allowed
- DROP, DELETE, UPDATE, INSERT, ALTER, CREATE are blocked
- JWT authentication with role-based access
- Full audit trail of every query

---

## Team

| Member | Responsibility |
|--------|---------------|
| Person A | Backend, NL-to-SQL pipeline, anomaly detection, Ollama integration |
| Person B | Frontend, chat UI, visualizations, upload, PDF export |

---

## License

This project is developed as a final year academic project.
