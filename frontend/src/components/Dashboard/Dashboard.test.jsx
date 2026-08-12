import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import Dashboard from "./Dashboard";

// Dashboard is the composition root behind the login wall: it owns which view
// is showing, holds the uploaded session, and is where Stripe drops the user
// back after checkout. Coverage was 15.71% of statements and 0% of functions.
//
// The children are stubbed. Each has its own suite (or is next in #70), and the
// question here is routing and hand-off, not what FileUpload renders.

// `toast(...)` is called directly for the cancel case *and* as `toast.success`,
// so the default export has to be a callable with methods hung off it.
vi.mock("react-hot-toast", () => {
  const fn = vi.fn();
  fn.success = vi.fn();
  fn.error = vi.fn();
  return { default: fn };
});

const sidebarProps = vi.fn();
const TABS = ["upload", "chat", "audit", "admin", "account"];

vi.mock("./Sidebar", () => ({
  default: (props) => {
    sidebarProps(props);
    return (
      <nav data-testid="sidebar">
        {TABS.map((t) => (
          <button key={t} onClick={() => props.onTabChange(t)}>{`go-${t}`}</button>
        ))}
        <button onClick={props.onLogout}>sidebar-logout</button>
      </nav>
    );
  },
}));

vi.mock("../Upload/FileUpload", () => ({
  default: ({ onUploadSuccess }) => (
    <button onClick={() => onUploadSuccess({ session_id: "sess-1", filename: "sales.csv" })}>
      finish-upload
    </button>
  ),
}));

vi.mock("../Chat/ChatWindow", () => ({
  default: ({ session }) => <div data-testid="chat">chat:{session?.session_id}</div>,
}));

vi.mock("./AuditLogs", () => ({ default: () => <div data-testid="audit" /> }));

const adminProps = vi.fn();
vi.mock("./AdminConsole", () => ({
  default: (props) => {
    adminProps(props);
    return <div data-testid="admin" />;
  },
}));

const accountProps = vi.fn();
vi.mock("./AccountSettings", () => ({
  default: (props) => {
    accountProps(props);
    return <div data-testid="account" />;
  },
}));

const toast = (await import("react-hot-toast")).default;

/** Put a query string on the URL before mounting, the way Stripe's redirect does. */
const setUrl = (search) =>
  window.history.replaceState({}, "", search ? `/?${search}` : "/");

const renderDashboard = ({ role = "owner", onLogout = vi.fn() } = {}) => {
  render(<Dashboard auth={{ role }} onLogout={onLogout} />);
  return onLogout;
};

const goTo = async (tab) =>
  act(async () => {
    await userEvent.click(screen.getByRole("button", { name: `go-${tab}` }));
  });

beforeEach(() => {
  vi.clearAllMocks();
  sidebarProps.mockClear();
  adminProps.mockClear();
  accountProps.mockClear();
  setUrl("");
});

afterEach(() => {
  vi.restoreAllMocks();
  setUrl("");
});

// ── Default landing ──────────────────────────────────────────────────────────

test("without a Stripe marker the user lands on chat", async () => {
  renderDashboard();
  expect(screen.getByText(/no data loaded/i)).toBeInTheDocument();
  expect(toast).not.toHaveBeenCalled();
  expect(toast.success).not.toHaveBeenCalled();
});

test("a plain load does not rewrite the URL", async () => {
  // replaceState is only for clearing the Stripe marker. Firing it every mount
  // would stamp on any other query string the app is given.
  const replaceState = vi.spyOn(window.history, "replaceState");
  renderDashboard();
  expect(replaceState).not.toHaveBeenCalled();
});

// ── The Stripe round trip ────────────────────────────────────────────────────

test("returning from a successful checkout lands an admin on the billing view", async () => {
  // The point of the marker: the round trip should visibly end where it
  // started, not dump the user back on the chat tab wondering if it worked.
  setUrl("status=success");
  renderDashboard({ role: "owner" });

  expect(screen.getByTestId("admin")).toBeInTheDocument();
});

test("a cancelled checkout also lands on the billing view", async () => {
  // Any marker means "you just came back from Stripe", not "you paid".
  setUrl("status=cancel");
  renderDashboard({ role: "owner" });

  expect(screen.getByTestId("admin")).toBeInTheDocument();
});

