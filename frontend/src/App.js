import React, { useState } from "react";
import { Routes, Route, Navigate } from "react-router-dom";
import Login from "./components/Auth/Login";
import Dashboard from "./components/Dashboard/Dashboard";
import "./App.css";

function App() {
  const [auth, setAuth] = useState(() => {
    const token = localStorage.getItem("token");
    const role = localStorage.getItem("role");
    return token ? { token, role } : null;
  });

  const handleLogin = (data) => {
    localStorage.setItem("token", data.access_token);
    localStorage.setItem("role", data.role);
    setAuth({ token: data.access_token, role: data.role });
  };

  const handleLogout = () => {
    localStorage.removeItem("token");
    localStorage.removeItem("role");
    setAuth(null);
  };

  return (
    <div className="app">
      <Routes>
        <Route
          path="/login"
          element={
            auth ? <Navigate to="/" /> : <Login onLogin={handleLogin} />
          }
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
  );
}

export default App;
