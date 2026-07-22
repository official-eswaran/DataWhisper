import axios from "axios";

// API base URL resolution:
//  - Same-origin "/api" by default (works behind the nginx reverse proxy in
//    production and avoids the previous hardcoded-https mixed-content bug).
//  - Override with VITE_API_URL for split-origin/dev setups, e.g.
//    VITE_API_URL=http://localhost:8000/api
const API_BASE = import.meta.env.VITE_API_URL || "/api";

// ── Token storage (issue #22) ─────────────────────────────────────────────────
// Nothing token-shaped goes in localStorage anymore. The access token and role
// live in module memory only, so an XSS payload can't read them from storage
// and can't persist beyond the current tab. The refresh token isn't here at all
// — it's an httpOnly cookie the browser holds and only ever sends to
// /api/auth/*, invisible to JavaScript. On reload, memory is empty and the app
// re-mints an access token via bootstrapSession() using that cookie.
let accessToken = null;
let userRole = null;

const tokens = {
  get access() {
    return accessToken;
  },
  get role() {
    return userRole;
  },
  set(data) {
    if (data?.access_token) accessToken = data.access_token;
    if (data?.role) userRole = data.role;
  },
  clear() {
    accessToken = null;
    userRole = null;
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
  // No token argument: the refresh token rides along as the httpOnly cookie.
  // credentials:"include" ensures it's sent (required if the API is ever on a
  // different origin; a no-op for the same-origin prod/proxy setup).
  if (!refreshPromise) {
    refreshPromise = fetch(`${API_BASE}/auth/refresh`, {
      method: "POST",
      credentials: "include",
    })
      .then(async (res) => {
        if (!res.ok) throw new Error("refresh failed");
        const data = await res.json();
        tokens.set(data);
        return data.access_token;
      })
      .catch(() => {
        // No redirect here: callers decide. On boot there may simply be no
        // session yet, which is not an error worth bouncing the user for.
        tokens.clear();
        return null;
      })
      .finally(() => {
        refreshPromise = null;
      });
  }
  return refreshPromise;
}

// Called once on app start to recover a session from the refresh cookie. Returns
// { token, role } when a valid cookie exists, else null.
export const bootstrapSession = async () => {
  const token = await refreshAccessToken();
  return token ? { token, role: tokens.role } : null;
};

// ── Axios instance ─────────────────────────────────────────────────────────────
// withCredentials so login/register Set-Cookie is stored and the refresh cookie
// is sent to /auth/*. Same-origin (prod nginx, dev proxy) would do this anyway;
// explicit keeps it working if the API is ever split to another origin.
const API = axios.create({ baseURL: API_BASE, withCredentials: true });

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

// ── Onboarding / signup ─────────────────────────────────────────────────────
export const register = (orgName, username, email, password) =>
  API.post("/auth/register", {
    org_name: orgName,
    username,
    email,
    password,
  });

// ── Admin console: org user management (owner/admin) ────────────────────────
export const listUsers = () => API.get("/users/");

export const createUser = (username, email, password, role = "member") =>
  API.post("/users/", { username, email, password, role });

export const setUserActive = (username, isActive) =>
  API.patch(`/users/${username}/status`, { is_active: isActive });

// ── Usage & plan (owner/admin) ──────────────────────────────────────────────
export const getUsage = () => API.get("/usage/");

export const changePlan = (plan) => API.put("/usage/plan", { plan });

// ── Billing (Stripe) ────────────────────────────────────────────────────────
// getBillingStatus reports enabled:false when the deployment has no Stripe keys
// configured — the UI hides the upgrade actions rather than showing dead buttons.
export const getBillingStatus = () => API.get("/billing/");

// Both of these return a Stripe-hosted URL to send the browser to; no card
// details are ever entered in this app.
export const startCheckout = (plan) => API.post("/billing/checkout", { plan });

export const openBillingPortal = () => API.post("/billing/portal");

// ── Account / GDPR (self-service) ───────────────────────────────────────────
export const exportMyData = () => API.get("/me/export");

export const deleteMyAccount = () => API.delete("/me");

export const deleteMyOrganization = () => API.delete("/org");

export { tokens };
export default API;
