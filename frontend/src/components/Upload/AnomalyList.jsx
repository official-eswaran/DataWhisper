import React from "react";
import { FiAlertTriangle, FiInfo } from "react-icons/fi";
import "./AnomalyList.css";

// What the ingestion pass noticed about the data: missing values, outliers,
// duplicate rows, sudden changes (backend services/anomaly_detector.py).
//
// This lives in its own component because it has to render in two places. It
// used to be inline in FileUpload, which meant it never rendered at all: a
// successful upload calls onUploadSuccess, Dashboard switches to the chat tab
// in the same React commit, and the upload view unmounts before the panel is
// ever painted. Coming back to the Upload tab remounts FileUpload with no
// result, so there was no path to these findings from anywhere in the app.
//
// Now the chat view shows them alongside the "Data loaded!" greeting, which is
// where the upload actually leaves you.

export function severityColor(severity) {
  switch (severity) {
    case "high":
      return "var(--danger)";
    case "medium":
      return "var(--warning)";
    default:
      return "var(--text-muted)";
  }
}

function AnomalyList({ anomalies }) {
  if (!anomalies || anomalies.length === 0) return null;

  return (
    <div className="result-anomalies">
      <h4>
        <FiAlertTriangle size={14} aria-hidden="true" /> Anomalies Detected
      </h4>
      {anomalies.map((a, i) => (
        <div
          key={`${a.type}-${i}`}
          className="anomaly-item"
          style={{ borderLeftColor: severityColor(a.severity) }}
        >
          <FiInfo size={14} aria-hidden="true" style={{ color: severityColor(a.severity) }} />
          <div>
            <span className="anomaly-type">{a.type}</span>
            <p>{a.message}</p>
          </div>
          <span className="anomaly-severity" style={{ color: severityColor(a.severity) }}>
            {a.severity}
          </span>
        </div>
      ))}
    </div>
  );
}

export default AnomalyList;
