import React, { useEffect, useState } from "react";
import { getAuditLogs } from "../../services/api";
import { FiClock, FiSearch, FiRefreshCw, FiDatabase, FiMessageSquare } from "react-icons/fi";
import "./AuditLogs.css";

function AuditLogs() {
  const [logs, setLogs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("");

  useEffect(() => {
    fetchLogs();
  }, []);

  const fetchLogs = async () => {
    setLoading(true);
    try {
      const res = await getAuditLogs(100);
      setLogs(res.data);
    } catch {
      setLogs([]);
    } finally {
      setLoading(false);
    }
  };

  const filtered = logs.filter(
    (log) =>
      (log.question || "").toLowerCase().includes(filter.toLowerCase()) ||
      (log.sql || "").toLowerCase().includes(filter.toLowerCase())
  );

  const totalQueries = logs.length;
  const dataQueries = logs.filter((l) => l.sql && l.sql.length > 0).length;
  const chatQueries = totalQueries - dataQueries;

  return (
    <div className="audit-page">
      <div className="audit-header">
        <div>
          <h2>Audit Trail</h2>
          <p>Complete log of all queries — who asked what and when</p>
        </div>
        <button className="refresh-btn" onClick={fetchLogs} disabled={loading}>
          <FiRefreshCw size={14} className={loading ? "spinning" : ""} />
          Refresh
        </button>
      </div>

      <div className="audit-stats">
        <div className="stat-card">
          <FiMessageSquare size={18} />
          <div>
            <span className="stat-value">{totalQueries}</span>
            <span className="stat-label">Total Queries</span>
          </div>
        </div>
        <div className="stat-card">
          <FiDatabase size={18} />
          <div>
            <span className="stat-value">{dataQueries}</span>
            <span className="stat-label">Data Queries</span>
          </div>
        </div>
        <div className="stat-card">
          <FiMessageSquare size={18} />
          <div>
            <span className="stat-value">{chatQueries}</span>
            <span className="stat-label">Chat / Off-topic</span>
          </div>
        </div>
      </div>

      <div className="audit-search">
        <FiSearch className="search-icon" />
        <input
          type="text"
          placeholder="Search queries or SQL..."
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        />
        {filter && (
          <span className="search-count">
            {filtered.length} result{filtered.length !== 1 ? "s" : ""}
          </span>
        )}
      </div>

      {loading ? (
        <div className="audit-loading">Loading audit logs...</div>
      ) : filtered.length === 0 ? (
        <div className="audit-empty">
          {filter ? "No matching queries found" : "No audit logs yet. Start asking questions!"}
        </div>
      ) : (
        <div className="audit-table-wrapper">
          <table className="audit-table">
            <thead>
              <tr>
                <th>#</th>
                <th>Question</th>
                <th>Generated SQL</th>
                <th>Result</th>
                <th>Status</th>
                <th>Time</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((log, i) => (
                <tr key={log.id}>
                  <td>{i + 1}</td>
                  <td className="audit-question">{log.question}</td>
                  <td>
                    {log.sql ? (
                      <code className="audit-sql">{log.sql}</code>
                    ) : (
                      <span className="audit-no-sql">— (no SQL)</span>
                    )}
                  </td>
                  <td className="audit-summary">{log.summary || "—"}</td>
                  <td>
                    <span className={`status-badge ${log.status}`}>
                      {log.status}
                    </span>
                  </td>
                  <td className="audit-time">
                    <FiClock size={12} />
                    {log.timestamp
                      ? new Date(log.timestamp).toLocaleString()
                      : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export default AuditLogs;
