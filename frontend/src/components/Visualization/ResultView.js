import React, { useState, useRef, useCallback } from "react";
import {
  BarChart, Bar,
  LineChart, Line,
  AreaChart, Area,
  PieChart, Pie,
  ScatterChart, Scatter,
  XAxis, YAxis,
  CartesianGrid, Tooltip,
  ResponsiveContainer,
  Cell, Legend,
} from "recharts";
import {
  FiBarChart2, FiTrendingUp, FiPieChart, FiGrid,
  FiActivity, FiCircle, FiDownload,
} from "react-icons/fi";
import "./ResultView.css";

const COLORS = [
  "#6366f1", "#22c55e", "#f59e0b", "#ef4444",
  "#8b5cf6", "#06b6d4", "#ec4899", "#14b8a6",
  "#f97316", "#a855f7",
];

const TOOLTIP_STYLE = {
  contentStyle: {
    background: "#1a1b23",
    border: "1px solid #2e2f3e",
    borderRadius: "8px",
    color: "#e4e4e7",
    fontSize: "13px",
  },
};

const PAGE_SIZE = 25;

// ── Chart type metadata for the toggle toolbar ────────────────────────────────
const CHART_TYPES = [
  { key: "bar",          icon: FiBarChart2,  label: "Bar"       },
  { key: "line",         icon: FiTrendingUp, label: "Line"      },
  { key: "area",         icon: FiActivity,   label: "Area"      },
  { key: "pie",          icon: FiPieChart,   label: "Pie"       },
  { key: "scatter",      icon: FiCircle,     label: "Scatter"   },
  { key: "multi_series", icon: FiBarChart2,  label: "Multi"     },
  { key: "table",        icon: FiGrid,       label: "Table"     },
];

// ── Determine which toggle options to show for a given result ─────────────────
function getAvailableTypes(backendType, columns, data) {
  if (!data || data.length === 0) return [];

  const numericCols = columns.filter(
    (c) => data.length > 0 && typeof data[0][c] === "number"
  );
  const categoryCols = columns.filter(
    (c) => data.length > 0 && typeof data[0][c] !== "number"
  );

  const types = new Set([backendType]);

  if (columns.length === 2 && numericCols.length >= 1) {
    types.add("bar").add("line").add("area");
    if (data.length <= 8 && categoryCols.length === 1) types.add("pie");
  }
  if (columns.length >= 3 && numericCols.length >= 2 && categoryCols.length === 1) {
    types.add("multi_series").add("bar").add("line");
  }
  if (numericCols.length === 2 && categoryCols.length === 0) {
    types.add("scatter");
  }
  types.add("table"); // always available

  // Keep CHART_TYPES order
  return CHART_TYPES.filter((t) => types.has(t.key));
}

// ── Histogram binning (frontend) ──────────────────────────────────────────────
function buildHistogramData(data, col, bins = 15) {
  const values = data.map((d) => d[col]).filter((v) => v != null);
  if (values.length === 0) return [];
  const min = Math.min(...values);
  const max = Math.max(...values);
  if (min === max) return [{ bin: String(min), count: values.length }];
  const width = (max - min) / bins;
  const buckets = Array.from({ length: bins }, (_, i) => ({
    bin: `${(min + i * width).toFixed(1)}–${(min + (i + 1) * width).toFixed(1)}`,
    binStart: min + i * width,
    count: 0,
  }));
  values.forEach((v) => {
    let i = Math.floor((v - min) / width);
    if (i === bins) i = bins - 1;
    buckets[i].count++;
  });
  return buckets;
}

