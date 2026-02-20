import React from "react";
import {
  FiDatabase,
  FiUpload,
  FiMessageSquare,
  FiFileText,
  FiLogOut,
  FiShield,
} from "react-icons/fi";
import "./Sidebar.css";

function Sidebar({ activeTab, onTabChange, onLogout, session, role }) {
  const menuItems = [
    { id: "upload", label: "Upload Data", icon: <FiUpload /> },
    { id: "chat", label: "Ask Questions", icon: <FiMessageSquare /> },
    { id: "audit", label: "Audit Logs", icon: <FiFileText /> },
  ];

  return (
    <aside className="sidebar">
      <div className="sidebar-header">
        <FiDatabase size={24} className="sidebar-logo-icon" />
        <div>
          <h2>DataWhisper</h2>
          <span className="sidebar-badge">
            <FiShield size={10} /> Private AI
          </span>
        </div>
      </div>

      <nav className="sidebar-nav">
        {menuItems.map((item) => (
          <button
            key={item.id}
            className={`sidebar-item ${activeTab === item.id ? "active" : ""}`}
            onClick={() => onTabChange(item.id)}
          >
            {item.icon}
            <span>{item.label}</span>
          </button>
        ))}
      </nav>

      {session && (
        <div className="sidebar-session">
          <h4>Active Session</h4>
          <p className="session-table">
            Table: <strong>{session.table_name}</strong>
          </p>
          <p className="session-rows">{session.rows} rows loaded</p>
          <p className="session-cols">
            {session.columns?.length ?? 0} columns
          </p>
        </div>
      )}

      <div className="sidebar-footer">
        <div className="sidebar-role">
          Role: <strong>{role}</strong>
        </div>
        <button className="sidebar-logout" onClick={onLogout}>
          <FiLogOut />
          <span>Logout</span>
        </button>
      </div>
    </aside>
  );
}

export default Sidebar;
