import React, { useState } from "react";
import { login } from "../../services/api";
import toast from "react-hot-toast";
import { FiDatabase, FiLock, FiUser } from "react-icons/fi";
import "./Login.css";

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
    } catch {
      toast.error("Invalid credentials");
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

        <form onSubmit={handleSubmit} className="login-form">
          <div className="input-group">
            <FiUser className="input-icon" />
            <input
              type="text"
              placeholder="Username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
            />
          </div>

          <div className="input-group">
            <FiLock className="input-icon" />
            <input
              type="password"
              placeholder="Password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </div>

          <button type="submit" className="login-btn" disabled={loading}>
            {loading ? "Signing in..." : "Sign In"}
          </button>
        </form>

        <div className="login-footer">
          <FiLock size={12} />
          <span>100% offline — your data never leaves this machine</span>
        </div>
      </div>
    </div>
  );
}

export default Login;
