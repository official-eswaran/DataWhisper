import { expect, test } from "@playwright/test";
import { OWNER, PASS, expectSignedIn, freshIdentity, login, loginAs, registerOrg } from "./helpers";

// Everything here is about who gets in and who is told why they didn't.
//
// The login form reports failures through react-hot-toast, and the wording is
// deliberately not uniform (see loginErrorMessage in Auth/Login.jsx): the
// remaining-attempt count and the lockout notice exist only in the API's
// `detail`, and collapsing them into "Invalid credentials" is the bug #77
// fixed. So these assert on the *specific* sentence a user would read, not just
// that something red appeared.
//
// Any test that burns login attempts registers its own throwaway org. The
// seeded `ceo` account is shared with every later spec, and locking it out for
// 15 minutes would take the rest of the run with it.

/** Register through the UI, then log out — leaves a known-good account that
 *  nothing is currently signed in to. */
async function registerAndSignOut(page) {
  const identity = await registerOrg(page);
  await page.getByRole("button", { name: /logout/i }).click();
  await expect(page.locator(".login-card")).toBeVisible();
  return identity;
}

test("valid credentials land on the dashboard", async ({ page }) => {
  await loginAs(page, OWNER);

  // The sidebar is the dashboard shell, and the role it prints is the role the
  // API returned — not something the client guessed.
  await expect(page.locator(".sidebar-role")).toContainText(OWNER.role);
  await expect(page).toHaveURL(/\/$/);
});

test("a wrong password names the failure and counts down the attempts left", async ({ page }) => {
  const user = await registerAndSignOut(page);

  await login(page, { username: user.username, password: "WrongPassw0rd!" });

  // MAX_LOGIN_ATTEMPTS is 5 and this is the first failure, so the API says four
  // remain. The count is the point: it is the difference between "you mistyped"
  // and "one more and you are locked out", and it exists nowhere but here.
  await expect(page.getByText(/4 attempt\(s\) remaining/i)).toBeVisible();
  await expect(page.locator(".login-card")).toBeVisible();
});

test("five failures lock the account, and the sixth attempt says so", async ({ page }) => {
  const user = await registerAndSignOut(page);
  const wrong = { username: user.username, password: "WrongPassw0rd!" };

  // Attempts 1–4 count down: 4, 3, 2, 1 remaining.
  for (const remaining of [4, 3, 2, 1]) {
    await login(page, wrong);
    await expect(page.getByText(new RegExp(`${remaining} attempt\\(s\\) remaining`, "i"))).toBeVisible();
  }

  // Attempt 5 trips the lock. Still a 401 — the lock is applied as this attempt
  // is rejected, so the message says so rather than reporting an existing lock.
  await login(page, wrong);
  await expect(page.getByText(/Account locked for 15 minutes/i)).toBeVisible();

  // Attempt 6 is refused before the password is even checked: 429, the message
  // the lockout exists to deliver.
  await login(page, wrong);
  await expect(page.getByText(/Account locked due to too many failed attempts/i)).toBeVisible();

  // And the lock is not a display state — the *right* password is refused too.
  await login(page, { username: user.username, password: PASS });
  await expect(page.getByText(/Account locked due to too many failed attempts/i)).toBeVisible();
  await expect(page.locator(".login-card")).toBeVisible();
});

