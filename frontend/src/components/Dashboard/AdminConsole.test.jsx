import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";
import AdminConsole from "./AdminConsole";
import {
  createUser,
  getBillingStatus,
  getUsage,
  listUsers,
  setUserActive,
} from "../../services/api";

// AdminConsole is the highest-risk untested component in #70's list: it is the
// only place in the product where one person's action changes another person's
// account. Deactivating the wrong user, or sending the inverse of the intended
// state, locks a colleague out of the workspace.
//
// Coverage before this file was 3.91% of statements and 0% of functions.

vi.mock("../../services/api", () => ({
  createUser: vi.fn(),
  getBillingStatus: vi.fn(),
  getUsage: vi.fn(),
  listUsers: vi.fn(),
  setUserActive: vi.fn(),
}));
vi.mock("react-hot-toast", () => ({
  default: { error: vi.fn(), success: vi.fn() },
}));

// Stubbed so these tests fail for AdminConsole's reasons only — BillingCard has
// its own suite. The stub records its props, which is how the `isOwner`
// permission boundary below is asserted.
const billingProps = vi.fn();
vi.mock("./BillingCard", () => ({
  default: (props) => {
    billingProps(props);
    return <div data-testid="billing-card" />;
  },
}));

const toast = (await import("react-hot-toast")).default;

const USERS = [
  { username: "ada", email: "ada@example.com", role: "owner", is_active: true },
  { username: "grace", email: "grace@example.com", role: "admin", is_active: true },
  { username: "alan", email: "alan@example.com", role: "member", is_active: false },
];

const USAGE = {
  plan: "pro",
  period: "2026-08",
  metrics: {
    llm_queries: { used: 1234, limit: 50000 },
    rows_processed: { used: 9_000_000, limit: 10_000_000 },  // 90% — warning
    uploads: { used: 5000, limit: 5000 },                     // 100% — full
    api_calls: { used: 42, limit: null },                     // unmetered
  },
};

const httpError = (status, data = {}) => ({ response: { status, data } });

function mockHappyPath({ users = USERS, usage = USAGE, billing = { plan: "pro" } } = {}) {
  getUsage.mockResolvedValue({ data: usage });
  listUsers.mockResolvedValue({ data: { users } });
  getBillingStatus.mockResolvedValue({ data: billing });
}

/** Render and wait past the async load gate. */
async function renderConsole(role = "owner") {
  render(<AdminConsole role={role} />);
  await waitFor(() => expect(screen.queryByText("Loading…")).not.toBeInTheDocument());
}

/** The <tr> for a username, so assertions can't accidentally match another row. */
const rowFor = (username) => screen.getByRole("row", { name: new RegExp(`\\b${username}\\b`) });

beforeEach(() => {
  vi.clearAllMocks();
  billingProps.mockClear();
});

// ── Loading ──────────────────────────────────────────────────────────────────

test("a loading state is shown until the data arrives", async () => {
  mockHappyPath();
  render(<AdminConsole role="owner" />);

  expect(screen.getByText("Loading…")).toBeInTheDocument();
  // The table must not render half-loaded — an empty team list reads as "no
  // colleagues" rather than "not fetched yet".
  expect(screen.queryByRole("table")).not.toBeInTheDocument();

  await waitFor(() => expect(screen.queryByText("Loading…")).not.toBeInTheDocument());
  expect(screen.getByRole("table")).toBeInTheDocument();
});

test("all three requests are issued on mount", async () => {
  mockHappyPath();
  await renderConsole();

  expect(getUsage).toHaveBeenCalledTimes(1);
  expect(listUsers).toHaveBeenCalledTimes(1);
  expect(getBillingStatus).toHaveBeenCalledTimes(1);
});

// ── Degrading when billing is unavailable ────────────────────────────────────

