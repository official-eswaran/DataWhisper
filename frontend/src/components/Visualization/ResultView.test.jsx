import React from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import ResultView from "./ResultView";

// ResultView is what every answer is actually displayed in, and it had no
// cover at all — so a recharts upgrade could only be judged by "the bundle
// still builds".
//
// recharts sizes itself from the DOM, and jsdom reports every element as 0x0,
// so a real ResponsiveContainer renders an empty box and every chart assertion
// would pass vacuously. Substituting it with one that hands the chart a fixed
// size is what makes the SVG real enough to assert on. Everything else here is
// the genuine component.
vi.mock("recharts", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    ResponsiveContainer: ({ children, height = 300 }) =>
      React.cloneElement(children, { width: 800, height }),
  };
});

const labelValue = (n = 4) =>
  Array.from({ length: n }, (_, i) => ({ region: `R${i}`, revenue: (i + 1) * 100 }));

const rows = (n) =>
  Array.from({ length: n }, (_, i) => ({ region: `R${i}`, revenue: i }));

const svg = (container) => container.querySelector("svg.recharts-surface");

// ── Guards ───────────────────────────────────────────────────────────────────

test("renders nothing when there are no rows", () => {
  const { container } = render(
    <ResultView type="table" data={[]} columns={["region"]} />
  );
  expect(container).toBeEmptyDOMElement();
});

test("renders nothing when data is missing entirely", () => {
  const { container } = render(
    <ResultView type="table" data={null} columns={["region"]} />
  );
  expect(container).toBeEmptyDOMElement();
});

// ── single_value ─────────────────────────────────────────────────────────────

test("a scalar result shows its column name and value", () => {
  render(
    <ResultView type="single_value" data={[{ total_revenue: 525 }]} columns={["total_revenue"]} />
  );
  expect(screen.getByText("total_revenue")).toBeInTheDocument();
  expect(screen.getByText("525")).toBeInTheDocument();
});

test("a large scalar is thousands-separated", () => {
  render(
    <ResultView type="single_value" data={[{ total: 1234567 }]} columns={["total"]} />
  );
  // Matches the backend summary formatting, which also separates thousands.
  expect(screen.getByText("1,234,567")).toBeInTheDocument();
});

test("a non-numeric scalar is shown as-is", () => {
  render(<ResultView type="single_value" data={[{ region: "North" }]} columns={["region"]} />);
  expect(screen.getByText("North")).toBeInTheDocument();
});

// ── table ────────────────────────────────────────────────────────────────────

test("a table renders a header per column and a row per record", () => {
  render(<ResultView type="table" data={labelValue(3)} columns={["region", "revenue"]} />);
  expect(screen.getByRole("columnheader", { name: /region/i })).toBeInTheDocument();
  expect(screen.getAllByRole("row")).toHaveLength(4); // header + 3
});

test("null cells render as an em dash rather than blank", () => {
  render(
    <ResultView type="table" data={[{ region: "N", revenue: null }]} columns={["region", "revenue"]} />
  );
  expect(screen.getByText("—")).toBeInTheDocument();
});

test("clicking a header sorts ascending, clicking again reverses it", async () => {
  const data = [
    { region: "C", revenue: 30 },
    { region: "A", revenue: 10 },
    { region: "B", revenue: 20 },
  ];
  render(<ResultView type="table" data={data} columns={["region", "revenue"]} />);
  const header = screen.getByRole("columnheader", { name: /region/i });

  await userEvent.click(header);
  let firstCell = within(screen.getAllByRole("row")[1]).getAllByRole("cell")[0];
  expect(firstCell).toHaveTextContent("A");

  await userEvent.click(header);
  firstCell = within(screen.getAllByRole("row")[1]).getAllByRole("cell")[0];
  expect(firstCell).toHaveTextContent("C");
});

test("numeric columns sort numerically, not as strings", async () => {
  // "100" < "9" as strings; the component must not fall into that.
  const data = [{ region: "A", revenue: 9 }, { region: "B", revenue: 100 }];
  render(<ResultView type="table" data={data} columns={["region", "revenue"]} />);

  await userEvent.click(screen.getByRole("columnheader", { name: /revenue/i }));

  const firstRow = within(screen.getAllByRole("row")[1]).getAllByRole("cell");
  expect(firstRow[1]).toHaveTextContent("9");
});

