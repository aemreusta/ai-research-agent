// Hash router and app shell. Each view is a module exporting `render(root, params)`, which may
// return a cleanup function (closing an EventSource, stopping a poll).

import { api, h, keyReadiness, mount } from "./lib.js";
import * as costs from "./views/costs.js";
import * as newRun from "./views/new.js";
import * as prompts from "./views/prompts.js";
import * as run from "./views/run.js";
import * as runs from "./views/runs.js";
import * as settings from "./views/settings.js";

const routes = [
  { pattern: /^#\/new$/, view: newRun, name: "new" },
  { pattern: /^#\/runs$/, view: runs, name: "runs" },
  { pattern: /^#\/runs\/([0-9a-f-]{36})$/, view: run, name: "runs" },
  { pattern: /^#\/costs$/, view: costs, name: "costs" },
  { pattern: /^#\/prompts$/, view: prompts, name: "prompts" },
  { pattern: /^#\/settings$/, view: settings, name: "settings" },
];

let cleanup = null;
const root = document.getElementById("view");

async function navigate() {
  const hash = window.location.hash || "#/new";
  const route = routes.find((r) => r.pattern.test(hash));
  if (!route) {
    window.location.hash = "#/new";
    return;
  }
  if (typeof cleanup === "function") cleanup();
  cleanup = null;
  for (const link of document.querySelectorAll(".nav a")) {
    link.classList.toggle("active", link.dataset.route === route.name);
  }
  const params = hash.match(route.pattern).slice(1);
  mount(root, h("div", { class: "empty" }, h("span", { class: "spinner" }), " Loading…"));
  try {
    cleanup = await route.view.render(root, ...params);
  } catch (error) {
    mount(root, h("div", { class: "banner bad" }, `Could not load this page: ${error.message}`));
  }
  window.scrollTo(0, 0);
}

async function refreshKeyBadge() {
  const badge = document.getElementById("key-badge");
  const state = await keyReadiness();
  badge.className = `badge ${state.ready ? "ok" : "warn"}`;
  badge.textContent = state.ready ? "keys ready" : "keys missing";
  badge.title = state.ready
    ? "An LLM key and a search key are available (this tab or .env)."
    : "Add at least one LLM key (Gemini/OpenAI) and one search key (Tavily/Brave) in Settings.";
  badge.onclick = () => { window.location.hash = "#/settings"; };
  badge.style.cursor = "pointer";
}

async function boot() {
  try {
    const meta = await api("/api/meta");
    document.getElementById("version").textContent = `v${meta.version}`;
    if (meta.langfuse_url) {
      const link = document.getElementById("langfuse-link");
      link.href = meta.langfuse_url;
      link.classList.remove("hidden");
    }
  } catch { /* the pages report their own errors */ }
  await refreshKeyBadge();
  window.addEventListener("keys-changed", refreshKeyBadge);
  window.addEventListener("hashchange", navigate);
  navigate();
}

boot();
