import axios from "axios";

// API base URL resolution:
//  - Same-origin "/api" by default (works behind the nginx reverse proxy in
//    production and avoids the previous hardcoded-https mixed-content bug).
//  - Override with VITE_API_URL for split-origin/dev setups, e.g.
//    VITE_API_URL=http://localhost:8000/api
const API_BASE = import.meta.env.VITE_API_URL || "/api";

// ── Token storage ─────────────────────────────────────────────────────────────
const tokens = {
  get access() {
    return localStorage.getItem("token");
  },
  get refresh() {
    return localStorage.getItem("refresh_token");
  },
  set(data) {
    localStorage.setItem("token", data.access_token);
    if (data.refresh_token) localStorage.setItem("refresh_token", data.refresh_token);
    if (data.role) localStorage.setItem("role", data.role);
  },
  clear() {
    localStorage.removeItem("token");
    localStorage.removeItem("refresh_token");
    localStorage.removeItem("role");
  },
};

function redirectToLogin() {
  tokens.clear();
  if (window.location.pathname !== "/login") {
    window.location.href = "/login";
  }
}

// ── Refresh coordination (single in-flight refresh shared by all callers) ──────
let refreshPromise = null;

async function refreshAccessToken() {
  const refresh_token = tokens.refresh;
  if (!refresh_token) return null;

  if (!refreshPromise) {
    refreshPromise = fetch(`${API_BASE}/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token }),
    })
      .then(async (res) => {
        if (!res.ok) throw new Error("refresh failed");
        const data = await res.json();
        tokens.set(data);
        return data.access_token;
      })
      .catch(() => {
        redirectToLogin();
        return null;
      })
      .finally(() => {
        refreshPromise = null;
      });
  }
  return refreshPromise;
}

// ── Axios instance ─────────────────────────────────────────────────────────────
const API = axios.create({ baseURL: API_BASE });

API.interceptors.request.use((config) => {
  const token = tokens.access;
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

API.interceptors.response.use(
  (response) => response,
  async (error) => {
    const original = error.config;
    if (error.response?.status === 401 && original && !original._retried) {
      original._retried = true;
      const newToken = await refreshAccessToken();
      if (newToken) {
        original.headers.Authorization = `Bearer ${newToken}`;
        return API(original);
      }
    }
    if (error.response?.status === 401) redirectToLogin();
    return Promise.reject(error);
  }
);

// ── Endpoints ──────────────────────────────────────────────────────────────────
export const login = (username, password) =>
  API.post("/auth/login", { username, password });

export const logout = () => API.post("/auth/logout").catch(() => {});

export const uploadFile = (file, onProgress) => {
  const formData = new FormData();
  formData.append("file", file);
  return API.post("/upload/", formData, {
    headers: { "Content-Type": "multipart/form-data" },
    onUploadProgress: (e) => {
      if (onProgress && e.total) onProgress(Math.round((e.loaded * 100) / e.total));
    },
  });
};

export const askQuestion = (sessionId, question) =>
  API.post("/query/", { session_id: sessionId, question });

/**
 * Streaming query — POST /query/stream, dispatching SSE events to callbacks.
 * Transparently refreshes the access token once on a 401.
 */
export const askQuestionStream = async (
  sessionId,
  question,
  onStage,
  onDone,
  onError,
  onToken
) => {
  const doFetch = (token) =>
    fetch(`${API_BASE}/query/stream`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({ session_id: sessionId, question }),
    });

  let response;
  try {
    response = await doFetch(tokens.access);
    if (response.status === 401) {
      const newToken = await refreshAccessToken();
      if (!newToken) return;
      response = await doFetch(newToken);
    }
  } catch {
    onError("Cannot reach the server. Is the backend running?");
    return;
  }

  if (response.status === 401) {
    redirectToLogin();
    return;
  }
  if (!response.ok) {
    const err = await response.json().catch(() => ({}));
    onError(err.detail || "Request failed");
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop();
    for (const line of lines) {
      if (!line.startsWith("data: ")) continue;
      try {
        const data = JSON.parse(line.slice(6));
        if (data.stage === "done") onDone(data.result);
        else if (data.stage === "error") onError(data.message);
        else if (data.stage === "token") {
          if (onToken) onToken(data.token);
        } else onStage(data.stage, data.message);
      } catch {
        // malformed SSE line — ignore
      }
    }
  }
};

export const getAuditLogs = (limit = 50, offset = 0) =>
  API.get(`/audit/logs?limit=${limit}&offset=${offset}`);

export const exportPdf = (sessionId) =>
  API.get(`/export/pdf/${sessionId}`, { responseType: "blob" });

export { tokens };
export default API;
