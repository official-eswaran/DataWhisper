import { expect, test } from "@playwright/test";
import { OWNER, SALES_CSV, ask, composer, lastAssistantMessage, loginAs, uploadDataset } from "./helpers";

// One session, driven in order. Uploading and re-registering per test would add
// minutes to a suite whose real cost is already CPU inference, and several of
// these assertions are *about* order: the suggestion chips only exist before the
// first question, and the conversational-memory follow-up only means anything
// immediately after the question it refers back to.
test.describe.configure({ mode: "serial" });

// The labels the UI shows for each backend stage (Chat/ChatWindow.jsx
// STAGE_LABELS), in the order the stream emits them (routes/query.py).
const STAGE_ORDER = [
  "Analyzing your question...",     // classifying
  "Exploring your data structure...", // analyzing
  "Writing SQL query",              // generating, before the first token lands
  "Running the query...",           // executing
];

let page;

test.beforeAll(async ({ browser }) => {
  page = await browser.newPage();
  await loginAs(page, OWNER);
  await uploadDataset(page, SALES_CSV);
});

test.afterAll(async () => {
  await page?.close();
});

test("a suggestion chip fills the composer without sending it", async ({}) => {
  // Chips are only rendered while `messages.length <= 1`, i.e. before the first
  // question — so this has to run before anything else asks anything.
  const chip = page.getByRole("button", { name: "Show me the total revenue" });
  await expect(chip).toBeVisible();
  await chip.click();

  await expect(composer(page)).toHaveValue("Show me the total revenue");
  // Filling is not sending: the chip is a starting point the user can edit.
  await expect(page.locator(".chat-msg.user")).toHaveCount(0);
  await expect(page.locator(".stage-indicator")).toHaveCount(0);

  await composer(page).fill("");
});

test("the stages run in order, the SQL streams in, and the composer is locked while it does", async ({}) => {
  // A DOM probe rather than a series of `toBeVisible` waits: the stages pass in
  // hundreds of milliseconds and an assertion that arrives late cannot see the
  // frame it was looking for. The observer records every change as it happens
  // and the assertions read the record afterwards.
  await page.evaluate(() => {
    window.__probe = { stages: [], sqlLengths: [], composerWasLocked: false };
    const p = window.__probe;
    const sample = () => {
      const stage = document.querySelector(".stage-text")?.textContent?.trim();
      if (stage && p.stages[p.stages.length - 1] !== stage) p.stages.push(stage);

      const sql = document.querySelector("pre.streaming-sql")?.textContent ?? "";
      if (sql.length && p.sqlLengths[p.sqlLengths.length - 1] !== sql.length) {
        p.sqlLengths.push(sql.length);
      }
      if (document.querySelector(".chat-input-area textarea")?.disabled) {
        p.composerWasLocked = true;
      }
    };
    new MutationObserver(sample).observe(document.body, {
      childList: true,
      subtree: true,
      characterData: true,
    });
    sample();
  });

  const box = composer(page);
  await box.fill("What is the total sales by region?");
  await box.press("Enter");

  // Locked mid-flight — asserted live, because this one is observable for the
  // whole length of the request rather than for a single frame.
  await expect(box).toBeDisabled();

  await expect(page.locator(".chat-msg.assistant .msg-text")).toHaveCount(2, { timeout: 150_000 });
  await expect(page.locator(".stage-indicator, .streaming-sql-wrapper")).toHaveCount(0);

  const probe = await page.evaluate(() => window.__probe);

  // Order, not completeness. A MutationObserver coalesces changes that land in
  // the same task, so a stage can go by without being sampled; what must never
  // happen is two stages arriving out of order.
  const positions = probe.stages
    .map((s) => STAGE_ORDER.indexOf(s))
    .filter((i) => i >= 0);
  expect(positions.length).toBeGreaterThan(1);
  expect(positions).toEqual([...positions].sort((a, b) => a - b));
  expect(probe.stages[0]).toBe(STAGE_ORDER[0]);
  expect(probe.stages).toContain("Running the query...");

  // Token by token: the SQL pane grew in steps rather than appearing whole.
  // (One step would still be a legitimate response from the LLM cache — but
  // this question has not been asked before on a database this runner just
  // created, so the tokens come off the model.)
  expect(probe.sqlLengths.length).toBeGreaterThan(1);
  expect(probe.sqlLengths).toEqual([...probe.sqlLengths].sort((a, b) => a - b));

  expect(probe.composerWasLocked).toBe(true);
  // …and unlocked again afterwards.
  await expect(box).toBeEnabled();
});

test("a data result renders a summary, its SQL expanded, and a chart", async ({}) => {
  const reply = lastAssistantMessage(page);

  // A summary sentence — not an empty bubble.
  await expect(reply.locator(".msg-text")).not.toBeEmpty();

  // The SQL is shown expanded, not folded behind a closed toggle: it is the
  // answer's provenance, and a user who has to go looking for it will not.
  const sql = reply.locator("details.msg-sql");
  await expect(sql).toBeVisible();
  await expect(sql).toHaveAttribute("open", "");
  await expect(sql.locator("pre")).toContainText(/select/i);

  // And it drew something. Which shape depends on what the LLM returned, so any
  // of the three the result view can pick counts.
  await expect(reply.locator(".result-chart, .result-table, .result-single").first()).toBeVisible();
});

