import axios from "axios";

// Use the same host the app is served from — works on localhost AND mobile (same WiFi)
const API_HOST = window.location.hostname;
const API_BASE = `https://${API_HOST}:8000/api`;

const API = axios.create({
  baseURL: API_BASE,
});

API.interceptors.request.use((config) => {
  const token = localStorage.getItem("token");
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

API.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem("token");
      localStorage.removeItem("role");
      window.location.href = "/login";
    }
    return Promise.reject(error);
  }
);

export const login = (username, password) =>
  API.post("/auth/login", { username, password });

export const uploadFile = (file, onProgress) => {
  const formData = new FormData();
  formData.append("file", file);
  return API.post("/upload/", formData, {
    headers: { "Content-Type": "multipart/form-data" },
    onUploadProgress: (e) => {
      if (onProgress) onProgress(Math.round((e.loaded * 100) / e.total));
    },
  });
};

export const askQuestion = (sessionId, question) =>
  API.post("/query/", { session_id: sessionId, question });

/**
 * Streaming query — calls /query/stream and fires callbacks for each event.
 * onStage(stage, message)  — called for each intermediate stage
 * onDone(result)           — called with the final result object
 * onError(message)         — called on any error
 */
export const askQuestionStream = async (sessionId, question, onStage, onDone, onError) => {
  const token = localStorage.getItem("token");

  let response;
  try {
    response = await fetch(`${API_BASE}/query/stream`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({ session_id: sessionId, question }),
    });
  } catch {
    onError("Cannot reach the server. Is the backend running?");
    return;
  }

  if (response.status === 401) {
    localStorage.removeItem("token");
    localStorage.removeItem("role");
    window.location.href = "/login";
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
    buffer = lines.pop(); // keep any partial last line

    for (const line of lines) {
      if (!line.startsWith("data: ")) continue;
      try {
        const data = JSON.parse(line.slice(6));
        if (data.stage === "done") {
          onDone(data.result);
        } else if (data.stage === "error") {
          onError(data.message);
        } else {
          onStage(data.stage, data.message);
        }
      } catch {
        // malformed SSE line — ignore
      }
    }
  }
};

export const getAuditLogs = (limit = 50) =>
  API.get(`/audit/logs?limit=${limit}`);

export const exportPdf = (sessionId) =>
  API.get(`/export/pdf/${sessionId}`, { responseType: "blob" });

export default API;
