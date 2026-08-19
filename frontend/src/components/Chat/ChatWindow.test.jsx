import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";
import ChatWindow from "./ChatWindow";
import { askQuestionStream, exportPdf } from "../../services/api";

// ChatWindow owns the frontend half of the streaming query flow — the backend
// half is covered by test_query_stream.py. Everything interesting here is a
// state machine fed by SSE callbacks: stage labels, token accumulation, the
// done/error branches, and the loading gate that stops a second question being
// sent mid-flight. None of it had cover.
//
// askQuestionStream is mocked at the callback boundary rather than at fetch,
// so tests drive the stream event by event and can hold it open mid-flight —
// which is the only way to assert on the transient states.

vi.mock("../../services/api", () => ({
  askQuestionStream: vi.fn(),
  exportPdf: vi.fn(),
}));

vi.mock("react-hot-toast", () => ({
  default: { error: vi.fn(), success: vi.fn() },
}));

// ResultView is React.lazy'd; a stub keeps this file about ChatWindow and
// avoids pulling recharts into every case.
vi.mock("../Visualization/ResultView", () => ({
  default: ({ type, data, columns }) => (
    <div data-testid="result-view" data-type={type} data-rows={data?.length}>
      {columns?.join(",")}
    </div>
  ),
}));

const toast = (await import("react-hot-toast")).default;

const SESSION = {
  session_id: "11111111-2222-3333-4444-555555555555",
  table_name: "sales",
  rows: 3,
  columns: ["region", "revenue"],
};

/** Holds the stream open so transient UI states can be asserted on. */
function openStream() {
  const stream = {};
  askQuestionStream.mockImplementation((sid, question, onStage, onDone, onError, onToken) => {
    Object.assign(stream, { sid, question, onStage, onDone, onError, onToken });
    return new Promise((resolve) => { stream.close = resolve; });
  });
  return stream;
}

const sendBtn = (container) => container.querySelector(".send-btn");

