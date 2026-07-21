import React, { useState } from "react";
import { Routes, Route, Navigate } from "react-router-dom";
import Login from "./components/Auth/Login";
import Dashboard from "./components/Dashboard/Dashboard";
import ErrorBoundary from "./components/ErrorBoundary";
import { tokens, logout as apiLogout } from "./services/api";
import "./App.css";

function App() {
  const [auth, setAuth] = useState(() => {
    const token = tokens.access;
    const role = localStorage.getItem("role");
    return token ? { token, role } : null;
  });

  const handleLogin = (data) => {
    // data: { access_token, refresh_token, role, expires_in }
    tokens.set(data);
    setAuth({ token: data.access_token, role: data.role });
  };

  const handleLogout = async () => {
    await apiLogout(); // revoke refresh tokens server-side (best effort)
    tokens.clear();
    setAuth(null);
  };

  return (
    <ErrorBoundary>
      <div className="app">
        <Routes>
          <Route
            path="/login"
            element={auth ? <Navigate to="/" /> : <Login onLogin={handleLogin} />}
          />
          <Route
            path="/*"
            element={
              auth ? (
                <Dashboard auth={auth} onLogout={handleLogout} />
              ) : (
                <Navigate to="/login" />
              )
            }
          />
        </Routes>
      </div>
    </ErrorBoundary>
  );
}

export default App;