test("a non-admin returning with a marker is not sent to a view they cannot see", async () => {
  setUrl("status=success");
  renderDashboard({ role: "member" });

  expect(screen.queryByTestId("admin")).not.toBeInTheDocument();
  expect(screen.getByText(/no data loaded/i)).toBeInTheDocument();
});

test("the success toast does not promise an upgrade that has not landed", async () => {
  // The plan changes on the Stripe webhook, not on this redirect, so it can lag
  // the user's return. "You're on Pro" would be a claim we cannot back up yet —
  // and if the webhook then fails, a lie. The wording has to stay hedged.
  setUrl("status=success");
  renderDashboard();

  expect(toast.success).toHaveBeenCalledWith(
    expect.stringMatching(/payment received/i)
  );
  const [message] = toast.success.mock.calls[0];
  expect(message).toMatch(/shortly|will appear/i);
  expect(message).not.toMatch(/upgraded|you're on|activated/i);
});

test("a cancelled checkout says the plan is unchanged", async () => {
  // Reassurance, not an error: nothing went wrong and nothing was charged.
  setUrl("status=cancel");
  renderDashboard();

  expect(toast).toHaveBeenCalledWith(expect.stringMatching(/cancelled/i));
  const [message] = toast.mock.calls[0];
  expect(message).toMatch(/unchanged/i);
  expect(toast.success).not.toHaveBeenCalled();
});

test("an unrecognised marker is treated as cancelled, not as payment", async () => {
  // Fail safe. A truncated or tampered redirect must never claim money arrived.
  setUrl("status=somethingelse");
  renderDashboard();

  expect(toast.success).not.toHaveBeenCalled();
  expect(toast).toHaveBeenCalledWith(expect.stringMatching(/unchanged/i));
});

test("the marker is stripped so a refresh does not replay the toast", async () => {
  setUrl("status=success");
  const replaceState = vi.spyOn(window.history, "replaceState");
  renderDashboard();

  expect(replaceState).toHaveBeenCalledWith({}, "", window.location.pathname);
  expect(window.location.search).toBe("");
});

test("the toast fires once, not on every render", async () => {
  // A re-render replaying "Payment received" would be alarming the second time
  // and meaningless the third.
  //
  // Note what actually guarantees this, because it is not what it looks like:
  // stripping the marker is what makes the effect idempotent, not the empty
  // dependency array. Removing `[]` leaves this green — the second run reads no
  // `status` and returns early. The two are deliberate redundancy, and the
  // marker strip is the one carrying the weight; it has its own test above.
  setUrl("status=success");
  renderDashboard();

  await goTo("audit");
  await goTo("chat");

  expect(toast.success).toHaveBeenCalledTimes(1);
});

// ── Tab routing ──────────────────────────────────────────────────────────────

test("each tab renders its own view and only that one", async () => {
  renderDashboard({ role: "owner" });

  await goTo("upload");
  expect(screen.getByRole("button", { name: "finish-upload" })).toBeInTheDocument();
  expect(screen.queryByTestId("audit")).not.toBeInTheDocument();

  await goTo("audit");
  expect(screen.getByTestId("audit")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "finish-upload" })).not.toBeInTheDocument();

  await goTo("account");
  expect(screen.getByTestId("account")).toBeInTheDocument();
  expect(screen.queryByTestId("audit")).not.toBeInTheDocument();
});

test("the admin view is gated on the role, not only on the tab", async () => {
  // The tab is state, and state can be set from anywhere in this component.
  // `activeTab === "admin" && isAdmin` is what makes the console unreachable
  // for a member rather than merely unlisted in the sidebar.
  renderDashboard({ role: "member" });

  await goTo("admin");

  expect(screen.queryByTestId("admin")).not.toBeInTheDocument();
  expect(adminProps).not.toHaveBeenCalled();
});

test("owner and admin both reach the admin console", async () => {
  for (const role of ["owner", "admin"]) {
    const { unmount } = render(<Dashboard auth={{ role }} onLogout={vi.fn()} />);
    await goTo("admin");
    expect(screen.getByTestId("admin")).toBeInTheDocument();
    unmount();
  }
});

