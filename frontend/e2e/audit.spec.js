import { expect, test } from "@playwright/test";
import { MEMBER, OWNER, SALES_CSV, ask, loginAs, openTab, uploadDataset } from "./helpers";

// The audit trail is the record of who asked what. Two things matter about it:
// that a query really lands in it (question *and* the SQL that ran), and that a
// failure to load is never dressed up as an empty trail — issue #82, where a
// 500 and "nothing happened" rendered identically.

const QUESTION = "How many orders are in the data?";

test.describe("with a query already in the trail", () => {
  // Serial and share the page: the setup is an upload plus a real inference
  // call, and paying that twice to assert two things about the same row buys
  // nothing.
  test.describe.configure({ mode: "serial" });

  let page;

  test.beforeAll(async ({ browser }) => {
    page = await browser.newPage();
    await loginAs(page, OWNER);
    await uploadDataset(page, SALES_CSV);
    await ask(page, QUESTION);
    await openTab(page, "Audit Logs");
    await expect(page.getByRole("heading", { name: /Audit Trail/i })).toBeVisible();
  });

  test.afterAll(async () => {
    await page?.close();
  });

  test("the query is in the trail, with the SQL that answered it", async ({}) => {
    const row = page.locator("table.audit-table tbody tr").filter({ hasText: QUESTION });
    await expect(row).toHaveCount(1, { timeout: 30_000 });

    // The generated SQL is the compliance artifact — the question alone does not
    // record what actually ran against the data.
    await expect(row.locator("code.audit-sql")).toContainText(/select/i);
    // The status badge carries the result type the pipeline produced; what
    // matters here is that the entry is classified at all, not which shape the
    // model's answer happened to take.
    await expect(row.locator(".status-badge")).not.toBeEmpty();
  });

  test("the search box filters by question and by SQL", async ({}) => {
    const rows = page.locator("table.audit-table tbody tr");
    const search = page.getByPlaceholder(/Search queries or SQL/i);
    await expect(rows.first()).toBeVisible();

    // By question text.
    await search.fill("orders are in the data");
    await expect(rows).toHaveCount(1);
    await expect(rows.first()).toContainText(QUESTION);
    await expect(page.locator(".search-count")).toHaveText(/^1 result$/);

    // By SQL text. The question contains no "select" — matching on it is the
    // filter reading the SQL column, which is what makes this useful to someone
    // auditing *what ran* rather than what was asked.
    await search.fill("select");
    const matches = await rows.count();
    expect(matches).toBeGreaterThan(0);
    await expect(page.locator(".search-count")).toHaveText(
      new RegExp(`^${matches} results?$`),
    );

    // A term in neither field matches nothing, and says so rather than leaving
    // a stale list on screen.
    await search.fill("zzz-no-such-query");
    await expect(rows).toHaveCount(0);
    await expect(page.getByText(/No matching queries found/i)).toBeVisible();

    await search.fill("");
    await expect(rows.first()).toBeVisible();
  });
});

test("Refresh re-fetches the trail", async ({ page }) => {
  await loginAs(page, OWNER);

  let fetches = 0;
  page.on("request", (r) => {
    if (r.url().includes("/api/audit/logs")) fetches += 1;
  });

  await openTab(page, "Audit Logs");
  await expect(page.getByRole("heading", { name: /Audit Trail/i })).toBeVisible();
  await expect(page.getByRole("button", { name: /refresh/i })).toBeEnabled();

  // How many requests mounting costs is not this test's business — React's
  // StrictMode double-invokes effects under the dev server the suite runs
  // against, so it is two here and one in a production build. What matters is
  // the delta the click adds.
  const onMount = fetches;

  await page.getByRole("button", { name: /refresh/i }).click();
  // Not a client-side re-render of what it already had: a trail is only
  // trustworthy if the button goes back and asks.
  await expect.poll(() => fetches).toBe(onMount + 1);
  await expect(page.getByRole("button", { name: /refresh/i })).toBeEnabled();
});

test("a member is refused the trail, and is told it is unknown — not empty", async ({ page }) => {
  await loginAs(page, MEMBER);
  await openTab(page, "Audit Logs");

  // require_admin refuses with 403. What the page must not do is fall back to
  // the empty state: "we could not look" and "nothing was queried" are
  // different answers, and only one of them is true (#82).
  await expect(page.getByRole("alert")).toContainText(/Could not load the audit trail/i);
  await expect(page.getByText(/No audit logs yet/i)).toHaveCount(0);
  // The stat tiles are hidden too — "Total Queries 0" asserts emptiness just as
  // plainly as the empty-state copy does.
  await expect(page.locator(".audit-stats")).toHaveCount(0);
  await expect(page.locator("table.audit-table")).toHaveCount(0);
});
