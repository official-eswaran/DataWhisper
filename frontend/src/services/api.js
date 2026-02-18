import axios from "axios";

const API = axios.create({
  baseURL: "http://localhost:8000/api",
});

API.interceptors.request.use((config) => {
  const token = localStorage.getItem("token");
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

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

export const getAuditLogs = (limit = 50) =>
  API.get(`/audit/logs?limit=${limit}`);

export const exportPdf = (sessionId) =>
  API.get(`/export/pdf/${sessionId}`, { responseType: "blob" });

export default API;
