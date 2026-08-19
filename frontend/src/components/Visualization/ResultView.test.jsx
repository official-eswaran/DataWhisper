import React from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";
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

test("a large scalar is digit-grouped for the reader's locale", () => {
  render(
    <ResultView type="single_value" data={[{ total: 1234567 }]} columns={["total"]} />
  );
  // Derived, not hardcoded. ResultView calls toLocaleString() with no explicit
  // locale, so the grouping follows whoever is running the test: "1,234,567" on
  // en-US, "12,34,567" on en-IN. Pinning the en-US string asserted the runner's
  // locale rather than the component's behaviour, and failed everywhere else.
  expect(screen.getByText((1234567).toLocaleString())).toBeInTheDocument();
  // The grouping itself is the claim — the raw digits must not reach the DOM.
  expect(screen.queryByText("1234567")).not.toBeInTheDocument();
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

// ── PNG download affordance ──────────────────────────────────────────────────

test("charts offer a PNG download, tables do not", async () => {
  render(<ResultView type="bar" data={labelValue(4)} columns={["region", "revenue"]} />);
  const download = screen.getByTitle(/download chart as png/i);
  expect(download).toBeInTheDocument();

  // A table has no SVG to export, so the affordance goes away.
  await userEvent.click(screen.getByRole("button", { name: /table/i }));
  expect(screen.queryByTitle(/download chart as png/i)).not.toBeInTheDocument();
});