test("a billing failure hides the card without taking down the console", async () => {
  // Deliberate design (see the comment on `load`): an older backend with no
  // /api/billing must not cost an admin their team management. This is the only
  // one of the three requests that is allowed to fail quietly.
  mockHappyPath();
  getBillingStatus.mockRejectedValue(httpError(404));

  await renderConsole();

  expect(screen.getByRole("table")).toBeInTheDocument();
  expect(screen.getByText("grace")).toBeInTheDocument();
  expect(toast.error).not.toHaveBeenCalled();
  expect(billingProps).toHaveBeenCalledWith(expect.objectContaining({ billing: null }));
});

test("a usage failure does report an error", async () => {
  // The contrast with the test above: only billing is allowed to fail silently.
  mockHappyPath();
  getUsage.mockRejectedValue(new Error("boom"));

  await renderConsole();

  expect(toast.error).toHaveBeenCalledWith("Could not load the admin console");
});

test("a user-list failure does report an error", async () => {
  mockHappyPath();
  listUsers.mockRejectedValue(new Error("boom"));

  await renderConsole();

  expect(toast.error).toHaveBeenCalledWith("Could not load the admin console");
});

test("a failed load still clears the loading state", async () => {
  // The `finally`. Without it the console is stuck on "Loading…" forever and
  // the error toast is the only clue anything happened.
  mockHappyPath();
  listUsers.mockRejectedValue(new Error("boom"));

  render(<AdminConsole role="owner" />);
  await waitFor(() => expect(screen.queryByText("Loading…")).not.toBeInTheDocument());
});

test("a user list missing its array renders an empty table rather than crashing", async () => {
  // The `|| []` in `setUsers`. Written first as `mockHappyPath({ users: undefined })`,
  // which tested nothing: the helper's default parameter treats an explicit
  // undefined as "not passed" and substituted the full list. Mutation testing
  // caught it — the guard could be deleted with this test still green. Mock the
  // response shape directly so the key really is absent.
  mockHappyPath();
  listUsers.mockResolvedValue({ data: {} });
  await renderConsole();

  expect(screen.getByRole("table")).toBeInTheDocument();
  expect(screen.getAllByRole("row")).toHaveLength(1); // header only
});

// ── The permission boundary ──────────────────────────────────────────────────

test("the owner row has no deactivate button", async () => {
  // The one assertion in this file that is a security property rather than a
  // UX one: an admin must not be able to lock the owner out of the workspace.
  mockHappyPath();
  await renderConsole();

  expect(within(rowFor("ada")).queryByRole("button")).not.toBeInTheDocument();
  expect(within(rowFor("grace")).getByRole("button")).toBeInTheDocument();
  expect(within(rowFor("alan")).getByRole("button")).toBeInTheDocument();
});

test("isOwner is passed to BillingCard from the session role, not from the user list", async () => {
  // Plan changes are owner-only. `role` comes from the authed session held in
  // memory (#22) — deriving it from the rendered table would let a doctored
  // /users/ response unlock checkout.
  mockHappyPath();
  await renderConsole("admin");

  expect(billingProps).toHaveBeenCalledWith(expect.objectContaining({ isOwner: false }));

  billingProps.mockClear();
  vi.clearAllMocks();
  mockHappyPath();
  await renderConsole("owner");
  expect(billingProps).toHaveBeenCalledWith(expect.objectContaining({ isOwner: true }));
});

// ── Activating and deactivating ──────────────────────────────────────────────

test("deactivating sends the inverse of the user's current state", async () => {
  // Sending `is_active: true` for an active user is a silent no-op; sending it
  // for the wrong user locks out a colleague. Both the name and the flag matter.
  mockHappyPath();
  setUserActive.mockResolvedValue({});
  await renderConsole();

  await act(async () => {
    await userEvent.click(within(rowFor("grace")).getByRole("button", { name: /deactivate/i }));
  });

  expect(setUserActive).toHaveBeenCalledWith("grace", false);
});

test("reactivating an inactive user sends true", async () => {
  mockHappyPath();
  setUserActive.mockResolvedValue({});
  await renderConsole();

  await act(async () => {
    await userEvent.click(within(rowFor("alan")).getByRole("button", { name: /reactivate/i }));
  });

  expect(setUserActive).toHaveBeenCalledWith("alan", true);
});

