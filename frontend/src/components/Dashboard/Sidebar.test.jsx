import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";
import Sidebar from "./Sidebar";

// The navigation for everything behind the login wall, and the only route to
// the admin console. Coverage was 7.01% of statements and 0% of functions —
// every one of its behaviours was reachable only through `Dashboard`, whose
// suite stubs it out entirely.
//
// Two things here are more than cosmetic. The `isAdmin` gate decides whether a
// member is offered a console that manages other people's accounts, and
// `aria-current` is the only signal a screen-reader user gets about where they
// are; the `active` class says nothing to them.

const TABS = [
  ["upload", "Upload Data"],
  ["chat", "Ask Questions"],
  ["audit", "Audit Logs"],
  ["account", "Account"],
];

const SESSION = {
  session_id: "sess-1",
  table_name: "sales_data",
  rows: 25,
  columns: ["order_id", "product", "quantity"],
};

function renderSidebar(overrides = {}) {
  const props = {
    activeTab: "upload",
    onTabChange: vi.fn(),
    onLogout: vi.fn(),
    session: null,
    role: "member",
    isAdmin: false,
    ...overrides,
  };
  return { ...render(<Sidebar {...props} />), props };
}

const navItems = () =>
  within(screen.getByRole("navigation", { name: "Main" }))
    .getAllByRole("button")
    .map((b) => b.textContent);

// ── The menu ──────────────────────────────────────────────────────────────────

test.each(TABS)("renders the %s tab", (_id, label) => {
  renderSidebar();
  expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
});

test("the nav is labelled, so it is reachable as a landmark", () => {
  renderSidebar();
  expect(screen.getByRole("navigation", { name: "Main" })).toBeInTheDocument();
});

test.each(TABS)("clicking %s asks for that tab", async (id, label) => {
  const { props } = renderSidebar();
  await userEvent.click(screen.getByRole("button", { name: label }));
  expect(props.onTabChange).toHaveBeenCalledWith(id);
  expect(props.onTabChange).toHaveBeenCalledTimes(1);
});

test("a click reports the tab that was clicked, not the one already open", async () => {
  const { props } = renderSidebar({ activeTab: "upload" });
  await userEvent.click(screen.getByRole("button", { name: "Audit Logs" }));
  expect(props.onTabChange).toHaveBeenCalledWith("audit");
});

test("the icons are hidden from assistive tech, leaving one label per item", () => {
  renderSidebar();
  const button = screen.getByRole("button", { name: "Upload Data" });
  expect(button.firstElementChild).toHaveAttribute("aria-hidden", "true");
  expect(button).toHaveAccessibleName("Upload Data");
});

// ── The admin gate ────────────────────────────────────────────────────────────

test("a non-admin is not offered the admin console", () => {
  renderSidebar({ isAdmin: false });
  expect(screen.queryByRole("button", { name: "Admin" })).not.toBeInTheDocument();
  expect(navItems()).toEqual(["Upload Data", "Ask Questions", "Audit Logs", "Account"]);
});

test("an admin is", () => {
  renderSidebar({ isAdmin: true });
  expect(screen.getByRole("button", { name: "Admin" })).toBeInTheDocument();
});

test("the admin item sits between the audit trail and account settings", () => {
  renderSidebar({ isAdmin: true });
  expect(navItems()).toEqual([
    "Upload Data",
    "Ask Questions",
    "Audit Logs",
    "Admin",
    "Account",
  ]);
});

test("clicking admin asks for the admin tab", async () => {
  const { props } = renderSidebar({ isAdmin: true });
  await userEvent.click(screen.getByRole("button", { name: "Admin" }));
  expect(props.onTabChange).toHaveBeenCalledWith("admin");
});

// ── Which tab is current ──────────────────────────────────────────────────────

test("the active tab is announced as the current page", () => {
  renderSidebar({ activeTab: "chat" });
  expect(screen.getByRole("button", { name: "Ask Questions" })).toHaveAttribute(
    "aria-current",
    "page",
  );
});