test("nulls sort last regardless of direction", async () => {
  const data = [
    { region: "A", revenue: null },
    { region: "B", revenue: 5 },
  ];
  render(<ResultView type="table" data={data} columns={["region", "revenue"]} />);
  await userEvent.click(screen.getByRole("columnheader", { name: /revenue/i }));
  const lastRow = within(screen.getAllByRole("row")[2]).getAllByRole("cell");
  expect(lastRow[0]).toHaveTextContent("A");
});

test("nulls sort last whichever side of the comparison they land on", async () => {
  // The comparator has a branch per side, and which one runs depends on the
  // order the sort happens to visit pairs in — so a two-row fixture exercises
  // only one of them and the other half of the rule goes unchecked.
  const data = [
    { region: "A", revenue: 5 },
    { region: "B", revenue: null },
    { region: "C", revenue: 1 },
  ];
  render(<ResultView type="table" data={data} columns={["region", "revenue"]} />);

  await userEvent.click(screen.getByRole("columnheader", { name: /revenue/i }));

  const firstCells = screen.getAllByRole("row").slice(1).map(
    (r) => within(r).getAllByRole("cell")[0].textContent
  );
  expect(firstCells).toEqual(["C", "A", "B"]);
});

test("a second click sorts numbers descending, not just reversed as text", async () => {
  const data = [
    { region: "A", revenue: 9 },
    { region: "B", revenue: 100 },
    { region: "C", revenue: 20 },
  ];
  render(<ResultView type="table" data={data} columns={["region", "revenue"]} />);
  const header = screen.getByRole("columnheader", { name: /revenue/i });

  await userEvent.click(header);
  await userEvent.click(header);

  const values = screen.getAllByRole("row").slice(1).map(
    (r) => within(r).getAllByRole("cell")[1].textContent
  );
  expect(values).toEqual(["100", "20", "9"]);
});

// ── pagination ───────────────────────────────────────────────────────────────

test("a short table has no pagination controls", () => {
  render(<ResultView type="table" data={rows(25)} columns={["region", "revenue"]} />);
  expect(screen.queryByRole("button", { name: /next/i })).not.toBeInTheDocument();
});

test("a long table paginates at 25 rows", () => {
  render(<ResultView type="table" data={rows(60)} columns={["region", "revenue"]} />);
  expect(screen.getAllByRole("row")).toHaveLength(26); // header + PAGE_SIZE
  expect(screen.getByText(/page 1 of 3/i)).toBeInTheDocument();
  expect(screen.getByText(/60 rows/i)).toBeInTheDocument();
});

test("next and prev move between pages and stop at the ends", async () => {
  render(<ResultView type="table" data={rows(60)} columns={["region", "revenue"]} />);
  const next = screen.getByRole("button", { name: /next/i });
  const prev = screen.getByRole("button", { name: /prev/i });

  expect(prev).toBeDisabled(); // first page

  await userEvent.click(next);
  expect(screen.getByText(/page 2 of 3/i)).toBeInTheDocument();
  expect(prev).not.toBeDisabled();

  await userEvent.click(next);
  expect(screen.getByText(/page 3 of 3/i)).toBeInTheDocument();
  expect(next).toBeDisabled(); // last page

  await userEvent.click(prev);
  expect(screen.getByText(/page 2 of 3/i)).toBeInTheDocument();
});

test("re-sorting returns to the first page", async () => {
  render(<ResultView type="table" data={rows(60)} columns={["region", "revenue"]} />);
  await userEvent.click(screen.getByRole("button", { name: /next/i }));
  expect(screen.getByText(/page 2 of 3/i)).toBeInTheDocument();

  await userEvent.click(screen.getByRole("columnheader", { name: /revenue/i }));
  // Otherwise the user sorts and is left looking at page 2 of the new order.
  expect(screen.getByText(/page 1 of 3/i)).toBeInTheDocument();
});

// ── charts actually draw ─────────────────────────────────────────────────────

test("a bar chart draws one bar per row", () => {
  const { container } = render(
    <ResultView type="bar" data={labelValue(4)} columns={["region", "revenue"]} />
  );
  expect(svg(container)).toBeTruthy();
  expect(container.querySelectorAll(".recharts-bar-rectangle")).toHaveLength(4);
});