test("the button label reflects what it will do, not the current state", async () => {
  mockHappyPath();
  await renderConsole();

  expect(within(rowFor("grace")).getByRole("button")).toHaveTextContent(/deactivate/i);
  expect(within(rowFor("alan")).getByRole("button")).toHaveTextContent(/reactivate/i);
});

test("the status cell shows each user's current state", async () => {
  mockHappyPath();
  await renderConsole();

  expect(within(rowFor("grace")).getByText("active")).toBeInTheDocument();
  expect(within(rowFor("alan")).getByText("inactive")).toBeInTheDocument();
});

test("a successful toggle re-reads the list rather than trusting local state", async () => {
  // The server is the authority on who is active. Patching state locally would
  // drift the moment another admin is making changes at the same time.
  mockHappyPath();
  setUserActive.mockResolvedValue({});
  await renderConsole();
  expect(listUsers).toHaveBeenCalledTimes(1);

  await act(async () => {
    await userEvent.click(within(rowFor("grace")).getByRole("button"));
  });

  await waitFor(() => expect(listUsers).toHaveBeenCalledTimes(2));
  expect(toast.success).toHaveBeenCalledWith("Deactivated grace");
});

test("a failed toggle reports it and does not claim success", async () => {
  mockHappyPath();
  setUserActive.mockRejectedValue(httpError(403));
  await renderConsole();

  await act(async () => {
    await userEvent.click(within(rowFor("grace")).getByRole("button"));
  });

  await waitFor(() => expect(toast.error).toHaveBeenCalledWith("Could not update the user"));
  expect(toast.success).not.toHaveBeenCalled();
});

// ── Adding a member ──────────────────────────────────────────────────────────

async function fillNewMember({ username = "linus", email = "linus@example.com",
                               password = "Temp0rary!", role } = {}) {
  await userEvent.type(screen.getByLabelText(/^username$/i), username);
  await userEvent.type(screen.getByLabelText(/^email$/i), email);
  await userEvent.type(screen.getByLabelText(/temporary password/i), password);
  if (role) await userEvent.selectOptions(screen.getByLabelText(/^role$/i), role);
  await act(async () => {
    await userEvent.click(screen.getByRole("button", { name: /add member/i }));
  });
  return { username, email, password };
}

test("creating a member submits what was typed and defaults the role to member", async () => {
  // The role default is a least-privilege choice: a mis-click must not mint an
  // admin.
  mockHappyPath();
  createUser.mockResolvedValue({});
  await renderConsole();

  const { username, email, password } = await fillNewMember();

  expect(createUser).toHaveBeenCalledWith(username, email, password, "member");
});

test("an explicitly chosen admin role is submitted", async () => {
  mockHappyPath();
  createUser.mockResolvedValue({});
  await renderConsole();

  await fillNewMember({ role: "admin" });

  expect(createUser).toHaveBeenCalledWith(
    expect.any(String), expect.any(String), expect.any(String), "admin"
  );
});

test("a successful creation clears the form and re-reads the list", async () => {
  // A form left populated invites a double submit, which 409s and reads to the
  // admin as a failure.
  mockHappyPath();
  createUser.mockResolvedValue({});
  await renderConsole();

  await fillNewMember();

  await waitFor(() => expect(listUsers).toHaveBeenCalledTimes(2));
  expect(screen.getByLabelText(/^username$/i)).toHaveValue("");
  expect(screen.getByLabelText(/^email$/i)).toHaveValue("");
  expect(screen.getByLabelText(/temporary password/i)).toHaveValue("");
  expect(toast.success).toHaveBeenCalledWith("Added linus");
});

test("a 409 says the account already exists", async () => {
  mockHappyPath();
  createUser.mockRejectedValue(httpError(409));
  await renderConsole();

  await fillNewMember();

  await waitFor(() =>
    expect(toast.error).toHaveBeenCalledWith("Username or email already exists")
  );
});

test("a 422 points at the details rather than blaming the server", async () => {
  mockHappyPath();
  createUser.mockRejectedValue(httpError(422));
  await renderConsole();

  await fillNewMember();

  await waitFor(() =>
    expect(toast.error).toHaveBeenCalledWith("Check the details and try again")
  );
});

