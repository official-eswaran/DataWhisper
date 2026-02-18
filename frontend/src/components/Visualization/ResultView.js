import React from "react";
import {
  BarChart,
  Bar,
  PieChart,
  Pie,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Cell,
  Legend,
} from "recharts";
import "./ResultView.css";

const COLORS = [
  "#6366f1",
  "#22c55e",
  "#f59e0b",
  "#ef4444",
  "#8b5cf6",
  "#06b6d4",
  "#ec4899",
  "#14b8a6",
];

function ResultView({ type, data, columns }) {
  if (!data || data.length === 0) return null;

  // Single value — show as big number
  if (type === "single_value") {
    const value = data[0][columns[0]];
    return (
      <div className="result-single">
        <span className="single-label">{columns[0]}</span>
        <span className="single-value">
          {typeof value === "number" ? value.toLocaleString() : value}
        </span>
      </div>
    );
  }

  // Chart — auto-detect best chart type
  if (type === "chart" && columns.length >= 2) {
    const labelKey = columns[0];
    const valueKey = columns[1];
    const isNumericLabel = data.every((d) => typeof d[labelKey] === "number");
    const itemCount = data.length;

    // Pie chart for small categorical data
    if (itemCount <= 8 && !isNumericLabel) {
      return (
        <div className="result-chart">
          <ResponsiveContainer width="100%" height={300}>
            <PieChart>
              <Pie
                data={data}
                dataKey={valueKey}
                nameKey={labelKey}
                cx="50%"
                cy="50%"
                outerRadius={100}
                label={({ name, percent }) =>
                  `${name} (${(percent * 100).toFixed(0)}%)`
                }
              >
                {data.map((_, i) => (
                  <Cell key={i} fill={COLORS[i % COLORS.length]} />
                ))}
              </Pie>
              <Tooltip
                contentStyle={{
                  background: "#1a1b23",
                  border: "1px solid #2e2f3e",
                  borderRadius: "8px",
                  color: "#e4e4e7",
                }}
              />
              <Legend />
            </PieChart>
          </ResponsiveContainer>
        </div>
      );
    }

    // Line chart for time-series or numeric data
    if (isNumericLabel || itemCount > 15) {
      return (
        <div className="result-chart">
          <ResponsiveContainer width="100%" height={300}>
            <LineChart data={data}>
              <CartesianGrid strokeDasharray="3 3" stroke="#2e2f3e" />
              <XAxis dataKey={labelKey} stroke="#71717a" fontSize={12} />
              <YAxis stroke="#71717a" fontSize={12} />
              <Tooltip
                contentStyle={{
                  background: "#1a1b23",
                  border: "1px solid #2e2f3e",
                  borderRadius: "8px",
                  color: "#e4e4e7",
                }}
              />
              <Line
                type="monotone"
                dataKey={valueKey}
                stroke="#6366f1"
                strokeWidth={2}
                dot={{ fill: "#6366f1", r: 4 }}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      );
    }

    // Default: bar chart
    return (
      <div className="result-chart">
        <ResponsiveContainer width="100%" height={300}>
          <BarChart data={data}>
            <CartesianGrid strokeDasharray="3 3" stroke="#2e2f3e" />
            <XAxis dataKey={labelKey} stroke="#71717a" fontSize={12} />
            <YAxis stroke="#71717a" fontSize={12} />
            <Tooltip
              contentStyle={{
                background: "#1a1b23",
                border: "1px solid #2e2f3e",
                borderRadius: "8px",
                color: "#e4e4e7",
              }}
            />
            <Bar dataKey={valueKey} radius={[6, 6, 0, 0]}>
              {data.map((_, i) => (
                <Cell key={i} fill={COLORS[i % COLORS.length]} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    );
  }

  // Table view — default for multi-column results
  return (
    <div className="result-table-wrapper">
      <table className="result-table">
        <thead>
          <tr>
            {columns.map((col) => (
              <th key={col}>{col}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.slice(0, 100).map((row, i) => (
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
      {data.length > 100 && (
        <p className="table-truncated">
          Showing 100 of {data.length} rows
        </p>
      )}
    </div>
  );
}

export default ResultView;