test("a pie chart binds one entry per row", async () => {
  const { container } = render(
    <ResultView type="pie" data={labelValue(3)} columns={["region", "revenue"]} />
  );
  const pie = container.querySelector(".recharts-pie");
  expect(pie).toBeTruthy();

  // The legend is populated a tick after mount, so this has to wait.
  await waitFor(() =>
    expect(container.querySelectorAll(".recharts-legend-item").length).toBe(3)
  );

  // Deliberately asserting on the legend, not on sector paths. Pie sectors are
  // the one chart recharts will not draw under jsdom — they need real layout
  // geometry, and the element stays empty even after waiting (bars, lines,
  // areas and scatter all render fine). The legend is driven by the same
  // data/nameKey binding, so it still catches a pie wired to the wrong column;
  // it just cannot vouch for the geometry. The E2E covers that it visibly draws.
  expect(container.textContent).toContain("R0");
});

test("a line chart draws a line", () => {
  const { container } = render(
    <ResultView type="line" data={labelValue(5)} columns={["region", "revenue"]} />
  );
  expect(container.querySelector(".recharts-line")).toBeTruthy();
});

test("an area chart draws a filled area", () => {
  const { container } = render(
    <ResultView type="area" data={labelValue(5)} columns={["region", "revenue"]} />
  );
  expect(container.querySelector(".recharts-area")).toBeTruthy();
});

test("a scatter chart plots the two numeric columns", () => {
  const data = Array.from({ length: 6 }, (_, i) => ({ height: i, weight: i * 2 }));
  const { container } = render(
    <ResultView type="scatter" data={data} columns={["height", "weight"]} />
  );
  expect(container.querySelectorAll(".recharts-scatter-symbol")).toHaveLength(6);
});

test("a scatter chart falls back to the first two columns when only one is numeric", () => {
  // The scatter toggle is offered on the backend's say-so, and a result with a
  // text column reaches this path. Plotting `undefined` against `undefined` —
  // which is what indexing past the end of numericCols would give — draws an
  // empty chart rather than an obviously wrong one, so it would go unnoticed.
  const data = Array.from({ length: 5 }, (_, i) => ({ name: `N${i}`, score: i * 3 }));
  const { container } = render(
    <ResultView type="scatter" data={data} columns={["name", "score"]} />
  );
  expect(container.querySelectorAll(".recharts-scatter-symbol")).toHaveLength(5);
});

test("multi_series draws one series per numeric column", () => {
  const data = [
    { region: "N", revenue: 1, cost: 2 },
    { region: "S", revenue: 3, cost: 4 },
  ];
  const { container } = render(
    <ResultView type="multi_series" data={data} columns={["region", "revenue", "cost"]} />
  );
  // Two numeric columns → two grouped bar series.
  expect(container.querySelectorAll(".recharts-bar")).toHaveLength(2);
});

// ── multi_series: bars or lines is a judgement about the data ────────────────
// Grouped bars compare categories; lines show a trend. Reading a trend off
// bars, or comparing four unrelated categories on a line chart, both mislead.

test("a time-like label column is drawn as lines, not grouped bars", () => {
  const data = [
    { month: "Jan", revenue: 1, cost: 2 },
    { month: "Feb", revenue: 3, cost: 4 },
  ];
  const { container } = render(
    <ResultView type="multi_series" data={data} columns={["month", "revenue", "cost"]} />
  );

  expect(container.querySelectorAll(".recharts-line")).toHaveLength(2);
  expect(container.querySelectorAll(".recharts-bar")).toHaveLength(0);
});

test("enough rows are drawn as lines even when the label is not time-like", () => {
  // Past ~10 categories grouped bars stop being readable whatever they are.
  const data = Array.from({ length: 11 }, (_, i) => ({
    region: `R${i}`, revenue: i, cost: i * 2,
  }));
  const { container } = render(
    <ResultView type="multi_series" data={data} columns={["region", "revenue", "cost"]} />
  );

  expect(container.querySelectorAll(".recharts-line")).toHaveLength(2);
});

test("too many series stays on bars, even with a time-like label", () => {
  // Five overlapping lines is spaghetti; the row-count and date rules are both
  // subordinate to that.
  const data = Array.from({ length: 12 }, (_, i) => ({
    month: `M${i}`, a: i, b: i, c: i, d: i, e: i,
  }));
  const { container } = render(
    <ResultView type="multi_series" data={data} columns={["month", "a", "b", "c", "d", "e"]} />
  );

  expect(container.querySelectorAll(".recharts-bar")).toHaveLength(5);
  expect(container.querySelectorAll(".recharts-line")).toHaveLength(0);
});

