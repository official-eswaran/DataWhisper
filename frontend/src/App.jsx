import React, { useEffect, useState } from "react";
import { Routes, Route, Navigate } from "react-router-dom";
import Login from "./components/Auth/Login";
import Signup from "./components/Auth/Signup";
import Dashboard from "./components/Dashboard/Dashboard";
import ErrorBoundary from "./components/ErrorBoundary";
import { tokens, bootstrapSession, logout as apiLogout } from "./services/api";
import "./App.css";

function App() {
  const [auth, setAuth] = useState(null);
  // The access token now lives only in memory, so a page reload starts with no
  // session. Recover it from the httpOnly refresh cookie before rendering routes
  // — otherwise a logged-in user would flash the login screen on every refresh.
  const [booting, setBooting] = useState(true);

  useEffect(() => {
    let active = true;
    bootstrapSession()
      .then((session) => {
        if (active) setAuth(session);
      })
      .finally(() => {
        if (active) setBooting(false);
      });
    return () => {
      active = false;
    };
  }, []);

  const handleLogin = (data) => {
    // data: { access_token, role, expires_in } — refresh token is set as a cookie
    tokens.set(data);
    setAuth({ token: data.access_token, role: data.role });
  };

  const handleLogout = async () => {
    await apiLogout(); // revoke refresh tokens + clear the cookie (best effort)
    tokens.clear();
    setAuth(null);
  };

  if (booting) {
    return (
      <div className="app app-booting" role="status" aria-live="polite">
        <span className="sr-only">Loading…</span>
      </div>
    );
  }

  return (
    <ErrorBoundary>
      <div className="app">
        <Routes>
          <Route
            path="/login"
            element={auth ? <Navigate to="/" /> : <Login onLogin={handleLogin} />}
          />
          <Route
            path="/signup"
            element={auth ? <Navigate to="/" /> : <Signup onLogin={handleLogin} />}
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
