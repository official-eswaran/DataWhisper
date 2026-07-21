import React, { useEffect, useState } from "react";
import toast from "react-hot-toast";
import Sidebar from "./Sidebar";
import FileUpload from "../Upload/FileUpload";
import ChatWindow from "../Chat/ChatWindow";
import AuditLogs from "./AuditLogs";
import AdminConsole from "./AdminConsole";
import AccountSettings from "./AccountSettings";
import "./Dashboard.css";

const ADMIN_ROLES = ["owner", "admin"];

// Stripe sends the user back to STRIPE_SUCCESS_URL / STRIPE_CANCEL_URL, which
// carry a ?status= marker. Land them on the billing view rather than the
// default chat tab, so the round trip visibly ends where it started.
function initialTab(isAdmin) {
  const status = new URLSearchParams(window.location.search).get("status");
  return status && isAdmin ? "admin" : "chat";
}

function Dashboard({ auth, onLogout }) {
  const isAdmin = ADMIN_ROLES.includes(auth.role);
  const [activeTab, setActiveTab] = useState(() => initialTab(isAdmin));
  const [session, setSession] = useState(null);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const status = params.get("status");
    if (!status) return;

    if (status === "success") {
      // The plan changes on the webhook, not this redirect, so it may lag the
      // user's return by a moment. Promising "you're upgraded" here would be a
      // lie we can't back up yet.
      toast.success("Payment received — your new plan will appear shortly.");
    } else {
      toast("Checkout cancelled — your plan is unchanged.");
    }

    // Drop the marker so a refresh doesn't replay the toast.
    window.history.replaceState({}, "", window.location.pathname);
  }, []);

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
        isAdmin={isAdmin}
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

        {activeTab === "admin" && isAdmin && <AdminConsole />}

        {activeTab === "account" && (
          <AccountSettings role={auth.role} onLogout={onLogout} />
        )}
      </main>
    </div>
  );
}

export default Dashboard;
