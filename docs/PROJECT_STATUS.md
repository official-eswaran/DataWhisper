# Project status & how to resume

**Last updated:** 2026-07-21
**Branch of record:** `master` — everything below is merged and pushed.

Read this first in a fresh session. It replaces the need to re-read the old
issue threads.

---

## Where things stand

**All engineering work is done and merged into `master`.** The only remaining
work is operational/business — see [GO_LIVE_CHECKLIST.md](GO_LIVE_CHECKLIST.md).

| Area | State |
|------|-------|
| Backend tests | **105 passing**, `ruff` clean |
| Frontend | Vite build OK, **4 Vitest tests** passing, runtime `npm audit` clean |
| Migrations | Head is `c7e1a2f4b9d0` (org plan + usage_counters) |
| Open issues | **#5 only** (go-live checklist — non-code) |

### What shipped (all merged to `master`)

| PR | Issue | What |
|----|-------|------|
| #1 | — | Base production hardening (security, multi-tenancy, GDPR, HA, DR, CI) |
| #10 | #2 | LLM cache unit tests + k6 load-test suite (`loadtest/`) |
| #11 | #3 | Per-session DuckDB datasets → object storage (S3), no RWX volume needed |
| #12 | #4 | Sentry error tracking + OpenTelemetry tracing + log/trace correlation |
| #13 | #6 | Per-tenant quotas + usage metering (`/api/usage`) |
| #14 | #7, #8 | Frontend CRA→Vite, code-splitting, a11y, admin console + onboarding UI |

---

## Verify everything still works

```bash
# Backend (from backend/)
export SECRET_KEY=$(python3 -c 'import secrets;print(secrets.token_hex(32))') DEBUG=true
python3 -m pytest                       # expect 105 passed
python3 -m ruff check app tests         # expect clean
python3 -m pip_audit -r requirements.txt

# Frontend (from frontend/)
npm ci
npm run build                           # outputs to build/
npm test                                # expect 4 passed
npm audit --omit=dev --audit-level=high # runtime deps must be clean
```

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
- **Sentry and OTel are strictly opt-in.** Both are complete no-ops unless
  `SENTRY_DSN` / `OTEL_EXPORTER_OTLP_ENDPOINT` are set, so dev and tests are
  unaffected. Don't "fix" them appearing inactive locally.
- **The audit log is a hash chain.** Editing/reordering/deleting rows breaks
  it; `/api/audit/verify` detects that. Don't write to `audit_logs` directly.
- **Frontend JSX files use the `.jsx` extension** (required post-Vite).
  `ResultView` is lazy-loaded so Recharts stays out of the initial bundle —
  keep it that way.

---

## Known gaps / follow-ups

None are blocking, but they're the honest loose ends:

- **k6 thresholds are placeholders.** Query p95 = 8s was picked without real
  hardware. Run the suite against staging and set real numbers
  (`loadtest/README.md` has a baseline template).
- **k6 has never been run against a live stack** from this machine — k6 wasn't
  installed. The script is syntax-checked only.
- **Plan changes are manual.** `PUT /api/usage/plan` (owner-only) exists, but
  nothing bills. Stripe wiring is described in the go-live checklist.
- **`rows_processed` is metered but not enforced.** Only `queries` and
  `uploads` have hard limits in `PLAN_LIMITS`.
- **`reportWebVitals.js` is dead code** left over from CRA — nothing imports it.
- **The README's "Project Structure" section still lists `.js` filenames** for
  frontend components that are now `.jsx`.

---

## If you pick this up next

1. Read [GO_LIVE_CHECKLIST.md](GO_LIVE_CHECKLIST.md) — that's the only open work (#5).
2. The highest-value *technical* next step is running k6 against a real
   staging stack and recording the capacity baseline, because every latency
   SLO and alert threshold currently depends on a guess.
3. If you're adding a new feature, mirror the existing pattern: code + tests,
   `ruff` clean, one focused PR against `master`, and update this file.
