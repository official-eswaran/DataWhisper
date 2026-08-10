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

- [x] **Merged** — 2026-08-05, PR #63. `run.sh` warms the model into RAM before
  Playwright runs, so the timed query no longer pays model load; `retries`
  dropped to `0` so a cold-path regression goes red instead of being masked;
  redundant `ollama serve &` removed from `e2e.yml`.

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

- [x] **Merged** — 2026-08-06. Queries now require a verified address; the gate
  is per-org keyed on the owner (a per-user check let an unverified owner create
  a member through the admin route and query as them). Off under DEBUG, existing
  accounts grandfathered by the migration. The mail transport is an interface
  with a no-op default — **the SMTP/captcha half is still deferred to Track B.**

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

- [x] **Merged** — 2026-08-02, PR #49. Coverage is 91.15% against the 85 gate.

Actual coverage is 90.4%, the gate is 70. Twenty points of real coverage are
unprotected — a regression could delete a third of the suite and CI stays green.

**Scope** — one line: `--cov-fail-under=70` → `85` in `.github/workflows/ci.yml:34`.
85 rather than 90 because `core/billing.py` sits at 82%.

**Files** — `.github/workflows/ci.yml`

**Done when** CI is green at the new gate. Confirm the real number first; this
file's 90.4% is a snapshot.

### 5. `#27` — Frontend coverage gate + remaining components (P2)

- [x] **Merged** — 2026-08-06. `npm test` is now `vitest run --coverage` with
  thresholds in `vite.config.js`, so the gate applies locally and in CI.
  `FileUpload` covered (16 tests, 94 total). **The remaining seven components
  are still uncovered** — follow-up PRs, in the value order below.

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

- [x] **Merged** — 2026-08-03, PR #51 (`kiran` branch, landed outside the
  session that wrote this checklist). Cells are ReportLab `Paragraph`s that wrap
  to the column; the 60-char cap became a 4000-char runaway guard.

### 7. `#47` — EOL runtime watch (P2)

- [x] **Merged** — 2026-08-06. `scripts/check_eol.py` + monthly `eol-watch.yml`.
  Parses the pins out of the real files (a re-declared list goes stale silently)
  and fails the job rather than reporting all-clear if a pin stops matching.
  Proven to fire against stale pins in `tests/test_eol_watch.py`.

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

### 7a. Accuracy defects found by the eval (#58, #59, #60, #61)

- [x] **#61** — merged 2026-08-04 (`4c68e7c`)
- [x] **#58** — merged 2026-08-06: `distinct` 60% → **100%**
- [ ] **#59** — attempted, measured, **reverted**. Still open.
- [ ] **#60** — attempted, measured, **reverted**. Still open.

| issue | defect | severity | state |
|---|---|---|---|
| #61 | Dates are `TIMESTAMP_NS`, model emits `LIKE '2021%'` — **and the self-heal never runs for bind errors** | P1 | fixed |
| #60 | "How many laptops sold" counts orders (3) instead of summing units (11) | P2 | **open** |
| #59 | "Which product is cheapest" returns the price, not the product | P2 | **open** |
| #58 | "List all the regions" omits `DISTINCT` — 25 rows instead of 4 | P2 | fixed |

**#58 — what worked.** The prompt route was tried first, being cheapest, and
rejected on measurement: a "use DISTINCT for list-the-X" rule fixed
`sales_distinct_regions` and broke `sales_product_variety`
(`COUNT(DISTINCT product)`), leaving the category at 60% exactly where it
started. The fix that shipped is deterministic
(`sql_repair.add_distinct_for_value_listing`), and unlike #52's it must read
the question — `SELECT region FROM t` is *correct* for "show every order's
region" and wrong only for "which regions exist". The AST guard is
correspondingly narrow: one plain column, one base table, no modifiers at all
(which excludes already-DISTINCT, `ORDER BY` and `LIMIT` in a single check).

**#59 and #60 — what didn't, and why it's written down.** Both issues
recommended a prompt rule. Both were written as recommended, measured with
`--repeat 3`, and reverted:

| attempt | target category | collateral |
|---|---|---|
| #59, rule 10a naming the superlative vocabulary | `sales_cheapest_product` still failed | `ranking` **87.5% → 62.5%** |
| #60, rule 8a distinguishing units from records | `filter` 91% → 100% ✅ | `ranking` **87.5% → 75%** |

