import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";
import AuditLogs from "./AuditLogs";
import { getAuditLogs } from "../../services/api";

// The audit trail is the record of who asked what, and the thing a compliance
// or security question gets answered from. Coverage was 4% of statements and 0%
// of functions.
//
// One behaviour below is pinned as a known defect rather than asserted as
// correct — see "a failed fetch is indistinguishable from an empty trail" at
// the bottom, and issue #82.

vi.mock("../../services/api", () => ({ getAuditLogs: vi.fn() }));

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

// ── A failed fetch looks like an empty trail — known defect (#82) ────────────

test("a failed fetch is indistinguishable from an empty trail (known defect)", async () => {
  // NOT a specification. Pinned so the defect is visible in the suite and so
  // fixing it is a deliberate edit here rather than a surprise red run.
  //
  // `fetchLogs` catches and does `setLogs([])` with nothing surfaced, so a 500,
  // a dropped connection or an expired session all render:
  //
  //     "No audit logs yet. Start asking questions!"
  //
  // For an audit trail specifically, that is the worst available wording: it
  // tells someone checking the record that nothing happened. Every other
  // component in this directory reports a load failure — AdminConsole raises a
  // toast for exactly this case.
  //
  // Tracked as #82. When fixed, replace this with an assertion that the failure
  // is reported and that the empty-state copy is not shown.
  getAuditLogs.mockRejectedValue(new Error("Network Error"));
  render(<AuditLogs />);
  await waitFor(() =>
    expect(screen.queryByText(/loading audit logs/i)).not.toBeInTheDocument()
  );

  expect(screen.getByText(/no audit logs yet. start asking questions/i)).toBeInTheDocument();
  expect(screen.queryByRole("table")).not.toBeInTheDocument();
});

test("a failed fetch still clears the loading state and leaves refresh usable", async () => {
  // Correct today and worth keeping whatever #82 does to the messaging: the
  // `finally` is what lets the user retry at all.
  getAuditLogs.mockRejectedValue(new Error("Network Error"));
  render(<AuditLogs />);

  await waitFor(() =>
    expect(screen.getByRole("button", { name: /refresh/i })).not.toBeDisabled()
  );
});
