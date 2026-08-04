# Issue checklist — the work queue

**Created:** 2026-08-02
**Source:** the 13 open issues, ranked by the priority order in
[PROJECT_STATUS.md](PROJECT_STATUS.md#priority-order).

One item at a time, one PR per item, merged before the next starts. This file is
the running state — tick a box only when the PR is **merged**, not when the code
is written.

## Ground rules for every item

- Branch off `main`, PR into `main`. One concern per PR.
- `ruff` clean; backend suite green; frontend suite green.
- **Mutation-test every new test.** Break the thing on purpose, confirm the test
  goes red, put it back. Two tests written on 2026-07-28 passed vacuously and
  were caught only this way.
- No test may reach a live Ollama — follow the autouse fixture in
  `test_query_stream.py` for anything that submits free-form questions.
- Update `PROJECT_STATUS.md` **and this file** in the same PR.
- Close the issue from the PR body (`Closes #N`).

---

## Track A — actionable from this machine

The order is by risk, not by effort. Item 1 is a full day; items 4–8 are about an
hour each.

### 1. `#16` — NL2SQL accuracy eval (P0)

- [x] **Merged** — 2026-08-02

Delivered `backend/evals/`: 57 cases over both sample datasets, scored by
execution accuracy (run the model's SQL, run a reference query, compare results)
so that phrasing differences don't count as errors. `python -m evals` from
`backend/`. Ollama-dependent, so it runs in its own weekly `eval.yml`, never in
the per-PR job — the "tests must not reach Ollama" rule holds.

**First measured baseline: 78.4%** (`evals/baseline.json`, 3 repeats, cache off,
laptop CPU). CI floor set to 70, roughly 7 points below the lowest observed
round, so it catches a category-sized regression without firing on the model's
run-to-run noise.

The eval's own logic is unit-tested without Ollama in
`tests/test_nl2sql_eval.py` (31 tests, all mutation-tested), including tests
that the case-set checker actually rejects bad cases — a validator that cannot
fail would make "the case set is valid" meaningless.

**It immediately paid for itself** → see item 2a below.

### 2a. Model drops the GROUP BY key from SELECT (#52)

- [x] **Merged** — 2026-08-03

Found by the eval, not by review. Fixed with a deterministic AST rewrite
(`app/nl2sql/sql_repair.py`) applied after validation on both the streaming and
non-streaming paths: if a plain `GROUP BY` key is missing from the projection,
it is added. Uses DuckDB's own `json_serialize_sql` round-trip, so the parser
doing the analysis is the one that executes the query — no new dependency, and
no regex on SQL.

**Accuracy 78.4% → 88.9%**; `group_by` 30% → 93%.

**The lesson is in what didn't work.** The obvious fix — more few-shot
examples — moved `group_by` to only 44%, helped just the table the examples
named, and **cost 5 points overall** by nudging the model toward bare measures,
breaking two unrelated cases that had been passing 3/3. It was reverted. The
prompt had forbidden this exact mistake in capitals since before the eval
existed. For a 3B model, prompt text is a suggestion; a rewrite is a guarantee.

### 2. `#45` — E2E passes only on retry (P1)

- [ ] **Merged**

Attempt 1 blows the 120s `expect` timeout on cold CPU inference, warms the LLM
cache, and the retry passes in 3.1s. `retries: 1` is what makes it green, so the
suite certifies the warm path and cannot detect a cold-path regression.

**Scope** — three separable pieces, smallest first:
- Drop the redundant `ollama serve &` in `e2e.yml` (the install script already
  starts the daemon; the step has never done anything and masks a real bind
  failure). Clearly correct, independent of the rest.
- Add an explicit warm-up query before the timed assertion, so the measured
  request isn't the one paying for model load.
- Say what the E2E is *for* in `frontend/e2e/README.md` — smoke test or cold-path
  guarantee. The workflow name currently reads as stronger than it is.

**Files** — `.github/workflows/e2e.yml`, `frontend/playwright.config.js`,
`frontend/e2e/full-flow.spec.js:65`

**Done when** the suite passes on attempt 1 without relying on the retry.

**Do not** just raise the timeout — that hides the symptom without answering
whether cold inference is supposed to take that long, which is the question
blocking `#25`.

### 3. `#21` — Signup abuse vector (P1)

- [ ] **Merged**

Each new org gets 1,000 free LLM queries + 10M rows, quotas are per-org, so the
bypass is "make another org". `RATE_LIMIT_REGISTER` (5/hour/IP) and
`SIGNUPS_OPEN=false` exist and only slow single-IP abuse.

**Scope** — the complete fix needs email or captcha infrastructure that isn't
here, so this splits:
- **Now:** require a verified email before *queries run* (not before register).
  Token-based, with the mail transport behind an interface that no-ops in dev,
  the way Sentry/OTel already do.
- **Deferred:** actual SMTP/provider wiring, or hCaptcha — needs an account, so
  it belongs in Track B.

**Files** — `backend/app/api/routes/auth.py:111`, quota/query entry points

**Done when** an unverified org gets a clear 403 on query, dev/test are
unaffected, and the deferred half is written down here rather than forgotten.

### 4. `#28` — Backend coverage gate 70 → 85 (P2)

- [ ] **Merged**

Actual coverage is 90.4%, the gate is 70. Twenty points of real coverage are
unprotected — a regression could delete a third of the suite and CI stays green.

**Scope** — one line: `--cov-fail-under=70` → `85` in `.github/workflows/ci.yml:34`.
85 rather than 90 because `core/billing.py` sits at 82%.

**Files** — `.github/workflows/ci.yml`

**Done when** CI is green at the new gate. Confirm the real number first; this
file's 90.4% is a snapshot.

### 5. `#27` — Frontend coverage gate + remaining components (P2)

- [ ] **Merged**

4 of 12 components tested (`ResultView`, `ChatWindow`, `Signup`, `BillingCard`),
and `npm test` runs with no threshold at all, so coverage can regress silently.

**Scope**
- Add a Vitest coverage threshold to CI — the gate matters more than the tests,
  because without it everything after this can rot.
- Backfill in value order: **`FileUpload` first**, then `Login`, `AdminConsole`,
  `AuditLogs`, `AccountSettings`, `Dashboard`, `Sidebar`, `ErrorBoundary`.

**Files** — `frontend/package.json` (`test` is a bare `vitest run`),
`frontend/vite.config.js`, `.github/workflows/ci.yml`, new test files

**Done when** CI enforces a threshold and `FileUpload` is covered. The remaining
seven components can be follow-up PRs — split them rather than growing one PR.

### 6. `#46` — PDF export truncates at 60 chars (P2)

- [ ] **Merged**

`_safe(val, limit=60)` in `export.py:47` is applied uniformly to the question,
the generated SQL and the result summary. This export is the compliance artifact
handed to an auditor — truncated SQL cannot serve that purpose, and the `WHERE`
clause that changes the meaning of the result is exactly what gets cut. The audit
log already stores the full text; only the PDF discards it.

**Scope** — wrap rather than truncate: ReportLab `Paragraph` cells inside the
`Table`. Keep a generous cap (a few thousand chars) as a runaway guard. Check
pagination still behaves once cells can be tall (`repeatRows=1` is already set).

**Files** — `backend/app/api/routes/export.py:47,59`

**Done when** a known long SQL string round-trips into the PDF intact.
`test_integration.py::test_upload_query_export_flow` asserts only that the bytes
start with `%PDF` — assert content.

### 7. `#47` — EOL runtime watch (P2)

- [ ] **Merged**

Dependabot ignores docker majors by design and structurally cannot see the
`node-version` inputs in `ci.yml` / `e2e.yml`. Node 20 sat three months past EOL
in three places before anyone noticed.

**Scope** — a scheduled workflow that checks the pins against the
`endoflife.date` JSON API (`python`, `nodejs`) and opens an issue when one is
within ~6 months of EOL. This also catches the support window *moving*, which a
calendar reminder does not.

**Files** — new `.github/workflows/`, `.github/dependabot.yml` (records the dates
today: python 3.12 → 2028-10-31, node 24 → 2028-04-30)

**Done when** the workflow runs on schedule and has been proven to fire — test it
against a deliberately stale pin before trusting it.

### 8. `#31` — Billing feature gaps (P2)

- [ ] **Merged**

Proration on plan switches, dunning config, `rows_processed` as metered usage,
invoice history in the UI. Flat-rate subscriptions are all that exists.

**Scope** — explicitly post-launch. Each piece is independent; split into
sub-issues when picked up. Do not start before `#19` proves the money path works
against a real account.

---

## Track B — blocked on something outside this repo

Not deferrable by preference, only by dependency. These are the *highest-risk*
items in the project despite sitting below Track A here.

### `#18` / `#5` — Restore drill + production hours (P0)

- [ ] **Done** (not a PR — an executed drill with recorded numbers)

`scripts/restore.sh` and `DISASTER_RECOVERY.md` exist and have never been
executed. RPO ≤ 15 min / RTO ≤ 1 h are assertions, not measurements. **An
untested backup is not a backup** — the single highest-risk open item.

**Unblocked by** provisioned infra (`deploy/terraform`), per
`GO_LIVE_CHECKLIST.md` steps 1–9.

### `#25` — k6 against real staging (P0)

- [ ] **Done**

Every latency SLO, alert threshold and the E2E's 120s timeout descends from one
dev-laptop run (38.3s cold / 61ms warm).

**Unblocked by** a provisioned cluster with GPU-node Ollama.

**Before running:** set `LLM_CACHE_ENABLED=false` and raise the target's rate
limits, or you measure the cache and the limiter instead of the app. Record which
mode the baseline used.

### `#19` — Stripe test-mode round trip (P0)

- [ ] **Done**

27 unit tests, none touch Stripe. No checkout has completed, no real webhook has
arrived. Confirm a signature-verified `customer.subscription.updated` moves the
plan and a portal cancel drops it.

**Unblocked by** a Stripe account — business verification takes days on their end,
so start that clock early even though the work sits here.

### `#21` (second half) — email/captcha infrastructure

- [ ] **Done**

The deferred half of Track A item 3. Needs an SMTP provider or hCaptcha account.

---

## Not on the queue

- **`#9`** — the roadmap tracking issue. Closes when everything above does.
- **`#5`** business/compliance section (Privacy Policy, ToS, DPA, SOC 2) — real
  work, but not engineering work and not ours to write.
