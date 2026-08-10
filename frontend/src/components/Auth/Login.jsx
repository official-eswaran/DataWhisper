import React, { useState } from "react";
import { Link } from "react-router-dom";
import { login } from "../../services/api";
import toast from "react-hot-toast";
import { FiDatabase, FiLock, FiUser } from "react-icons/fi";
import "./Login.css";

/**
 * Turn a failed login into something the user can act on (issue #77).
 *
 * The backend distinguishes four outcomes on purpose and this used to collapse
 * all of them into "Invalid credentials" — so a locked-out user retried, which
 * is the behaviour the lockout exists to stop, and an admin-disabled account
 * was indistinguishable from a typo.
 *
 * Where the API's own `detail` carries information the UI cannot reconstruct,
 * it is preferred over a fixed string:
 *
 *   401  "…(3 attempt(s) remaining)" / "…Account locked for 15 minutes."
 *        The count and the lockout notice exist only in the response.
 *   429  Two different causes share this status — the per-account lockout from
 *        the login route, and slowapi's per-IP limit ("Too many requests.
 *        Please slow down."). Only `detail` tells them apart.
 *
 * 403 is the exception: its detail ("Account is disabled") is accurate but
 * tells the user nothing to do next, so the UI owns that wording.
 *
 * Anything unrecognised falls back to a generic message rather than echoing an
 * arbitrary body — a 500's detail is not written for end users.
 */
function loginErrorMessage(err) {
  const status = err?.response?.status;
  const detail = err?.response?.data?.detail;
  const fromApi = typeof detail === "string" && detail.trim() ? detail.trim() : null;

  if (status === 401) return fromApi || "Invalid username or password";
  if (status === 403) return "This account has been disabled. Contact your administrator.";
  if (status === 429) return fromApi || "Too many attempts. Please wait and try again.";
  if (!err?.response) return "Could not reach the server. Check your connection.";
  return "Could not sign you in. Please try again.";
}

function Login({ onLogin }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    try {
      const res = await login(username, password);
      onLogin(res.data);
      toast.success("Welcome back!");
    } catch (err) {
      toast.error(loginErrorMessage(err));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="login-page">
      <div className="login-card">
        <div className="login-logo">
          <FiDatabase size={40} />
          <h1>DataWhisper</h1>
          <p>Private AI Data Assistant</p>
        </div>

        <form onSubmit={handleSubmit} className="login-form" aria-label="Sign in">
          <div className="input-group">
            <FiUser className="input-icon" aria-hidden="true" />
            <label htmlFor="username" className="sr-only">Username</label>
            <input
              id="username"
              name="username"
              type="text"
              placeholder="Username"
              autoComplete="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
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
              placeholder="Password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </div>

          <button type="submit" className="login-btn" disabled={loading} aria-busy={loading}>
            {loading ? "Signing in..." : "Sign In"}
          </button>
        </form>

        <div className="login-footer">
          <span>New here? </span>
          <Link to="/signup">Create a workspace</Link>
        </div>

        <div className="login-footer">
          <FiLock size={12} aria-hidden="true" />
          <span>100% offline — your data never leaves this machine</span>
        </div>
      </div>
    </div>
  );
}

export default Login;
