import { expect, test } from "@playwright/test";
import { OWNER, loginAs, openTab } from "./helpers";

// Not a full audit — those belong to an axe run. These are the four things this
// app can plausibly regress on, checked against the real rendered DOM: one title
// per region, a keyboard-only path through the front door, busy state announced
// while an action is in flight, and a viewport that lets people zoom.

test("the login screen is titled once and its form is labelled", async ({ page }) => {
  await page.goto("/login");

  // One H1, and it names the product rather than the page's job — which is fine,
  // because the form beside it carries its own accessible name.
  await expect(page.locator("h1")).toHaveCount(1);
  await expect(page.locator("h1")).toHaveText("DataWhisper");
  await expect(page.getByRole("form", { name: "Sign in" })).toBeVisible();

  // Placeholders are not labels. Both fields have a real <label>, visually
  // hidden — so the accessible name survives the moment the field is typed in.
  await expect(page.getByLabel("Username")).toBeVisible();
  await expect(page.getByLabel("Password")).toBeVisible();
});

test("the signup screen is titled once and its form is labelled", async ({ page }) => {
  await page.goto("/signup");

  await expect(page.locator("h1")).toHaveCount(1);
  await expect(page.getByRole("form", { name: "Create account" })).toBeVisible();
  // The password rule is described, not just enforced: a field that rejects
  // input without saying why is the failure this describedby exists to prevent.
  await expect(page.getByPlaceholder(/Password/)).toHaveAttribute("aria-describedby", "password-help");
  await expect(page.locator("#password-help")).toHaveText(/at least 10 characters/i);
});

test("every dashboard view has one main landmark with exactly one title", async ({ page }) => {
  await loginAs(page, OWNER);

  // The sidebar's own <h2> sits outside <main>, so "one title" is asked of the
  // main region — the part that changes when you switch tabs.
  for (const tab of ["Upload Data", "Ask Questions", "Audit Logs", "Admin", "Account"]) {
    await openTab(page, tab);
    await expect(page.getByRole("main")).toHaveCount(1);

    const main = page.getByRole("main");
    await expect(main.getByRole("heading")).not.toHaveCount(0);
    // The first heading is the region's title, and there is only one at that
    // level — no view opens with two competing headings.
    const levels = await main.getByRole("heading").evaluateAll((nodes) =>
      nodes.map((n) => Number(n.tagName.slice(1))),
    );
    const top = Math.min(...levels);
    expect(levels.filter((l) => l === top)).toHaveLength(1);
  }

  // The navigation is a named landmark, so it can be jumped to rather than
  // tabbed through.
  await expect(page.getByRole("navigation", { name: "Main" })).toBeVisible();
});

test("the login form can be reached and submitted with the keyboard alone", async ({ page }) => {
  await page.goto("/login");
  await page.locator("body").click({ position: { x: 1, y: 1 } }); // start with nothing focused

  // Tab until the username field has focus — no mouse, and no reaching into the
  // DOM to focus it directly.
  let reached = false;
  for (let i = 0; i < 10 && !reached; i++) {
    await page.keyboard.press("Tab");
    reached = await page.evaluate(() => document.activeElement?.id === "username");
  }
  expect(reached).toBe(true);

  await page.keyboard.type(OWNER.username);
  await page.keyboard.press("Tab");
  expect(await page.evaluate(() => document.activeElement?.id)).toBe("password");
  await page.keyboard.type(OWNER.password);

  // Enter submits from inside the form — a keyboard user should not have to
  // find the button.
  await page.keyboard.press("Enter");
  await expect(page.locator(".sidebar")).toBeVisible({ timeout: 30_000 });
});

test("a button in flight is marked aria-busy", async ({ page }) => {
  // Sign-in normally settles in a few hundred milliseconds — too fast to catch
  // an intermediate state reliably. Holding the response open makes the busy
  // window observable without changing what the app does with it.
  let release;
  const held = new Promise((resolve) => (release = resolve));
  await page.route("**/api/auth/login", async (route) => {
    await held;
    await route.continue();
  });

  await page.goto("/login");
  await page.getByPlaceholder("Username").fill(OWNER.username);
  await page.getByPlaceholder("Password").fill(OWNER.password);
  // Located by class, not by name: the label changes to "Signing in..." for
  // exactly the window under test, so a name-based locator stops matching the
  // moment the thing it is meant to observe happens.
  const button = page.locator("button.login-btn");
  await button.click();

  // Disabled stops a second submission; aria-busy is what tells a screen-reader
  // user that the wait is expected rather than a dead button.
  await expect(button).toHaveAttribute("aria-busy", "true");
  await expect(button).toBeDisabled();
  await expect(button).toHaveText(/signing in/i);

  release();
  await expect(page.locator(".sidebar")).toBeVisible({ timeout: 30_000 });
  await page.unroute("**/api/auth/login");
});

test("the viewport meta does not block zoom (WCAG 1.4.4)", async ({ page }) => {
  await page.goto("/login");

  const content = await page
    .locator('meta[name="viewport"]')
    .getAttribute("content");

  // index.html carries a comment about exactly this. Pinning it here means the
  // next person who pastes in a "fix the mobile layout" snippet finds out.
  expect(content).toBeTruthy();
  expect(content).not.toMatch(/user-scalable\s*=\s*no/i);
  expect(content).not.toMatch(/maximum-scale/i);
});
