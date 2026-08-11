import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";
import AuditLogs from "./AuditLogs";
import { getAuditLogs } from "../../services/api";

// The audit trail is the record of who asked what, and the thing a compliance
// or security question gets answered from. Coverage was 4% of statements and 0%
// of functions.
//
// The failure path at the bottom was a known defect (#82) when this file was
// written: a failed fetch rendered "No audit logs yet. Start asking questions!".
// It is now fixed, and those tests assert the fix rather than pin the defect.

vi.mock("../../services/api", () => ({ getAuditLogs: vi.fn() }));
vi.mock("react-hot-toast", () => ({ default: { error: vi.fn(), success: vi.fn() } }));

const toast = (await import("react-hot-toast")).default;

const LOGS = [
  {
    id: 1,
    question: "What is the total revenue?",
    sql: "SELECT SUM(total_amount) FROM sales_data",
    summary: "1 row",
    status: "success",
    timestamp: "2026-08-09T10:30:00Z",
  },
  {
    id: 2,
    question: "Hi, who are you?",
    sql: "",
    summary: "Chat reply",
    status: "success",
    timestamp: "2026-08-09T11:00:00Z",
  },
  {
    id: 3,
    question: "Show me the regions",
    sql: "SELECT DISTINCT region FROM sales_data",
    summary: null,
    status: "error",
    timestamp: null,
  },
];

/** Resolve as the real paginated envelope: { items, total, limit, offset }. */
const envelope = (items) => ({ data: { items, total: items.length, limit: 100, offset: 0 } });

async function renderLogs(items = LOGS) {
  getAuditLogs.mockResolvedValue(envelope(items));
  render(<AuditLogs />);
  await waitFor(() =>
    expect(screen.queryByText(/loading audit logs/i)).not.toBeInTheDocument()
  );
}

const search = () => screen.getByPlaceholderText(/search queries or sql/i);
const statTile = (label) => screen.getByText(label).closest(".stat-card");

beforeEach(() => {
  vi.clearAllMocks();
});

// ── Loading and fetching ─────────────────────────────────────────────────────

test("a loading state shows until the logs arrive", async () => {
  getAuditLogs.mockResolvedValue(envelope(LOGS));
  render(<AuditLogs />);

  expect(screen.getByText(/loading audit logs/i)).toBeInTheDocument();
  expect(screen.queryByRole("table")).not.toBeInTheDocument();

  await waitFor(() =>
    expect(screen.queryByText(/loading audit logs/i)).not.toBeInTheDocument()
  );
  expect(screen.getByRole("table")).toBeInTheDocument();
});

test("the first page asks for 100 rows", async () => {
  // The component has no pagination, so the fetch size is the whole trail the
  // user will ever see here. 100 rather than the API's default 50.
  await renderLogs();
  expect(getAuditLogs).toHaveBeenCalledWith(100);
});

test("the paginated envelope is unwrapped", async () => {
  await renderLogs();
  expect(screen.getByText("What is the total revenue?")).toBeInTheDocument();
  expect(screen.getAllByRole("row")).toHaveLength(LOGS.length + 1); // + header
});

test("a bare array response is still accepted", async () => {
  // The endpoint returns an envelope today. This branch is the compatibility
  // path for a response shaped as a plain list, and it is load-bearing: without
  // it, an array response renders `.items` of an array — undefined — as empty.
  getAuditLogs.mockResolvedValue({ data: LOGS });
  render(<AuditLogs />);
  await waitFor(() =>
    expect(screen.queryByText(/loading audit logs/i)).not.toBeInTheDocument()
  );

  expect(screen.getByText("What is the total revenue?")).toBeInTheDocument();
});

test("an envelope with no items renders the empty state, not a crash", async () => {
  getAuditLogs.mockResolvedValue({ data: { total: 0 } });
  render(<AuditLogs />);
  await waitFor(() =>
    expect(screen.queryByText(/loading audit logs/i)).not.toBeInTheDocument()
  );

  expect(screen.getByText(/no audit logs yet/i)).toBeInTheDocument();
});

test("refresh re-reads the trail", async () => {
  await renderLogs();
  expect(getAuditLogs).toHaveBeenCalledTimes(1);

  await act(async () => {
    await userEvent.click(screen.getByRole("button", { name: /refresh/i }));
  });

  await waitFor(() => expect(getAuditLogs).toHaveBeenCalledTimes(2));
});

test("refresh is disabled while a fetch is in flight", async () => {
  // Without this, holding the button queues fetches that resolve out of order
  // and the trail flickers between responses.
  let resolve;
  getAuditLogs.mockReturnValue(new Promise((r) => { resolve = r; }));
  render(<AuditLogs />);

  expect(screen.getByRole("button", { name: /refresh/i })).toBeDisabled();

  await act(async () => { resolve(envelope(LOGS)); });
  await waitFor(() =>
    expect(screen.getByRole("button", { name: /refresh/i })).not.toBeDisabled()
  );
});

// ── The three stat tiles ─────────────────────────────────────────────────────

