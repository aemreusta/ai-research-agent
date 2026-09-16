// Shared helpers: DOM building (no innerHTML), API calls, formatting, and key storage.

export function h(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "dataset") Object.assign(node.dataset, value);
    else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
    else if (key === "value") node.value = value;
    else if (key === "checked") node.checked = Boolean(value);
    else node.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

export function mount(target, ...children) {
  target.replaceChildren(...children.flat(Infinity).filter(Boolean));
}

export class ApiError extends Error {
  constructor(status, body) {
    super(body?.message || `HTTP ${status}`);
    this.status = status;
    this.body = body;
  }
}

export async function api(path, { method = "GET", body } = {}) {
  const response = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await response.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = { message: text }; }
  if (!response.ok) throw new ApiError(response.status, data);
  return data;
}

export function toast(message, kind = "") {
  const node = h("div", { class: `toast ${kind}` }, message);
  document.body.append(node);
  setTimeout(() => node.remove(), 4500);
}

// --- formatting ----------------------------------------------------------------------------------

export const money = (value) => `$${Number(value || 0).toFixed(Number(value) >= 1 ? 2 : 4)}`;
export const number = (value) => Number(value || 0).toLocaleString();
export const percent = (value) => `${(Number(value || 0) * 100).toFixed(0)}%`;

export function duration(seconds) {
  if (seconds === null || seconds === undefined) return "–";
  const s = Math.round(seconds);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  return m < 60 ? `${m}m ${s % 60}s` : `${Math.floor(m / 60)}h ${m % 60}m`;
}

export function when(iso) {
  if (!iso) return "–";
  const date = new Date(iso);
  const diff = (Date.now() - date.getTime()) / 1000;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)} min ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} h ago`;
  return date.toLocaleString();
}

export const clock = (iso) => (iso ? new Date(iso).toLocaleTimeString([], { hour12: false }) : "");

const STATUS = {
  queued: ["info", "queued"],
  dispatched: ["info", "dispatched"],
  running: ["info live", "running"],
  succeeded: ["ok", "succeeded"],
  succeeded_with_warnings: ["warn", "succeeded · warnings"],
  failed: ["bad", "failed"],
  cancelled: ["", "cancelled"],
};
export const ACTIVE = new Set(["queued", "dispatched", "running"]);

export function statusBadge(status) {
  const [kind, label] = STATUS[status] || ["", status];
  return h("span", { class: `badge ${kind}` }, label);
}

export function gateBadge(verdict) {
  if (!verdict) return h("span", { class: "muted" }, "–");
  const kind = { pass: "ok", pass_with_warnings: "warn", fail: "bad" }[verdict] || "";
  return h("span", { class: `badge ${kind}` }, verdict.replaceAll("_", " "));
}

export function truncate(text, limit = 110) {
  if (!text) return "";
  return text.length > limit ? `${text.slice(0, limit - 1)}…` : text;
}

// --- provider keys (browser tab only, v0.6 §15.3) --------------------------------------------------

const KEY_STORE = "research.keys";
export const PROVIDERS = [
  { id: "gemini", label: "Google Gemini", kind: "llm", placeholder: "AIza…", link: "https://aistudio.google.com/apikey" },
  { id: "openai", label: "OpenAI", kind: "llm", placeholder: "sk-…", link: "https://platform.openai.com/api-keys" },
  { id: "tavily", label: "Tavily", kind: "search", placeholder: "tvly-…", link: "https://app.tavily.com" },
  { id: "brave", label: "Brave Search", kind: "search", placeholder: "BSA…", link: "https://api-dashboard.search.brave.com" },
  { id: "ollama", label: "Ollama URL", kind: "llm", placeholder: "http://ollama:11434", link: "https://ollama.com", url: true },
];

export function loadKeys() {
  try { return JSON.parse(sessionStorage.getItem(KEY_STORE) || "{}"); } catch { return {}; }
}
export function saveKeys(keys) {
  const clean = Object.fromEntries(Object.entries(keys).filter(([, v]) => v && v.trim()));
  sessionStorage.setItem(KEY_STORE, JSON.stringify(clean));
  window.dispatchEvent(new Event("keys-changed"));
}

export async function keyReadiness() {
  const tab = loadKeys();
  let env = {};
  try { env = (await api("/api/keys/status")).env; } catch { /* keep going */ }
  const has = (id) => Boolean(tab[id]) || Boolean(env[id]);
  const llm = ["gemini", "openai"].some(has);
  const search = ["tavily", "brave"].some(has);
  return { tab, env, llm, search, ready: llm && search };
}
