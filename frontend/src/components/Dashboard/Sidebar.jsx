import React from "react";
import {
  FiDatabase,
  FiUpload,
  FiMessageSquare,
  FiFileText,
  FiLogOut,
  FiSettings,
  FiShield,
  FiUsers,
} from "react-icons/fi";
import "./Sidebar.css";

function Sidebar({ activeTab, onTabChange, onLogout, session, role, isAdmin }) {
  const menuItems = [
    { id: "upload", label: "Upload Data", icon: <FiUpload /> },
    { id: "chat", label: "Ask Questions", icon: <FiMessageSquare /> },
    { id: "audit", label: "Audit Logs", icon: <FiFileText /> },
    ...(isAdmin ? [{ id: "admin", label: "Admin", icon: <FiUsers /> }] : []),
    { id: "account", label: "Account", icon: <FiSettings /> },
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

      <nav className="sidebar-nav" aria-label="Main">
        {menuItems.map((item) => (
          <button
            key={item.id}
            className={`sidebar-item ${activeTab === item.id ? "active" : ""}`}
            onClick={() => onTabChange(item.id)}
            aria-current={activeTab === item.id ? "page" : undefined}
          >
            <span aria-hidden="true">{item.icon}</span>
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
