# Project status & how to resume

**Last updated:** 2026-07-28
**Branch of record:** `main`.

> ⚠️ **`master` is not the branch of record and does not exist on the remote.**
> This file said `master` for months while GitHub's default branch was `main`,
> and a stale local `origin/master` ref kept resolving, so a whole audit was
> once carried out against a tree five commits behind — concluding CI was red
> when it had already been fixed. Run `git fetch --prune` and check
> `gh repo view --json defaultBranchRef` before trusting any local ref.

Read this first in a fresh session. It replaces the need to re-read the old
issue threads.

---

## Where things stand

**Every planned feature is built, tested and merged.** The bulk of what remains
is operational — see [GO_LIVE_CHECKLIST.md](GO_LIVE_CHECKLIST.md) — but "done"
overstates it: there is real engineering work left in the "Known gaps" section
below. None of it blocks a deploy; all of it is worth doing before calling this
finished.

| Area | State |
|------|-------|
| Backend tests | **334 passing**, `ruff` clean, **90%** coverage (gate: 70%) |
| Frontend | Vite build OK, **15 Vitest tests** passing, dependency audit clean |
| E2E | Playwright full-flow smoke (**verified locally**, not yet run in CI) — `frontend/e2e/` |
| Migrations | Head is `a81e5f30c6d2` (signed audit checkpoints) |
| Open issues | **#5** (go-live checklist — non-code) |

> **Dependency advisories expire on their own.** Two CI gates have already gone
> red with no code change involved — sentry-sdk (PYSEC-2026-1917) and
> react-router (GHSA-qwww-vcr4-c8h2). Both are handled, and Dependabot now
> watches, but treat any "clean" claim above as a snapshot: run the verification
> block below rather than believing it.

### What shipped (all merged to `master`)

| PR | Issue | What |
|----|-------|------|
| #1 | — | Base production hardening (security, multi-tenancy, GDPR, HA, DR, CI) |
| #10 | #2 | LLM cache unit tests + k6 load-test suite (`loadtest/`) |
| #11 | #3 | Per-session DuckDB datasets → object storage (S3), no RWX volume needed |
| #12 | #4 | Sentry error tracking + OpenTelemetry tracing + log/trace correlation |
| #13 | #6 | Per-tenant quotas + usage metering (`/api/usage`) |
| #14 | #7, #8 | Frontend CRA→Vite, code-splitting, a11y, admin console + onboarding UI |
| — | #5 (part) | Stripe billing: hosted Checkout + Portal + webhooks ([BILLING.md](BILLING.md)) |

---

## Verify everything still works

```bash
# Backend (from backend/)
export SECRET_KEY=$(python3 -c 'import secrets;print(secrets.token_hex(32))') DEBUG=true
python3 -m pytest                       # expect 334 passed
python3 -m ruff check app tests         # expect clean
python3 -m pip_audit -r requirements.txt --strict   # --strict is what CI runs

# Frontend (from frontend/)
npm ci
npm run build                           # outputs to build/
npm test                                # expect 15 passed
npm audit --omit=dev                    # see the allowlist note in ci.yml
```

The backend commands are what `.github/workflows/ci.yml` runs. The frontend
audit is **not** a bare `npm audit` in CI: the job allowlists
`GHSA-qwww-vcr4-c8h2` by id (react-router has no clean 7.x — below 7.12 carries
14 advisories including an unauthenticated RCE, above it carries this one, which
is RSC-only and unreachable from a Vite SPA) and fails on everything else. Read
the comment in `ci.yml` before reacting to what a bare `npm audit` prints.

There is a third CI job these commands don't cover — `images`, which builds both
Dockerfiles and scans them with Trivy. It has caught a stale action pin and base
image CVEs that no source change would surface, so don't assume green locally
means green in CI.

Note: `boto3`, `moto`, `sentry-sdk`, and the OpenTelemetry packages are in
`requirements.txt` / `requirements-dev.txt`. If tests fail on import, run
`pip install -r requirements-dev.txt`.

---

## Architecture notes worth knowing

Things that are easy to miss when reading the code cold:

- **Dataset storage is pluggable.** `DATASET_STORAGE_BACKEND=local|s3`
  (`app/core/dataset_storage.py`). `local` keeps `*.duckdb` on `DATABASE_DIR`;
  `s3` stores them in object storage and materialises to a per-pod cache on
  demand. The k8s manifests default to `s3`, which is what removes the
  ReadWriteMany volume requirement and allows multi-replica scaling.
- **Quotas vs rate limits are different things.** slowapi rate limits are
  per-IP (burst protection). Quotas (`app/core/quota.py`) are per-org, per
  calendar month, enforced against `PLAN_LIMITS`. Quota is checked *before*
  the work and recorded *after* success, so failed requests don't burn it.
