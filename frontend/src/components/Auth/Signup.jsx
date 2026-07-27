import React, { useState } from "react";
import { Link } from "react-router-dom";
import toast from "react-hot-toast";
import { FiBriefcase, FiDatabase, FiLock, FiMail, FiUser } from "react-icons/fi";
import { register } from "../../services/api";
import "./Login.css";

function Signup({ onLogin }) {
  const [form, setForm] = useState({ org: "", username: "", email: "", password: "" });
  const [loading, setLoading] = useState(false);

  const update = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.value }));

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    try {
      const res = await register(form.org, form.username, form.email, form.password);
      onLogin(res.data); // register returns tokens — log straight in
      toast.success("Organization created — welcome to DataWhisper!");
    } catch (err) {
      const status = err?.response?.status;
      if (status === 409) toast.error("That username or email is already taken");
      else if (status === 422) {
        // The API says exactly which field failed and why (e.g. "Password must
        // contain a digit"). Showing that beats a generic "check your details",
        // which left the user guessing — most often about the password rule.
        const first = err?.response?.data?.errors?.[0]?.msg;
        toast.error(first || "Please check your details and try again");
      } else if (status === 403) {
        toast.error("Public signup is closed on this deployment");
      } else if (status === 429) {
        toast.error("Too many signups from this network. Please try again later.");
      } else toast.error("Could not create your account");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="login-page">
      <div className="login-card">
        <div className="login-logo">
          <FiDatabase size={40} aria-hidden="true" />
          <h1>Create your workspace</h1>
          <p>Start querying your data in plain English</p>
        </div>

        <form onSubmit={handleSubmit} className="login-form" aria-label="Create account">
          <div className="input-group">
            <FiBriefcase className="input-icon" aria-hidden="true" />
            <label htmlFor="org" className="sr-only">Organization name</label>
            <input
              id="org"
              name="organization"
              type="text"
              placeholder="Organization name"
              autoComplete="organization"
              value={form.org}
              onChange={update("org")}
              required
            />
          </div>

          <div className="input-group">
            <FiUser className="input-icon" aria-hidden="true" />
            <label htmlFor="username" className="sr-only">Username</label>
            <input
              id="username"
              name="username"
              type="text"
              placeholder="Username"
              autoComplete="username"
              value={form.username}
              onChange={update("username")}
              required
            />
          </div>

          <div className="input-group">
            <FiMail className="input-icon" aria-hidden="true" />
            <label htmlFor="email" className="sr-only">Email</label>
            <input
              id="email"
              name="email"
              type="email"
              placeholder="Email"
              autoComplete="email"
              value={form.email}
              onChange={update("email")}
              required
            />
          </div>

          <div className="input-group">
            <FiLock className="input-icon" aria-hidden="true" />
            <label htmlFor="password" className="sr-only">Password</label>
            <input
              id="password"
              name="password"
              type="password"
              placeholder="Password (10+ chars, with a letter and a number)"
              autoComplete="new-password"
              value={form.password}
              onChange={update("password")}
              minLength={10}
              required
              aria-describedby="password-help"
            />
            {/* Must mirror validate_password_strength in core/security.py — it
                said "min 8" while the backend enforced 10, so a valid-looking
                password 422'd with no indication of why. */}
            <span id="password-help" className="sr-only">
              Password must be at least 10 characters and contain a letter and a number
            </span>
          </div>

          <button type="submit" className="login-btn" disabled={loading} aria-busy={loading}>
            {loading ? "Creating…" : "Create workspace"}
          </button>
        </form>

        <div className="login-footer">
          <span>Already have an account? </span>
          <Link to="/login">Sign in</Link>
        </div>
      </div>
    </div>
  );
}

export default Signup;