test("any other failure falls back to a generic message", async () => {
  mockHappyPath();
  createUser.mockRejectedValue(new Error("Network Error"));
  await renderConsole();

  await fillNewMember();

  await waitFor(() => expect(toast.error).toHaveBeenCalledWith("Could not create the user"));
});

test("a failed creation keeps the form so the work is not lost", async () => {
  // The mirror of the reset-on-success test. Clearing on failure would make the
  // admin retype everything to fix one field.
  mockHappyPath();
  createUser.mockRejectedValue(httpError(409));
  await renderConsole();

  await fillNewMember();

  await waitFor(() => expect(toast.error).toHaveBeenCalled());
  expect(screen.getByLabelText(/^username$/i)).toHaveValue("linus");
});

test("the temporary password is masked and never echoed into a toast", async () => {
  // #32 was this bug on the backend — a 422 echoed the submitted password back.
  // Here an admin is typing someone else's initial credential.
  mockHappyPath();
  createUser.mockRejectedValue(httpError(422));
  await renderConsole();

  expect(screen.getByLabelText(/temporary password/i)).toHaveAttribute("type", "password");
  await fillNewMember({ password: "sup3r-secret-temp" });

  await waitFor(() => expect(toast.error).toHaveBeenCalled());
  for (const [message] of toast.error.mock.calls) {
    expect(String(message)).not.toContain("sup3r-secret-temp");
  }
});

test("the submit button is disabled and busy while creating", async () => {
  mockHappyPath();
  let resolve;
  createUser.mockReturnValue(new Promise((r) => { resolve = r; }));
  await renderConsole();

  await fillNewMember();

  const button = screen.getByRole("button", { name: /adding/i });
  expect(button).toBeDisabled();
  expect(button).toHaveAttribute("aria-busy", "true");

  await act(async () => { resolve({}); });
  await waitFor(() =>
    expect(screen.getByRole("button", { name: /add member/i })).not.toBeDisabled()
  );
});

test("the new-password field does not offer a saved credential", async () => {
  // An admin creating someone else's account must not have their own password
  // autofilled into it.
  mockHappyPath();
  await renderConsole();

  expect(screen.getByLabelText(/temporary password/i))
    .toHaveAttribute("autoComplete", "new-password");
});

// ── Usage card ───────────────────────────────────────────────────────────────

test("usage metrics render with the plan, period and thousands separators", async () => {
  mockHappyPath();
  await renderConsole();

  expect(screen.getByText(/pro plan/i)).toBeInTheDocument();
  expect(screen.getByText(/2026-08/)).toBeInTheDocument();
  // 1234 -> "1,234". A raw 9000000 is unreadable at a glance, which is the
  // whole point of a usage tile.
  expect(screen.getByText("1,234")).toBeInTheDocument();
  expect(screen.getByText(/50,000/)).toBeInTheDocument();
});

test("metric keys are humanised rather than shown as snake_case", async () => {
  mockHappyPath();
  await renderConsole();
  expect(screen.getByText("llm queries")).toBeInTheDocument();
  expect(screen.getByText("rows processed")).toBeInTheDocument();
});

test("an unmetered metric shows no limit and no bar", async () => {
  mockHappyPath({
    usage: { plan: "free", period: "2026-08", metrics: { api_calls: { used: 42, limit: null } } },
  });
  const { container } = render(<AdminConsole role="owner" />);
  await waitFor(() => expect(screen.queryByText("Loading…")).not.toBeInTheDocument());

  expect(screen.getByText("42")).toBeInTheDocument();
  expect(container.querySelector(".usage-limit")).toBeNull();
  expect(container.querySelector(".usage-bar")).toBeNull();
});