// ── histogram binning is real logic, not just a render ───────────────────────

test("a histogram bins a numeric column into 15 buckets", () => {
  const data = Array.from({ length: 100 }, (_, i) => ({ value: i }));
  const { container } = render(
    <ResultView type="histogram" data={data} columns={["value"]} />
  );
  expect(container.querySelectorAll(".recharts-bar-rectangle")).toHaveLength(15);
});

test("a constant column collapses to a single bin instead of dividing by zero", () => {
  // min === max would make the bin width 0 and every value land in bin NaN.
  const data = Array.from({ length: 10 }, () => ({ value: 7 }));
  const { container } = render(
    <ResultView type="histogram" data={data} columns={["value"]} />
  );
  expect(container.querySelectorAll(".recharts-bar-rectangle")).toHaveLength(1);
});

test("an all-null column draws no bins rather than a bin of NaN", () => {
  // `Math.min(...[])` is Infinity and `Math.max(...[])` is -Infinity, so
  // without the empty check the axis fills with "NaN–NaN" labels. The bars
  // themselves are zero-height either way, so the labels are what tells the
  // two apart — asserting only "no bars" passes without the guard.
  const data = Array.from({ length: 5 }, () => ({ value: null }));
  const { container } = render(
    <ResultView type="histogram" data={data} columns={["value"]} />
  );

  expect(container.querySelectorAll(".recharts-bar-rectangle")).toHaveLength(0);
  expect(container.textContent).not.toMatch(/NaN|Infinity/);
});

test("a numeric column whose first row is null is still binned", () => {
  // `numericCols` reads `typeof` off row 0 only, so one leading null makes a
  // perfectly numeric column look non-numeric — and the fallback to the first
  // column is what keeps it charted. This is the same frontend/backend
  // disagreement about "numeric" noted in buildHistogramData.
  const data = [{ v: null }, ...Array.from({ length: 30 }, (_, i) => ({ v: i }))];
  const { container } = render(
    <ResultView type="histogram" data={data} columns={["v"]} />
  );

  expect(container.querySelectorAll(".recharts-bar-rectangle").length).toBeGreaterThan(0);
});

test("a histogram of a text column draws nothing instead of crashing", () => {
  // With no numeric column the binner falls back to the first column. Text
  // values used to survive the filter, make Math.min return NaN, and land every
  // row in `buckets[NaN]` — a TypeError, which ErrorBoundary turns into the
  // whole app being replaced by its fallback screen.
  const data = [{ label: "a" }, { label: "b" }, { label: "a" }];

  expect(() =>
    render(<ResultView type="histogram" data={data} columns={["label"]} />)
  ).not.toThrow();
});

test("non-numeric values in a numeric column are binned out, not crashed on", () => {
  // The realistic version of the above: the backend calls the column numeric
  // (pandas sees a float column) while the frontend reads `typeof` off the
  // first row. One stray string is enough.
  const data = [{ v: 1 }, { v: "n/a" }, { v: 3 }, { v: 5 }];
  const { container } = render(
    <ResultView type="histogram" data={data} columns={["v"]} />
  );

  expect(container.querySelectorAll(".recharts-bar-rectangle").length).toBeGreaterThan(0);
});

// ── the type toggle ──────────────────────────────────────────────────────────

test("a label+value result offers the shapes that suit it", () => {
  render(<ResultView type="bar" data={labelValue(4)} columns={["region", "revenue"]} />);
  for (const name of [/bar/i, /line/i, /area/i, /pie/i, /table/i]) {
    expect(screen.getByRole("button", { name })).toBeInTheDocument();
  }
});

test("pie is withheld once there are too many slices to read", () => {
  render(<ResultView type="bar" data={labelValue(12)} columns={["region", "revenue"]} />);
  expect(screen.getByRole("button", { name: /bar/i })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /^pie$/i })).not.toBeInTheDocument();
});

