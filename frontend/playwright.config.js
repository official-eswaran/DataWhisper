import { defineConfig, devices } from "@playwright/test";

// End-to-end smoke: a real browser through the real SPA, hitting the real API,
// which runs real SQL against a real DuckDB dataset (issue #20). The one thing
// that is genuinely slow and variable is the LLM call — on CPU a single query
// can take 40s+ — so the timeouts here are deliberately generous.
//
// The frontend is started by this config (`npm run dev`, which proxies /api to
// :8000 exactly like the prod nginx). The BACKEND is not — it needs a Python
// env and a reachable Ollama, so e2e/run.sh brings it up first. Run the whole
// thing with `npm run test:e2e` (which calls run.sh), or point at an
// already-running stack with `npx playwright test`.

// A dedicated port, not vite's default 3000: a 3000 left running by another
// project would otherwise be silently reused and the test would drive the wrong
// app. For the same reason reuseExistingServer is off — we always start our own.
const PORT = process.env.E2E_PORT || 3178;
const BASE_URL = process.env.E2E_BASE_URL || `http://localhost:${PORT}`;

export default defineConfig({
  testDir: "./e2e",
  timeout: 180_000, // whole test: register + upload + a CPU-bound LLM query
  expect: { timeout: 120_000 }, // individual waits, incl. the query response
  fullyParallel: false,
  workers: 1, // one shared backend + one small model; don't fan out
  // No retries — on purpose. A retry passed only because attempt 1 warmed the
  // LLM cache, so `retries: 1` certified the warm path while a cold-path
  // regression stayed green (issue #45). run.sh now warms the model before the
  // test, so attempt 1 must pass on its own; a failure here is a real failure.
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: BASE_URL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: `npm run dev -- --port ${PORT} --strictPort`,
    url: BASE_URL,
    reuseExistingServer: false,
    timeout: 60_000,
  },
});
