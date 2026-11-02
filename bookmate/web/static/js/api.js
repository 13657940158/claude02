// 墨舟 BookMate —— 前端 API 客户端（原生 ESM，无构建链）
const BASE = "/api/v1";

export function getToken() {
  return localStorage.getItem("bm_token") || "";
}
export function setToken(token) {
  if (token) localStorage.setItem("bm_token", token);
  else localStorage.removeItem("bm_token");
}
export function currentUser() {
  try {
    return JSON.parse(localStorage.getItem("bm_user") || "null");
  } catch {
    return null;
  }
}
export function setUser(user) {
  if (user) localStorage.setItem("bm_user", JSON.stringify(user));
  else localStorage.removeItem("bm_user");
}

export class ApiError extends Error {
  constructor(code, message, status, fields) {
    super(message);
    this.code = code;
    this.status = status;
    this.fields = fields || null;
  }
}

async function request(path, { method = "GET", body, signal, raw = false } = {}) {
  const headers = { Accept: "application/json" };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  let payload;
  if (body !== undefined) {
    if (body instanceof FormData) payload = body;
    else {
      headers["Content-Type"] = "application/json";
      payload = JSON.stringify(body);
    }
  }
  let resp;
  try {
    resp = await fetch(`${BASE}${path}`, { method, headers, body: payload, signal });
  } catch (err) {
    if (err.name === "AbortError") throw err;
    throw new ApiError("NETWORK_ERROR", "网络连接失败，请检查网络后重试", 0);
  }
  if (resp.status === 204) return null;
  const text = await resp.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = null;
  }
  if (!resp.ok) {
    const err = new ApiError(
      data?.code || `HTTP_${resp.status}`,
      data?.message || `请求失败（${resp.status}）`,
      resp.status,
      data?.fields,
    );
    if (resp.status === 401) {
      setToken("");
      setUser(null);
    }
    throw err;
  }
  if (raw) return data;
  return data && typeof data === "object" && "data" in data ? data.data : data;
}

export const api = {
  get: (path, opts) => request(path, { ...opts, method: "GET" }),
  post: (path, body, opts) => request(path, { ...opts, method: "POST", body }),
  put: (path, body, opts) => request(path, { ...opts, method: "PUT", body }),
  patch: (path, body, opts) => request(path, { ...opts, method: "PATCH", body }),
  del: (path, opts) => request(path, { ...opts, method: "DELETE" }),
};

// ---- 通用 UI 工具 ----
export function $(sel, root = document) {
  return root.querySelector(sel);
}
export function $$(sel, root = document) {
  return [...root.querySelectorAll(sel)];
}

export function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

let toastTimer = null;
export function toast(message, kind = "") {
  const el = document.getElementById("toast");
  if (!el) return;
  el.textContent = message;
  el.className = `toast ${kind}`;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    el.hidden = true;
  }, 3200);
}

export function setBusy(button, busy, busyText = "处理中…") {
  if (!button) return;
  if (busy) {
    button.dataset.label = button.textContent;
    button.textContent = busyText;
    button.classList.add("is-loading");
    button.disabled = true;
  } else {
    if (button.dataset.label) button.textContent = button.dataset.label;
    button.classList.remove("is-loading");
    button.disabled = false;
  }
}

export function showAlert(container, message, kind = "error") {
  if (!container) return;
  container.innerHTML = message ? `<div class="alert ${kind}">${escapeHtml(message)}</div>` : "";
}

export function formatDate(value) {
  if (!value) return "";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

// ---- 首页服务状态 ----
function dot(kind) {
  const map = { ok: "ok", configured: "ok", mock: "warn", degraded: "warn" };
  return `<span class="dot ${map[kind] || "bad"}"></span>${escapeHtml(kind || "unknown")}`;
}

export async function loadStatus() {
  const ids = {
    app: "status-app",
    mysql: "status-mysql",
    redis: "status-redis",
    qdrant: "status-qdrant",
    llm: "status-llm",
  };
  try {
    const res = await fetch("/readyz");
    const body = await res.json().catch(() => ({}));
    const checks = body.checks || {};
    const appEl = document.getElementById(ids.app);
    if (appEl) appEl.innerHTML = dot(res.ok ? "ok" : "degraded");
    for (const key of ["mysql", "redis", "qdrant"]) {
      const el = document.getElementById(ids[key]);
      if (el) el.innerHTML = dot(checks[key]?.status);
    }
    const online = ["llm", "embedding", "rerank"].map((k) => checks[k]?.status);
    const onlineKind = online.every((s) => s === "ok" || s === "configured")
      ? "ok"
      : online.includes("mock") ? "mock" : "degraded";
    const llmEl = document.getElementById(ids.llm);
    if (llmEl) llmEl.innerHTML = dot(onlineKind);
  } catch {
    for (const id of Object.values(ids)) {
      const el = document.getElementById(id);
      if (el) el.innerHTML = dot("degraded");
    }
  }
}
