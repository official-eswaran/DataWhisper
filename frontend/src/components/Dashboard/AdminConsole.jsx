import React, { useCallback, useEffect, useState } from "react";
import toast from "react-hot-toast";
import { FiUserPlus, FiUsers } from "react-icons/fi";
import { createUser, getUsage, listUsers, setUserActive } from "../../services/api";
import "./AdminConsole.css";

function UsageCard({ usage }) {
  if (!usage) return null;
  const metrics = usage.metrics || {};
  return (
    <section className="admin-section" aria-labelledby="usage-heading">
      <h3 id="usage-heading">
        Usage <span className="admin-plan-badge">{usage.plan} plan</span>
      </h3>
      <p className="admin-muted">Billing period {usage.period}</p>
      <div className="usage-grid">
        {Object.entries(metrics).map(([name, m]) => (
          <div className="usage-tile" key={name}>
            <div className="usage-metric">{name.replace(/_/g, " ")}</div>
            <div className="usage-value">
              {m.used.toLocaleString()}
              {m.limit != null && (
                <span className="usage-limit"> / {m.limit.toLocaleString()}</span>
              )}
            </div>
            {m.limit != null && (
              <div className="usage-bar" aria-hidden="true">
                <div
                  className="usage-bar-fill"
                  style={{ width: `${Math.min(100, (m.used / m.limit) * 100)}%` }}
                />
              </div>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}

function AdminConsole() {
  const [usage, setUsage] = useState(null);
  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [form, setForm] = useState({ username: "", email: "", password: "", role: "member" });
  const [creating, setCreating] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [u, us] = await Promise.all([getUsage(), listUsers()]);
      setUsage(u.data);
      setUsers(us.data.users || []);
    } catch {
      toast.error("Could not load the admin console");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const update = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.value }));

  const handleCreate = async (e) => {
    e.preventDefault();
    setCreating(true);
    try {
      await createUser(form.username, form.email, form.password, form.role);
      toast.success(`Added ${form.username}`);
      setForm({ username: "", email: "", password: "", role: "member" });
      load();
    } catch (err) {
      const status = err?.response?.status;
      if (status === 409) toast.error("Username or email already exists");
      else if (status === 422) toast.error("Check the details and try again");
      else toast.error("Could not create the user");
    } finally {
      setCreating(false);
    }
  };

  const handleToggle = async (user) => {
    try {
      await setUserActive(user.username, !user.is_active);
      toast.success(`${user.is_active ? "Deactivated" : "Reactivated"} ${user.username}`);
      load();
    } catch {
      toast.error("Could not update the user");
    }
  };

  return (
    <div className="admin-console">
      <header className="admin-header">
        <FiUsers aria-hidden="true" />
        <h2>Admin console</h2>
      </header>

      {loading ? (
        <p className="admin-muted">Loading…</p>
      ) : (
        <>
          <UsageCard usage={usage} />

          <section className="admin-section" aria-labelledby="team-heading">
            <h3 id="team-heading">Team members</h3>
            <div className="admin-table-wrap">
              <table className="admin-table">
                <thead>
                  <tr>
                    <th scope="col">Username</th>
                    <th scope="col">Email</th>
                    <th scope="col">Role</th>
                    <th scope="col">Status</th>
                    <th scope="col">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {users.map((u) => (
                    <tr key={u.username}>
                      <td>{u.username}</td>
                      <td>{u.email}</td>
                      <td>{u.role}</td>
                      <td>
                        <span className={u.is_active ? "status-active" : "status-inactive"}>
                          {u.is_active ? "active" : "inactive"}
                        </span>
                      </td>
                      <td>
                        {u.role !== "owner" && (
                          <button className="btn-small" onClick={() => handleToggle(u)}>
                            {u.is_active ? "Deactivate" : "Reactivate"}
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section className="admin-section" aria-labelledby="invite-heading">
            <h3 id="invite-heading">
              <FiUserPlus aria-hidden="true" /> Add a team member
            </h3>
            <form className="admin-form" onSubmit={handleCreate}>
              <label htmlFor="new-username" className="sr-only">Username</label>
              <input
                id="new-username" placeholder="Username" value={form.username}
                onChange={update("username")} autoComplete="off" required
              />
              <label htmlFor="new-email" className="sr-only">Email</label>
              <input
                id="new-email" type="email" placeholder="Email" value={form.email}
                onChange={update("email")} autoComplete="off" required
              />
              <label htmlFor="new-password" className="sr-only">Temporary password</label>
              <input
                id="new-password" type="password" placeholder="Temp password" value={form.password}
                onChange={update("password")} autoComplete="new-password" required
              />
              <label htmlFor="new-role" className="sr-only">Role</label>
              <select id="new-role" value={form.role} onChange={update("role")}>
                <option value="member">Member</option>
                <option value="admin">Admin</option>
              </select>
              <button type="submit" className="btn-primary" disabled={creating} aria-busy={creating}>
                {creating ? "Adding…" : "Add member"}
              </button>
            </form>
          </section>
        </>
      )}
    </div>
  );
}

export default AdminConsole;