test("the admin console is told which role it is serving", async () => {
  // AdminConsole gates plan changes on `isOwner`, so passing the wrong role
  // here would either hide billing from an owner or offer it to an admin.
  renderDashboard({ role: "admin" });
  await goTo("admin");

  expect(adminProps).toHaveBeenCalledWith(expect.objectContaining({ role: "admin" }));
});

test("account settings gets the role and the logout callback", async () => {
  // Both matter: the role decides whether org deletion is offered, and the
  // callback is what tears down the session after the account is gone.
  const onLogout = renderDashboard({ role: "owner" });
  await goTo("account");

  expect(accountProps).toHaveBeenCalledWith(
    expect.objectContaining({ role: "owner", onLogout })
  );
});

// ── The upload → chat hand-off ───────────────────────────────────────────────

test("chat without a session offers the way to get one", async () => {
  // A bare empty chat would look broken. The prompt names the missing step.
  renderDashboard();

  expect(screen.getByText(/no data loaded/i)).toBeInTheDocument();
  expect(screen.queryByTestId("chat")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: /upload data/i })).toBeInTheDocument();
});

test("the no-session prompt switches to the upload tab", async () => {
  renderDashboard();

  await act(async () => {
    await userEvent.click(screen.getByRole("button", { name: /upload data/i }));
  });

  expect(screen.getByRole("button", { name: "finish-upload" })).toBeInTheDocument();
});

test("a finished upload switches to chat and hands the session over", async () => {
  // The whole point of the flow. Staying on the upload tab after a successful
  // upload leaves the user to work out that they should now go and ask
  // something.
  renderDashboard();
  await goTo("upload");

  await act(async () => {
    await userEvent.click(screen.getByRole("button", { name: "finish-upload" }));
  });

  expect(screen.getByTestId("chat")).toHaveTextContent("chat:sess-1");
  expect(screen.queryByText(/no data loaded/i)).not.toBeInTheDocument();
});

test("the session survives navigating away and back", async () => {
  // Held in Dashboard rather than in ChatWindow, so a detour through the audit
  // trail must not silently discard the uploaded data.
  renderDashboard();
  await goTo("upload");
  await act(async () => {
    await userEvent.click(screen.getByRole("button", { name: "finish-upload" }));
  });

  await goTo("audit");
  await goTo("chat");

  expect(screen.getByTestId("chat")).toHaveTextContent("chat:sess-1");
});

// ── What the sidebar is told ─────────────────────────────────────────────────

test("the sidebar is given the active tab, role, admin flag and session", async () => {
  renderDashboard({ role: "owner" });

  expect(sidebarProps).toHaveBeenCalledWith(
    expect.objectContaining({ activeTab: "chat", role: "owner", isAdmin: true, session: null })
  );

  await goTo("audit");
  expect(sidebarProps).toHaveBeenLastCalledWith(
    expect.objectContaining({ activeTab: "audit" })
  );
});

test("a member is not flagged as admin to the sidebar", async () => {
  // Drives whether the admin entry is listed at all. The role is asserted too,
  // not just the flag: a hardcoded "owner" passed the isAdmin check on its own.
  renderDashboard({ role: "member" });
  expect(sidebarProps).toHaveBeenCalledWith(
    expect.objectContaining({ isAdmin: false, role: "member" })
  );
});

test("the sidebar sees the session once one exists", async () => {
  // It renders the loaded filename, so a sidebar stuck on `null` would keep
  // claiming nothing is loaded while the chat is answering questions about it.
  renderDashboard();
  await goTo("upload");

  await act(async () => {
    await userEvent.click(screen.getByRole("button", { name: "finish-upload" }));
  });

  expect(sidebarProps).toHaveBeenLastCalledWith(
    expect.objectContaining({ session: { session_id: "sess-1", filename: "sales.csv" } })
  );
});

test("an unknown role is treated as non-admin", async () => {
  // Fail closed: `ADMIN_ROLES.includes` means anything unrecognised — a new
  // role added backend-first, or a malformed session — gets the safe answer.
  renderDashboard({ role: "auditor" });
  expect(sidebarProps).toHaveBeenCalledWith(expect.objectContaining({ isAdmin: false }));
});

test("logging out from the sidebar calls straight through", async () => {
  const onLogout = renderDashboard();

  await act(async () => {
    await userEvent.click(screen.getByRole("button", { name: "sidebar-logout" }));
  });

  expect(onLogout).toHaveBeenCalledTimes(1);
});
