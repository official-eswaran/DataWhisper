---
title: Frontend Application Workflow
aliases:
  - React App Workflow
  - Frontend Workflow
tags:
  - datawhisper
  - workflow
  - frontend
  - react
type: workflow
updated: 2026-08-19
---

# Frontend Application Workflow

How the React app boots, decides whether you are logged in, streams an answer, and draws it. Everything here lives under `frontend/src/`.

Related: [[01 Complete System Workflow]] for the backend half, [[03 System Architecture Flow]] for where this sits in the deployment, [[04 Data Flow Diagram]] for what the browser is allowed to hold.

## Component tree

```mermaid
flowchart TD
    IDX["index.jsx<br/>createRoot"] --> APP["App.jsx<br/>routes + auth state"]
    APP --> EB["ErrorBoundary"]
    EB --> LOGIN["Auth/Login.jsx"]
    EB --> SIGNUP["Auth/Signup.jsx"]
    EB --> DASH["Dashboard/Dashboard.jsx"]

    DASH --> SIDE["Dashboard/Sidebar.jsx"]
    DASH --> UP["Upload/FileUpload.jsx"]
    DASH --> CHAT["Chat/ChatWindow.jsx"]
    DASH --> AUD["Dashboard/AuditLogs.jsx"]
    DASH --> ADM["Dashboard/AdminConsole.jsx"]
    DASH --> ACC["Dashboard/AccountSettings.jsx"]
    ACC --> BILL["Dashboard/BillingCard.jsx"]
    CHAT -. "React.lazy" .-> RV["Visualization/ResultView.jsx"]

    APP --- API["services/api.js<br/>axios + fetch + tokens"]
```

`ResultView` is the only lazily loaded component — it pulls in all of Recharts, so it stays out of the initial bundle until an answer actually needs a chart.

## Boot sequence

> [!important] Why there is a booting state
> The access token lives in **memory only**, so a page reload starts with nothing. Without a boot step, a logged-in user would flash the login screen on every refresh. `App.jsx` therefore renders a `role="status"` placeholder until `bootstrapSession()` resolves.

```mermaid
sequenceDiagram
    autonumber
    participant B as Browser
    participant APP as App.jsx
    participant API as services/api.js
    participant BE as Backend

    B->>APP: mount
    APP->>APP: booting = true
    APP->>API: bootstrapSession()
    API->>BE: POST /api/auth/refresh (httpOnly cookie)
    alt cookie valid
        BE-->>API: new access_token + role
        API-->>APP: session
        APP->>APP: auth set → Dashboard
    else no / expired cookie
        BE-->>API: 401
        API-->>APP: null
        APP->>APP: auth null → redirect /login
    end
    APP->>APP: booting = false
```

The 401 you see in devtools on a cold load is this call, and it is expected — not a bug.

## Token handling

`frontend/src/services/api.js` is the single place that knows about credentials.

| Thing | Where it lives | Why |
|---|---|---|
| Access token | Module-scope variable | XSS cannot read it from storage; it dies with the tab |
| Role | Module-scope variable | Same |
| Refresh token | `httpOnly` cookie, sent only to `/api/auth/*` | JavaScript never sees it |

