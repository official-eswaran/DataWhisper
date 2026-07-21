import React, { useState } from "react";
import Sidebar from "./Sidebar";
import FileUpload from "../Upload/FileUpload";
import ChatWindow from "../Chat/ChatWindow";
import AuditLogs from "./AuditLogs";
import "./Dashboard.css";

function Dashboard({ auth, onLogout }) {
  const [activeTab, setActiveTab] = useState("chat");
  const [session, setSession] = useState(null);

  const handleUploadSuccess = (data) => {
    setSession(data);
    setActiveTab("chat");
  };

  return (
    <div className="dashboard">
      <Sidebar
        activeTab={activeTab}
        onTabChange={setActiveTab}
        onLogout={onLogout}
        session={session}
        role={auth.role}
      />

      <main className="dashboard-main">
        {activeTab === "upload" && (
          <FileUpload onUploadSuccess={handleUploadSuccess} />
        )}

        {activeTab === "chat" && (
          <>
            {session ? (
              <ChatWindow session={session} />
            ) : (
              <div className="no-session">
                <div className="no-session-content">
                  <h2>No data loaded</h2>
                  <p>Upload a file first to start asking questions</p>
                  <button
                    className="btn-primary"
                    onClick={() => setActiveTab("upload")}
                  >
                    Upload Data
                  </button>
                </div>
              </div>
            )}
          </>
        )}

        {activeTab === "audit" && <AuditLogs />}
      </main>
    </div>
  );
}

export default Dashboard;