async function ask(container, question = "How many rows?") {
  await userEvent.type(screen.getByPlaceholderText(/ask a question about your data/i), question);
  await act(async () => {
    await userEvent.click(sendBtn(container));
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  // jsdom implements neither of these, and both run on the happy path.
  Element.prototype.scrollIntoView = vi.fn();
  window.URL.createObjectURL = vi.fn(() => "blob:report");
});

// ── The opening message ──────────────────────────────────────────────────────

test("opens by describing the loaded table", () => {
  render(<ChatWindow session={SESSION} />);
  const greeting = screen.getByText(/data loaded/i);
  expect(greeting).toHaveTextContent("sales");
  expect(greeting).toHaveTextContent("3 rows");
  expect(greeting).toHaveTextContent("2 columns");
});

test("survives a session with no column list", () => {
  // The upload response has always carried columns, but a greeting that throws
  // would take the whole chat down with it.
  render(<ChatWindow session={{ ...SESSION, columns: undefined }} />);
  expect(screen.getByText(/0 columns/i)).toBeInTheDocument();
});

test("the opening message carries the ingestion anomalies", () => {
  // These are computed during upload and returned with the session. They used
  // to render only on the upload screen, which unmounts in the same commit as a
  // successful upload — so nobody ever saw them. The greeting is where the
  // upload actually leaves the user, so they belong here.
  render(
    <ChatWindow
      session={{
        ...SESSION,
        anomalies: [
          { type: "missing_data", message: "Column 'notes' has 31.0% missing values", severity: "medium" },
          { type: "duplicates", message: "Found 1 duplicate rows in 'sales'", severity: "low" },
        ],
      }}
    />,
  );

  expect(screen.getByText(/anomalies detected/i)).toBeInTheDocument();
  expect(screen.getByText(/31.0% missing values/)).toBeInTheDocument();
  expect(screen.getByText(/1 duplicate rows/)).toBeInTheDocument();
});

test("a session with nothing to report shows no anomalies panel", () => {
  // Absent, not empty — an "Anomalies Detected" heading over nothing would read
  // as a warning about a clean file.
  const { container } = render(<ChatWindow session={{ ...SESSION, anomalies: [] }} />);
  expect(screen.queryByText(/anomalies detected/i)).not.toBeInTheDocument();
  expect(container.querySelector(".result-anomalies")).toBeNull();
});

test("a session with no anomalies key at all does not crash the greeting", () => {
  // Every other test in this file passes a SESSION without one, but say so
  // explicitly: the backend omits the key on some paths.
  render(<ChatWindow session={SESSION} />);
  expect(screen.getByText(/data loaded/i)).toBeInTheDocument();
  expect(screen.queryByText(/anomalies detected/i)).not.toBeInTheDocument();
});

test("answers that follow the greeting carry no anomalies panel of their own", async () => {
  // The panel hangs off one message, not off every assistant bubble.
  const { container } = render(
    <ChatWindow
      session={{
        ...SESSION,
        anomalies: [{ type: "outlier", message: "Column 'revenue' has 1 outlier values", severity: "medium" }],
      }}
    />,
  );
  const stream = openStream();
  await ask(container);
  await act(async () => {
    stream.onDone({ type: "text", summary: "Three rows." });
    stream.close();
  });

  expect(container.querySelectorAll(".result-anomalies")).toHaveLength(1);
});

// ── Sending ──────────────────────────────────────────────────────────────────

test("sending shows the question and calls the stream with the session", async () => {
  const stream = openStream();
  const { container } = render(<ChatWindow session={SESSION} />);

  await ask(container, "How many rows?");

  expect(screen.getByText("How many rows?")).toBeInTheDocument();
  expect(stream.sid).toBe(SESSION.session_id);
  expect(stream.question).toBe("How many rows?");
});

test("the input clears once a question is sent", async () => {
  openStream();
  const { container } = render(<ChatWindow session={SESSION} />);
  const box = screen.getByPlaceholderText(/ask a question about your data/i);

  await ask(container, "total revenue?");
  expect(box).toHaveValue("");
});

test("whitespace-only input is not sent", async () => {
  const { container } = render(<ChatWindow session={SESSION} />);
  await userEvent.type(screen.getByPlaceholderText(/ask a question/i), "   ");
  await act(async () => {
    await userEvent.click(sendBtn(container));
  });
  expect(askQuestionStream).not.toHaveBeenCalled();
});

test("Enter sends but Shift+Enter does not", async () => {
  openStream();
  render(<ChatWindow session={SESSION} />);
  const box = screen.getByPlaceholderText(/ask a question/i);

  await userEvent.type(box, "first{Shift>}{Enter}{/Shift}");
  expect(askQuestionStream).not.toHaveBeenCalled();

  await act(async () => {
    await userEvent.type(box, "{Enter}");
  });
  expect(askQuestionStream).toHaveBeenCalledTimes(1);
});

test("the composer is locked while a question is in flight", async () => {
  // The backend charges quota per query, so a double-send costs real money.
  // What actually prevents it is the textarea being disabled — asserting only
  // that the send button is disabled proves nothing, because the input is
  // empty by then and the button would be disabled either way.
  const stream = openStream();
  const { container } = render(<ChatWindow session={SESSION} />);

  await ask(container, "first question");
  const box = screen.getByPlaceholderText(/ask a question/i);
  expect(box).toBeDisabled();
  expect(sendBtn(container)).toBeDisabled();

  // Typing is refused while the stream is open, so no second question forms.
  await userEvent.type(box, "second question");
  expect(box).toHaveValue("");
  expect(askQuestionStream).toHaveBeenCalledTimes(1);
});

test("the composer unlocks once the stream closes", async () => {
  const stream = openStream();
  const { container } = render(<ChatWindow session={SESSION} />);
  await ask(container, "first question");

  await act(async () => {
    stream.onDone({ type: "chat", summary: "done", data: [], columns: [], sql: null, row_count: 0 });
    stream.close();
  });

  const box = screen.getByPlaceholderText(/ask a question/i);
  await waitFor(() => expect(box).not.toBeDisabled());

  await userEvent.type(box, "second question");
  await act(async () => {
    await userEvent.click(sendBtn(container));
  });
  expect(askQuestionStream).toHaveBeenCalledTimes(2);
});

// ── Stage reporting ──────────────────────────────────────────────────────────

test("each stage shows its own label", async () => {
  const stream = openStream();
  const { container } = render(<ChatWindow session={SESSION} />);
  await ask(container);

  for (const [stage, label] of [
    ["analyzing", /exploring your data structure/i],
    ["executing", /running the query/i],
    ["healing", /fine-tuning the query/i],
  ]) {
    await act(async () => { stream.onStage(stage); });
    expect(screen.getByText(label)).toBeInTheDocument();
  }
});

test("an unrecognised stage clears the label rather than showing a raw key", async () => {
  const stream = openStream();
  const { container } = render(<ChatWindow session={SESSION} />);
  await ask(container);

  await act(async () => { stream.onStage("some_future_stage"); });
  expect(screen.queryByText("some_future_stage")).not.toBeInTheDocument();
});

// ── Token streaming ──────────────────────────────────────────────────────────

test("SQL tokens accumulate as they arrive", async () => {
  const stream = openStream();
  const { container } = render(<ChatWindow session={SESSION} />);
  await ask(container);

  await act(async () => { stream.onStage("generating"); });
  await act(async () => { stream.onToken("SELECT "); });
  await act(async () => { stream.onToken("COUNT(*) "); });
  await act(async () => { stream.onToken("FROM sales"); });

  expect(container.querySelector(".streaming-sql")).toHaveTextContent(
    "SELECT COUNT(*) FROM sales"
  );
});

test("moving past generation discards the partial SQL", async () => {
  // Otherwise the half-written query stays on screen while the result renders.
  const stream = openStream();
  const { container } = render(<ChatWindow session={SESSION} />);
  await ask(container);

  await act(async () => { stream.onStage("generating"); });
  await act(async () => { stream.onToken("SELECT 1"); });
  expect(container.querySelector(".streaming-sql")).toBeTruthy();

  await act(async () => { stream.onStage("executing"); });
  expect(container.querySelector(".streaming-sql")).toBeNull();
});

// ── Results ──────────────────────────────────────────────────────────────────

test("a result renders its summary, its SQL and the chart view", async () => {
  const stream = openStream();
  const { container } = render(<ChatWindow session={SESSION} />);
  await ask(container);

  await act(async () => {
    stream.onDone({
      type: "bar",
      summary: "3 results — highest: South (250)",
      data: [{ region: "N", revenue: 100 }],
      columns: ["region", "revenue"],
      sql: "SELECT region, revenue FROM sales",
      row_count: 1,
    });
  });

  expect(screen.getByText(/highest: South/)).toBeInTheDocument();
  expect(screen.getByText(/view sql query/i)).toBeInTheDocument();
  expect(container.querySelector(".msg-sql pre")).toHaveTextContent(
    "SELECT region, revenue FROM sales"
  );

  const view = await screen.findByTestId("result-view");
  expect(view).toHaveAttribute("data-type", "bar");
  expect(view).toHaveAttribute("data-rows", "1");
});

test("the SQL block is expanded, not folded away behind a click", async () => {
  const stream = openStream();
  const { container } = render(<ChatWindow session={SESSION} />);
  await ask(container);

  await act(async () => {
    stream.onDone({
      type: "bar",
      summary: "3 results",
      data: [{ region: "N", revenue: 100 }],
      columns: ["region", "revenue"],
      sql: "SELECT region, revenue FROM sales",
      row_count: 1,
    });
  });

  expect(container.querySelector(".msg-sql")).toHaveAttribute("open");
});

// The stream reports failures as `stage: "done"` with `message` where a success
// carries `summary` — they reach onDone, not onError. Reading only `summary`
// left content undefined, so a failure that had still produced SQL rendered as
// an empty bubble with nothing but a "View SQL Query" toggle under it.
test("an error result delivered as done still shows its message", async () => {
  const stream = openStream();
  const { container } = render(<ChatWindow session={SESSION} />);
  await ask(container);

  await act(async () => {
    stream.onDone({
      type: "error",
      message: "The query could not be executed on your data. Please rephrase.",
      sql: "SELECT nope FROM sales",
    });
  });

  expect(screen.getByText(/could not be executed on your data/i)).toBeInTheDocument();
  expect(container.querySelector(".msg-sql pre")).toHaveTextContent("SELECT nope FROM sales");
});

test("a done envelope with neither summary nor message still says something", async () => {
  const stream = openStream();
  const { container } = render(<ChatWindow session={SESSION} />);
  await ask(container);

  await act(async () => {
    stream.onDone({ type: "error", sql: null });
  });

  expect(screen.getByText(/something went wrong/i)).toBeInTheDocument();
});

test("a chat answer renders no SQL block and no chart", async () => {
  const stream = openStream();
  const { container } = render(<ChatWindow session={SESSION} />);
  await ask(container, "hello");

  await act(async () => {
    stream.onDone({
      type: "chat", summary: "Hello! Ask me about your data.",
      data: [], columns: [], sql: null, row_count: 0,
    });
  });

  expect(screen.getByText(/ask me about your data/i)).toBeInTheDocument();
  expect(container.querySelector(".msg-sql")).toBeNull();
  expect(screen.queryByTestId("result-view")).not.toBeInTheDocument();
});

test("a zero-row result shows the summary without an empty chart", async () => {
  const stream = openStream();
  const { container } = render(<ChatWindow session={SESSION} />);
  await ask(container, "revenue in 1990");

  await act(async () => {
    stream.onDone({
      type: "table", summary: "No results found for your query.",
      data: [], columns: ["region"], sql: "SELECT * FROM sales WHERE year = 1990",
      row_count: 0,
    });
  });

  expect(screen.getByText(/no results found/i)).toBeInTheDocument();
  // The SQL is still worth showing — it is how the user sees why nothing matched.
  expect(container.querySelector(".msg-sql pre")).toBeTruthy();
  expect(screen.queryByTestId("result-view")).not.toBeInTheDocument();
});

// ── Failures ─────────────────────────────────────────────────────────────────

test("a stream error is shown to the user verbatim", async () => {
  const stream = openStream();
  const { container } = render(<ChatWindow session={SESSION} />);
  await ask(container);

  await act(async () => { stream.onError("The AI engine is temporarily unavailable."); });
  expect(screen.getByText(/temporarily unavailable/i)).toBeInTheDocument();
});

test("an error with no message still says something useful", async () => {
  const stream = openStream();
  const { container } = render(<ChatWindow session={SESSION} />);
  await ask(container);

  await act(async () => { stream.onError(""); });
  expect(screen.getByText(/something went wrong/i)).toBeInTheDocument();
});

test("a rejected stream is caught instead of breaking the chat", async () => {
  askQuestionStream.mockRejectedValue(new Error("Network Error"));
  const { container } = render(<ChatWindow session={SESSION} />);

  await ask(container);

  expect(screen.getByText("Network Error")).toBeInTheDocument();
  // And the composer is usable again rather than stuck loading.
  await waitFor(() =>
    expect(screen.getByPlaceholderText(/ask a question/i)).not.toBeDisabled()
  );
});

test("the composer is re-enabled after an error", async () => {
  const stream = openStream();
  const { container } = render(<ChatWindow session={SESSION} />);
  await ask(container);

  await act(async () => { stream.onError("boom"); });
  await act(async () => { stream.close(); });

  await waitFor(() =>
    expect(screen.getByPlaceholderText(/ask a question/i)).not.toBeDisabled()
  );
});

// ── Suggestions ──────────────────────────────────────────────────────────────

test("suggestions are offered before the first question and withdrawn after", async () => {
  const stream = openStream();
  const { container } = render(<ChatWindow session={SESSION} />);

  const chip = screen.getByRole("button", { name: /show me the total revenue/i });
  expect(chip).toBeInTheDocument();

  await ask(container, "anything");
  await act(async () => { stream.close(); });

  expect(
    screen.queryByRole("button", { name: /show me the total revenue/i })
  ).not.toBeInTheDocument();
});

test("clicking a suggestion fills the composer without sending", async () => {
  render(<ChatWindow session={SESSION} />);
  await userEvent.click(screen.getByRole("button", { name: /any trends over time/i }));

  expect(screen.getByPlaceholderText(/ask a question/i)).toHaveValue("Any trends over time?");
  expect(askQuestionStream).not.toHaveBeenCalled();
});

// ── PDF export ───────────────────────────────────────────────────────────────

test("exporting requests the session's report and confirms", async () => {
  exportPdf.mockResolvedValue({ data: new Blob(["%PDF-1.4"]) });
  render(<ChatWindow session={SESSION} />);

  await act(async () => {
    await userEvent.click(screen.getByRole("button", { name: /export pdf/i }));
  });

  expect(exportPdf).toHaveBeenCalledWith(SESSION.session_id);
  await waitFor(() => expect(toast.success).toHaveBeenCalled());
});

test("a failed export is reported rather than silently doing nothing", async () => {
  exportPdf.mockRejectedValue(new Error("500"));
  render(<ChatWindow session={SESSION} />);

  await act(async () => {
    await userEvent.click(screen.getByRole("button", { name: /export pdf/i }));
  });

  await waitFor(() => expect(toast.error).toHaveBeenCalledWith("Failed to export report"));
});
