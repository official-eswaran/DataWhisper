# Project status & how to resume

**Last updated:** 2026-08-09
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
| Backend tests | **581 passing**, `ruff` clean, **90.59%** coverage, gate **85** (#28) |
| NL2SQL accuracy | **96.5%** (3 repeats, cache off); 6 of 8 categories at 100% |
| Frontend tests | **177 passing**, 8 of 12 components covered; gate **80/93/64/80** (#27, #70) |
| Build/runtime | Vite build OK, Node 24 (LTS), dependency audit clean |
| E2E | Runs in GitHub Actions ✅; passes on attempt 1 since PR #63 (#45 fixed) |
| Migrations | Head is `b92c4d17ae03` (email verification, #21) |
| Dependencies | Dependabot active; majors gated for pip/npm/docker |

> **Dependency advisories expire on their own.** Two CI gates have already gone
> red with no code change involved — sentry-sdk (PYSEC-2026-1917) and
> react-router (GHSA-qwww-vcr4-c8h2). Both are handled, and Dependabot now
> watches, but treat any "clean" claim above as a snapshot: run the verification
> block below rather than believing it.

### What shipped

| PR | Issue | What |
|----|-------|------|
| #1 | — | Base production hardening (security, multi-tenancy, GDPR, HA, DR, CI) |
| #10 | #2 | LLM cache unit tests + k6 load-test suite (`loadtest/`) |
| #11 | #3 | Per-session DuckDB datasets → object storage (S3), no RWX volume needed |
| #12 | #4 | Sentry error tracking + OpenTelemetry tracing + log/trace correlation |
| #13 | #6 | Per-tenant quotas + usage metering (`/api/usage`) |
| #14 | #7, #8 | Frontend CRA→Vite, code-splitting, a11y, admin console + onboarding UI |
| — | #5 (part) | Stripe billing: hosted Checkout + Portal + webhooks ([BILLING.md](BILLING.md)) |

### Shipped 2026-08-02

| PR | What |
|----|------|
| #49 | Coverage gate 70 → 85 (#28); `ISSUE_CHECKLIST.md` as the work queue |
| — | **NL2SQL accuracy eval** (#16): `backend/evals/`, 57 cases, first measured baseline **78.4%** |
| — | **GROUP BY key repair** (#52): deterministic AST rewrite; accuracy **78.4% → 88.9%** |

### Shipped 2026-08-06

| Issue | What |
|-------|------|
| #21 | **Email verification gates queries.** Per-org, keyed on the owner — a per-user check let an unverified owner create a member via the admin route and query as them. Off under DEBUG; existing accounts grandfathered by the migration. Mail transport is a no-op interface (SMTP/captcha still deferred). |
| #47 | **EOL-runtime watch.** `scripts/check_eol.py` + monthly `eol-watch.yml`, checking `endoflife.date`. Parses pins from the real files and *fails* rather than reporting all-clear if one stops matching. |
| #27 | **Frontend coverage gate.** `npm test` is now `vitest run --coverage`; `FileUpload` covered. |
| #58 | **DISTINCT repair.** `distinct` 60% → **100%**, deterministic. |
| #69 | **Date period repair.** `date` 7/15 → **13/15**; overall 87.1% → **90.6%** with no other category moving. |
| #73 | **Missing-GROUP-BY repair.** `group_by` 22/27 → **27/27**; overall **94.7%**. Five categories now at 100%. |

### Shipped 2026-08-08

| Issue | What |
|-------|------|
| #74 | **Aggregate-threshold repair.** `having` 0/3 → **3/3** — the last category at zero. Overall 94.7% → **96.5%**, with every other category unmoved attempt-for-attempt. Fifth deterministic repair. The "which aggregate?" guess is handled by declining on any non-SUM vocabulary; the "is this even an aggregate threshold?" question turned out to be answerable from the *data* (does the projected column repeat?) rather than the phrasing. See "Known gaps". |
| — | **Both query paths now provably apply the same repairs.** #74 was first wired into `pipeline.py` only — the eval would have scored it green while every real user, who goes through the SSE stream in `query.py`, still saw the bug. `test_sql_repair.py` now asserts the two call sites match, structurally, so the next repair is covered the day it is written. |
| #59, #60 | **Attempted, measured, reverted — still open.** See the note below. |

### Shipped 2026-08-10

| Issue | What |
|-------|------|
| #70 | **`AuditLogs` covered** — third of the seven. 25 tests, component to **100%** on all four metrics, suite 152 → 177; overall coverage now **80.31%**. Gate **73/92/62/73 → 80/93/64/80**. 25 of 25 mutants killed. |
| #82 | **Filed, not fixed.** A failed audit-log fetch renders as "No audit logs yet. Start asking questions!" — the page asserts the trail is empty when it simply failed to load, which for an *audit* trail is a wrong answer to the question the page exists to answer. Pinned by a characterization test rather than asserted as correct. |
| #70 | **`AdminConsole` covered** — second of the seven, and the highest-risk one: the only place a person's action changes someone else's account. 36 tests, component to **100%** on all four metrics, suite 116 → 152. Gate **64/91/57/64 → 73/92/62/73**, verified to bite. 33 of 33 mutants killed, including "the owner row has a deactivate button" and "the toggle sends the current state instead of its inverse". |

### Shipped 2026-08-09

| Issue | What |
|-------|------|
| #70 | **`Login` covered** — first of the seven. 13 tests, component to 100% on all four metrics, suite 94 → 107. Gate ratcheted 60/85/50/60 → **64/90/56/64**, and *verified to bite*: the first attempt raised it by the old "few points under" rule and still passed with all 13 new tests deleted. Six components remain. |
| #77 | **`Login` failure messages fixed.** Lockout, disabled-account and rate-limit outcomes each say what actually happened, instead of all rendering as "Invalid credentials". Found while writing #70's tests, filed rather than folded in, then fixed straight after — and the characterization test that pinned the defect was replaced by per-status assertions. Note `429` has two unrelated causes (account lockout vs slowapi per-IP limit) that only the response body tells apart. Suite 107 → 116; gate 64/90/56/64 → **64/91/57/64**. |

### Shipped 2026-07-28 (audit session)

| PR | What |
|----|------|
| #32 | **Security:** 422 responses echoed the submitted password back in the body — register *and* login |
| #33 | Chart heuristics never matched snake_case columns; signup password rule; `REPLACE()` rejected as DDL; `query.py` 40% → 100%; Dependabot |
| #42 | Node 20 was **past EOL** — moved to 24 (LTS) in the Dockerfile *and* both workflows; docker majors gated |
| #43 | `ResultView` (28 tests) + `Signup` (12 tests) |
| #44 | `ChatWindow` (23 tests) — the SSE state machine |
| #36–#41 | Dependabot queue triaged: DuckDB 1.1→1.5.5, pandas, Pydantic, numpy 2, OTel, react, recharts, actions |

---

## Verify everything still works

```bash
# Backend (from backend/)
export SECRET_KEY=$(python3 -c 'import secrets;print(secrets.token_hex(32))') DEBUG=true
python3 -m pytest                       # expect 581 passed
python3 -m ruff check app tests evals   # expect clean
python3 -m pip_audit -r requirements.txt --strict   # --strict is what CI runs

# Frontend (from frontend/)
npm ci
npm run build                           # outputs to build/
npm test                                # expect 177 passed; enforces coverage thresholds
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
- **Email verification gates queries, and it is per-org, not per-user
  (issue #21).** `require_verified_email` reads the *owner's* `email_verified`
  via `is_org_email_verified`. Quota is an org-level budget and the abuse path
  is "register another org", so the org is the unit that has to be paid for
  once — and a per-user check leaves a hole, because an unverified owner can
  create a member through the admin route and query as them. Admin-created users
  are therefore marked verified on insert; their org's status is what counts.
  The gate follows `REQUIRE_EMAIL_VERIFICATION`, which is `None` = auto = "on
  when `DEBUG=false`", mirroring `SEED_DEMO_DATA` — so dev and the test suite are
  unaffected with nothing set. Registration and login still succeed and return
  tokens (plus `email_verified`), because the user needs a session to reach the
  UI that tells them to check their mail. **The migration grandfathers every
  existing user to verified**; switching this on under orgs that predate it must
  not lock them out. Tokens are single-use, expiring, stored as SHA-256 only, and
  a resend invalidates the previous one and returns an identical response for
  unknown accounts (no enumeration oracle). `app/core/mailer.py` is a no-op that
  logs unless `SMTP_HOST` is set — and if it *is* set with no transport
  implemented, it logs an error rather than silently stranding users.
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
- **Accuracy is measured (94.7%), and it is not 100%.** `backend/evals/` runs 57
  question→answer cases through the real pipeline and compares *results* against
  a reference query (execution accuracy), because many different SQL strings are
  equally correct. `python -m evals` from `backend/`; needs Ollama. The floor
  lives in `evals/baseline.json` and CI enforces it weekly via `eval.yml`, not
  per-PR. The eval's own comparison logic and case set are unit-tested in
  `tests/test_nl2sql_eval.py`, which needs no Ollama and does run per-PR — so a
  broken *checker* is caught even when the eval itself isn't running.
  Run it with the LLM cache off (the default) or you score the cache.

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
- ~~**The E2E cold-path masking (#45)**~~ — **fixed**, merged 2026-08-05 (#63).
  Background: it has executed on a hosted runner (2026-07-28) and nightly since
  2026-07-25, but on the manual run attempt 1 blew the 120s `expect` timeout on
  cold CPU inference and the retry passed in 3.1s off a warm LLM cache — so
  `retries: 1` was doing real work and the suite certified the *warm* path,
  unable to detect a cold-path regression. The fix does not raise the timeout:
  `run.sh` warms the model into RAM before Playwright starts, so the timed query
  no longer pays model load, and `retries` is now `0` so attempt 1 must pass on
  its own. Whether cold inference is *supposed* to be this slow is still #25's
  question, answered against real staging, not a shared runner.
- ~~**Open signup has no verification (#21)**~~ — **half fixed** 2026-08-06.
  Queries now require a confirmed address (`REQUIRE_EMAIL_VERIFICATION`,
  auto-on when `DEBUG=false`), gated per-org on the owner. **The deferred half
  is real and still open:** there is no mail transport, so with `SMTP_HOST`
  unset the verification link is only ever written to the log. Fine for
  self-hosting, *not* fine for a public signup flow — wiring SMTP/a provider (or
  hCaptcha instead) is Track B and needs an account.
- ~~**No frontend coverage gate (#27)**~~ — **gate fixed** 2026-08-06:
  `npm test` is `vitest run --coverage` with thresholds in `vite.config.js`,
  enforced locally and in CI. Now **64 statements / 90 branches / 56 functions /
  64 lines**. The coverage config needs its explicit `include`: without it the
  v8 provider also measures `build/assets/*.js` and reports a number ~10 points
  below the truth.

  **`Login` 2026-08-09**, **`AdminConsole` and `AuditLogs` 2026-08-10** (#70) —
  all three to 100% on all four metrics; suite 94 → 177, overall coverage
  63.72% → **80.31%**. **Four remain**: `AccountSettings`, `Dashboard`,
  `Sidebar`, `ErrorBoundary`. One per PR.

  **A raised gate is not automatically a ratchet, and this one wasn't.** Raising
  the thresholds by the original "a few points under measured" rule left the
  suite passing with all 13 of Login's new tests deleted — one component is
  worth ~0.6 points of statements, and the slack was ~3.7. Whatever the gate
  claimed, the coverage it had just gained was free to delete again. The
  thresholds now sit ~0.4 under measured, and the rule for the next component is
  to **delete the suite you just added and confirm `npm test` exits non-zero**
  before opening the PR. v8 coverage is deterministic, so tight thresholds do
  not flake.
- ~~**`Login` reports every failure as "Invalid credentials" (#77)**~~ —
  **fixed** 2026-08-09, immediately after the coverage PR that found it. Each of
  the backend's outcomes now reaches the user distinctly, and the
  characterization test that pinned the collapse was replaced by per-status
  assertions — the workflow it was written for, start to finish.

  **The non-obvious part was `429`, which has two unrelated causes.** The login
  route returns it for a per-account lockout, and slowapi returns it for a
  per-IP rate limit ("Too many requests. Please slow down."). They call for
  different things from the user, and only the response body separates them — so
  the mapping passes `detail` through for 401 and 429, where it carries the
  attempt count, the lockout duration, and that distinction. 403 is the
  exception: its detail is accurate but says nothing to do next, so the UI owns
  that wording. Unrecognised statuses fall back rather than echo a body, because
  a 500's detail is not written for end users.
- ~~**The backend coverage gate is 70% while actual coverage is 90.4%**~~ —
  **fixed:** the gate is now 85 (#28). 85 rather than 90 because
  `core/billing.py` sits at 82%, so a 90 gate would fail on any billing change
  that didn't also add tests. The remaining ~5 points are still unprotected;
  raise the gate again when billing coverage catches up.
- ~~**PDF export truncates every field at 60 characters (#46)**~~ — fixed
  2026-08-03 (PR #51): cells wrap as ReportLab `Paragraph`s, with a 4000-char
  runaway guard in place of the 60-char cap.
- **Two of the four eval-found defects remain (#59, #60).** #61 was fixed
  2026-08-04 and #58 on 2026-08-06 (`distinct` 60% → **100%**, deterministic
  repair). #59 and #60 are still open **and both have a failed attempt on
  record** — read this before trying again:
  - **#59** (superlatives return the measure): the recommended rule naming the
    superlative vocabulary took `ranking` from **87.5% to 62.5%**. The 3B model
    copied the concrete example verbatim, including its `ASC` direction on a
    "highest" question, breaking two cases that had been passing 3/3. An
    abstract rewrite measured the same. Reverted.
  - **#60** (units vs orders): the recommended rule *did* fix its own case —
    `filter` 91% → 100% — but cost `ranking` 87.5% → 75%, for an identical
    93/99 across the three categories either way. It moves failures rather than
    removing them. Reverted.

  **This is the third confirmation of the #52 lesson, and the sharpest**: a rule
  that provably fixes its target case can still be not worth shipping. Compare
  *category* totals across the whole eval, not the headline — 86.0–91.2 is the
  observed range, wide enough to hide a 12-point category swing.
- ~~**Date handling is the largest accuracy gap (#69)**~~ and ~~**per-group
  questions answered with one scalar (#73)**~~ — both **fixed**. `date` 7/15 →
  13-15/15, `group_by` 22/27 → **27/27**. Overall **94.7%**, five categories at
  100%.

  **Read the attribution, not the headline.** Of the +7 attempts in the #73
  round, +5 are the repair and +2 are two flaky `date` cases landing well; that
  repair cannot touch `date`. A round where they fall the other way reads ~92.4%
  on identical code. This is why `baseline.json` records category tables.

- ~~**Aggregate thresholds become row-level filters (#74)**~~ — **fixed**
  2026-08-08. `having` **0/3 → 3/3**, overall 94.7% → **96.5%**. The cleanest
  attribution of any round so far: +3 attempts, all three of them this category,
  every other category identical attempt-for-attempt. Nothing to subtract.

  **This file predicted it would resist a deterministic repair, and that was
  half wrong — the useful half is *why*.** The prediction rested on three things
  needing to be inferred, of which two looked underivable: which aggregate the
  threshold means, and whether the threshold is an aggregate one at all. The
  first really is a guess, and the repair handles it by declining — only
  accumulation vocabulary ("generated", "total", "combined") fires, and any word
  naming a different aggregate ("averaged", "highest") stops it dead.

  The second turned out to be **derivable from the data, not the phrasing** —
  which is what the analysis missed. Compare:

  | question | correct SQL |
  |---|---|
  | "Which **orders** had a **total** amount above 100000?" | `WHERE` — passing today |
  | "Which **products** **generated** more than 200000 in revenue?" | `GROUP BY … HAVING SUM(…)` |

  Both say "total". No amount of vocabulary separates them. What does is that
  `order_id` is unique and `product` repeats: a per-order threshold *is* a row
  filter, and a per-product one cannot be evaluated a row at a time. So the
  guard is `COUNT(DISTINCT entity) < COUNT(*)` against the real table — a
  question with an answer, not a guess.

  **The transferable lesson: "the SQL cannot reveal it" is not the same as "it
  is not derivable."** #52/#58/#69/#73 all derived their answer from the AST or
  the question text, so those were the two places anyone thought to look. The
  table itself is a third source, and #59/#60 have not been re-examined against
  it. That is not a promise either yields — see below — only that the reason
  they were grouped with #74 no longer holds for all three.
- **Two cases now fail, each 0/3, each with an issue.** #59 (superlative returns
  the measure) and #60 (units vs orders). Both turn on a semantic choice, both
  have a **failed, measured, reverted attempt on record**, and #60's attempt
  fixed its own case while costing `ranking` 12.5 points — so "I made the target
  case pass" is not evidence of anything. Read the note above before either.
- ~~**Nothing watches for EOL runtimes (#47)**~~ — **fixed** 2026-08-06.
  `eol-watch.yml` runs monthly against `endoflife.date` and opens/updates one
  rolling issue. It parses the pins out of the real files rather than
  re-declaring them, and a rule that stops matching **fails the job** instead of
  reporting all-clear — a checker that has gone blind is worse than none.
  Current: python 3.12 → 2028-10-31, node 24 → 2028-04-30, nginx stable (no
  announced end).
- **Prompting is not a control; a deterministic rewrite is.** The eval's first
  run found the model dropping the GROUP BY key from the projection
  (`SELECT SUM(total_amount) FROM sales_data GROUP BY region` — unlabelled
  numbers), scoring grouped questions at 30% *while the prompt forbade exactly
  that in capitals*. Two fixes were measured. Adding few-shot examples moved
  `group_by` 30% → 44% and only for the table the examples named — and it
  **cost 5 points overall**, because the new examples pulled the model toward
  projecting bare measures and broke two unrelated cases that had been passing
  3/3. Rewriting the AST after validation (`nl2sql/sql_repair.py`) took
  `group_by` to 93% and overall accuracy 78.4% → **88.9%**. The examples were
  reverted. Read this before "improving" accuracy by editing the prompt: measure
  the whole eval, not the category you aimed at.
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

**First, don't trust this file.** Run `git fetch --prune`, then the six
verification commands above. Dependency advisories accrue while nothing is being
committed, and this file is a snapshot.

### Priority order

The honest ranking, which is *not* the same as "what is easiest".
[ISSUE_CHECKLIST.md](ISSUE_CHECKLIST.md) turns this into the running work queue —
one item, one PR, merged before the next starts.

**P0 — decides whether a bad day is survivable.** None of these are code.

1. **Restore drill from a real backup** (#5, #18). `scripts/restore.sh` and
   `DISASTER_RECOVERY.md` exist and have never been executed. The RPO ≤ 15 min /
   RTO ≤ 1 h targets are assertions, not measurements. An untested backup is not
   a backup — this is the single highest-risk open item in the project.
2. **k6 against real staging** (#25). Every latency SLO, alert threshold and the
   E2E's 120s timeout currently descends from one laptop run. Set
   `LLM_CACHE_ENABLED=false` and raise the target's rate limits first, or you
   measure the cache and the limiter instead of the app.
3. **Stripe test-mode round trip** (#19). The money path is fully unit-tested
   with stubs and has never touched a real account.
4. ~~**NL2SQL accuracy eval** (#16)~~ — done 2026-08-02, and it immediately
   found a real bug (#52, fixed 2026-08-03). Accuracy 78.4% → **88.9%**.

**Everything below P0 that could be done from a dev machine now has been.** What
is left in P0 is blocked on infrastructure and accounts, not on effort — which
means the top of this list has not moved since 2026-07-21 and will not until
someone provisions something. That is the honest state of the project.

**P1 — real bugs and abuse vectors.**

5. ~~E2E cold-path flakiness (#45)~~ — done 2026-08-05 (PR #63).
6. ~~Email verification on signup (#21)~~ — **half done** 2026-08-06. Queries are
   gated; the mail transport is a no-op, so the remaining half is Track B.

**P2 — quality, cheap.** Each is roughly an hour.

7. ~~Raise the backend coverage gate 70 → 85 (#28)~~ — done 2026-08-02.
8. ~~Frontend coverage gate (#27)~~ — done 2026-08-06, `FileUpload` covered.
   **`Login` done 2026-08-09; six components (#70) remain and are still the
   cheapest win**, in value order: `AdminConsole`, `AuditLogs`,
   `AccountSettings`, `Dashboard`, `Sidebar`, `ErrorBoundary`. One per PR, and
   raise the thresholds as each lands — *then delete the suite you just wrote
   and confirm `npm test` goes red*, because raising them is not the same as
   ratcheting them. `AdminConsole`, `AuditLogs` and `AccountSettings` touch
   other people's accounts and audit data, so they carry the most risk per
   untested line.
9. ~~PDF export truncation (#46)~~ — done 2026-08-03.
10. ~~EOL-runtime watch (#47)~~ — done 2026-08-06.
11. ~~Accuracy: #69 (dates)~~, ~~#73 (per-group)~~, ~~#74 (HAVING)~~ — all done.
    Accuracy is **96.5%** and six of eight categories are at 100%.
    **#59 and #60 remain, and both have a failed attempt on record** — read the
    note in "Known gaps" before touching either; the prompt fix each issue
    recommends is the one that was tried and reverted. Five deterministic
    repairs have now landed (#52, #58, #69, #73, #74) and every prompt edit
    attempted has been reverted; that is the pattern to plan around.
12. Billing feature gaps — proration, dunning, invoices (#31). Explicitly
    post-launch; **do not start before #19** proves the money path.

### Conventions worth keeping

- **Mutation-test new tests.** Break the thing on purpose and confirm the test
  fails. Two tests written on 2026-07-28 passed vacuously and were only caught
  this way: one asserted a button was disabled when it would have been disabled
  regardless, another compared a global thread count that other tests inflated.
  Green is not evidence on its own.
- **Tests must not reach a live Ollama.** See the architecture note above.
- One focused PR per concern, against **`main`**, `ruff` clean, and update this
  file in the same PR.
