# End-to-end tests (Playwright)

One smoke test that exercises the **whole stack as a user does it** — real
browser → SPA → API → real Ollama → DuckDB → rendered result (issue #20).
Everything else in the repo tests a layer with the next one mocked; this is the
only test that would catch a break *between* layers.

It is a **smoke test, not an accuracy test**. The LLM is real and
non-deterministic, so it asserts the flow completes and a result renders — not
that a specific number is correct. SQL correctness is tracked separately
(issue #16).

## What it does

`full-flow.spec.js`: register a fresh workspace → upload `fixtures/sales.csv` →
ask "How many rows are in the data?" → assert a result renders and it isn't the
error state.

## Prerequisites

- Frontend deps: `npm ci`
- Playwright browser: `npx playwright install chromium`
- Backend deps installed (`backend/requirements*.txt`)
- **Ollama running** with the model pulled (`LLM_MODEL`, default `llama3.2:3b`) —
  the query step makes a real inference call.

## Run

```bash
npm run test:e2e
```

That calls `e2e/run.sh`, which starts the backend on :8000 with a throwaway
SQLite DB and temp dirs, waits for `/health/ready`, then runs Playwright.
Playwright starts its own frontend dev server (a dedicated port, `--strictPort`,
never reusing a foreign one) and tears everything down after.

To run against an already-running stack instead:

```bash
npx playwright test              # expects frontend on E2E_PORT (default 3178)
```

## Notes for whoever wires this into CI

- The blocker that kept #20 open was CI orchestration, not the test. `run.sh` is
  the single entry point — a CI job needs: Python + backend deps, Node + `npm
  ci`, `npx playwright install --with-deps chromium`, an Ollama service with the
  model pulled, then `npm run test:e2e`.
- **Timeouts are generous on purpose** (query wait up to 120s). CPU inference is
  slow; a GPU runner will be far faster. Don't tighten these to match a fast box
  and make the suite flaky on a slow one.
- **The model is warmed before the test, and there are no retries** (issue #45).
  `run.sh` makes one throwaway inference call to load the model's weights into
  RAM *before* Playwright runs, so the timed query is not the one paying for the
  cold model load. Because that cost is now paid up front, `retries` is `0`:
  attempt 1 must pass on its own. Previously `retries: 1` hid the problem — the
  cold first attempt blew the timeout and warmed the cache, and only the retry
  passed, so the suite reported green while certifying the *warm* path and could
  not catch a cold-path regression.
- **This is a smoke test, not a cold-path SLO.** It answers "does the whole flow
  work end to end?", not "is cold inference fast enough?" — that latency question
  is issue #25, and needs real staging numbers, not a laptop or a shared runner.
- Artifacts (`test-results/`, `playwright-report/`) are gitignored.
