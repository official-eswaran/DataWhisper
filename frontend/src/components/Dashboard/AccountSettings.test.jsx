import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import AccountSettings from "./AccountSettings";
import { deleteMyAccount, deleteMyOrganization, exportMyData } from "../../services/api";

// Every button on this page is irreversible or hands over personal data. It is
// the only component in the product that can delete an entire organization —
// all users, sessions and datasets — and the only guard in front of that is a
// window.confirm. Coverage was 6.06% of statements and 0% of functions.
//
// The tests below are weighted accordingly: the ones that matter are the ones
// asserting that nothing happens.

vi.mock("../../services/api", () => ({
  deleteMyAccount: vi.fn(),
  deleteMyOrganization: vi.fn(),
  exportMyData: vi.fn(),
}));
vi.mock("react-hot-toast", () => ({
  default: { error: vi.fn(), success: vi.fn() },
}));

const toast = (await import("react-hot-toast")).default;

const httpError = (status, detail) => ({ response: { status, data: { detail } } });

/** Read a Blob's contents. jsdom's Blob has no `.text()`, but FileReader works. */
const readBlob = (blob) =>
  new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(reader.error);
    reader.readAsText(blob);
  });

// jsdom implements none of the download path, so it is stubbed rather than
// mocked away — the assertions below are about what the component asked the
// browser to do.
let createdBlobs;
let revoked;
let anchorClicks;

beforeEach(() => {
  vi.clearAllMocks();
  createdBlobs = [];
  revoked = [];
  anchorClicks = [];

  // Assigned rather than spied: jsdom does not implement the object-URL API at
  // all, and `vi.spyOn` cannot attach to a method that does not exist.
  URL.createObjectURL = vi.fn((blob) => {
    createdBlobs.push(blob);
    return `blob:mock/${createdBlobs.length}`;
  });
  URL.revokeObjectURL = vi.fn((url) => revoked.push(url));
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function () {
    anchorClicks.push({ href: this.href, download: this.download });
  });
  // jsdom's confirm is a no-op returning undefined, which would read as
  // "cancelled" and make every destructive test vacuously pass.
  vi.spyOn(window, "confirm").mockReturnValue(true);
});

afterEach(() => {
  vi.restoreAllMocks();
  // Not covered by restoreAllMocks — these were assigned onto a global that had
  // no such properties to begin with.
  delete URL.createObjectURL;
  delete URL.revokeObjectURL;
});

const renderSettings = (role = "owner", onLogout = vi.fn()) => {
  render(<AccountSettings role={role} onLogout={onLogout} />);
  return onLogout;
};

const click = async (name) =>
  act(async () => {
    await userEvent.click(screen.getByRole("button", { name }));
  });

// ── The confirm gate: nothing happens unless the user agrees ─────────────────

test("cancelling the confirm does not delete the account", async () => {
  // The single most important assertion in this file. `window.confirm` is the
  // only thing between a misclick and an irreversible delete.
  window.confirm.mockReturnValue(false);
  const onLogout = renderSettings();

  await click(/delete account/i);

  expect(deleteMyAccount).not.toHaveBeenCalled();
  expect(onLogout).not.toHaveBeenCalled();
  expect(toast.success).not.toHaveBeenCalled();
});

test("cancelling the confirm does not delete the organization", async () => {
  // Same guard, far larger blast radius: every user, session and dataset.
  window.confirm.mockReturnValue(false);
  const onLogout = renderSettings("owner");

  await click(/delete organization/i);

  expect(deleteMyOrganization).not.toHaveBeenCalled();
  expect(onLogout).not.toHaveBeenCalled();
});

test("the account confirm says the deletion is permanent", async () => {
  // A confirm that does not say what it does is not consent. Asserting the text
  // stops it being softened into "Are you sure?" later.
  deleteMyAccount.mockResolvedValue({});
  renderSettings();

  await click(/delete account/i);

  expect(window.confirm).toHaveBeenCalledWith(
    expect.stringMatching(/permanently\?? this cannot be undone/i)
  );
});

test("the organization confirm spells out the blast radius", async () => {
  // "Delete the organization?" would understate it — the user needs to know
  // this removes other people's accounts and data too, not just their own.
  deleteMyOrganization.mockResolvedValue({});
  renderSettings("owner");

  await click(/delete organization/i);

  const [message] = window.confirm.mock.calls[0];
  expect(message).toMatch(/all users/i);
  expect(message).toMatch(/data/i);
  expect(message).toMatch(/cannot be undone/i);
});

// ── The permission boundary ──────────────────────────────────────────────────