test("the tiles split the trail into data queries and chat", async () => {
  // "Data query" means SQL was generated and run against the user's data;
  // chat/off-topic never touched it. That split is the first thing anyone
  // auditing the trail wants, so miscounting it misleads at a glance.
  await renderLogs();

  expect(within(statTile("Total Queries")).getByText("3")).toBeInTheDocument();
  expect(within(statTile("Data Queries")).getByText("2")).toBeInTheDocument();
  expect(within(statTile("Chat / Off-topic")).getByText("1")).toBeInTheDocument();
});

test("an empty-string sql counts as chat, not as a data query", async () => {
  // The distinction is `sql && sql.length > 0`, not merely "has the key".
  await renderLogs([{ id: 1, question: "hello", sql: "", status: "success" }]);

  expect(within(statTile("Data Queries")).getByText("0")).toBeInTheDocument();
  expect(within(statTile("Chat / Off-topic")).getByText("1")).toBeInTheDocument();
});

test("the tiles count the whole trail, not the current filter", async () => {
  // Deliberate: the tiles describe the audit trail, and a search box narrowing
  // "Total Queries" would make the headline number depend on what someone
  // happened to type.
  await renderLogs();
  await userEvent.type(search(), "revenue");

  expect(screen.getAllByRole("row")).toHaveLength(2); // header + 1 match
  expect(within(statTile("Total Queries")).getByText("3")).toBeInTheDocument();
});

// ── Search ───────────────────────────────────────────────────────────────────

test("search matches the question, case-insensitively", async () => {
  await renderLogs();
  await userEvent.type(search(), "REVENUE");

  expect(screen.getByText("What is the total revenue?")).toBeInTheDocument();
  expect(screen.queryByText("Hi, who are you?")).not.toBeInTheDocument();
});

test("search also matches the generated SQL", async () => {
  // Searching the SQL is how you answer "did anyone ever query this table?",
  // which is a different question from "did anyone ask about it".
  await renderLogs();
  await userEvent.type(search(), "distinct");

  expect(screen.getByText("Show me the regions")).toBeInTheDocument();
  expect(screen.queryByText("What is the total revenue?")).not.toBeInTheDocument();
});

test("a log with neither question nor sql does not break the filter", async () => {
  // The `|| ""` guards. A null question would throw on .toLowerCase() and take
  // the whole page down with it.
  await renderLogs([
    { id: 99, question: null, sql: null, status: "error" },
    ...LOGS,
  ]);

  await userEvent.type(search(), "revenue");
  expect(screen.getByText("What is the total revenue?")).toBeInTheDocument();
});

test("the result count appears only while searching, and pluralises", async () => {
  await renderLogs();
  expect(screen.queryByText(/result/)).not.toBeInTheDocument();

  await userEvent.type(search(), "revenue");
  expect(screen.getByText("1 result")).toBeInTheDocument();

  await userEvent.clear(search());
  await userEvent.type(search(), "sales_data");
  expect(screen.getByText("2 results")).toBeInTheDocument();

  await userEvent.clear(search());
  await userEvent.type(search(), "nothingmatches");
  expect(screen.getByText("0 results")).toBeInTheDocument();
});

test("a search with no matches says so, distinctly from an empty trail", async () => {
  // "No audit logs yet. Start asking questions!" would be wrong here — there
  // are logs, they just don't match — and it sends the reader to the wrong fix.
  await renderLogs();
  await userEvent.type(search(), "nothingmatches");

  expect(screen.getByText(/no matching queries found/i)).toBeInTheDocument();
  expect(screen.queryByText(/start asking questions/i)).not.toBeInTheDocument();
});

test("an empty trail invites the user to start, with no filter applied", async () => {
  await renderLogs([]);
  expect(screen.getByText(/no audit logs yet/i)).toBeInTheDocument();
});

// ── Row rendering ────────────────────────────────────────────────────────────

test("each row shows the question, SQL, summary, status and time", async () => {
  await renderLogs();
  const row = screen.getByRole("row", { name: /total revenue/i });

  expect(within(row).getByText("SELECT SUM(total_amount) FROM sales_data")).toBeInTheDocument();
  expect(within(row).getByText("1 row")).toBeInTheDocument();
  expect(within(row).getByText("success")).toBeInTheDocument();
});

test("a chat entry is labelled as having no SQL rather than left blank", async () => {
  // A blank cell reads as "the SQL is missing from the record". "— (no SQL)"
  // says no SQL was ever generated, which is a different fact.
  await renderLogs();
  const row = screen.getByRole("row", { name: /who are you/i });

  expect(within(row).getByText(/no sql/i)).toBeInTheDocument();
  expect(within(row).queryByText(/^SELECT/)).not.toBeInTheDocument();
});

test("a missing summary and a missing timestamp both render an em dash", async () => {
  await renderLogs();
  const row = screen.getByRole("row", { name: /show me the regions/i });

  expect(within(row).getAllByText("—")).toHaveLength(2);
});