test.describe("signup validation", () => {
  /** Fill the signup form without submitting. */
  async function fillSignup(page, { org, username, email, password }) {
    await page.goto("/signup");
    await page.getByPlaceholder("Organization name").fill(org);
    await page.getByPlaceholder("Username").fill(username);
    await page.getByPlaceholder("Email").fill(email);
    await page.getByPlaceholder(/Password/).fill(password);
  }

  test("a password under 10 characters never leaves the browser", async ({ page }) => {
    const id = freshIdentity();
    await fillSignup(page, { ...id, password: "Short1!" });

    // The field carries minLength={10}, so the browser blocks submission itself
    // — there is no round trip to fail. Asserting on `tooShort` is asserting on
    // the constraint the user actually hits.
    let posted = false;
    page.on("request", (r) => {
      if (r.url().includes("/api/auth/register")) posted = true;
    });
    await page.getByRole("button", { name: /create workspace/i }).click();

    const pwd = page.getByPlaceholder(/Password/);
    expect(await pwd.evaluate((el) => el.validity.tooShort)).toBe(true);
    expect(posted).toBe(false);
    await expect(page.locator(".login-card")).toBeVisible();
  });

  test("a password with no digit is refused, and says which rule it broke", async ({ page }) => {
    const id = freshIdentity();
    await fillSignup(page, { ...id, password: "NoDigitsHere!" });
    await page.getByRole("button", { name: /create workspace/i }).click();

    // The API's own field error is surfaced rather than a generic "check your
    // details" — most signup failures here are the password rule, and the user
    // cannot guess which half of it they missed.
    await expect(page.getByText(/Password must contain a digit/i)).toBeVisible();
  });

  test("a password with no letter is refused, and says which rule it broke", async ({ page }) => {
    const id = freshIdentity();
    await fillSignup(page, { ...id, password: "1234567890!" });
    await page.getByRole("button", { name: /create workspace/i }).click();

    await expect(page.getByText(/Password must contain a letter/i)).toBeVisible();
  });

  test("a duplicate username is refused", async ({ page }) => {
    const taken = await registerAndSignOut(page);
    const fresh = freshIdentity();

    await fillSignup(page, { ...fresh, username: taken.username, password: PASS });
    await page.getByRole("button", { name: /create workspace/i }).click();

    await expect(page.getByText(/already taken/i)).toBeVisible();
    await expect(page.locator(".login-card")).toBeVisible();
  });

  test("a duplicate email is refused", async ({ page }) => {
    const taken = await registerAndSignOut(page);
    const fresh = freshIdentity();

    await fillSignup(page, { ...fresh, email: taken.email, password: PASS });
    await page.getByRole("button", { name: /create workspace/i }).click();

    await expect(page.getByText(/already taken/i)).toBeVisible();
  });

  test("a reserved domain is refused by the email validator", async ({ page }) => {
    const id = freshIdentity();
    // `.local` is special-use, and pydantic's EmailStr rejects it. It passes the
    // browser's own type=email check, so this really does reach the API — which
    // is why the fixtures elsewhere in this suite all use example.com.
    await fillSignup(page, { ...id, email: `${id.username}@example.local`, password: PASS });
    await page.getByRole("button", { name: /create workspace/i }).click();

    await expect(page.getByText(/not a valid email address/i)).toBeVisible();
    await expect(page.locator(".login-card")).toBeVisible();
  });
});

test("logout returns to the login screen, and Back does not undo it", async ({ page }) => {
  await loginAs(page, OWNER);

  await page.getByRole("button", { name: /logout/i }).click();
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.locator(".login-card")).toBeVisible();

  // The access token lived in memory and the refresh cookie has been revoked
  // and cleared, so there is nothing left for a history entry to restore.
  await page.goBack();
  await expect(page.locator(".login-card")).toBeVisible();
  await expect(page.locator(".sidebar")).toHaveCount(0);
});

test("a reload keeps the session and never flashes the login screen", async ({ page }) => {
  await loginAs(page, OWNER);

  // The access token is memory-only, so a reload starts with no session and
  // re-mints one from the httpOnly refresh cookie. App.jsx holds the routes back
  // behind `booting` for exactly this reason — if it didn't, every refresh would
  // show a frame of the login form. A `toBeVisible` check after the fact cannot
  // see a frame that has already gone, so this watches from before the reload.
  await page.addInitScript(() => {
    window.__sawLoginCard = false;
    const check = () => {
      if (document.querySelector(".login-card")) window.__sawLoginCard = true;
    };
    const start = () => {
      check();
      new MutationObserver(check).observe(document.body, { childList: true, subtree: true });
    };
    if (document.body) start();
    else document.addEventListener("DOMContentLoaded", start);
  });

  await page.reload();
  await expectSignedIn(page);
  expect(await page.evaluate(() => window.__sawLoginCard)).toBe(false);
});

test("routes redirect on the wrong side of the session", async ({ page }) => {
  // Anonymous: anything under /* bounces to the login screen.
  await page.goto("/");
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.locator(".login-card")).toBeVisible();

  await loginAs(page, OWNER);

  // Authenticated: /login and /signup bounce home rather than offering a second
  // session to someone who already has one.
  await page.goto("/login");
  await expect(page).toHaveURL(/\/$/);
  await expectSignedIn(page);

  await page.goto("/signup");
  await expect(page).toHaveURL(/\/$/);
  await expectSignedIn(page);
});