test("the chart-type switcher swaps between the shapes the data supports", async ({}) => {
  const reply = lastAssistantMessage(page);
  const toolbar = reply.locator(".result-toolbar");

  // The toolbar only appears when more than one shape fits the result. "Total
  // sales by region" is one label column and one numeric column over a handful
  // of rows, which is exactly the shape that offers bar/line/area/pie/table.
  await expect(toolbar).toBeVisible();

  for (const [label, marker] of [
    ["Bar", ".recharts-bar"],
    ["Line", ".recharts-line"],
    ["Area", ".recharts-area"],
    ["Pie", ".recharts-pie"],
  ]) {
    const button = toolbar.getByRole("button", { name: label, exact: true });
    if ((await button.count()) === 0) continue; // shape not offered for this result
    await button.click();
    await expect(reply.locator(marker)).toBeVisible();
  }

  // Table is always offered, and is a table rather than a chart.
  await toolbar.getByRole("button", { name: "Table", exact: true }).click();
  await expect(reply.locator("table.result-table")).toBeVisible();
  await expect(reply.locator(".result-chart")).toHaveCount(0);
});

test("a follow-up question is answered in the context of the last one", async ({}) => {
  const sqlBlocks = page.locator("details.msg-sql pre");
  const firstSql = (await sqlBlocks.last().innerText()).toLowerCase();

  // "the top 2" names nothing to take the top 2 *of*. It can only be answered by
  // a server that remembered the previous turn (conversation_store history is
  // fed into the prompt).
  await ask(page, "now show only the top 2");

  const secondSql = (await sqlBlocks.last().innerText()).toLowerCase();
  expect(secondSql).not.toBe(firstSql);
  // Same subject as the question before it…
  expect(secondSql).toContain("sales");
  // …narrowed. Not an assertion about which two rows: only that the follow-up
  // was understood as a restriction of the previous answer.
  expect(secondSql).toMatch(/limit\s+2\b/);
});

test("chitchat is answered as text, with no SQL and no chart", async ({}) => {
  await ask(page, "hello");

  const reply = lastAssistantMessage(page);
  await expect(reply.locator(".msg-text")).not.toBeEmpty();
  // The classifier short-circuits before the SQL pipeline, so there is nothing
  // to show provenance for and nothing to plot.
  await expect(reply.locator("details.msg-sql")).toHaveCount(0);
  await expect(reply.locator(".result-chart, .result-table, .result-single")).toHaveCount(0);
});

test("an off-topic question is refused, and says what it will answer instead", async ({}) => {
  await ask(page, "what is the capital of France?");

  const reply = lastAssistantMessage(page);
  await expect(reply).toContainText(/I can only answer questions about your uploaded data/i);
  await expect(reply.locator("details.msg-sql")).toHaveCount(0);
});

test("a query that fails at runtime still shows why (regression, PR #111)", async ({}) => {
  // The shape that used to break: the stream reports failures as `stage: "done"`
  // with `message` and no `summary`, and the chat only read `summary` — so a
  // query that produced SQL and then died rendered an empty bubble with nothing
  // but a "View SQL Query" toggle under it.
  //
  // Pinned with a stubbed stream rather than a live one. The defect is in how
  // the client reads the envelope, and only an exact envelope proves it is read
  // correctly; whether today's model writes SQL that happens to fail is not
  // something a regression test should be betting on. The live half follows.
  const body = [
    'data: {"stage":"classifying","message":"Analyzing your question..."}\n\n',
    'data: {"stage":"executing","message":"Running the query on your data..."}\n\n',
    'data: {"stage":"done","result":{"type":"error","summary":null,' +
      '"message":"Conversion Error: Could not convert string \'Laptop\' to INT64",' +
      '"sql":"SELECT SUM(CAST(product AS INTEGER)) FROM sales"}}\n\n',
  ].join("");

  await page.route("**/api/query/stream", (route) =>
    route.fulfill({ status: 200, contentType: "text/event-stream", body }),
  );

  try {
    await ask(page, "cast the product column to an integer and sum it");

    const reply = lastAssistantMessage(page);
    // The message is the whole point: it is the only thing that tells the user
    // what went wrong.
    await expect(reply.locator(".msg-text")).toContainText(/Could not convert string/i);
    await expect(reply.locator(".msg-text")).not.toBeEmpty();
    // The SQL still shows — it failed, but it is what ran, and the bubble is no
    // longer just that toggle on its own.
    await expect(reply.locator("details.msg-sql pre")).toContainText(/select/i);
  } finally {
    await page.unroute("**/api/query/stream");
  }
});

test("the same question against the real stack never lands in an empty bubble", async ({}) => {
  // The live half of the case above. Whether the model writes SQL that binds and
  // then fails, or SQL that works, is genuinely non-deterministic — so the
  // assertion is the invariant that holds either way, and that PR #111 broke:
  // an assistant turn always says something.
  await ask(page, "cast the product column to an integer and sum it");

  const reply = lastAssistantMessage(page);
  await expect(reply.locator(".msg-text")).not.toBeEmpty();
  expect((await reply.locator(".msg-text").innerText()).trim().length).toBeGreaterThan(0);
});

test("Export PDF downloads the session report", async ({}) => {
  const [download] = await Promise.all([
    page.waitForEvent("download"),
    page.getByRole("button", { name: /export pdf/i }).click(),
  ]);

  expect(download.suggestedFilename()).toMatch(/^report_sales\.pdf$/);
  await expect(page.getByText(/Report downloaded/i)).toBeVisible();
});