test("the timestamp is rendered in the reader's locale, not as raw ISO", async () => {
  // An auditor reading "2026-08-09T10:30:00Z" has to do timezone arithmetic in
  // their head to answer "was this during business hours".
  await renderLogs();
  const row = screen.getByRole("row", { name: /total revenue/i });

  const expected = new Date("2026-08-09T10:30:00Z").toLocaleString();
  expect(within(row).getByText(new RegExp(expected.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))))
    .toBeInTheDocument();
  expect(within(row).queryByText(/2026-08-09T10:30:00Z/)).not.toBeInTheDocument();
});

test("the status badge carries the status as a class so it can be coloured", async () => {
  await renderLogs();
  const errorRow = screen.getByRole("row", { name: /show me the regions/i });

  expect(within(errorRow).getByText("error")).toHaveClass("status-badge", "error");
});

test("rows are numbered from 1 in display order", async () => {
  await renderLogs();
  const cells = screen.getAllByRole("row").slice(1).map((r) => r.cells[0].textContent);
  expect(cells).toEqual(["1", "2", "3"]);
});

test("filtering renumbers the rows rather than preserving the original index", async () => {
  // Pinned because it is a real trap when reading the page: the "#" column is a
  // position in the current view, not a stable identifier for a log entry, so
  // "row 2" means different things before and after a search.
  await renderLogs();
  await userEvent.type(search(), "sales_data");

  const cells = screen.getAllByRole("row").slice(1).map((r) => r.cells[0].textContent);
  expect(cells).toEqual(["1", "2"]);
});

// ── A failed fetch must not read as an empty trail (#82) ─────────────────────
//
// These replace a characterization test that pinned the old behaviour, where a
// failed fetch rendered "No audit logs yet. Start asking questions!". For an
// audit trail that is a wrong answer to the question the page exists to answer.

async function renderFailed(rejection = new Error("Network Error")) {
  getAuditLogs.mockRejectedValue(rejection);
  render(<AuditLogs />);
  await waitFor(() =>
    expect(screen.queryByText(/loading audit logs/i)).not.toBeInTheDocument()
  );
}

test("a failed fetch says the request failed, not that the trail is empty", async () => {
  await renderFailed();

  expect(screen.getByRole("alert")).toHaveTextContent(/could not load the audit trail/i);
  expect(screen.queryByText(/no audit logs yet/i)).not.toBeInTheDocument();
  expect(screen.queryByText(/start asking questions/i)).not.toBeInTheDocument();
});

test("the failure message says no conclusion can be drawn", async () => {
  // The substantive half of #82. "Could not load" alone still leaves a reader
  // to guess; the page has to state that emptiness is not the finding, because
  // someone is here to answer "was anything queried?" and the honest answer is
  // "we don't know".
  await renderFailed();

  const alert = screen.getByRole("alert");
  expect(alert).toHaveTextContent(/does not mean the trail is empty/i);
  expect(alert).toHaveTextContent(/refresh/i);
});

test("the stat tiles are hidden on failure rather than left reading zero", async () => {
  // "Total Queries 0" asserts an empty trail exactly as plainly as the copy
  // did. Fixing only the message would have left the same false claim on screen.
  await renderFailed();

  expect(screen.queryByText("Total Queries")).not.toBeInTheDocument();
  expect(screen.queryByText("Data Queries")).not.toBeInTheDocument();
  expect(screen.queryByText("Chat / Off-topic")).not.toBeInTheDocument();
});

test("the failure is also surfaced as a toast, like the sibling components", async () => {
  await renderFailed();
  expect(toast.error).toHaveBeenCalledWith("Could not load the audit trail");
});

test("no table is rendered on failure", async () => {
  await renderFailed();
  expect(screen.queryByRole("table")).not.toBeInTheDocument();
});

test("a failed fetch clears the loading state and leaves refresh usable", async () => {
  // The `finally` is what makes retry possible at all, and the error copy
  // points at Refresh, so this has to hold.
  await renderFailed();
  expect(screen.getByRole("button", { name: /refresh/i })).not.toBeDisabled();
});

test("a successful retry clears the banner and restores the trail", async () => {
  // `setFailed(false)` on success. Without it the banner is permanent and the
  // page keeps claiming a failure that has since been fixed — which is the same
  // class of lie as the original defect, pointing the other way.
  await renderFailed();
  expect(screen.getByRole("alert")).toBeInTheDocument();

  getAuditLogs.mockResolvedValue(envelope(LOGS));
  await act(async () => {
    await userEvent.click(screen.getByRole("button", { name: /refresh/i }));
  });

  await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
  expect(screen.getByRole("table")).toBeInTheDocument();
  expect(screen.getByText("Total Queries")).toBeInTheDocument();
});

test("a genuinely empty trail still gets the friendly empty state", async () => {
  // The other side of the distinction: this fix must not turn every empty
  // workspace into an error.
  await renderLogs([]);

  expect(screen.getByText(/no audit logs yet. start asking questions/i)).toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(toast.error).not.toHaveBeenCalled();
});
