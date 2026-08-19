import { expect, test } from "@playwright/test";
import { MEMBER, OWNER, SALES_CSV, loginAs, navTab, openTab, uploadDataset } from "./helpers";

// The dashboard is a single route with a tab-switched <main>; the sidebar is the
// only navigation. What is worth pinning here is that each tab renders its own
// view, and that the tab list itself is cut to the signed-in user's role — a
// member must not be shown a door they cannot open.

test("the sidebar switches the main view, one tab at a time", async ({ page }) => {
  await loginAs(page, OWNER);

  const views = [
    ["Upload Data", /Upload Your Data/i],
    ["Audit Logs", /Audit Trail/i],
    ["Admin", /Admin console/i],
    ["Account", /Account settings/i],
    ["Ask Questions", /No data loaded/i],
  ];

  for (const [tab, heading] of views) {
    await openTab(page, tab);
    await expect(page.getByRole("main").getByRole("heading", { name: heading })).toBeVisible();
    // The nav marks the current tab for assistive tech, not just with a colour.
    await expect(navTab(page, tab)).toHaveAttribute("aria-current", "page");
  }
});

test("an owner is offered the Admin tab", async ({ page }) => {
  await loginAs(page, OWNER);
  await expect(navTab(page, "Admin")).toBeVisible();
});

test("a member is not offered the Admin tab at all", async ({ page }) => {
  await loginAs(page, MEMBER);

  // Absent, not disabled: Sidebar builds its menu from `isAdmin`, so there is
  // nothing to click and nothing to explain.
  await expect(navTab(page, "Admin")).toHaveCount(0);
  // The rest of the navigation is unchanged.
  for (const tab of ["Upload Data", "Ask Questions", "Audit Logs", "Account"]) {
    await expect(navTab(page, tab)).toBeVisible();
  }
});

test("the chat tab with nothing loaded explains itself and offers the way out", async ({ page }) => {
  await loginAs(page, OWNER);

  // This is the landing view: Dashboard opens on `chat` with no session.
  const main = page.getByRole("main");
  await expect(main.getByRole("heading", { name: /No data loaded/i })).toBeVisible();
  await expect(main.getByText(/Upload a file first/i)).toBeVisible();

  // The CTA is the dead end's exit — and it is a second "Upload Data" control,
  // which is why everything in this suite scopes that name to a region.
  await main.getByRole("button", { name: /upload data/i }).click();
  await expect(main.getByRole("heading", { name: /Upload Your Data/i })).toBeVisible();
});

test("the sidebar footer names the role the session actually has", async ({ page }) => {
  await loginAs(page, OWNER);
  // The role is the one the login response carried, held in memory — not a
  // value read back out of localStorage (#22).
  await expect(page.locator(".sidebar-role")).toContainText("owner");

  await page.getByRole("button", { name: /logout/i }).click();
  await loginAs(page, MEMBER);
  await expect(page.locator(".sidebar-role")).toContainText("member");
  await expect(page.locator(".sidebar-role")).not.toContainText("owner");
});

test("the session panel appears only once something is loaded", async ({ page }) => {
  await loginAs(page, OWNER);
  await expect(page.locator(".sidebar-session")).toHaveCount(0);

  await uploadDataset(page, SALES_CSV);
  await expect(page.locator(".sidebar-session")).toBeVisible();
  await expect(page.locator(".sidebar-session")).toContainText("sales");
});