test("switching the toggle re-renders as the chosen type", async () => {
  const { container } = render(
    <ResultView type="bar" data={labelValue(4)} columns={["region", "revenue"]} />
  );
  expect(container.querySelectorAll(".recharts-bar-rectangle")).toHaveLength(4);

  await userEvent.click(screen.getByRole("button", { name: /table/i }));
  expect(screen.getAllByRole("row")).toHaveLength(5); // header + 4
  expect(container.querySelector(".recharts-bar-rectangle")).toBeNull();
});

test("table is always on offer, whatever the backend chose", () => {
  render(<ResultView type="scatter" data={[{ a: 1, b: 2 }, { a: 3, b: 4 }]} columns={["a", "b"]} />);
  expect(screen.getByRole("button", { name: /table/i })).toBeInTheDocument();
});

test("a scalar result gets no toolbar to toggle", () => {
  render(<ResultView type="single_value" data={[{ total: 5 }]} columns={["total"]} />);
  expect(screen.queryByRole("button", { name: /table/i })).not.toBeInTheDocument();
});

test("a chart type this build does not know falls back to the table", async () => {
  // The backend chooses the type, and the two deploy separately — a new shape
  // added there reaches a frontend that has never heard of it. Rendering
  // nothing would lose the answer entirely; the table can always show it.
  const { container } = render(
    <ResultView type="treemap" data={labelValue(3)} columns={["region", "revenue"]} />
  );

  expect(screen.getAllByRole("row")).toHaveLength(4); // header + 3
  // No chart was drawn — the toolbar's own icons are SVGs too, so this has to
  // look for the recharts surface specifically.
  expect(svg(container)).toBeNull();
});

// ── PNG download affordance ──────────────────────────────────────────────────

test("charts offer a PNG download, tables do not", async () => {
  render(<ResultView type="bar" data={labelValue(4)} columns={["region", "revenue"]} />);
  const download = screen.getByTitle(/download chart as png/i);
  expect(download).toBeInTheDocument();

  // A table has no SVG to export, so the affordance goes away.
  await userEvent.click(screen.getByRole("button", { name: /table/i }));
  expect(screen.queryByTitle(/download chart as png/i)).not.toBeInTheDocument();
});

// **Two things in this file are deliberately not covered, both unreachable
// rather than untested:**
//
//   * `if (!ref.current) return` in `downloadChartAsPng`. The download button
//     and the chart container render under the same condition, so React has set
//     the ref before the button can be clicked. Left in place because it is a
//     cheap guard against that arrangement changing, and a throw inside an
//     onClick would take the whole app to the ErrorBoundary fallback.
//   * the histogram tooltip's `formatter`. recharts activates the tooltip in
//     jsdom but never populates a payload — the active index comes from layout
//     measurements jsdom does not produce — so the render prop is never called.
//     Fighting that would test recharts, not this component.
//
// Everything else is at 100%.

// ── PNG download, end to end ─────────────────────────────────────────────────
// Until now the tests asserted the *button* existed and never pressed it, so
// the whole export — serialise, size a canvas, paint, hand the browser a file —
// ran nowhere. jsdom implements none of canvas, blob URLs or image decoding, so
// each is replaced with a recorder; what is being asserted is the sequence this
// code performs, which is where its bugs would be.

/** Replace the browser bits `downloadChartAsPng` reaches for. */
function stubExportEnvironment({ dpr = 1 } = {}) {
  const order = [];
  const ctx = {
    set fillStyle(v) { this._fillStyle = v; },
    get fillStyle() { return this._fillStyle; },
    scale: vi.fn((...a) => order.push(["scale", ...a])),
    fillRect: vi.fn((...a) => order.push(["fillRect", ...a])),
    drawImage: vi.fn(() => order.push(["drawImage"])),
  };

  const images = [];
  const anchor = { click: vi.fn(), download: "", href: "" };
  const realCreateElement = document.createElement.bind(document);
  let canvas = null;

  vi.spyOn(document, "createElement").mockImplementation((tag, ...rest) => {
    if (tag === "a") return anchor;
    const el = realCreateElement(tag, ...rest);
    if (tag === "canvas") canvas = el;
    return el;
  });
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(ctx);
  vi.spyOn(HTMLCanvasElement.prototype, "toDataURL").mockReturnValue("data:image/png;base64,PNG");
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockReturnValue({
    width: 800, height: 300, top: 0, left: 0, right: 800, bottom: 300, x: 0, y: 0,
  });
  vi.stubGlobal("Image", class {
    constructor() { images.push(this); }
  });
  vi.stubGlobal("URL", {
    createObjectURL: vi.fn(() => "blob:chart"),
    revokeObjectURL: vi.fn(),
  });

  return { ctx, order, images, anchor, canvas: () => canvas };
}

