// Shared plumbing for the e2e specs.
//
// Two kinds of identity are used across the suite, and the difference matters:
//
//   * The SEEDED accounts (`OWNER` / `MEMBER` below). `run.sh` starts the
//     backend against an empty throwaway database with SEED_DEMO_DATA=true, so
//     init_db creates the demo org with `ceo` (owner) and `manager` (member).
//     They are stable, need no registration, and give the role-gated screens a
//     second role to be checked from. Specs that only READ are welcome to them.
//
//   * A FRESH registered org (`freshIdentity()`). Anything that locks, disables
//     or deletes an account must use one of these, because the seeded pair is
//     shared by every later spec — locking `ceo` out for 15 minutes would take
//     the rest of the run down with it.
//
// Username and email are globally unique in the schema, so a fresh identity is
// always stamped: a fixed name collides on the second run against a persistent
// database.

import { expect } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";

const dir = path.dirname(fileURLToPath(import.meta.url));

export const fixture = (name) => path.join(dir, "fixtures", name);

export const SALES_CSV = fixture("sales.csv");
export const WIDE_CSV = fixture("wide.csv");
export const ANOMALIES_CSV = fixture("anomalies.csv");
// Deliberately boring: evenly spaced values, no gaps, no repeats — nothing for
// detect_anomalies to report. sales.csv is not this; it has outliers.
export const CLEAN_CSV = fixture("clean.csv");
export const BAD_TXT = fixture("bad.txt");
export const FAKE_CSV = fixture("fake.csv");

// Seeded by init_db when the database is empty and SEED_DEMO_DATA is on. The
// passwords are the ones run.sh passes to the backend.
export const OWNER = { username: "ceo", password: "Admin@2024", role: "owner" };
export const MEMBER = { username: "manager", password: "Manager@2024", role: "member" };

// Satisfies validate_password_strength (backend/app/core/security.py): 10–128
// characters, at least one letter, at least one digit.
export const PASS = "E2ePassw0rd!";

let counter = 0;

/** A never-before-seen org/user/email triple. */
export function freshIdentity(prefix = "e2e") {
  const stamp = `${Date.now()}${counter++}`;
  const username = `${prefix}_user_${stamp}`;
  return {
    org: `${prefix}-org-${stamp}`,
    username,
    // example.com, not .local/.test — pydantic's EmailStr rejects special-use
    // and reserved domains, so those never register.
    email: `${username}@example.com`,
    password: PASS,
    role: "owner",
  };
}

/** Register a new org and land in the dashboard. Returns the identity used. */
export async function registerOrg(page, identity = freshIdentity()) {
  await page.goto("/signup");
  await page.getByPlaceholder("Organization name").fill(identity.org);
  await page.getByPlaceholder("Username").fill(identity.username);
  await page.getByPlaceholder("Email").fill(identity.email);
  await page.getByPlaceholder(/Password/).fill(identity.password);
  await page.getByRole("button", { name: /create workspace/i }).click();
  await expectSignedIn(page);
  return identity;
}

/** Sign in through the real login form. */
export async function login(page, { username, password }) {
  await page.goto("/login");
  await page.getByPlaceholder("Username").fill(username);
  await page.getByPlaceholder("Password").fill(password);
  await page.getByRole("button", { name: /sign in/i }).click();
}

export async function loginAs(page, account) {
  await login(page, account);
  await expectSignedIn(page);
}

/** The dashboard shell is the proof of a session — the sidebar is always there. */
export async function expectSignedIn(page) {
  await expect(page.locator(".sidebar")).toBeVisible({ timeout: 30_000 });
}

/** Click one of the sidebar's nav buttons. Scoped to the nav, because
 *  "Upload Data" is also the label of the empty-chat CTA in <main>. */
export function navTab(page, label) {
  return page.getByRole("navigation", { name: "Main" }).getByRole("button", { name: label });
}

export async function openTab(page, label) {
  await navTab(page, label).click();
}

/**
 * Upload a dataset through the hidden file input react-dropzone renders.
 * Resolves once the chat greeting for the new session is on screen, which is
 * what proves the file reached the API and a DuckDB session was registered.
 */
export async function uploadDataset(page, file) {
  await openTab(page, "Upload Data");
  await page.locator('input[type="file"]').setInputFiles(file);
  await expect(page.getByText(/Data loaded!/i)).toBeVisible({ timeout: 60_000 });
}

export const composer = (page) => page.getByPlaceholder(/Ask a question about your data/i);

/**
 * Ask a question and wait for the assistant's reply to land.
 *
 * "Landed" is the disappearance of the in-flight indicator, not the appearance
 * of any particular result shape: chitchat, off-topic, errors and data results
 * all render differently, and every one of them is a legitimate answer here.
 * The timeout is the config's expect timeout — a CPU-bound inference is slow.
 */
export async function ask(page, question) {
  // The in-flight indicator is also a `.chat-msg.assistant`, but it has no
  // `.msg-text` — counting those counts settled replies only.
  const replies = page.locator(".chat-msg.assistant .msg-text");
  const before = await replies.count();

  const box = composer(page);
  await expect(box).toBeEnabled();
  await box.fill(question);
  await box.press("Enter");

  await expect(replies).toHaveCount(before + 1, { timeout: 150_000 });
  // `loading` is false again once the stage indicator is gone, which is also
  // when the composer unlocks.
  await expect(page.locator(".stage-indicator, .streaming-sql-wrapper")).toHaveCount(0);
}

/** The last assistant bubble on screen. */
export const lastAssistantMessage = (page) => page.locator(".chat-msg.assistant").last();

/** react-hot-toast renders its messages into a live region; match on text. */
export function toast(page, pattern) {
  return page.getByText(pattern);
}
