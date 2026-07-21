import React, { useState } from "react";
import toast from "react-hot-toast";
import { FiDownload, FiSettings, FiTrash2 } from "react-icons/fi";
import { deleteMyAccount, deleteMyOrganization, exportMyData } from "../../services/api";
import "./AdminConsole.css";

function AccountSettings({ role, onLogout }) {
  const [busy, setBusy] = useState("");

  const handleExport = async () => {
    setBusy("export");
    try {
      const res = await exportMyData();
      const blob = new Blob([JSON.stringify(res.data, null, 2)], {
        type: "application/json",
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "datawhisper-my-data.json";
      a.click();
      URL.revokeObjectURL(url);
      toast.success("Your data export has downloaded");
    } catch {
      toast.error("Could not export your data");
    } finally {
      setBusy("");
    }
  };

  const handleDeleteAccount = async () => {
    if (!window.confirm("Delete your account permanently? This cannot be undone.")) return;
    setBusy("account");
    try {
      await deleteMyAccount();
      toast.success("Account deleted");
      onLogout();
    } catch (err) {
      const msg = err?.response?.data?.detail || "Could not delete your account";
      toast.error(msg);
    } finally {
      setBusy("");
    }
  };

  const handleDeleteOrg = async () => {
    if (
      !window.confirm(
        "Delete the entire organization — all users, sessions, and data? This cannot be undone."
      )
    )
      return;
    setBusy("org");
    try {
      await deleteMyOrganization();
      toast.success("Organization deleted");
      onLogout();
    } catch (err) {
      const msg = err?.response?.data?.detail || "Could not delete the organization";
      toast.error(msg);
    } finally {
      setBusy("");
    }
  };

  return (
    <div className="admin-console">
      <header className="admin-header">
        <FiSettings aria-hidden="true" />
        <h2>Account settings</h2>
      </header>

      <section className="admin-section" aria-labelledby="data-heading">
        <h3 id="data-heading">Your data</h3>
        <p className="admin-muted">
          Download everything we hold about your account (GDPR data portability).
        </p>
        <button className="btn-primary" onClick={handleExport} disabled={busy === "export"}>
          <FiDownload aria-hidden="true" /> {busy === "export" ? "Preparing…" : "Export my data"}
        </button>
      </section>

      <section className="admin-section danger-zone" aria-labelledby="danger-heading">
        <h3 id="danger-heading">Danger zone</h3>
        <div className="danger-row">
          <div>
            <strong>Delete my account</strong>
            <p className="admin-muted">Removes your user and your personal sessions.</p>
          </div>
          <button className="btn-danger" onClick={handleDeleteAccount} disabled={busy === "account"}>
            <FiTrash2 aria-hidden="true" /> Delete account
          </button>
        </div>

        {role === "owner" && (
          <div className="danger-row">
            <div>
              <strong>Delete organization</strong>
              <p className="admin-muted">
                Permanently deletes the org and every user, session, and dataset in it.
              </p>
            </div>
            <button className="btn-danger" onClick={handleDeleteOrg} disabled={busy === "org"}>
              <FiTrash2 aria-hidden="true" /> Delete organization
            </button>
          </div>
        )}
      </section>
    </div>
  );
}

export default AccountSettings;
