import { expect, test } from "@playwright/test";
import { MEMBER, OWNER, loginAs, openTab, registerOrg } from "./helpers";

// Account settings is the self-service GDPR surface: export what we hold, and
// delete the account or the whole org. The destructive halves are guarded by a
// window.confirm, which Playwright auto-dismisses unless a test opts in — so
// "dismissed" is the default here, and only the very last test opts in, against
// an org registered for the purpose.

test("the account screen reflects the signed-in role", async ({ page }) => {
  await loginAs(page, OWNER);
  await openTab(page, "Account");

  await expect(page.getByRole("heading", { name: /Account settings/i })).toBeVisible();
  // The role is on screen throughout, in the sidebar footer. (The Account view
  // itself never prints it — see the suite README.) What the view *does* vary by
  // role is the danger zone: only an owner is offered the org-wide delete.
  await expect(page.locator(".sidebar-role")).toContainText("owner");
  await expect(page.getByRole("button", { name: /delete organization/i })).toBeVisible();
});

test("a member is not offered the organisation-wide delete", async ({ page }) => {
  await loginAs(page, MEMBER);
  await openTab(page, "Account");

  await expect(page.locator(".sidebar-role")).toContainText("member");
  // Deleting the org takes every other user's data with it. A member can leave;
  // they cannot close the office.
  await expect(page.getByRole("button", { name: /delete organization/i })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /delete account/i })).toBeVisible();
});

test("Export my data downloads a file", async ({ page }) => {
  await loginAs(page, OWNER);
  await openTab(page, "Account");

  const [download] = await Promise.all([
    page.waitForEvent("download"),
    page.getByRole("button", { name: /export my data/i }).click(),
  ]);

  expect(download.suggestedFilename()).toBe("datawhisper-my-data.json");
  await expect(page.getByText(/data export has downloaded/i)).toBeVisible();
});

test("the destructive actions do nothing until the confirmation is accepted", async ({ page }) => {
  await loginAs(page, OWNER);
  await openTab(page, "Account");

  const dismissed = [];
  page.on("dialog", (dialog) => {
    dismissed.push(dialog.message());
    return dialog.dismiss();
  });

  // Both buttons exist, and both ask first. The prompt has to name what is
  // about to be lost — "are you sure?" is not informed consent when the answer
  // takes every user and dataset in the org with it.
  await page.getByRole("button", { name: /delete account/i }).click();
  await page.getByRole("button", { name: /delete organization/i }).click();

  await expect.poll(() => dismissed.length).toBe(2);
  expect(dismissed[0]).toMatch(/cannot be undone/i);
  expect(dismissed[1]).toMatch(/all users, sessions, and data/i);

  // Cancelled means nothing happened: still signed in, still on the account
  // screen, no request went out.
  await expect(page.locator(".sidebar")).toBeVisible();
  await expect(page.getByRole("heading", { name: /Account settings/i })).toBeVisible();
});

// Last in the file, deliberately. This one goes through with it — against an org
// registered seconds earlier that nothing else in the suite refers to.
test("confirming the organisation delete really deletes it", async ({ page }) => {
  const identity = await registerOrg(page);
  await openTab(page, "Account");

  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: /delete organization/i }).click();

  // Deleting the org logs the owner out — there is nothing left to be signed in
  // to.
  await expect(page.getByText(/Organization deleted/i)).toBeVisible();
  await expect(page.locator(".login-card")).toBeVisible({ timeout: 30_000 });

  // And the credentials are gone with it: the account cannot sign back in.
  await page.getByPlaceholder("Username").fill(identity.username);
  await page.getByPlaceholder("Password").fill(identity.password);
  await page.getByRole("button", { name: /sign in/i }).click();
  await expect(page.getByText(/Invalid username or password/i)).toBeVisible();
  await expect(page.locator(".sidebar")).toHaveCount(0);
});