/** Click download and run the image's onload, which is where the work happens. */
async function exportChart(env) {
  await userEvent.click(screen.getByTitle(/download chart as png/i));
  env.images.forEach((img) => img.onload?.());
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

test("downloading hands the browser a named PNG", async () => {
  const env = stubExportEnvironment();
  render(<ResultView type="bar" data={labelValue(4)} columns={["region", "revenue"]} />);

  await exportChart(env);

  expect(env.anchor.href).toBe("data:image/png;base64,PNG");
  expect(env.anchor.download).toMatch(/^chart_\d+\.png$/);
  expect(env.anchor.click).toHaveBeenCalled();
});

test("the blob URL is released once the image has been drawn", async () => {
  // One leaked object URL per download, held for the life of the tab. Nothing
  // visibly breaks, which is why it would never be noticed.
  const env = stubExportEnvironment();
  render(<ResultView type="bar" data={labelValue(4)} columns={["region", "revenue"]} />);

  await exportChart(env);

  expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:chart");
});

test("the background is painted before the chart, not over it", async () => {
  // Both calls happen either way, so only the order tells the difference — and
  // getting it wrong yields a plain dark rectangle with the chart underneath.
  const env = stubExportEnvironment();
  render(<ResultView type="bar" data={labelValue(4)} columns={["region", "revenue"]} />);

  await exportChart(env);

  const steps = env.order.map(([name]) => name);
  expect(steps.indexOf("fillRect")).toBeLessThan(steps.indexOf("drawImage"));
});

test("the exported PNG is opaque, matching the app's own background", async () => {
  // An SVG has no background of its own. Without the fill the PNG is
  // transparent, which renders as black in some viewers and white in others —
  // and the chart's text is light grey, so half of them show nothing.
  const env = stubExportEnvironment();
  render(<ResultView type="bar" data={labelValue(4)} columns={["region", "revenue"]} />);

  await exportChart(env);

  expect(env.ctx.fillStyle).toBe("#13141b");
  expect(env.ctx.fillRect).toHaveBeenCalledWith(0, 0, 800, 300);
});

test("a retina screen gets a canvas at its own pixel density", async () => {
  // Sizing the canvas in CSS pixels produces a visibly soft export on every
  // modern laptop. The scale() is what keeps the drawing coordinates in CSS
  // pixels after the canvas is enlarged.
  const env = stubExportEnvironment();
  vi.stubGlobal("devicePixelRatio", 2);
  render(<ResultView type="bar" data={labelValue(4)} columns={["region", "revenue"]} />);

  await exportChart(env);

  // The canvas is enlarged by the ratio...
  expect(env.canvas().width).toBe(1600);
  expect(env.canvas().height).toBe(600);
  // ...and the context scaled to match, so drawing stays in CSS pixels and the
  // chart is not cropped to a quarter of the frame.
  expect(env.ctx.scale).toHaveBeenCalledWith(2, 2);
  expect(env.ctx.fillRect).toHaveBeenCalledWith(0, 0, 800, 300);
});

test("a browser that reports no pixel ratio still exports at 1x", async () => {
  // `devicePixelRatio` is absent in older embedded webviews and in jsdom. A
  // missing fallback makes the canvas NaN wide, and `toDataURL` on a NaN-sized
  // canvas yields a blank file — a download that silently produces nothing.
  const env = stubExportEnvironment();
  vi.stubGlobal("devicePixelRatio", undefined);
  render(<ResultView type="bar" data={labelValue(4)} columns={["region", "revenue"]} />);

  await exportChart(env);

  expect(env.ctx.scale).toHaveBeenCalledWith(1, 1);
  expect(env.anchor.click).toHaveBeenCalled();
});

test("a chart area with no SVG exports nothing rather than a blank file", async () => {
  const env = stubExportEnvironment();
  const { container } = render(
    <ResultView type="bar" data={labelValue(4)} columns={["region", "revenue"]} />
  );
  container.querySelector(".result-chart").innerHTML = "";

  await exportChart(env);

  expect(env.anchor.click).not.toHaveBeenCalled();
  expect(URL.createObjectURL).not.toHaveBeenCalled();
});
