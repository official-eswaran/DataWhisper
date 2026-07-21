import { expect, test } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";

// The gap this closes (#20): every layer was tested with the next one mocked.
// This drives the real stack end to end — browser → SPA → API → DuckDB →
// rendered result — so a break *between* layers can no longer pass CI.
//
// It is a SMOKE test, not an accuracy test. The LLM is real and
// non-deterministic, so we assert the flow completes and a result renders, not
// that a specific number is right. SQL correctness is issue #16's job.

const dir = path.dirname(fileURLToPath(import.meta.url));
const FIXTURE = path.join(dir, "fixtures", "sales.csv");

// Unique identity per run — username/email are globally unique in the schema,
// so a fixed name would collide on the second run against a persistent DB.
const stamp = Date.now();
const ORG = `e2e-org-${stamp}`;
const USER = `e2e_user_${stamp}`;
// example.com, not a .local/.test address — the email validator rejects
// reserved/special-use domains, so those never register.
const EMAIL = `${USER}@example.com`;
const PASS = "E2ePassw0rd!";

test("register → upload → query → rendered result", async ({ page }) => {
  // 1. Register a fresh workspace and land in the dashboard.
  await page.goto("/signup");
  await page.getByPlaceholder("Organization name").fill(ORG);
  await page.getByPlaceholder("Username").fill(USER);
  await page.getByPlaceholder("Email").fill(EMAIL);
  await page.getByPlaceholder(/Password/).fill(PASS);
  await page.getByRole("button", { name: /create workspace/i }).click();

  // The app opens on the chat tab with no data, which offers an upload CTA.
  // Scope to <main>: an "Upload Data" button also lives in the sidebar nav.
  const uploadCta = page
    .getByRole("main")
    .getByRole("button", { name: /upload data/i });
  await expect(uploadCta).toBeVisible();

  // 2. Upload a dataset. react-dropzone hides a real file input; drive that.
  await uploadCta.click();
  await page.locator('input[type="file"]').setInputFiles(FIXTURE);

  // A successful upload switches to chat and greets with the loaded table.
  // This proves the file reached the API, was parsed, and a DuckDB session
  // was registered.
  await expect(page.getByText(/Data loaded!/i)).toBeVisible({ timeout: 30_000 });

  // 3. Ask a question — this is the real Ollama round trip and the real SQL
  // execution against DuckDB. Enter submits (no accessible name on the icon
  // send button, and this matches how a user actually sends).
  const box = page.getByPlaceholder(/Ask a question about your data/i);
  await box.fill("How many rows are in the data?");
  await box.press("Enter");

  // The question echoes back immediately as the user's message.
  await expect(page.getByText("How many rows are in the data?")).toBeVisible();

  // 4. A result renders. ResultView picks single-value / table / chart by shape;
  // any of them proves the response came back and drew. Generous timeout: this
  // is waiting on CPU inference.
  const result = page.locator(".result-single, .result-table, .result-chart");
  await expect(result.first()).toBeVisible({ timeout: 120_000 });

  // And the flow did not end in the generic failure message.
  await expect(
    page.getByText(/something went wrong|connection error/i),
  ).toHaveCount(0);
});