- **`rows_processed` is checked differently from the other metrics.** Queries
  and uploads always cost 1, so a plain "already at the limit?" check works.
  A row cost isn't known until the work is done, so uploads re-check with the
  real `len(df)` *after parsing but before committing* — that's why the call
  sits inside the try block, where the existing cleanup can discard it.
  Queries only get the cheap check, because refusing to return results for a
  query that already ran helps nobody; per-query overshoot is bounded by
  `MAX_RESULT_ROWS`.
- **A plan's row ceiling and `MAX_UPLOAD_SIZE_MB` are coupled.** If the ceiling
  is below the row count of one maximum-size upload, every large upload 429s
  *after* being parsed and the plan is effectively unusable. They live in
  different files, so `check_limits_are_reachable()` warns at startup when they
  drift. If you raise `MAX_UPLOAD_SIZE_MB`, check the logs. The rows-per-MB
  figure it compares against is **measured, not assumed** (issue #24): every
  upload of ≥1,000 rows is folded into `upload_shape_stats`, and after 20
  samples the check switches from the assumed 100 B/row to the *narrowest*
  file observed — narrow rows being the case that overflows a ceiling. See
  `GET /api/usage/limits`.
- **Sentry and OTel are strictly opt-in.** Both are complete no-ops unless
  `SENTRY_DSN` / `OTEL_EXPORTER_OTLP_ENDPOINT` are set, so dev and tests are
  unaffected. Don't "fix" them appearing inactive locally.
- **The audit log is a hash chain.** Editing/reordering/deleting rows breaks
  it; `/api/audit/verify` detects that. Don't write to `audit_logs` directly.
  Verification comes in two scopes (issue #30). `full=true` (the default) walks
  the whole chain — the answer an audit needs, cost linear in history.
  `full=false` verifies only entries after the newest **signed** checkpoint
  (`audit_checkpoints`, HMAC'd with `SECRET_KEY`, written every
  `AUDIT_CHECKPOINT_INTERVAL` appends), which bounds the work but is blind to
  tampering older than that anchor. The response always reports its own `scope`
  and `verified_from_id`, so the two can't be confused. Keep running a full
  verify periodically; incremental is for the frequent check, not the audit.
- **Tokens: refresh in an httpOnly cookie, access in memory only (issue #22).**
  `/api/auth/*` set a `dw_refresh` cookie (httpOnly, SameSite=Lax, Secure when
  `DEBUG=false`, path `/api/auth`); the refresh token is never in the JSON body
  or `localStorage`. `/api/auth/refresh` reads the cookie, not a body. The
  frontend keeps the access token and role in module memory, so a reload starts
  with no session and `bootstrapSession()` re-mints an access token from the
  cookie before rendering routes — that's the brief boot gate in `App.jsx`. Deploy
  note: this assumes SPA and API share an origin (prod nginx / dev vite proxy).
  A split-origin setup needs SameSite=None+Secure and CORS `allow_credentials`.
- **Demo accounts don't seed in production.** `init_db` seeds `ceo`/`manager`
  only when `settings.should_seed_demo` — which follows `DEBUG` unless
  `SEED_DEMO_DATA` is set (issue #23). So dev/test get the demo org and prod
  (`DEBUG=false`) starts empty, first org via `/api/auth/register`. Tests rely
  on the DEBUG-true default seeding for the `admin_token`/`manager_token`
  fixtures — don't break that.
- **Stripe is opt-in and webhook-driven.** Unset `STRIPE_SECRET_KEY` → billing
  routes 503, everything else unaffected. Entitlements change *only* on a
  signature-verified webhook, never on the post-checkout redirect. The webhook
  route must keep reading the raw body — see [BILLING.md](BILLING.md).
- **One source of truth for a plan at a time.** `PUT /api/usage/plan` is the
  manual tier control for self-hosted (no-Stripe) deployments. Once billing is
  enabled it returns 409 — the webhook owns `organizations.plan`, and a manual
  override would just be reverted by the next subscription event (issue #17).
- **Frontend JSX files use the `.jsx` extension** (required post-Vite).
  `ResultView` is lazy-loaded so Recharts stays out of the initial bundle —
  keep it that way.
- **Column-name heuristics must match against `_name_words()`, not the raw
  column.** `_` is a word character, so `\b(date)\b` cannot match `order_date`
  and `\b(name)\b` cannot match `customer_name` — and SQL columns are
  overwhelmingly snake_case. Every date-detection and named-entity rule in
  `chart_advisor.py` was silently dead until this was fixed, so time series
  rendered as bar charts. If you add a column-name pattern, match it against the
  split form or it will never fire. Keep the patterns to words that identify an
  entity on their own: bare stems like `first`/`last` add nothing once the split
  is in place (the `name` alternative already reaches `first_name`) but do
  capture `first_seen` and `last_login`. `test_chart_advisor.py` pins both
  directions.
- **Tests must not reach a live Ollama.** `classify_intent` falls back to the
  LLM for questions its keyword lists don't recognise, so on a machine with
  Ollama running, any test asking an unrecognised question silently becomes a
  network test — slow, non-deterministic, and green for the wrong reason.
  `test_query_stream.py` has an autouse fixture that makes the fallback raise;
  do the same in any new test that submits free-form questions.
- **The E2E lives in `frontend/e2e/` and drives the real stack.** `npm run
  test:e2e` starts the backend + a dedicated-port frontend and runs one browser
  smoke through register → upload → real Ollama query → rendered result. It
  needs Ollama running. Vitest is configured to ignore `e2e/**`; the two runners
  don't overlap. CI job is `.github/workflows/e2e.yml` (manual/nightly, not
  per-PR — it pulls a model). See `frontend/e2e/README.md`.

---

## Known gaps / follow-ups

None are blocking, but they're the honest loose ends:

- **k6 thresholds are still placeholders.** Query p95 = 8s was picked without
  real hardware and remains unvalidated. The suite has now been run end-to-end
  (2026-07-21) but only on a dev laptop with CPU inference, which is not
  representative — see the "Runs so far" section of `loadtest/README.md`. A
  staging run is still the outstanding work.
- **Load testing needs the target stack's rate limits raised.** k6 drives from
  one IP and slowapi limits are per-IP, so a stack on production limits 429s
  almost every query and measures the limiter instead of the app. The script
  now fails loudly on `dw_rate_limited` when this happens.
- **The load test measures a warm LLM cache by default.** Only 5 fixed
  questions against one dataset, and the cache is keyed on model+prompt, so
  after a few iterations it stops exercising the LLM at all (observed: 38.3s
  p95 cold vs 61ms warm on the same stack). Set `LLM_CACHE_ENABLED=false` to
  size real inference capacity, and always record which mode a baseline used.
- **Stripe has never run against a real account.** The integration is fully
  unit-tested with stubs, but no live or test-mode checkout has been completed
  from this machine — that needs a Stripe account (go-live checklist).
- **The billing UI has never seen a real Stripe redirect.** `BillingCard` is
  unit-tested with the API mocked, but no browser has actually made the round
  trip to Stripe and back.
- **The E2E has never run in GitHub Actions.** It passes locally against real
  Ollama, but the CI workflow (`e2e.yml`) is unverified on a hosted runner —
  the Ollama install/model-pull is the likely rough edge on first run.
- **Open signup has a rate limit + kill switch, but no verification (issue
  #21).** Registration is now capped at `RATE_LIMIT_REGISTER` (5/hour/IP) and
  can be closed entirely with `SIGNUPS_OPEN=false`. The complete fix — email
  verification or captcha before an org gets free quota — still needs email/
  captcha infra that isn't here.
- **The frontend has tests for 1 of its 12 components.** The 15 Vitest tests
  cover `BillingCard` and `App` (the router shell, not one of the 12).
  `ChatWindow`, `ResultView`, `FileUpload`, `Login`, `Signup`, `AdminConsole`,
  `AuditLogs`, `AccountSettings`, `Dashboard`, `Sidebar` and `ErrorBoundary`
  have none — including `ChatWindow` and `ResultView`, which are the product.
  `Signup` is the place to start: its error handling was recently changed and
  nothing pins it.
- **PDF export truncates every field at 60 characters** (`export.py:_safe`),
  including the SQL, so session reports cut mid-statement and are not much use
  as a compliance artifact.
- **The `rows_processed` ceilings are still guesses, but no longer unmeasured.**
  10M/month on free and 50M on pro were picked without usage data. The platform
  now measures bytes-per-row from every upload of ≥1,000 rows and
  `GET /api/usage/limits` reports what those ceilings actually buy (issue #24) —
  but nothing has uploaded to a real deployment yet, so the sample is empty and
  the check still falls back to the assumed 100 B/row until 20 uploads land.
  Revisit the numbers once that endpoint has data. Nothing is reported to Stripe
  as metered usage; the cap blocks work rather than adding to the bill.

---

## If you pick this up next

1. Read [GO_LIVE_CHECKLIST.md](GO_LIVE_CHECKLIST.md) — that's the only open work (#5).
2. The highest-value *technical* next step is running k6 against a real
   staging stack and recording the capacity baseline, because every latency
   SLO and alert threshold currently depends on a guess.
3. If you're adding a new feature, mirror the existing pattern: code + tests,
   `ruff` clean, one focused PR against `master`, and update this file.