For #59 the 3B model copied the concrete example verbatim — including its `ASC`
direction on a "highest" question — and projected the example's second column,
breaking `sales_most_expensive_product` and `emp_top5_salary`, both of which had
been passing 3/3. Rewriting the rule abstractly (no copyable query, explicit
DESC/ASC mapping, "project ONLY the entity") still measured 62.5%.

For #60 the rule genuinely fixed its target — but the three categories together
scored 93/99 with it and 93/99 without. It moves failures rather than removing
them.

**This is the third independent confirmation of the #52 lesson**, and the first
where a rule that provably fixed its own case was still not worth shipping.
Anyone picking these up: run the *whole* eval, and compare category totals
rather than the headline, which is noisy enough (86.0–91.2 observed) to hide a
12-point category swing.

**Do not** re-attempt either of these with another prompt edit without reading
the two rows above first.

**#61 is the one to read first.** Half of it is not an accuracy bug at all: the
self-heal path in `pipeline.py` is unreachable for the failure it was written to
catch, because `validate_and_fix_sql`'s `EXPLAIN` check rejects bind errors and
returns early. That is a structural finding, not a prompt tweak.

**Before fixing any of these, read the #52 lesson** in the note above: prompt
edits regressed unrelated categories, and only a full `--repeat 3` run catches
that. The floor is 80; the current baseline is 88.9%.

### 7b. `#69` — Date period expressions become wrong boundaries (P2)

- [x] **Merged** — 2026-08-06. `date` **7/15 → 13/15**, overall 87.1% → **90.6%**,
  and every other category unchanged. Third deterministic repair
  (`repair_date_period_bounds`). The two residual date failures are model
  non-determinism (an invented upper bound; a `LIKE '%03-2024%'`), not this
  defect — the repair correctly declines both.

`date` is now the largest category gap (**7/15**), having overtaken `distinct`
when #58 landed. All three failures are one defect: a period expression becomes
a wrong boundary instead of a range — "in March 2024" and "in 2021" collapse to
an equality against the first day, "before 2020" becomes `< '2020-12-31'` and
admits the entire year it is meant to exclude. Each returns a plausible,
silently wrong number.

**Two dead ends already ruled out** — don't re-derive them. It is *not* #61
(dates typed `TIMESTAMP_NS`; genuinely fixed, and this SQL binds and runs), and
it is *not* eval strictness over an extra projected column (`subset_ok=True`
means the comparator permutes and matches on one column; the failure is row
count).

**Try a deterministic repair first.** This is a better candidate than #58 was:
the correct half-open range is *derivable* from the period in the question,
where #59's entity column was a guess. Prompt rules come second, and only
validated on the full eval — see 7a for what happens otherwise.

`sales_q1_orders` and `sales_h2_orders` pass 3/3 and phrase their range
explicitly; they are what a clumsy fix breaks.

### 7d. `#73` — Per-group questions answered with a single scalar (P2)

- [x] **Merged** — 2026-08-07. `group_by` 22/27 → **27/27**. The adjacent defect
  to #52: there the GROUP BY existed and the key was missing from the
  projection; here there was no GROUP BY at all, so `add_missing_group_keys`
  had nothing to key off. Fourth deterministic repair.

  Attribution matters — the round measured 94.7% overall (+7 attempts), but only
  **+5 are this repair**; the other +2 are two flaky `date` cases landing well,
  which it cannot influence.

### 7e. `#74` — Aggregate thresholds become row-level filters (P2)

- [x] **Merged** — 2026-08-08. `having` **0/3 → 3/3**, overall 94.7% → **96.5%**,
  with every other category identical attempt-for-attempt. Fifth deterministic
  repair, and the cleanest attribution of any round: there is no flaky-case
  contribution to subtract.

  **It was expected to resist this approach, and the reason it didn't is worth
  carrying forward.** The "which aggregate?" inference really is a guess, and
  the repair declines rather than making it — only accumulation vocabulary
  fires, and "averaged"/"highest"/"per" stop it dead. But the inference thought
  to be equally hopeless — *is this an aggregate threshold at all?* — turned out
  to be derivable from the **data**: "which orders had a total above 100000"
  (correct as `WHERE`) and "which products generated more than 200000" (wrong as
  `WHERE`) are indistinguishable by phrasing, and separated cleanly by whether
  the projected column repeats across rows. `COUNT(DISTINCT x) < COUNT(*)`.

  So the table is a third source of derivable truth alongside the AST and the
  question text, and it is the one nobody had looked at. #59 and #60 were
  grouped with this issue on the assumption that all three were the same kind of
  unanswerable; that assumption is now only proven for two of them.

  **Wired into both query paths.** It went into `pipeline.py` first and *not*
  the SSE route in `query.py` — which the eval cannot catch, because the eval
  scores the pipeline while every real user goes through the stream.
  `test_sql_repair.py` now asserts the two call sites apply the same set of
  repairs, structurally, so this cannot recur silently.