// ── Chart download helper ─────────────────────────────────────────────────────
function downloadChartAsPng(ref, filename) {
  if (!ref.current) return;
  const svgEl = ref.current.querySelector("svg");
  if (!svgEl) return;

  const serializer = new XMLSerializer();
  const svgStr = serializer.serializeToString(svgEl);
  const { width, height } = svgEl.getBoundingClientRect();

  const canvas = document.createElement("canvas");
  const dpr = window.devicePixelRatio || 1;
  canvas.width = width * dpr;
  canvas.height = height * dpr;
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  ctx.fillStyle = "#13141b";
  ctx.fillRect(0, 0, width, height);

  const img = new Image();
  const blob = new Blob([svgStr], { type: "image/svg+xml;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  img.onload = () => {
    ctx.drawImage(img, 0, 0);
    URL.revokeObjectURL(url);
    const link = document.createElement("a");
    link.download = filename;
    link.href = canvas.toDataURL("image/png");
    link.click();
  };
  img.src = url;
}

// ─────────────────────────────────────────────────────────────────────────────
function ResultView({ type, data, columns }) {
  const [viewType, setViewType]     = useState(type);
  const [page, setPage]             = useState(0);
  const [sortCol, setSortCol]       = useState(null);
  const [sortAsc, setSortAsc]       = useState(true);
  const chartRef                    = useRef(null);

  // ── Sort helper (table only) — must be before early return ─────────────────
  const getSortedData = useCallback(() => {
    if (!data || !sortCol) return data || [];
    return [...data].sort((a, b) => {
      const av = a[sortCol], bv = b[sortCol];
      if (av == null) return 1;
      if (bv == null) return -1;
      if (typeof av === "number" && typeof bv === "number") {
        return sortAsc ? av - bv : bv - av;
      }
      return sortAsc
        ? String(av).localeCompare(String(bv))
        : String(bv).localeCompare(String(av));
    });
  }, [data, sortCol, sortAsc]);

  if (!data || data.length === 0) return null;

  const availableTypes = getAvailableTypes(type, columns, data);
  const totalPages     = Math.ceil(data.length / PAGE_SIZE);

  const handleSort = (col) => {
    if (sortCol === col) setSortAsc((a) => !a);
    else { setSortCol(col); setSortAsc(true); }
    setPage(0);
  };

  // ── Numeric columns helper ──────────────────────────────────────────────────
  const numericCols = columns.filter(
    (c) => data.length > 0 && typeof data[0][c] === "number"
  );
  const labelCol  = columns.find((c) => typeof data[0][c] !== "number") || columns[0];
  const valueCol  = numericCols[0] || columns[1];

  // ── Type-specific renders ─────────────────────────────────────────────────

  const renderSingleValue = () => {
    const value = data[0][columns[0]];
    return (
      <div className="result-single">
        <span className="single-label">{columns[0]}</span>
        <span className="single-value">
          {typeof value === "number" ? value.toLocaleString() : value}
        </span>
      </div>
    );
  };

  const renderBarChart = () => (
    <ResponsiveContainer width="100%" height={300}>
      <BarChart data={data} margin={{ top: 4, right: 16, left: 0, bottom: 4 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#2e2f3e" />
        <XAxis dataKey={labelCol} stroke="#71717a" fontSize={12} tick={{ fill: "#71717a" }} />
        <YAxis stroke="#71717a" fontSize={12} tick={{ fill: "#71717a" }} />
        <Tooltip {...TOOLTIP_STYLE} />
        <Bar dataKey={valueCol} radius={[6, 6, 0, 0]}>
          {data.map((_, i) => <Cell key={i} fill={COLORS[i % COLORS.length]} />)}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );

  const renderLineChart = () => (
    <ResponsiveContainer width="100%" height={300}>
      <LineChart data={data} margin={{ top: 4, right: 16, left: 0, bottom: 4 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#2e2f3e" />
        <XAxis dataKey={labelCol} stroke="#71717a" fontSize={12} tick={{ fill: "#71717a" }} />
        <YAxis stroke="#71717a" fontSize={12} tick={{ fill: "#71717a" }} />
        <Tooltip {...TOOLTIP_STYLE} />
        <Line type="monotone" dataKey={valueCol} stroke="#6366f1" strokeWidth={2}
          dot={{ fill: "#6366f1", r: 3 }} activeDot={{ r: 5 }} />
      </LineChart>
    </ResponsiveContainer>
  );

  const renderAreaChart = () => (
    <ResponsiveContainer width="100%" height={300}>
      <AreaChart data={data} margin={{ top: 4, right: 16, left: 0, bottom: 4 }}>
        <defs>
          <linearGradient id="areaGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%"  stopColor="#6366f1" stopOpacity={0.35} />
            <stop offset="95%" stopColor="#6366f1" stopOpacity={0.02} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="#2e2f3e" />
        <XAxis dataKey={labelCol} stroke="#71717a" fontSize={12} tick={{ fill: "#71717a" }} />
        <YAxis stroke="#71717a" fontSize={12} tick={{ fill: "#71717a" }} />
        <Tooltip {...TOOLTIP_STYLE} />
        <Area type="monotone" dataKey={valueCol} stroke="#6366f1" strokeWidth={2}
          fill="url(#areaGrad)" />
      </AreaChart>
    </ResponsiveContainer>
  );

  const renderPieChart = () => (
    <ResponsiveContainer width="100%" height={320}>
      <PieChart>
        <Pie data={data} dataKey={valueCol} nameKey={labelCol}
          cx="50%" cy="50%" outerRadius={110}
          label={({ name, percent }) => `${name} (${(percent * 100).toFixed(0)}%)`}
          labelLine={{ stroke: "#71717a" }}>
          {data.map((_, i) => <Cell key={i} fill={COLORS[i % COLORS.length]} />)}
        </Pie>
        <Tooltip {...TOOLTIP_STYLE} />
        <Legend wrapperStyle={{ fontSize: "12px", color: "#71717a" }} />
      </PieChart>
    </ResponsiveContainer>
  );

  const renderScatterChart = () => {
    const [xCol, yCol] = numericCols.length >= 2
      ? [numericCols[0], numericCols[1]]
      : [columns[0], columns[1]];
    const scatterData = data.map((d) => ({ x: d[xCol], y: d[yCol] }));
    return (
      <ResponsiveContainer width="100%" height={300}>
        <ScatterChart margin={{ top: 4, right: 16, left: 0, bottom: 4 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#2e2f3e" />
          <XAxis dataKey="x" name={xCol} stroke="#71717a" fontSize={12}
            tick={{ fill: "#71717a" }} label={{ value: xCol, position: "insideBottom", offset: -2, fill: "#71717a", fontSize: 11 }} />
          <YAxis dataKey="y" name={yCol} stroke="#71717a" fontSize={12}
            tick={{ fill: "#71717a" }} label={{ value: yCol, angle: -90, position: "insideLeft", fill: "#71717a", fontSize: 11 }} />
          <Tooltip cursor={{ strokeDasharray: "3 3" }} {...TOOLTIP_STYLE} />
          <Scatter data={scatterData} fill="#6366f1" opacity={0.75} />
        </ScatterChart>
      </ResponsiveContainer>
    );
  };

  const renderHistogram = () => {
    const col      = numericCols[0] || columns[0];
    const histData = buildHistogramData(data, col);
    return (
      <ResponsiveContainer width="100%" height={300}>
        <BarChart data={histData} margin={{ top: 4, right: 16, left: 0, bottom: 4 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#2e2f3e" />
          <XAxis dataKey="bin" stroke="#71717a" fontSize={10} tick={{ fill: "#71717a" }}
            interval="preserveStartEnd" />
          <YAxis stroke="#71717a" fontSize={12} tick={{ fill: "#71717a" }}
            label={{ value: "Count", angle: -90, position: "insideLeft", fill: "#71717a", fontSize: 11 }} />
          <Tooltip {...TOOLTIP_STYLE} formatter={(v) => [v, "Count"]} />
          <Bar dataKey="count" radius={[4, 4, 0, 0]}>
            {histData.map((_, i) => <Cell key={i} fill={COLORS[i % COLORS.length]} />)}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    );
  };

  const renderMultiSeries = () => {
    const isTimeLike = (col) =>
      /date|time|year|month|day|week|quarter/i.test(col);
    const useLines = numericCols.length <= 4 &&
      (isTimeLike(labelCol) || data.length > 10);

    return (
      <ResponsiveContainer width="100%" height={300}>
        {useLines ? (
          <LineChart data={data} margin={{ top: 4, right: 16, left: 0, bottom: 4 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#2e2f3e" />
            <XAxis dataKey={labelCol} stroke="#71717a" fontSize={12} tick={{ fill: "#71717a" }} />
            <YAxis stroke="#71717a" fontSize={12} tick={{ fill: "#71717a" }} />
            <Tooltip {...TOOLTIP_STYLE} />
            <Legend wrapperStyle={{ fontSize: "12px", color: "#71717a" }} />
            {numericCols.map((col, i) => (
              <Line key={col} type="monotone" dataKey={col}
                stroke={COLORS[i % COLORS.length]} strokeWidth={2}
                dot={{ r: 2 }} activeDot={{ r: 4 }} />
            ))}
          </LineChart>
        ) : (
          <BarChart data={data} margin={{ top: 4, right: 16, left: 0, bottom: 4 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#2e2f3e" />
            <XAxis dataKey={labelCol} stroke="#71717a" fontSize={12} tick={{ fill: "#71717a" }} />
            <YAxis stroke="#71717a" fontSize={12} tick={{ fill: "#71717a" }} />
            <Tooltip {...TOOLTIP_STYLE} />
            <Legend wrapperStyle={{ fontSize: "12px", color: "#71717a" }} />
            {numericCols.map((col, i) => (
              <Bar key={col} dataKey={col} fill={COLORS[i % COLORS.length]}
                radius={[4, 4, 0, 0]} />
            ))}
          </BarChart>
        )}
      </ResponsiveContainer>
    );
  };

  const renderTable = () => {
    const sorted    = getSortedData();
    const pageData  = sorted.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);
    return (
      <>
        <div className="result-table-wrapper">
          <table className="result-table">
            <thead>
              <tr>
                {columns.map((col) => (
                  <th key={col} onClick={() => handleSort(col)} className="sortable-th">
                    {col}
                    {sortCol === col ? (sortAsc ? " ↑" : " ↓") : ""}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {pageData.map((row, i) => (
                <tr key={i}>
                  {columns.map((col) => (
                    <td key={col}>
                      {typeof row[col] === "number"
                        ? row[col].toLocaleString()
                        : row[col] ?? "—"}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {totalPages > 1 && (
          <div className="table-pagination">
            <button
              className="page-btn"
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              disabled={page === 0}
            >
              ← Prev
            </button>
            <span className="page-info">
              Page {page + 1} of {totalPages} &nbsp;·&nbsp; {data.length} rows
            </span>
            <button
              className="page-btn"
              onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
              disabled={page === totalPages - 1}
            >
              Next →
            </button>
          </div>
        )}
      </>
    );
  };

  // ── Main chart renderer ───────────────────────────────────────────────────
  const renderChart = () => {
    switch (viewType) {
      case "single_value": return renderSingleValue();
      case "bar":          return renderBarChart();
      case "line":         return renderLineChart();
      case "area":         return renderAreaChart();
      case "pie":          return renderPieChart();
      case "scatter":      return renderScatterChart();
      case "histogram":    return renderHistogram();
      case "multi_series": return renderMultiSeries();
      case "table":        return renderTable();
      default:             return renderTable();
    }
  };

  const isChartType = viewType !== "table" && viewType !== "single_value";

  return (
    <div className="result-wrapper">
      {/* ── Toolbar: type toggle + download ─────────────────────────────── */}
      {availableTypes.length > 1 && (
        <div className="result-toolbar">
          <div className="type-toggle">
            {availableTypes.map(({ key, icon: Icon, label }) => (
              <button
                key={key}
                className={`toggle-btn ${viewType === key ? "active" : ""}`}
                onClick={() => { setViewType(key); setPage(0); }}
                title={label}
              >
                <Icon size={13} />
                <span>{label}</span>
              </button>
            ))}
          </div>
          {isChartType && (
            <button
              className="chart-download-btn"
              title="Download chart as PNG"
              onClick={() => downloadChartAsPng(chartRef, `chart_${Date.now()}.png`)}
            >
              <FiDownload size={13} />
            </button>
          )}
        </div>
      )}

      {/* ── Chart / table area ────────────────────────────────────────────── */}
      {viewType === "single_value" ? (
        renderSingleValue()
      ) : viewType === "table" ? (
        renderTable()
      ) : (
        <div className="result-chart" ref={chartRef}>
          {renderChart()}
        </div>
      )}
    </div>
  );
}

export default ResultView;
