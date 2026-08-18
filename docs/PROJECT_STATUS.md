# Project status & how to resume

**Last updated:** 2026-08-18
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

> **The single most useful thing in this file, if you read nothing else.** On
> 2026-08-13 the four P0/P1 items blocked on infrastructure were each given the
> part that *could* be done from a dev machine: the backup scripts, the money
> path, the load-test target check, and the mail transport all now execute
> instead of being described. The first of those drills found that **every
> Stripe webhook had returned 500 since billing shipped** (#93) — no customer
> could ever have been upgraded — past 27 passing billing tests, because those
> tests stubbed the exact function that failed.
>
> The pattern behind it, and behind two more near-misses the same day: **a check
> that cannot fail is worse than no check, because it is believed.** Before
> trusting anything green in this repo, ask what it would take for it to go red,
> and then do that.
>
> **2026-08-16 → 18 made the same point from the other side.** Frontend coverage
> went from 92.8% to every file at 100% statements and lines, and the three
> defects that turned up were all in code with a green suite over it:
>
> * `App.jsx` booted through `.then().finally()` with no `.catch` — and
>   `.finally` re-throws. The only thing between a failed boot and an unhandled
>   rejection was a `.catch` inside `services/api` that `App.jsx` cannot see.
> * `ResultView` took the whole app to the `ErrorBoundary` fallback on a text
>   column, because `chart_advisor` asks pandas about the whole column while the
>   frontend reads `typeof` off the first row — one leading `NULL` splits them.
> * CI went red on `main` with **no source change at all**: the backend image was
>   never `apt-get upgrade`d, so it inherited whatever was current the day
>   `python:3.12-slim` was last pushed.
>
> **None of the three was findable by reading the file it lived in.** Two were
> couplings across a boundary that nothing recorded, and the third was not in the
> source at all. Coverage did not find them either — *writing the tests* did.

| Area | State |
|------|-------|
| Backend tests | **727 passing**, `ruff` clean, **91%** coverage, gate **85** (#28) |
| NL2SQL accuracy | **98.2%** (3 repeats, cache off); 6 of 8 at 100%, no case failing every run |
| Frontend tests | **403 passing**; every file at 100% statements/lines; gate **99.4/97.7/98.7/99.4** (#27, #70, #21) |
| Build/runtime | Vite build OK, Node 24 (LTS), dependency audit clean |
| E2E | Runs in GitHub Actions ✅; passes on attempt 1 since PR #63 (#45 fixed) |
| Migrations | Head is `b92c4d17ae03` (email verification, #21) |
| Dependencies | Dependabot active; majors gated for pip/npm/docker |

> **Dependency advisories expire on their own.** Three CI gates have now gone
> red with no code change involved — sentry-sdk (PYSEC-2026-1917), react-router
> (GHSA-qwww-vcr4-c8h2) and, on 2026-08-17, **CVE-2026-53615 in the backend's
> base image**. All three are handled, and Dependabot now watches the first two,
> but treat any "clean" claim above as a snapshot: run the verification block
> below rather than believing it.
>
> **The third one is a different kind, and worth its own line.** Dependabot
> cannot see it: nothing in `requirements.txt` was involved. `python:3.12-slim`
> is a snapshot of the day it was built, Debian keeps shipping fixes to it
> afterwards, and the backend Dockerfile installed `curl` without ever running
> `apt-get upgrade` — so the image inherited whatever was current when the tag
> was last pushed. The frontend image has run `apk upgrade` since it was
> written, with a comment explaining exactly this; **the reasoning simply never
> crossed to the other Dockerfile.** It went red between #103's PR run (green)
> and the merge run on `main` an hour later, which is the clearest illustration
> available that a green check is a measurement with a timestamp on it.

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

### Session 2026-08-16 → 18

Six PRs, in this order: **#103** signup captcha (#21) · **#105** base-image CVE
(fixing a `main` that was already red) · **#104** `services/api.js` · **#107**
`App.jsx` · **#108** `ResultView.jsx` · this file. Frontend suite **287 → 403**,
gate **92.4/93.2/73.9/92.4 → 99.4/97.7/98.7/99.4**, ratcheted once per PR and
verified to bite each time. Detail in the three dated tables below.

**What changed strategically:** #21 is now code-complete, which means **every
part of every open issue that can be done without provisioning something is
done.** This file has asserted that since 2026-08-13 and it was not true then —
see the note under "If you pick this up next". It is true now. The next move on
#18, #19, #25 and #31 belongs to whoever can create a Stripe account, an
SMTP/captcha account, or a staging environment.

### Shipped 2026-08-18

| Issue | What |
|-------|------|
| — | **`ResultView.jsx` covered — 88.7% → 100% statements and lines.** 18 tests, suite 385 → 403; overall **99.8 / 98.13 / 99.13 / 99.8**. Gate **97.4/95.9/97/97.4 → 99.4/97.7/98.7/99.4**, verified to bite. 22 of 22 mutants killed. **Every file in `src/` is now at 100% statements and lines**, which closes the coverage arc that started with #27 on 08-06 and ran through #70. |
| — | **The PNG export had never been executed by a test.** The old suite asserted the download *button* existed and never pressed it, so serialise → size a canvas → paint → hand over a file ran nowhere. jsdom implements none of canvas, blob URLs or image decoding, so each is replaced with a recorder and what is asserted is the sequence. Four properties came out of it that a render test cannot see: the background is filled **before** the chart is drawn (reversed, the export is a plain dark rectangle), the object URL is **revoked** (otherwise one leak per download, invisible forever), the canvas is enlarged by `devicePixelRatio` **and** the context scaled to match (otherwise soft on every modern laptop, or cropped to a quarter frame), and a missing `devicePixelRatio` falls back to 1 (otherwise a NaN-sized canvas exports a blank file). |
| — | **A latent crash in the histogram binner, and the reason it was reachable at all.** `buildHistogramData` filtered `v != null`, so a text value survived, `Math.min` returned `NaN`, and `buckets[NaN].count++` threw — which `ErrorBoundary` turns into the whole app being replaced by its fallback. It is latent because `chart_advisor` only labels a result `histogram` when it sees a numeric column. **But the two ends do not agree on what "numeric" means**: the backend asks pandas about the whole column, while `ResultView` reads `typeof` off the *first row only*. One leading `NULL` is enough to make them disagree, and that gap is the crack. Fixed by filtering to finite numbers; the disagreement itself is still there and is worth remembering. |
| — | **Four dead branches deleted rather than tested.** `getAvailableTypes` and `getSortedData` each re-checked `!data`, which the component guarantees ~100 lines above; `renderChart`'s switch had `case "single_value"` and `case "table"` that the JSX routes away before the function is called. `default` is the opposite — genuinely live, because the backend picks the type and the two deploy separately, so a shape this build has never heard of must still render *something*. It now has a test. **A second copy of a check that cannot fail reads like the case is possible, and it is not.** |
| — | **A failing assertion can arrive as a nonsense error with no stack.** `expect(container.querySelector("svg")).toBeNull()` was simply wrong — the toolbar's react-icons are SVGs too — but the failure surfaced as `TypeError: Cannot read properties of undefined (reading 'name')` with no stack and no line, because Vitest's DOM serialiser crashed while formatting the element it was about to print. It reads exactly like a crash inside the component. **If a DOM assertion fails with an unrelated `TypeError` and no stack, suspect the assertion before the code.** |

### Shipped 2026-08-17

| Issue | What |
|-------|------|
| — | **`App.jsx` covered — 87.3% → 100% on all four metrics.** 16 tests, suite 371 → 385; overall **97.84 / 96.36 / 97.39 / 97.84**. Gate **97/95.4/95.2/97 → 97.4/95.9/97/97.4**, verified to bite. 17 of 20 mutants killed. What had no cover was the part no component test can reach: the boot gate (#22) and the login/logout transitions either side of it. |
| — | **`App.jsx`'s boot had no error handling, and the tests found it.** The effect chained `.then().finally()` with no `.catch` — and `.finally` does not handle a rejection, it re-throws. The only thing standing between a failed boot and an unhandled rejection was a `.catch` inside `services/api` that `App.jsx` cannot see and no test asserted. Latent rather than live, because that catch does exist today — but it is a coupling across a module boundary that nothing recorded, which is how it would have broken. Fixed in the same PR rather than filed, a deliberate deviation from the #77/#82 convention: the alternative was shipping a test file that leaves an unhandled error in every CI run. |
| — | **A test written for that PR was vacuous, and deleting it is the finding.** It asserted that unmounting mid-boot produced no `console.error`, to cover the effect's `active` guard — and it passed with the guard deleted. **React 18 removed that warning**, so a state update on an unmounted component is a silent no-op with nothing left to observe. The guard stays as the standard idiom (it becomes load-bearing the moment that effect gains a dependency) and both files now carry a note so it is not written again. The three surviving mutants are all this flag: unobservable by construction rather than untested. **This is the mutation-testing rule catching a test written by someone who had just written the rule down.** |
| — | **`services/api.js` covered — 58.07% → 100% on all four metrics.** 55 tests, suite 314 → 371; overall **97.45 / 95.81 / 95.65 / 97.45**, and `branches` is back above where it stood before 08-16. Gate **92.4/93.2/73.9/92.4 → 97/95.4/95.2/97**, verified to bite on all four. 27 of 27 mutants killed. |
| — | **"Thin wrappers exercised through the components that call them" was half true, and the wrong half was load-bearing.** That description — this file's, repeated three times — covers about half the file. The other half is the session machinery: the token store, the single-flight refresh, the retry-once 401 interceptor, and the SSE parser. **None of it had a single assertion**, and two of its properties cannot be reached from a component test at all: that concurrent 401s share *one* refresh call (one per 401 rotates the refresh cookie repeatedly and logs the user out), and that an SSE event split across two network chunks survives the boundary (a lost `done` event hangs the UI on "thinking" with the answer already delivered). |
| — | **Three mutants survived the first pass, and each named a property the tests only appeared to hold.** Worth reading, because all three are the same mistake: an assertion that passes for a reason other than the one intended. (1) Removing `tokens.clear()` from `redirectToLogin` changed nothing, because the refresh's own catch already cleared them — the case that needs it is a *successful* refresh whose replay is still rejected, and nothing exercised it. (2) Removing the `if (!res.ok) throw` from the refresh changed nothing, because the mocked error body was empty — the real property is that a non-OK response must not install an `access_token` carried in its own body. (3) Removing the `data: ` prefix check changed nothing, because the malformed lines tested happened to fail `JSON.parse` anyway — but `event: {"stage":"done"}` is a legal SSE line whose tail *does* parse, so a keep-alive could end the query early. All three now have tests that fail without the code. |

### Shipped 2026-08-16

| Issue | What |
|-------|------|
| #21 | **The signup captcha is implemented — only credentials are missing now.** The last piece of #21 that did not need an account, and the one the file has called "entirely unstarted" since 08-06. `CAPTCHA_SECRET` unset is still a no-op, so dev, tests and self-hosted installs are unchanged; set a key pair and `/api/auth/register` will not create an org without a solved challenge. hCaptcha and Turnstile share one implementation. **It fails closed**: a provider that times out gets a 503 and no organization, because a control that opens when it cannot be evaluated is not a control — and that state is precisely what an attacker would manufacture. A *failed* challenge is a 400, kept distinct because the two ask different things of the user. 19 of 19 mutants killed. **No challenge has ever been solved against a real provider**; `GO_LIVE_CHECKLIST.md` §3 has the checks. |
| #21 | **`CaptchaWidget` covered — the thirteenth component, 100% on all four metrics.** 18 tests, suite 287 → 314. The two that earned their place: a failed *signup* (not a failed captcha) must reset the widget, because the token is single-use and a user fixing a duplicate username would otherwise resubmit a spent one and be told the captcha failed; and a script that never loads says so, rather than leaving an empty box above a permanently disabled button — ad blockers block these routinely. |
| — | **The frontend `branches` gate went DOWN, and nothing regressed.** 94.36 → 93.65 while statements, functions and lines all rose. v8 reports branch data only for functions it actually enters, so `services/api.js` had **6** counted branches (5 covered, 83.33%); adding one exported function that tests do execute made v8 report the file's other blocks too — **17** counted, 9 covered, 52.94%. Eleven uncovered branches appeared that had been there all along. **A branch percentage is only comparable between runs over identical code**, which makes it the weakest of the four as a ratchet. The other three were raised to ~0.4 under measured as usual, and the gate was verified to bite. |

### Shipped 2026-08-13

| Issue | What |
|-------|------|
| #31 | **Invoice history shipped** — the one part of #31 that does not depend on billing having run for real. `GET /api/billing/invoices` (owner-only, a nine-field projection of Stripe's ~100) plus a table in `BillingCard`. A failed load says so rather than rendering as "no invoices", per #82. 10 of 10 mutants killed, and one of them found a real bug first: the formatter divided by 100 unconditionally, which prints ¥29 for a ¥2,900 invoice. `Intl` knows each currency's decimal places; a hand-maintained list of zero-decimal currencies would have gone stale. **Proration, dunning and metered usage remain open** — all three need real subscription behaviour to observe. |
| #21 | **The mail transport is implemented — only credentials are missing now.** `SMTP_HOST` unset is still a no-op that logs the link, so dev, tests and self-hosted installs are unchanged; set it and mail is actually sent over STARTTLS or implicit TLS. **The mailer refuses to authenticate over an unencrypted connection**, so a misconfiguration stops mail rather than leaking the SMTP password, and the verification link is no longer logged once a transport exists — it is a bearer credential, and the log was only ever the delivery mechanism in dev. 13 of 13 mutants killed. **It has never sent to a real server**; `GO_LIVE_CHECKLIST.md` has the one-command check. |
| #25 | **A staging load test can no longer silently measure the wrong thing.** `loadtest/preflight.py` refuses the run when the target would throttle the generator, serve the queries from cache under `CACHE_MODE=cold`, or run out of monthly quota part-way through. All three were already documented as prose to remember; this fails instead. Verified in both directions against a local target — production-default limits block it, raised limits pass. **#25 itself is untouched:** the 8s p95 is still a laptop number, because there is still no staging to point it at. |
| #19 | **The money path is now executed on every PR** — as far as it can be without an account. `scripts/stripe_drill.sh` signs Stripe-shaped events with a real HMAC and posts them to a running app (no Stripe account needed), and runs the SDK's outbound calls against stripe-mock, whose OpenAPI validation is the first thing other than our own monkeypatches to look at those requests. **It found #93 immediately.** **#19 stays open**: no browser has completed a Checkout and no event Stripe generated has arrived. `BILLING.md` carries the checklist for when an account exists. |
| #93 | **Every real Stripe webhook was 500ing, and had been since billing shipped.** `verify_event` converted the SDK's `Event` with `to_dict_recursive()`, which v15 does not have — and `isinstance(event, dict)` is False there, so *every* verified event took that branch. No subscription event could ever apply: a customer could pay and stay on `free`, and a cancellation never downgraded. Found by the #19 drill, which is the first thing that ever delivered a genuinely signed event. The suite's 27 billing tests all enter *after* the broken line, because they stub the function that fails. |
| #18 | **The backup scripts are now executed, on every PR.** `scripts/restore_drill.sh` runs `backup.sh` → destroy → `restore.sh` → verify against a throwaway database, in both the Postgres and SQLite modes, and CI grew a Postgres service to run the path production actually uses. Until this, neither script had ever run — the runbook's own "an untested backup is not a backup" applied to the runbook. **It is not the production drill** and does not touch PITR, cross-region recovery or the real RTO; what it removes is "the scripts might not work" from the list of things that could go wrong during one. |
| #70 | **`ErrorBoundary` covered — #70 closed.** Last of the seven and of the twelve. It sat at 94.44% because two tests lived in `App.test.jsx` from before it had a file of its own; they moved here, and the part nobody had reached was `handleReload` — the only way out of the fallback. 10 tests, component to **100%** on all four metrics, suite 262 → 270; overall **91.59%**. Gate **91/94/70/91 → 91/94/71/91**, verified to bite. 12 of 12 mutants killed. |
| #70 | **`Sidebar` covered** — sixth of the seven, and the last with any logic in it. The navigation behind the login wall, and the only route to the admin console. 29 tests, component to **100%** on all four metrics, suite 233 → 262; overall **91.43%**. Gate **88/94/68/88 → 91/94/70/91**, verified to bite. 21 of 21 mutants killed, including "the admin item is shown to everyone" and three separate ways of getting `aria-current` wrong. |

### Shipped 2026-08-12

| Issue | What |
|-------|------|
| #60 | **Units-vs-rows repair.** `filter` 30/33 → **33/33**; `sales_laptop_units` 0/3 → 3/3. Seventh deterministic repair, and the last eval case that failed every run. **The headline stayed at 98.2%** — the +3 attempts were offset by two flaky cases losing 3 between them, neither reachable by this repair. `sales_order_count` and `sales_total_quantity`, the two a clumsy fix breaks, stayed 3/3. |
| #59 | **Superlative repair.** `ranking` 21/24 → **24/24**; `sales_cheapest_product` 0/3 → 3/3 with `sales_most_expensive_product` — the case a clumsy fix breaks — still 3/3. Sixth deterministic repair, and the first to overturn a "not derivable" verdict recorded in this file. **Read the attribution:** the round measured 98.2% against a 95.3% control taken on the same machine minutes earlier, but only +3 of the +5 attempts are the repair; the other +2 are the flaky `sales_march_revenue` landing 3/3 where the control got 1/3. |

### Shipped 2026-08-11

| Issue | What |
|-------|------|
| #70 | **`Dashboard` covered** — fifth of the seven. The composition root: tab routing, the upload→chat hand-off, and the Stripe return path. 24 tests, component to **100%** on all four metrics, suite 209 → 233; overall **88.59%**. Gate **85/93/66/85 → 88/94/68/88**. 21 of 22 mutants killed; the survivor is documented redundancy (stripping the `?status=` marker, not the effect's empty dep array, is what stops the toast replaying). |
| #70 | **`AccountSettings` covered** — fourth of the seven, and the last of the destructive ones: GDPR export, account deletion, and the only control in the product that deletes an entire organization. 26 tests, component to **100%** on all four metrics, suite 183 → 209; overall **85.43%**. Gate **80/93/64/80 → 85/93/66/85**. 21 of 21 mutants killed, including both `window.confirm` gates and "the org control is shown to everyone". |

### Shipped 2026-08-10

| Issue | What |
|-------|------|
| #70 | **`AuditLogs` covered** — third of the seven. 25 tests, component to **100%** on all four metrics, suite 152 → 177; overall coverage now **80.31%**. Gate **73/92/62/73 → 80/93/64/80**. 25 of 25 mutants killed. |
| #82 | **A failed audit-log fetch no longer reads as an empty trail.** The page said "No audit logs yet. Start asking questions!" when the request had simply failed — for an *audit* trail, a wrong answer to the question the page exists to answer. Found while writing #70's tests, filed rather than folded in, fixed the next PR. Hiding the stat tiles matters as much as the copy: "Total Queries 0" asserts emptiness just as plainly. Suite 177 → 183. |
| #70 | **`AdminConsole` covered** — second of the seven, and the highest-risk one: the only place a person's action changes someone else's account. 36 tests, component to **100%** on all four metrics, suite 116 → 152. Gate **64/91/57/64 → 73/92/62/73**, verified to bite. 33 of 33 mutants killed, including "the owner row has a deactivate button" and "the toggle sends the current state instead of its inverse". |

### Shipped 2026-08-09

| Issue | What |
|-------|------|
| #70 | **`Login` covered** — first of the seven. 13 tests, component to 100% on all four metrics, suite 94 → 107. Gate ratcheted 60/85/50/60 → **64/90/56/64**, and *verified to bite*: the first attempt raised it by the old "few points under" rule and still passed with all 13 new tests deleted. Six components remain. |
| #77 | **`Login` failure messages fixed.** Lockout, disabled-account and rate-limit outcomes each say what actually happened, instead of all rendering as "Invalid credentials". Found while writing #70's tests, filed rather than folded in, then fixed straight after — and the characterization test that pinned the defect was replaced by per-status assertions. Note `429` has two unrelated causes (account lockout vs slowapi per-IP limit) that only the response body tells apart. Suite 107 → 116; gate 64/90/56/64 → **64/91/57/64**. |

### Shipped 2026-08-08

| Issue | What |
|-------|------|
| #74 | **Aggregate-threshold repair.** `having` 0/3 → **3/3** — the last category at zero. Overall 94.7% → **96.5%**, with every other category unmoved attempt-for-attempt. Fifth deterministic repair. The "which aggregate?" guess is handled by declining on any non-SUM vocabulary; the "is this even an aggregate threshold?" question turned out to be answerable from the *data* (does the projected column repeat?) rather than the phrasing. See "Known gaps". |
| — | **Both query paths now provably apply the same repairs.** #74 was first wired into `pipeline.py` only — the eval would have scored it green while every real user, who goes through the SSE stream in `query.py`, still saw the bug. `test_sql_repair.py` now asserts the two call sites match, structurally, so the next repair is covered the day it is written. |
| #59, #60 | **Attempted, measured, reverted — still open.** See the note below. (Both fixed 2026-08-12, as rewrites rather than prompt rules.) |

### Shipped 2026-08-06

| Issue | What |
|-------|------|
| #21 | **Email verification gates queries.** Per-org, keyed on the owner — a per-user check let an unverified owner create a member via the admin route and query as them. Off under DEBUG; existing accounts grandfathered by the migration. Mail transport is a no-op interface (SMTP/captcha still deferred). |
| #47 | **EOL-runtime watch.** `scripts/check_eol.py` + monthly `eol-watch.yml`, checking `endoflife.date`. Parses pins from the real files and *fails* rather than reporting all-clear if one stops matching. |
| #27 | **Frontend coverage gate.** `npm test` is now `vitest run --coverage`; `FileUpload` covered. |
| #58 | **DISTINCT repair.** `distinct` 60% → **100%**, deterministic. |
| #69 | **Date period repair.** `date` 7/15 → **13/15**; overall 87.1% → **90.6%** with no other category moving. |
| #73 | **Missing-GROUP-BY repair.** `group_by` 22/27 → **27/27**; overall **94.7%**. Five categories now at 100%. |

### Shipped 2026-08-02

| PR | What |
|----|------|
| #49 | Coverage gate 70 → 85 (#28); `ISSUE_CHECKLIST.md` as the work queue |
| — | **NL2SQL accuracy eval** (#16): `backend/evals/`, 57 cases, first measured baseline **78.4%** |
| — | **GROUP BY key repair** (#52): deterministic AST rewrite; accuracy **78.4% → 88.9%** |

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
python3 -m pytest                       # expect 727 passed
python3 -m ruff check app tests evals   # expect clean
python3 -m pip_audit -r requirements.txt --strict   # --strict is what CI runs

# Frontend (from frontend/)
npm ci
npm run build                           # outputs to build/
npm test                                # expect 403 passed; enforces coverage thresholds
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
- **The signup captcha fails closed, and that is deliberate (issue #21).**
  `app/core/captcha.py` is off unless `CAPTCHA_SECRET` is set — keyed on the
  *secret*, because a site key alone would draw a widget whose answer nobody
  checks. When it is on and the provider cannot be reached, `/register` returns
  **503 and creates nothing**; a rejected challenge returns **400**. The two are
  kept apart because they ask different things of the user, and the 503 is the
  half that matters: an abuse control that opens when it cannot be evaluated is
  not a control, and "the provider is unreachable" is a state an attacker can
  manufacture. Set both keys or neither — the startup check warns about either
  half alone, and the secret-only case closes signup completely. hCaptcha and
  Turnstile share the siteverify contract, so `PROVIDERS` is a table rather than
  two code paths. The SPA gets the site key from `GET /api/auth/signup-config`
  instead of a build-time var, and holds its own provider → script map so a
  misconfigured API cannot name JavaScript for it to load.
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
- ~~**Open signup has no verification (#21)**~~ — **the code is complete as of
  2026-08-16; only accounts are missing.** Queries require a confirmed address
  (`REQUIRE_EMAIL_VERIFICATION`, auto-on when `DEBUG=false`, gated per-org on
  the owner, 08-06); mail is really sent when `SMTP_HOST` is set (08-13); and a
  configured captcha is really verified (08-16). All three default to off, so a
  self-hosted install is unchanged by any of them.

  **Verification and captcha are not alternatives — they price different
  things.** A verified address puts a cost on each *identity*; a captcha puts
  one on each *attempt*. The abuse path is "make another org", which needs both
  a fresh address and a fresh attempt, so a deployment with public signup should
  run both. Neither replaces `RATE_LIMIT_REGISTER`, which is the only one of the
  three that costs an attacker nothing to defeat but also costs nothing to run.

  **Still open, and it is the same blocker for both halves:** nothing has sent
  to a real mail server, and no challenge has been solved against a real
  provider.
- ~~**No frontend coverage gate (#27)**~~ — **gate fixed** 2026-08-06:
  `npm test` is `vitest run --coverage` with thresholds in `vite.config.js`,
  enforced locally and in CI. Now **64 statements / 90 branches / 56 functions /
  64 lines**. The coverage config needs its explicit `include`: without it the
  v8 provider also measures `build/assets/*.js` and reports a number ~10 points
  below the truth.

  **#70 is closed as of 2026-08-13: all twelve components are covered**, each
  to 100% on all four metrics — `Login` (08-09), `AdminConsole` and `AuditLogs`
  (08-10), `AccountSettings` and `Dashboard` (08-11), `Sidebar` and
  `ErrorBoundary` (08-13). Suite 94 → 270, overall coverage 63.72% →
  **91.59%**.

  **`CaptchaWidget` (#21, 08-16) makes it thirteen**, on the same terms: 100%
  on all four metrics, 18 tests, suite 287 → 314.

  **`services/api.js` was covered on 08-17** — 58.07% → **100%**, 55 tests,
  suite 314 → 371. It had been dismissed here three times as "mostly thin
  wrappers exercised through the components that call them"; that was true of
  about half of it, and the other half was the session machinery — the token
  store, the single-flight refresh, the retry-once 401 interceptor and the SSE
  parser — with no assertion anywhere in the suite. See the 08-17 entry above
  for the three mutants that survived the first pass; each one exposed a test
  that passed for a reason other than the one intended.

  **`App.jsx` was covered the same day** — 87.3% → **100%**, 16 tests, suite
  371 → 385. It was never in #70's scope, and the uncovered part was the boot
  gate and the login/logout transitions around it: the session lifecycle, which
  no component test can reach because no component owns it. See the 08-17
  entries above — the tests found a missing `.catch` on the boot chain, and one
  of the tests written for it turned out to assert nothing.

  **`ResultView.jsx` closed it out on 2026-08-18** — 88.7% → **100%**
  statements and lines, 18 tests, suite 385 → 403. **Every file in `src/` is
  now at 100% statements and lines**, which ends the arc that began with #27 on
  08-06.

  **What is left is not coverage.** The residual branches and functions are
  unreachable rather than untested — a ref guard whose null case cannot occur
  while the button that reaches it exists, and recharts render props that jsdom
  never invokes because it produces no layout measurements. They are named in
  `vite.config.js` so the next person does not spend an afternoon on them.

  **A raised gate is not automatically a ratchet, and this one wasn't.** Raising
  the thresholds by the original "a few points under measured" rule left the
  suite passing with all 13 of Login's new tests deleted — one component is
  worth ~0.6 points of statements, and the slack was ~3.7. Whatever the gate
  claimed, the coverage it had just gained was free to delete again. The
  thresholds now sit ~0.4 under measured, and the rule for the next component is
  to **delete the suite you just added and confirm `npm test` exits non-zero**
  before opening the PR. v8 coverage is deterministic, so tight thresholds do
  not flake.
- ~~**A failed audit-log fetch reads as an empty trail (#82)**~~ — **fixed**
  2026-08-10, the PR after the one that found it. `AuditLogs` caught its error,
  did `setLogs([])` and surfaced nothing, so a 500 and a genuinely empty trail
  both rendered "No audit logs yet. Start asking questions!".

  **The copy was only half of it.** Fixing the sentence alone would have left
  "Total Queries 0" on screen, which asserts an empty trail exactly as plainly,
  so the stat tiles are hidden on failure too. The banner states outright that
  no conclusion can be drawn — "we don't know" and "nothing happened" are
  different answers and only one is true after a failed fetch — and carries
  `role="alert"`, because it contradicts what the page would otherwise imply and
  a screen-reader user must not be left with the empty reading.

  **Generalisable:** when a load failure is the bug, audit everything on the
  page that implies a count, not just the error message.
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
- ~~**Four eval-found defects (#58, #59, #60, #61)**~~ — **all fixed**, the last
  two on 2026-08-12 (`ranking` 87.5% → **100%**, `filter` 90.9% → **100%**).
  **#59 and #60 each have a failed prompt attempt on record**, and it is the
  attempt each issue recommends — kept here because the next accuracy defect
  will come with the same recommendation:
  - **#59** (superlatives return the measure): the recommended rule naming the
    superlative vocabulary took `ranking` from **87.5% to 62.5%**. The 3B model
    copied the concrete example verbatim, including its `ASC` direction on a
    "highest" question, breaking two cases that had been passing 3/3. An
    abstract rewrite measured the same. Reverted — and then fixed as a rewrite,
    see below.
  - **#60** (units vs orders): the recommended rule *did* fix its own case —
    `filter` 91% → 100% — but cost `ranking` 87.5% → 75%, for an identical
    93/99 across the three categories either way. It moves failures rather than
    removing them. Reverted — and then fixed as a rewrite, see below.

  **This is the third confirmation of the #52 lesson, and the sharpest**: a rule
  that provably fixes its target case can still be not worth shipping. Compare
  *category* totals across the whole eval, not the headline — 86.0–91.2 is the
  observed range, wide enough to hide a 12-point category swing.
- ~~**Superlatives answer with the measure instead of the row (#59)**~~ —
  **fixed** 2026-08-12, deterministically, and it is the second entry in this
  file to overturn its own "not derivable" verdict after #74 did.

  **The verdict was wrong about where to look, in a way worth naming.** The
  issue said the entity column "requires knowing which column is the intended
  entity, and that is a guess — unlike #52, the AST does not carry the answer."
  Both halves of that are true and the conclusion still does not follow: the
  *question* carries it ("which **product**") and the *schema* confirms it,
  which is precisely how #73 resolves "for each **category**". #74 found the
  data as a third source; this one is a reminder that the question text was
  always a source too, and "the AST does not carry it" had been read as "nothing
  does".

  The direction — the thing the reverted prompt rule got wrong, applying `ASC`
  to "highest" — is read off the aggregate, so this cannot make that mistake.

  **It declines where the measure needs aggregating first.** "Which region has
  the highest total revenue?" ranks per-region sums, and the row holding the
  single largest order is a different and plausible answer. That is #74's
  shape, and guessing between them would be worse than leaving it.

  **It also declines on "who".** `emp_best_performer` ("Who has the best
  performance score?") produced the bare `MAX` twice in the round after this
  landed — the identical defect — and the repair left it alone, because "who"
  names no column and `emp_name` would have to be guessed. That case is now
  flaky rather than fixed. Widening the trigger to "who" means picking an entity
  column out of the schema, which is the guess the issue warned about; the
  narrow version is the one that measured well.
- ~~**"How many X?" counts rows instead of summing units (#60)**~~ — **fixed**
  2026-08-12. `filter` 30/33 → **33/33**; `sales_laptop_units` was the last case
  in the set failing every run.

  **The ambiguity is real and the repair does not resolve it — the model
  already had.** "How many laptops" can mean units or orders, and no rule
  decides that from the phrasing, which is what sank the prompt attempt. But
  `COUNT(CASE WHEN product = 'Laptop' THEN quantity END)` *selects the quantity
  column and then discards its values*; nobody writes that to count rows, which
  is `COUNT(*)`. So the repair changes the aggregate to match the column the
  model reached for, and never touches a `COUNT(*)` — which is why "How many
  orders are there?" (3/3) is out of its reach by construction.

  The trigger also requires the question's noun to be the value the query
  filters on ("how many **laptops**" against `product = 'Laptop'`), so what is
  being counted is an entity in the data rather than a row of the table.

  **The headline did not move: 98.2% before and after.** +3 attempts from this
  repair, −3 from two flaky cases in categories it cannot reach. That is the
  clearest illustration yet of why `baseline.json` records category tables —
  read as a headline, this round says the repair did nothing.
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

  **#59 then paid this out, though not from the source predicted.** Re-reading
  it turned the answer up in the *question* — "which **product**" names the
  entity and the schema confirms it — not in the table. The prediction was right
  that a re-examination was owed and wrong about where it would land, which is a
  better argument for doing it than the one made here.
- **No case fails every run any more, and every remaining failure is model
  non-determinism** — `emp_best_performer`, `sales_march_revenue`,
  `emp_joined_2021`, `sales_electronics_by_region`. That is a first for this
  eval, and it changes what a red run means: there is no longer a known-bad case
  to attribute one to, so treat any repeat failure as new. It also means the
  next accuracy work is either a new case set (the two datasets are small — see
  "Limitations" in `evals/README.md`) or the flaky four, which are a model
  problem rather than a repair-shaped one.
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
  as metered usage; the cap blocks work rather than adding to the bill — that
  half is #31, and it stays open.

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

1. **Restore drill from a real backup** (#5, #18). Still the single
   highest-risk open item, and still blocked on a real deployment — but the
   ground under it moved on 2026-08-13. `scripts/restore_drill.sh` now executes
   `backup.sh` and `restore.sh` end to end on every PR, in both the Postgres and
   SQLite modes, and fails if the round trip loses an org, an audit entry, a
   hash-chain link or a byte of a dataset file. **The scripts work.** What is
   still unmeasured is everything the environment owns: PITR, cross-region
   recovery, and the RPO ≤ 15 min / RTO ≤ 1 h targets, which remain assertions.
   The quarterly-drill checklist in `DISASTER_RECOVERY.md` now says exactly what
   to record.
2. **k6 against real staging** (#25). Every latency SLO, alert threshold and the
   E2E's 120s timeout still descends from one laptop run. **Run
   `loadtest/preflight.py` first** — it refuses the run if the target would make
   it measure the rate limiter, the LLM cache or the quota gate instead of the
   app, which are the three ways this has historically gone wrong and were until
   2026-08-13 only prose in `loadtest/README.md`.
3. **Stripe test-mode round trip** (#19). No browser has ever completed a hosted
   Checkout and no event Stripe generated has ever arrived. The parts that do
   not need an account now run on every PR (`scripts/stripe_drill.sh`), and the
   first thing they did was find #93 — **the money path had never worked**. The
   remaining checklist is in `BILLING.md`; its last line, diffing a real payload
   against the test fixtures, is the one that would have caught #93 years
   earlier.
4. ~~**NL2SQL accuracy eval** (#16)~~ — done 2026-08-02, and it immediately
   found a real bug (#52, fixed 2026-08-03). Accuracy 78.4% → **88.9%**.

**Everything that could be done from a dev machine now has been — including
inside P0.** Each of the three above has had its dev-machine half built and
merged: the backup and restore scripts execute in CI, the money path is drilled
end to end short of a real account, and a staging run can no longer silently
measure the wrong thing. What is left in each is what only an environment can
answer — PITR and a real RTO, a real Checkout, real hardware under load.

> **This sentence was not true when it was written.** It was added on
> 2026-08-13, the same day this file recorded elsewhere that "hCaptcha remains
> entirely unstarted" — a piece of P1 #21 needing no account, no infrastructure
> and no environment, sitting in the same document as a claim that no such work
> remained. It was made true on 2026-08-16 by building it. The generalisable
> version: **a summary sentence stops tracking the list it summarises the moment
> the list changes**, and this file's summaries are the part to distrust first.

**So the top of this list still has not moved since 2026-07-21, and will not
until someone provisions something.** That is the honest state of the project.
The difference is that when someone does, none of these will start with
"first find out whether the script works".

**P1 — real bugs and abuse vectors.**

5. ~~E2E cold-path flakiness (#45)~~ — done 2026-08-05 (PR #63).
6. ~~Email verification on signup (#21)~~ — **gate done** 2026-08-06,
   **transport done** 2026-08-13, **captcha done** 2026-08-16. Queries are gated,
   mail is really sent once `SMTP_HOST` is set, and a configured captcha is
   really verified. Both refuse rather than degrade: the mailer will not
   authenticate over an unencrypted connection, and the captcha refuses a signup
   it cannot verify. **Every part of #21 that does not need an account is now
   built.** What remains is only the account: nothing has sent to a real mail
   server and no challenge has been solved against a real provider.
   `GO_LIVE_CHECKLIST.md` §3 has both sets of checks.

**P2 — quality, cheap.** Each is roughly an hour.

7. ~~Raise the backend coverage gate 70 → 85 (#28)~~ — done 2026-08-02.
8. ~~Frontend coverage gate (#27)~~ — done 2026-08-06, and ~~**#70**~~ —
   **done 2026-08-13**. All twelve components covered, each to 100% on all four
   metrics; suite 94 → 270, gate 60/85/50/60 → **91/94/71/91**, ratcheted once
   per PR and verified to bite each time. ~~`CaptchaWidget`~~ (08-16),
   ~~`services/api.js`~~ and ~~`App.jsx`~~ (08-17) and ~~`ResultView.jsx`~~
   (08-18) followed on the same terms — suite **403**, gate
   **99.4/97.7/98.7/99.4**. **Frontend coverage is done:** every file in `src/`
   is at 100% statements and lines, and what remains below 100% on branches and
   functions is unreachable rather than untested (named in `vite.config.js`).
9. ~~PDF export truncation (#46)~~ — done 2026-08-03.
10. ~~EOL-runtime watch (#47)~~ — done 2026-08-06.
11. ~~Accuracy: #69 (dates)~~, ~~#73 (per-group)~~, ~~#74 (HAVING)~~,
    ~~#59 (superlatives)~~, ~~#60 (units vs rows)~~ — **all done**. Accuracy is
    **98.2%**, six of eight categories are at 100%, and no case fails every run.
    Seven deterministic repairs have now landed (#52, #58, #69, #73, #74, #59,
    #60) and **every prompt edit attempted has been reverted** — that is the
    pattern to plan around when the next defect turns up.
12. Billing feature gaps (#31). ~~Invoice history~~ done 2026-08-13 — it was
    the one piece that needed nothing from a live account. **Three remain, and
    none of them should be started before #19 proves the money path:**

    - **Metered usage.** `rows_processed` is capped per plan and never reported
      to Stripe, so hitting the ceiling *blocks work* instead of adding to the
      bill. That is a pricing decision before it is a feature, and the ceilings
      themselves are still guesses (see "Known gaps").
    - **Proration on plan switches.** Left entirely to Stripe's defaults, and
      nobody has watched what those defaults do. `PUT /api/usage/plan` refuses
      when Stripe is configured, so every switch already goes through Checkout
      or the portal — which means this cannot be specified from the code, only
      observed against a real subscription.
    - **Dunning.** Configured in the Stripe dashboard, not here. The code half
      already exists and is correct: `past_due` keeps the paid plan
      (`ENTITLED_STATUSES`), only `canceled`/`unpaid` drop to free. What is
      missing is the retry schedule and the mails, which are Stripe's to set.

    The common blocker is not effort — it is that all three are designed against
    behaviour nobody has seen yet.

### Conventions worth keeping

- **Mutation-test new tests.** Break the thing on purpose and confirm the test
  fails. Two tests written on 2026-07-28 passed vacuously and were only caught
  this way: one asserted a button was disabled when it would have been disabled
  regardless, another compared a global thread count that other tests inflated.
  Green is not evidence on its own.
- **Ask what would make a green check go red, then do that.** Mutation testing
  is this rule applied to unit tests; it applies to everything else too, and
  three things written on 2026-08-13 needed it. The restore drill grew an
  `assert-destroyed` step, without which a restore that did nothing would have
  passed a seed→verify pair. The load-test preflight's first rate-limit check
  bursted an endpoint carrying no limiter, and passed against a target on
  production defaults. And #93 — a total failure of the money path — survived 27
  tests that stubbed the function containing the bug. **A check that cannot fail
  is worse than no check, because it is believed.**
- **Tests must not reach a live Ollama.** See the architecture note above.
- **`gh pr merge --auto` does not wait in this repo.** There is no branch
  protection requiring status checks, so `--auto` has nothing to gate on and
  merges immediately. On 2026-08-18 that merged #108 with the backend and image
  jobs still running — they passed, but the flag did not do what its name
  implies. **Read `gh pr checks` yourself before merging**, and remember that
  the answer has a timestamp on it: #103 was green when it merged and its merge
  commit was red an hour later (see the advisories note at the top).
- **A failing DOM assertion can arrive as an unrelated error with no stack.**
  `expect(el).toBeNull()` on a DOM node that is *not* null crashed Vitest's
  serialiser while it formatted the failure, and what surfaced was
  `TypeError: Cannot read properties of undefined (reading 'name')` — no stack,
  no line, reading exactly like a crash inside the component. Suspect the
  assertion before the code.
- One focused PR per concern, against **`main`**, `ruff` clean, and update this
  file in the same PR.
