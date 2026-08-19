import { expect, test } from "@playwright/test";
import fs from "node:fs";
import {
  ANOMALIES_CSV,
  BAD_TXT,
  CLEAN_CSV,
  FAKE_CSV,
  OWNER,
  SALES_CSV,
  WIDE_CSV,
  loginAs,
  openTab,
} from "./helpers";

// The upload screen is a react-dropzone over a hidden `input[type=file]`. Both
// ways in are driven here, because they are genuinely different code paths: the
// input's change event, and a synthesised drop with a DataTransfer.

test.beforeEach(async ({ page }) => {
  await loginAs(page, OWNER);
  await openTab(page, "Upload Data");
});

/** Drop a real file onto the dropzone the way a drag from the desktop does. */
async function dropFile(page, filePath, name, type = "text/csv") {
  const buffer = fs.readFileSync(filePath);
  const dataTransfer = await page.evaluateHandle(
    ({ bytes, name, type }) => {
      const dt = new DataTransfer();
      dt.items.add(new File([new Uint8Array(bytes)], name, { type }));
      return dt;
    },
    { bytes: Array.from(buffer), name, type },
  );
  await page.locator(".dropzone").dispatchEvent("drop", { dataTransfer });
}

test("click-to-browse loads a dataset and the sidebar reports its shape", async ({ page }) => {
  await page.locator('input[type="file"]').setInputFiles(SALES_CSV);

  // A successful upload flips straight to chat and greets with the new session.
  await expect(page.getByText(/Data loaded!/i)).toBeVisible({ timeout: 60_000 });

  // The sidebar's session panel is the persistent record of what is loaded.
  // `sales.csv` becomes the table `sales` (_safe_table_name strips the suffix)
  // and has eight columns. The row count is left as a pattern: it is the
  // fixture's business, not this test's.
  await expect(page.locator(".session-table")).toContainText("sales");
  await expect(page.locator(".session-rows")).toHaveText(/\d+ rows loaded/);
  await expect(page.locator(".session-cols")).toHaveText("8 columns");
});

test("drag-and-drop loads a dataset too", async ({ page }) => {
  await dropFile(page, SALES_CSV, "sales.csv");

  await expect(page.getByText(/Data loaded!/i)).toBeVisible({ timeout: 60_000 });
  await expect(page.locator(".session-table")).toContainText("sales");
});

test("anomalies found during ingestion are shown to the user", async ({ page }) => {
  // `anomalies.csv` is shaped to trip three of detect_anomalies' four rules
  // (missing values in `notes`, an outlier in `amount`, one duplicate row), so
  // this does not depend on what the detector happens to find in sales.csv.
  await page.locator('input[type="file"]').setInputFiles(ANOMALIES_CSV);

  // Asserted after the tab switch, deliberately. The panel used to live only on
  // the upload screen, which unmounts in the same React commit as a successful
  // upload — so the findings were computed, returned, and never painted. They
  // now ride along with the chat greeting, which is where the upload leaves you.
  await expect(page.getByText(/Data loaded!/i)).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText(/Anomalies Detected/i)).toBeVisible();

  const panel = page.locator(".result-anomalies");
  await expect(panel).toContainText(/missing values/i);
  await expect(panel).toContainText(/outlier/i);
  await expect(panel).toContainText(/duplicate rows/i);
});

test("a clean file shows no anomalies panel", async ({ page }) => {
  // The other half of the same wiring: the panel is absent, not empty. Without
  // this, rendering it unconditionally would satisfy the test above.
  // `clean.csv` is the only fixture here with nothing to report — sales.csv has
  // outliers in `quantity` and `total_amount`.
  await page.locator('input[type="file"]').setInputFiles(CLEAN_CSV);

  await expect(page.getByText(/Data loaded!/i)).toBeVisible({ timeout: 60_000 });
  await expect(page.locator(".result-anomalies")).toHaveCount(0);
});

test("an unsupported extension is refused", async ({ page }) => {
  let uploads = 0;
  page.on("request", (r) => {
    if (r.method() === "POST" && r.url().includes("/api/upload")) uploads += 1;
  });

  await page.locator('input[type="file"]').setInputFiles(BAD_TXT);

  // `.txt` is not in the dropzone's accept map, so it is filtered client-side
  // and never sent. Counting the requests is what makes that a real assertion:
  // "no success message" would also be true of an upload still in flight.
  // (It is refused silently, with no message at all — see the suite README.)
  await page.waitForTimeout(1_000);
  expect(uploads).toBe(0);
  await expect(page.getByText(/Data loaded!/i)).toHaveCount(0);
  await expect(page.locator(".sidebar-session")).toHaveCount(0);
  await expect(page.getByRole("heading", { name: /Upload Your Data/i })).toBeVisible();
});

test("a file whose content does not match its extension is refused", async ({ page }) => {
  // `fake.csv` is a PNG. The extension gets it past the dropzone, and the
  // backend's magic-byte check is what stops it — the case that a purely
  // client-side filter would wave through.
  await page.locator('input[type="file"]').setInputFiles(FAKE_CSV);

  await expect(page.getByText(/File content does not match the declared extension/i))
    .toBeVisible({ timeout: 30_000 });
  await expect(page.locator(".sidebar-session")).toHaveCount(0);
});

test("a second upload replaces the active session", async ({ page }) => {
  await page.locator('input[type="file"]').setInputFiles(SALES_CSV);
  await expect(page.locator(".session-table")).toContainText("sales");
  await expect(page.locator(".session-cols")).toHaveText("8 columns");

  await openTab(page, "Upload Data");
  await page.locator('input[type="file"]').setInputFiles(WIDE_CSV);

  // One session at a time: the sidebar now describes the new table, not both.
  await expect(page.locator(".session-table")).toContainText("wide");
  await expect(page.locator(".session-cols")).toHaveText("18 columns");
  await expect(page.locator(".session-table")).not.toContainText("sales");
  // …and the chat is talking about the new table.
  await expect(page.locator(".chat-table-name")).toContainText("wide");
});
