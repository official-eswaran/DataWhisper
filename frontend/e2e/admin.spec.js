import { expect, request, test } from "@playwright/test";
import { MEMBER, OWNER, PASS, loginAs, navTab, openTab } from "./helpers";

// The admin console is owner/admin only. Everything here runs as the seeded
// `ceo`, and every user it creates is stamped: the console is a shared surface
// and a fixed name would collide with itself on the second run.

let seq = 0;
const newMember = () => {
  const name = `e2e_member_${Date.now()}${seq++}`;
  return { username: name, email: `${name}@example.com`, password: PASS };
};

async function openAdmin(page) {
  await loginAs(page, OWNER);
  await openTab(page, "Admin");
  await expect(page.getByRole("heading", { name: /Admin console/i })).toBeVisible();
  // The console loads users, usage and billing together; wait for the table
  // rather than racing the "Loading…" placeholder.
  await expect(page.locator("table.admin-table")).toBeVisible({ timeout: 30_000 });
}

test("the console lists the organisation's users", async ({ page }) => {
  await openAdmin(page);

  const rows = page.locator("table.admin-table tbody tr");
  // The seeded org has an owner and a member; both are in the org, so both are
  // listed. Listing is scoped to the caller's org — this is the whole table.
  await expect(rows.filter({ hasText: OWNER.username })).toHaveCount(1);
  await expect(rows.filter({ hasText: MEMBER.username })).toHaveCount(1);
  await expect(rows.filter({ hasText: OWNER.username })).toContainText("owner");
  await expect(rows.filter({ hasText: MEMBER.username })).toContainText("active");
});

test("a user created here appears in the table and can sign in", async ({ page, browser }) => {
  await openAdmin(page);
  const member = newMember();

  await page.locator("#new-username").fill(member.username);
  await page.locator("#new-email").fill(member.email);
  await page.locator("#new-password").fill(member.password);
  await page.locator("#new-role").selectOption("member");
  await page.getByRole("button", { name: /add member/i }).click();

  // The form reloads the list on success, so the new row is the proof the write
  // landed — not just that the toast fired.
  await expect(page.getByText(`Added ${member.username}`)).toBeVisible();
  const row = page.locator("table.admin-table tbody tr").filter({ hasText: member.username });
  await expect(row).toHaveCount(1);
  await expect(row).toContainText("member");
  await expect(row).toContainText("active");

  // The account is real: a fresh browser signs in with it and gets a member's
  // dashboard, not an owner's.
  const context = await browser.newContext();
  const memberPage = await context.newPage();
  try {
    await loginAs(memberPage, member);
    await expect(memberPage.locator(".sidebar-role")).toContainText("member");
    await expect(navTab(memberPage, "Admin")).toHaveCount(0);
  } finally {
    await context.close();
  }
});

test("deactivating a user refuses their next sign-in", async ({ page, browser }) => {
  await openAdmin(page);
  const member = newMember();

  await page.locator("#new-username").fill(member.username);
  await page.locator("#new-email").fill(member.email);
  await page.locator("#new-password").fill(member.password);
  await page.getByRole("button", { name: /add member/i }).click();
  const row = page.locator("table.admin-table tbody tr").filter({ hasText: member.username });
  await expect(row).toContainText("active");

  await row.getByRole("button", { name: /deactivate/i }).click();
  await expect(page.getByText(`Deactivated ${member.username}`)).toBeVisible();
  await expect(row).toContainText("inactive");
  // The action inverts rather than disappearing — deactivation is reversible.
  await expect(row.getByRole("button", { name: /reactivate/i })).toBeVisible();

  const context = await browser.newContext();
  const memberPage = await context.newPage();
  try {
    await memberPage.goto("/login");
    await memberPage.getByPlaceholder("Username").fill(member.username);
    await memberPage.getByPlaceholder("Password").fill(member.password);
    await memberPage.getByRole("button", { name: /sign in/i }).click();

    // The API answers 403 "Account is disabled". The UI deliberately owns the
    // wording here (Auth/Login.jsx): the API's phrasing is accurate but tells
    // the user nothing to do next, and this is the one status where the client
    // knows better than the server what to say.
    await expect(memberPage.getByText(/account has been disabled/i)).toBeVisible();
    await expect(memberPage.locator(".login-card")).toBeVisible();
    await expect(memberPage.locator(".sidebar")).toHaveCount(0);
  } finally {
    await context.close();
  }
});

test("a member cannot reach the console — in the UI or behind it", async ({ page, baseURL }) => {
  await loginAs(page, MEMBER);

  // Nothing to click…
  await expect(navTab(page, "Admin")).toHaveCount(0);
  await expect(page.getByRole("heading", { name: /Admin console/i })).toHaveCount(0);
  await expect(page.getByText(/Team members/i)).toHaveCount(0);

  // …and nothing behind it either. A hidden tab is a UI convenience; the gate is
  // require_admin on the server, and that is what actually protects the data.
  const api = await request.newContext({ baseURL });
  try {
    const auth = await api.post("/api/auth/login", {
      data: { username: MEMBER.username, password: MEMBER.password },
    });
    expect(auth.ok()).toBe(true);
    const token = (await auth.json()).access_token;

    const users = await api.get("/api/users/", {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(users.status()).toBe(403);
    expect((await users.json()).detail).toMatch(/admin access required/i);
  } finally {
    await api.dispose();
  }
});