### 7c. `#70` — Seven frontend components still untested (P2)

- [ ] **Merged**

Follow-up to #27, which delivered the gate (the part that mattered) plus
`FileUpload`. `Login`, `AdminConsole`, `AuditLogs`, `AccountSettings`,
`Dashboard`, `Sidebar`, `ErrorBoundary` remain. `AdminConsole`, `AuditLogs` and
`AccountSettings` manipulate other people's accounts and audit data, so they
carry the most risk per untested line.

**One component per PR**, and **raise the thresholds in `vite.config.js` as each
lands** — a gate that never moves is decoration, not a ratchet.

**Progress**

- [x] **`Login`** — 2026-08-09. 13 tests, `Login.jsx` to **100%** on all four
  metrics; suite 94 → 107. Gate raised 60/85/50/60 → **64/90/56/64**. The #77
  fix that followed took it to 22 tests, suite 116, gate **64/91/57/64**.
- [x] **`AdminConsole`** — 2026-08-10. 36 tests, to **100%** on all four
  metrics; suite 116 → 152. Gate **64/91/57/64 → 73/92/62/73**. The highest-risk
  component in the list, so the tests lean on the properties that matter rather
  than on render output: the owner row has no deactivate button, the toggle
  sends the *inverse* of the current state and the right username, `isOwner`
  comes from the session role and not from the rendered table, and a billing
  outage hides the card without costing an admin their team management.
- [ ] `AuditLogs` · `AccountSettings` · `Dashboard` · `Sidebar` · `ErrorBoundary`

**Mutation-test the tests, not just the component — a helper's defaults can
swallow the case.** `AdminConsole`'s "user list missing its array" test was
written as `mockHappyPath({ users: undefined })` and tested nothing: an explicit
`undefined` triggers the parameter default, so the helper substituted the full
list and the `|| []` guard could be deleted with the test still green. Only the
mutation run found it. If a test passes a falsy or absent value *through a
helper*, check the helper is not filling it back in.

**"Raise the thresholds" needs a sharper test than it sounds — read this before
the next one.** Raising them by the "few points under measured" rule the #27
gate used does **not** protect the component you just covered: one component is
worth ~0.6 points of statements, and the old gate had ~3.7 points of slack, so
the first attempt at this PR passed with all 13 new tests deleted. The gate was
decoration for precisely the coverage it had just gained.

The check that actually means something, and the one to repeat every time:

```bash
rm src/components/<the component you just covered>.test.jsx
npm test          # must exit NON-ZERO
git checkout -- . # put it back
```

Thresholds now sit ~0.4 under measured rather than a few points. v8 coverage is
deterministic, so tight thresholds do not flake — they fail only on a real drop.

**Defects found while testing go in their own issue, not into the tests.**
`Login` turned up one (**#77**: every backend failure — lockout, disabled
account, rate limit — rendered as "Invalid credentials"). Asserting the current
message would have promoted the bug to a specification, so the coverage PR
carried a clearly-labelled characterization test, mutation-verified to go red
the moment the mapping was fixed.

**That loop closed the same day** — #77 was fixed in the next PR and the
characterization test was replaced by per-status assertions, including the
inverse of the original (`seen.size` 1 → 4). Worth repeating for the remaining
six: the pattern costs one extra PR and keeps a found defect from being either
silently blessed or silently forgotten.

The fix also turned up something the issue had not: **`429` has two unrelated
causes.** The login route returns it for a per-account lockout, slowapi returns
it for a per-IP rate limit, and they ask different things of the user. Only the
response body separates them — so the mapping passes `detail` through for 401
and 429 (where it carries the attempt count, the lockout duration, and that
distinction), owns the wording for 403 (whose detail is accurate but not
actionable), and falls back rather than echoing anything else, since a 500's
detail is not written for end users.

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