> [!warning] Nothing token-shaped goes in `localStorage`
> This was a deliberate change (issue #22). If you add a feature that "just needs to remember the user", use the refresh cookie path — do not reintroduce storage.

### Refresh coordination

```mermaid
flowchart TD
    R1[Request A → 401] --> CHK{refreshPromise<br/>in flight?}
    R2[Request B → 401] --> CHK
    CHK -- no --> NEW[Start one refresh call]
    CHK -- yes --> WAIT[Await the same promise]
    NEW --> TOK{New token?}
    WAIT --> TOK
    TOK -- yes --> RETRY[Retry original request once]
    TOK -- no --> RED[redirectToLogin]
    RETRY --> DONE([Response])
```

A single in-flight `refreshPromise` is shared by every caller, so ten parallel 401s produce **one** refresh, not ten. Each request is retried at most once — the `_retried` flag on the axios config prevents a refresh loop.

## Dashboard and routing

- Routes are `/login`, `/signup`, and `/*` → `Dashboard`. Authenticated users hitting `/login` are redirected home, and unauthenticated users hitting anything else are redirected to `/login`.
- Inside the dashboard, navigation is **tab state, not routes**: `upload`, `chat`, `audit`, `admin` (owners/admins only), `account`.
- Returning from Stripe Checkout carries a `?status=` marker, and the dashboard opens on the billing view instead of the default tab.
- `session` state holds the uploaded dataset descriptor. Until it exists, the chat tab shows **"No data loaded — upload a file first"**.

## Upload flow in the browser

```mermaid
sequenceDiagram
    autonumber
    participant U as User
    participant FU as FileUpload.jsx
    participant API as api.js
    participant BE as Backend

    U->>FU: drag & drop or click to browse
    FU->>FU: react-dropzone accepts csv/xlsx/xls/json/parquet
    FU->>API: uploadFile(file, onProgress)
    API->>BE: POST /api/upload/ (multipart)
    BE-->>API: session_id, table_name, rows, columns, dtypes, anomalies
    API-->>FU: response
    FU->>FU: onUploadSuccess(session)
    FU-->>U: switch to chat tab, sidebar shows active session
```

Upload progress is reported through an axios `onUploadProgress` callback, so a 400 MB file shows a real percentage rather than a spinner.

## Ask flow and SSE consumption

`ChatWindow.jsx` calls `askQuestionStream()` with six callbacks: `onStage`, `onDone`, `onError`, `onToken`.

```mermaid
sequenceDiagram
    autonumber
    participant CW as ChatWindow
    participant API as api.js
    participant BE as /api/query/stream

    CW->>API: askQuestionStream(sessionId, question, ...)
    API->>BE: POST with Bearer token
    alt 401
        API->>API: refreshAccessToken() then retry once
    end
    BE-->>API: text/event-stream
    loop each "data: " line
        API->>API: JSON.parse
        alt stage = token
            API->>CW: onToken → append to streamingSQL
        else stage = done
            API->>CW: onDone(result)
        else stage = error
            API->>CW: onError(message)
        else
            API->>CW: onStage(stage, message)
        end
    end
    CW->>CW: render message + lazy-load ResultView
```

Notes that matter when editing this code:

- The reader keeps a `buffer` and splits on `\n`, holding back the last partial line. A chunk boundary in the middle of a JSON payload will not corrupt parsing.
- A malformed SSE line is **ignored**, not thrown — one bad frame must not kill the stream.
- `STAGE_LABELS` maps the backend `stage` string to the human sentence shown while waiting. If you add a stage in `query.py`, add it here or the UI shows nothing for it.
- Tokens are appended to `streamingSQL` so the user watches the SQL being typed out live.

## Rendering the answer

`ResultView.jsx` receives the result envelope and offers a chart-type switcher — the backend's `recommend_chart_type()` picks the default, the user can override it.

| Switcher key | Recharts component |
|---|---|
| `bar` | `BarChart` |
| `line` | `LineChart` |
| `area` | `AreaChart` |
| `pie` | `PieChart` |
| `scatter` | `ScatterChart` |
| `multi_series` | grouped `BarChart` |
| `table` | sortable HTML table |

A `single_value` result renders as one large number instead of a chart. **Export PDF** posts the session to `/api/export` and downloads a board-ready report.

## Dev-time proxy

`frontend/vite.config.js` proxies `/api` and `/health` to `http://localhost:8000`, so the SPA and API share an origin in development exactly as they do behind nginx in production.

> [!tip] Split-origin setups
> `API_BASE` defaults to same-origin `/api`. Set `VITE_API_URL=http://localhost:8000/api` only when the frontend is served from a different origin than the backend — the default is what avoids the old hardcoded-https mixed-content bug.

## Error handling and quality gates

- `ErrorBoundary` wraps the entire route tree, so a render crash shows a recovery screen instead of a white page.
- Vitest coverage thresholds are enforced in `vite.config.js`: **91 statements / 94 branches / 71 functions / 91 lines**. They sit just under the measured values on purpose — a gate with wide headroom does not bite, and one that does not bite is decoration.
- Playwright owns `e2e/` and is excluded from the Vitest run.