test("only an owner is offered organization deletion", async () => {
  // Rendered from the session role (#22), so a member cannot reach the control
  // at all. The backend also enforces this with a 403 — the UI is the first
  // gate, not the only one.
  for (const role of ["member", "admin"]) {
    const { unmount } = render(<AccountSettings role={role} onLogout={vi.fn()} />);
    expect(screen.queryByRole("button", { name: /delete organization/i })).not.toBeInTheDocument();
    // …but they can still delete their own account.
    expect(screen.getByRole("button", { name: /delete account/i })).toBeInTheDocument();
    unmount();
  }
});

test("an owner sees both destructive controls", async () => {
  renderSettings("owner");
  expect(screen.getByRole("button", { name: /delete account/i })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /delete organization/i })).toBeInTheDocument();
});

// ── After a delete, the session must not survive ─────────────────────────────

test("deleting the account logs the user out", async () => {
  // The account is gone; leaving the session up means every subsequent request
  // 401s against a user that no longer exists.
  deleteMyAccount.mockResolvedValue({});
  const onLogout = renderSettings();

  await click(/delete account/i);

  await waitFor(() => expect(onLogout).toHaveBeenCalledTimes(1));
  expect(deleteMyAccount).toHaveBeenCalledTimes(1);
  expect(toast.success).toHaveBeenCalledWith("Account deleted");
});

test("deleting the organization logs the user out", async () => {
  deleteMyOrganization.mockResolvedValue({});
  const onLogout = renderSettings("owner");

  await click(/delete organization/i);

  await waitFor(() => expect(onLogout).toHaveBeenCalledTimes(1));
  expect(toast.success).toHaveBeenCalledWith("Organization deleted");
});

test("a failed account deletion does not log the user out", async () => {
  // Logging out on failure would strand the user outside an account that still
  // exists, with no indication the delete never happened.
  deleteMyAccount.mockRejectedValue(httpError(409, "You are the only owner."));
  const onLogout = renderSettings();

  await click(/delete account/i);

  await waitFor(() => expect(toast.error).toHaveBeenCalled());
  expect(onLogout).not.toHaveBeenCalled();
  expect(toast.success).not.toHaveBeenCalled();
});

test("a failed organization deletion does not log the user out", async () => {
  deleteMyOrganization.mockRejectedValue(httpError(403, "Only an organization owner can delete it"));
  const onLogout = renderSettings("owner");

  await click(/delete organization/i);

  await waitFor(() => expect(toast.error).toHaveBeenCalled());
  expect(onLogout).not.toHaveBeenCalled();
});

// ── Failure messages carry the reason the API gave ───────────────────────────

test("the only-owner conflict is shown verbatim, because it says what to do", async () => {
  // The backend's 409 is genuinely actionable: "assign another owner first" is
  // the fix. A generic "Could not delete your account" would strand the user.
  const detail = "You are the only owner. Delete the organization or assign another owner first.";
  deleteMyAccount.mockRejectedValue(httpError(409, detail));
  renderSettings();

  await click(/delete account/i);

  await waitFor(() => expect(toast.error).toHaveBeenCalledWith(detail));
});

test("the forbidden reason for an org delete is shown verbatim", async () => {
  const detail = "Only an organization owner can delete it";
  deleteMyOrganization.mockRejectedValue(httpError(403, detail));
  renderSettings("owner");

  await click(/delete organization/i);

  await waitFor(() => expect(toast.error).toHaveBeenCalledWith(detail));
});

test("a failure with no detail falls back to a message of our own", async () => {
  deleteMyAccount.mockRejectedValue(new Error("Network Error"));
  renderSettings();

  await click(/delete account/i);

  await waitFor(() =>
    expect(toast.error).toHaveBeenCalledWith("Could not delete your account")
  );
});

test("an org failure with no detail falls back too", async () => {
  deleteMyOrganization.mockRejectedValue(new Error("Network Error"));
  renderSettings("owner");

  await click(/delete organization/i);

  await waitFor(() =>
    expect(toast.error).toHaveBeenCalledWith("Could not delete the organization")
  );
});

// ── GDPR export ──────────────────────────────────────────────────────────────

test("the export downloads exactly what the API returned", async () => {
  // Data portability is a legal obligation, so the file has to be the record —
  // not a summary, and not silently truncated.
  const payload = { user: { username: "ada" }, sessions: [{ id: 1 }], queries: [] };
  exportMyData.mockResolvedValue({ data: payload });
  renderSettings();

  await click(/export my data/i);

  await waitFor(() => expect(createdBlobs).toHaveLength(1));
  expect(JSON.parse(await readBlob(createdBlobs[0]))).toEqual(payload);
  expect(createdBlobs[0].type).toBe("application/json");
});