test("the bar warns at 80% and marks full at 100%", async () => {
  // Colour the bar before the wall, not at it. A quota that only turns red on
  // the day it blocks you gives no warning at all.
  mockHappyPath();
  const { container } = render(<AdminConsole role="owner" />);
  await waitFor(() => expect(screen.queryByText("Loading…")).not.toBeInTheDocument());

  const fills = [...container.querySelectorAll(".usage-bar-fill")].map((n) => n.className);
  expect(fills.some((c) => c.includes("is-warning"))).toBe(true);  // 90%
  expect(fills.some((c) => c.includes("is-full"))).toBe(true);     // 100%
  // 1234/50000 is ~2% and must be neither.
  expect(fills.some((c) => !c.includes("is-warning") && !c.includes("is-full"))).toBe(true);
});

test("the bar never overflows its track when usage exceeds the limit", async () => {
  // Overshoot is real: quota is checked before the work, so a single large
  // upload can land over the line. A 340%-wide div escapes the card.
  mockHappyPath({
    usage: { plan: "free", period: "2026-08", metrics: { rows: { used: 34, limit: 10 } } },
  });
  const { container } = render(<AdminConsole role="owner" />);
  await waitFor(() => expect(screen.queryByText("Loading…")).not.toBeInTheDocument());

  const fill = container.querySelector(".usage-bar-fill");
  expect(fill).toHaveStyle({ width: "100%" });
  expect(fill.className).toContain("is-full");
});

test("a zero limit does not produce an Infinity-wide bar", async () => {
  // `barState`'s `if (!limit)` guard. No plan defines a 0 limit today —
  // `PLAN_LIMITS` uses -1 for unlimited and `usage_summary` maps that to null
  // before it leaves the backend — so this is defensive, and the thing it
  // defends against is `used / 0` giving a bar `Infinity%` wide.
  mockHappyPath({
    usage: { plan: "free", period: "2026-08", metrics: { seats: { used: 3, limit: 0 } } },
  });
  const { container } = render(<AdminConsole role="owner" />);
  await waitFor(() => expect(screen.queryByText("Loading…")).not.toBeInTheDocument());

  const fill = container.querySelector(".usage-bar-fill");
  expect(fill).toHaveStyle({ width: "100%" });
  expect(fill.style.width).not.toContain("Infinity");
});

test("no usage payload renders no usage section at all", async () => {
  mockHappyPath({ usage: null });
  await renderConsole();

  expect(screen.queryByRole("region", { name: /usage/i })).not.toBeInTheDocument();
  // …but the rest of the console still works.
  expect(screen.getByRole("table")).toBeInTheDocument();
});

test("usage with no metrics object does not crash the console", async () => {
  mockHappyPath({ usage: { plan: "free", period: "2026-08" } });
  await renderConsole();
  expect(screen.getByText(/free plan/i)).toBeInTheDocument();
});

// ── Accessibility ────────────────────────────────────────────────────────────

test("each section is a labelled region and the table has column headers", async () => {
  mockHappyPath();
  await renderConsole();

  expect(screen.getByRole("region", { name: /usage/i })).toBeInTheDocument();
  expect(screen.getByRole("region", { name: /team members/i })).toBeInTheDocument();
  expect(screen.getByRole("region", { name: /add a team member/i })).toBeInTheDocument();

  const headers = screen.getAllByRole("columnheader");
  expect(headers.map((h) => h.textContent)).toEqual([
    "Username", "Email", "Role", "Status", "Actions",
  ]);
  headers.forEach((h) => expect(h).toHaveAttribute("scope", "col"));
});

test("every form control has a label, not just a placeholder", async () => {
  mockHappyPath();
  await renderConsole();

  expect(screen.getByLabelText(/^username$/i)).toHaveAttribute("id", "new-username");
  expect(screen.getByLabelText(/^email$/i)).toHaveAttribute("id", "new-email");
  expect(screen.getByLabelText(/temporary password/i)).toHaveAttribute("id", "new-password");
  expect(screen.getByLabelText(/^role$/i)).toHaveAttribute("id", "new-role");
});

test("the email field is typed so mobile keyboards and validation cooperate", async () => {
  mockHappyPath();
  await renderConsole();
  expect(screen.getByLabelText(/^email$/i)).toHaveAttribute("type", "email");
});