test("exactly one tab is current at a time", () => {
  renderSidebar({ activeTab: "chat" });
  const current = within(screen.getByRole("navigation", { name: "Main" }))
    .getAllByRole("button")
    .filter((b) => b.getAttribute("aria-current") === "page");
  expect(current.map((b) => b.textContent)).toEqual(["Ask Questions"]);
});

test("an inactive tab carries no aria-current at all", () => {
  renderSidebar({ activeTab: "chat" });
  // Not `false` or `"none"` — the attribute must be absent, or every tab reads
  // as a candidate for the current page.
  expect(screen.getByRole("button", { name: "Upload Data" })).not.toHaveAttribute(
    "aria-current",
  );
});

test("the active tab is styled as active and the others are not", () => {
  renderSidebar({ activeTab: "audit" });
  expect(screen.getByRole("button", { name: "Audit Logs" })).toHaveClass("active");
  expect(screen.getByRole("button", { name: "Account" })).not.toHaveClass("active");
});

test("an unknown active tab leaves nothing marked current", () => {
  renderSidebar({ activeTab: "nonsense" });
  const nav = screen.getByRole("navigation", { name: "Main" });
  expect(
    within(nav)
      .getAllByRole("button")
      .filter((b) => b.hasAttribute("aria-current")),
  ).toHaveLength(0);
});

// ── The session panel ─────────────────────────────────────────────────────────

test("no session means no session panel", () => {
  renderSidebar({ session: null });
  expect(screen.queryByText("Active Session")).not.toBeInTheDocument();
  expect(screen.queryByText(/rows loaded/)).not.toBeInTheDocument();
});

test("a session shows the table, the row count and the column count", () => {
  renderSidebar({ session: SESSION });
  expect(screen.getByText("Active Session")).toBeInTheDocument();
  expect(screen.getByText("sales_data")).toBeInTheDocument();
  expect(screen.getByText("25 rows loaded")).toBeInTheDocument();
  expect(screen.getByText("3 columns")).toBeInTheDocument();
});

test("a session without a columns list reads as 0, not as a crash", () => {
  // The upload response has carried `columns` on every path seen so far, but
  // the component already guards for its absence and that guard must hold: an
  // exception here takes the whole navigation down, not just this line.
  renderSidebar({ session: { ...SESSION, columns: undefined } });
  expect(screen.getByText("0 columns")).toBeInTheDocument();
});

test("an empty columns list reads as 0", () => {
  renderSidebar({ session: { ...SESSION, columns: [] } });
  expect(screen.getByText("0 columns")).toBeInTheDocument();
});

test("a zero-row table says so rather than hiding the panel", () => {
  renderSidebar({ session: { ...SESSION, rows: 0 } });
  expect(screen.getByText("0 rows loaded")).toBeInTheDocument();
});

// ── The footer ────────────────────────────────────────────────────────────────

test("the role is shown", () => {
  renderSidebar({ role: "owner" });
  expect(screen.getByText("owner")).toBeInTheDocument();
});

test("the role shown is the one passed in", () => {
  renderSidebar({ role: "member" });
  expect(screen.getByText("member")).toBeInTheDocument();
  expect(screen.queryByText("owner")).not.toBeInTheDocument();
});

test("logging out calls back rather than changing tab", async () => {
  const { props } = renderSidebar();
  await userEvent.click(screen.getByRole("button", { name: "Logout" }));
  expect(props.onLogout).toHaveBeenCalledTimes(1);
  expect(props.onTabChange).not.toHaveBeenCalled();
});

test("the logout button is outside the nav landmark", () => {
  renderSidebar();
  // It is not navigation, and inside the landmark it would read as a fifth
  // destination — which is also what `navItems()` above relies on.
  expect(navItems()).not.toContain("Logout");
});