test("the export is pretty-printed rather than one long line", async () => {
  // The user is the audience for this file, not a parser.
  exportMyData.mockResolvedValue({ data: { user: { username: "ada" } } });
  renderSettings();

  await click(/export my data/i);

  await waitFor(() => expect(createdBlobs).toHaveLength(1));
  expect(await readBlob(createdBlobs[0])).toContain("\n");
});

test("the download has a meaningful filename", async () => {
  exportMyData.mockResolvedValue({ data: {} });
  renderSettings();

  await click(/export my data/i);

  await waitFor(() => expect(anchorClicks).toHaveLength(1));
  expect(anchorClicks[0].download).toBe("datawhisper-my-data.json");
  expect(anchorClicks[0].href).toMatch(/^blob:/);
});

test("the object URL is revoked so the blob is not held for the tab's life", async () => {
  // Without the revoke, every export pins its payload in memory until the tab
  // closes — and this payload is the user's entire personal record.
  exportMyData.mockResolvedValue({ data: {} });
  renderSettings();

  await click(/export my data/i);

  await waitFor(() => expect(revoked).toHaveLength(1));
  expect(revoked[0]).toBe(anchorClicks[0].href);
});

test("a failed export says so and downloads nothing", async () => {
  exportMyData.mockRejectedValue(new Error("Network Error"));
  renderSettings();

  await click(/export my data/i);

  await waitFor(() => expect(toast.error).toHaveBeenCalledWith("Could not export your data"));
  expect(anchorClicks).toHaveLength(0);
  expect(toast.success).not.toHaveBeenCalled();
});

// ── Busy state ───────────────────────────────────────────────────────────────

test("the export button reports progress and re-enables afterwards", async () => {
  let resolve;
  exportMyData.mockReturnValue(new Promise((r) => { resolve = r; }));
  renderSettings();

  await click(/export my data/i);

  const button = screen.getByRole("button", { name: /preparing/i });
  expect(button).toBeDisabled();

  await act(async () => { resolve({ data: {} }); });
  await waitFor(() =>
    expect(screen.getByRole("button", { name: /export my data/i })).not.toBeDisabled()
  );
});

test("a failed export re-enables the button so it can be retried", async () => {
  // The `finally`. Data portability is a right — a dead button after one
  // network blip is not an acceptable answer to it.
  exportMyData.mockRejectedValue(new Error("Network Error"));
  renderSettings();

  await click(/export my data/i);

  await waitFor(() =>
    expect(screen.getByRole("button", { name: /export my data/i })).not.toBeDisabled()
  );
});

test("busy is per-action: exporting does not disable the delete buttons", async () => {
  // `busy` is one string compared against a key, so only the acting button is
  // disabled. Pinned as the intended reading rather than an oversight.
  let resolve;
  exportMyData.mockReturnValue(new Promise((r) => { resolve = r; }));
  renderSettings("owner");

  await click(/export my data/i);

  expect(screen.getByRole("button", { name: /delete account/i })).not.toBeDisabled();
  expect(screen.getByRole("button", { name: /delete organization/i })).not.toBeDisabled();

  await act(async () => { resolve({ data: {} }); });
});

test("a failed delete re-enables its button", async () => {
  deleteMyAccount.mockRejectedValue(new Error("Network Error"));
  renderSettings();

  await click(/delete account/i);

  await waitFor(() =>
    expect(screen.getByRole("button", { name: /delete account/i })).not.toBeDisabled()
  );
});

// ── Accessibility and framing ────────────────────────────────────────────────

test("both sections are labelled regions", async () => {
  renderSettings("owner");
  expect(screen.getByRole("region", { name: /your data/i })).toBeInTheDocument();
  expect(screen.getByRole("region", { name: /danger zone/i })).toBeInTheDocument();
});

test("the destructive controls are separated into a danger zone", async () => {
  // The visual grouping is the warning. If deletion sat next to Export in the
  // same block, the two would read as equally routine.
  const { container } = render(<AccountSettings role="owner" onLogout={vi.fn()} />);
  const danger = container.querySelector(".danger-zone");

  expect(danger).not.toBeNull();
  expect(danger).toContainElement(screen.getByRole("button", { name: /delete account/i }));
  expect(danger).not.toContainElement(screen.getByRole("button", { name: /export my data/i }));
});

test("the export is described as GDPR data portability", async () => {
  // Names the right the button exercises, so a user looking for "my GDPR
  // request" can find it.
  renderSettings();
  expect(screen.getByText(/gdpr data portability/i)).toBeInTheDocument();
});
