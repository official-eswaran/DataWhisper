import React, { useEffect, useState } from "react";
import { getAuditLogs } from "../../services/api";
import { FiClock, FiSearch } from "react-icons/fi";
import "./AuditLogs.css";

function AuditLogs() {
  const [logs, setLogs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("");

  useEffect(() => {
    fetchLogs();
  }, []);

  const fetchLogs = async () => {
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
      log.question.toLowerCase().includes(filter.toLowerCase()) ||
      log.sql.toLowerCase().includes(filter.toLowerCase())
  );

  return (
    <div className="audit-page">
      <div className="audit-header">
        <h2>Audit Trail</h2>
        <p>Complete log of all queries — who asked what and when</p>
      </div>

      <div className="audit-search">
        <FiSearch className="search-icon" />
        <input
          type="text"
          placeholder="Search queries..."
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        />
      </div>

      {loading ? (
        <div className="audit-loading">Loading audit logs...</div>
      ) : filtered.length === 0 ? (
        <div className="audit-empty">No audit logs found</div>
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
                    <code className="audit-sql">{log.sql}</code>
                  </td>
                  <td className="audit-summary">{log.summary}</td>
                  <td>
                    <span className={`status-badge ${log.status}`}>
                      {log.status}
                    </span>
                  </td>
                  <td className="audit-time">
                    <FiClock size={12} />
                    {new Date(log.timestamp).toLocaleString()}
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
