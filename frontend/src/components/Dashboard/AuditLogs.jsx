import React, { useEffect, useState } from "react";
import toast from "react-hot-toast";
import { getAuditLogs } from "../../services/api";
import { FiAlertTriangle, FiClock, FiSearch, FiRefreshCw, FiDatabase, FiMessageSquare } from "react-icons/fi";
import "./AuditLogs.css";

function AuditLogs() {
  const [logs, setLogs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const [filter, setFilter] = useState("");

  useEffect(() => {
    fetchLogs();
  }, []);

  const fetchLogs = async () => {
    setLoading(true);
    try {
      const res = await getAuditLogs(100);
      // API returns a paginated envelope: { items, total, limit, offset }.
      setLogs(Array.isArray(res.data) ? res.data : res.data.items || []);
      // Cleared on success so a retry that works removes the banner (#82).
      setFailed(false);
    } catch {
      // Issue #82. This used to `setLogs([])` and surface nothing, so a 500, a
      // dropped connection and a genuinely empty trail all rendered "No audit
      // logs yet. Start asking questions!".
      //
      // For an audit trail that is not a cosmetic problem: it is a wrong answer
      // to the question the page exists to answer. Someone checking the record
      // during an incident was told, in a friendly tone, that nothing happened.
      //
      // So nothing on the page may assert emptiness while this is set — see the
      // stat tiles below, which are hidden rather than left reading zero.
      setLogs([]);
      setFailed(true);
      toast.error("Could not load the audit trail");
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

      {/* Hidden on failure: "Total Queries 0" asserts an empty trail just as
          plainly as the empty-state copy does (#82). */}
      {!failed && (
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
      )}

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
      ) : failed ? (
        // role="alert" because this contradicts what the page would otherwise
        // imply, and a screen-reader user must not be left with the empty
        // reading. Says explicitly that no conclusion can be drawn: the whole
        // point of #82 is that "we don't know" and "nothing happened" are
        // different answers, and only one of them is true here.
        <div className="audit-error" role="alert">
          <p>
            <FiAlertTriangle aria-hidden="true" /> <strong>Could not load the audit trail.</strong>
          </p>
          <p>
            The request failed — this does not mean the trail is empty, and
            nothing should be concluded from it about what was or wasn’t
            queried. Use Refresh to try again.
          </p>
        </div>
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
