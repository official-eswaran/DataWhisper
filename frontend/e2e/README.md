# End-to-end tests (Playwright)

The suite that exercises the **whole stack as a user does it** — real browser →
SPA → API → real Ollama → DuckDB → rendered result (issue #20). Everything else
in the repo tests a layer with the next one mocked; this is the only thing that
would catch a break *between* layers.

It is a **behaviour suite, not an accuracy suite**. The LLM is real and
non-deterministic, so nothing here asserts that a specific number is correct —
only that the flow completes, the right thing renders, and the guardrails hold.
SQL correctness is tracked separately (issue #16).

## What each spec covers

| Spec | Covers |
|---|---|
| `full-flow.spec.js` | The original smoke test (#20): register → upload → query → rendered result. Left as it was. |
| `auth.spec.js` | Sign-in; the wrong-password message *and* its remaining-attempt count; account lockout at `MAX_LOGIN_ATTEMPTS` and the 429 after it; signup validation (short / letterless / digitless passwords, duplicate username, duplicate email, reserved email domain); logout and the Back button; session survival across a reload with no login flash; the redirects in both directions. |
| `upload.spec.js` | Click-to-browse and drag-and-drop; the sidebar's table/rows/columns readout; rejection of an unsupported extension and of a file whose bytes contradict its extension; one session at a time; the ingestion anomalies panel, present and absent. |
| `chat.spec.js` | Suggestion chips; stage order, token-by-token SQL streaming and the locked composer during one real query; a result's summary + expanded SQL + chart; the chart-type switcher; conversational memory across two turns; chitchat; off-topic refusal; the PR #111 error-envelope regression; Export PDF. |
| `dashboard.spec.js` | Tab switching and `aria-current`; the Admin tab present for an owner and absent for a member; the no-session empty state and its CTA; the role in the sidebar footer; the session panel's lifecycle. |
| `admin.spec.js` | The user list; creating a member and signing in as them; deactivating a member and the refusal that follows; a member blocked from the console in the UI **and** at `require_admin` behind it. |
| `audit.spec.js` | A query landing in the trail with the SQL that answered it; filtering by question text and by SQL text; Refresh really re-fetching; and #82 — a member's 403 reported as "we could not look", never as an empty trail. |
| `account.spec.js` | The role-dependent danger zone; the data export download; both destructive actions guarded by a confirmation that is dismissed; and, last in the file, one org registered purely to be deleted for real. |
| `a11y.spec.js` | One title per region and labelled forms; a keyboard-only path through sign-in; `aria-busy` while a submit is in flight; and a viewport meta that still permits zoom (WCAG 1.4.4). |

`helpers.js` holds the shared plumbing: fixture paths, the two account kinds,
`registerOrg` / `loginAs` / `uploadDataset` / `ask`, and the locators that need
a comment more than they need repeating.

## Accounts

Two kinds, and the difference matters:

* **Seeded** — `ceo` (owner) and `manager` (member), created by `init_db`
  because `run.sh` starts the backend with `SEED_DEMO_DATA=true` against an
  empty database. Stable, no registration round trip, and they give the
  role-gated screens a second role to be checked from.
* **Fresh** — `freshIdentity()` stamps a new org/user/email. Anything that
  **locks, disables or deletes** an account must use one of these; the seeded
  pair is shared with every later spec, and locking `ceo` out for fifteen
  minutes would take the rest of the run down with it.

Emails are always `@example.com`. Pydantic's `EmailStr` rejects special-use and
reserved domains, so `.local` and `.test` addresses never register — which is
itself one of the cases `auth.spec.js` pins.

## Prerequisites

- Frontend deps: `npm ci`
- Playwright browser: `npx playwright install chromium`
- Backend deps installed (`backend/requirements*.txt`), or a `backend/.venv`
- **Ollama running** with the model pulled (`LLM_MODEL`, default `llama3.2:3b`) —
  the query steps make real inference calls.

## Run

```bash
npm run test:e2e            # everything
npm run test:e2e auth.spec.js   # one file
```

That calls `e2e/run.sh`, which starts the backend on :8000 with a throwaway
SQLite DB and temp dirs, waits for `/health/ready`, then runs Playwright.
Playwright starts its own frontend dev server (a dedicated port, `--strictPort`,
never reusing a foreign one) and tears everything down after.

To run against an already-running stack instead:

```bash
npx playwright test              # expects frontend on E2E_PORT (default 3178)
```

### Backend env `run.sh` sets, and why

Beyond the throwaway paths: `SEED_DEMO_DATA` and the two seed passwords (see
*Accounts*), and raised `RATE_LIMIT_*` values. The shipped limits are sized for
humans — registration is **5/hour** per IP — and the whole suite arrives from
one address inside a couple of minutes. Left alone they fail specs for the wrong
reason: the lockout spec would be served slowapi's per-IP 429 instead of the
per-account one it is about. `MAX_LOGIN_ATTEMPTS` is per-account and is
deliberately left at its default of 5.

## What this suite found

**Ingestion anomalies were computed, returned, and never shown — now fixed.**

`POST /api/upload/` returns an `anomalies` array (`anomalies.csv` yields
missing-data, outlier and duplicate findings), and `FileUpload` rendered a panel
for it. But a successful upload also calls `onUploadSuccess`, and
`Dashboard.handleUploadSuccess` switches `activeTab` to `chat` — in the same
React commit as `setResult`. The upload view unmounted before the panel was ever
painted, and returning to the Upload tab remounted `FileUpload` with `result`
back to `null`, so there was no path to the findings from anywhere in the app.

Every layer's own tests passed throughout: the detector had unit tests, the API
returned the data, and `FileUpload.test.jsx` rendered the panel with
`onUploadSuccess` stubbed out so nothing unmounted it. Only a test that drives
the two components together could see it — which is issue #20's whole argument.

The panel is now `components/Upload/AnomalyList.jsx`, rendered both by
`FileUpload` and, attached to the "Data loaded!" greeting, by `ChatWindow` —
which is where a finished upload actually leaves the user. `upload.spec.js`
covers both directions: the findings appear for `anomalies.csv`, and no panel at
all appears for `clean.csv`.

Two smaller things, reported rather than asserted:

- **An unsupported extension is refused silently.** `.txt` is filtered by
  react-dropzone's `accept` map, and `FileUpload`'s `onDrop` only takes
  `acceptedFiles` — so a rejected file produces no toast, no message, nothing.
  The spec asserts the file is never sent; it cannot assert that the user was
  told, because they are not.
- **The Account screen never prints the role** it is given. The role is on
  screen in the sidebar footer, which is what `account.spec.js` asserts, but the
  view's own `role` prop is used only to decide whether to offer the org-wide
  delete.

## Notes for whoever wires this into CI

- The blocker that kept #20 open was CI orchestration, not the test. `run.sh` is
  the single entry point — a CI job needs: Python + backend deps, Node + `npm
  ci`, `npx playwright install --with-deps chromium`, an Ollama service with the
  model pulled, then `npm run test:e2e`.
- **Timeouts are generous on purpose** (query wait up to 120s). CPU inference is
  slow; a GPU runner will be far faster. Don't tighten these to match a fast box
  and make the suite flaky on a slow one.
- **The model is warmed before the tests, and there are no retries** (issue #45).
  `run.sh` makes one throwaway inference call to load the model's weights into
  RAM *before* Playwright runs, so a timed query is not the one paying for the
  cold model load. Because that cost is paid up front, `retries` is `0`: attempt
  1 must pass on its own. Previously `retries: 1` hid the problem — the cold
  first attempt blew the timeout and warmed the cache, and only the retry passed,
  so the suite reported green while certifying the *warm* path and could not
  catch a cold-path regression.
- **`workers: 1` is not a performance knob.** One backend, one Ollama, one small
  model, and specs that share the seeded accounts. Fanning out would have them
  logging each other out.
- **This is not a cold-path SLO.** It answers "does the whole flow work end to
  end?", not "is cold inference fast enough?" — that latency question is issue
  #25, and needs real staging numbers, not a laptop or a shared runner.
- Two things about the dev server the suite runs against, worth knowing before
  reading a diff: React **StrictMode double-invokes effects**, so mount-time
  fetches happen twice (`audit.spec.js` asserts the delta a click adds, not an
  absolute count), and Vite proxies `/api` to :8000 exactly as the prod nginx
  does.
- Artifacts (`test-results/`, `playwright-report/`) are gitignored.
