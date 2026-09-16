// #/settings - provider keys live in this tab's sessionStorage only and travel with each run,
// where the server encrypts them for that run and deletes them when it ends (v0.6 §15.3).

import { PROVIDERS, api, h, loadKeys, mount, saveKeys, toast } from "../lib.js";

export async function render(root) {
  const keys = loadKeys();
  let env = {};
  try { env = (await api("/api/keys/status")).env; } catch { /* shown as unknown */ }

  const rows = PROVIDERS.map((provider) => {
    const input = h("input", {
      type: provider.url ? "text" : "password",
      placeholder: env[provider.id] ? "using the default from .env" : provider.placeholder,
      value: keys[provider.id] || "",
      autocomplete: "off",
      spellcheck: "false",
    });
    const status = h("span", { class: "badge muted" }, "not tested");
    const test = h("button", {
      class: "ghost small",
      onclick: async () => {
        const value = input.value.trim();
        if (!value) {
          toast(env[provider.id] ? "The .env default is used; paste a key to test it here." : "Paste a key first.");
          return;
        }
        test.disabled = true;
        status.className = "badge";
        status.textContent = "testing…";
        try {
          const result = await api("/api/keys/validate", { method: "POST", body: { provider: provider.id, key: value } });
          status.className = `badge ${result.ok ? "ok" : "bad"}`;
          status.textContent = result.ok ? "works" : "rejected";
          status.title = result.detail;
          toast(`${provider.label}: ${result.detail}`, result.ok ? "" : "bad");
        } catch (error) {
          status.className = "badge bad";
          status.textContent = "error";
          toast(error.message, "bad");
        } finally {
          test.disabled = false;
        }
      },
    }, "Test");
    return { provider, input, element: h("div", { class: "field" },
      h("label", {}, provider.label,
        env[provider.id] ? h("span", { class: "badge ok small", style: "margin-left:8px" }, ".env default") : null,
        h("span", { class: "badge small", style: "margin-left:6px" }, provider.kind)),
      h("div", { class: "row" }, h("div", { style: "flex:1" }, input), test, status),
      provider.url
        ? h("div", { class: "help" }, "Used only when the ", h("code", {}, "local-llm"), " compose profile is running; otherwise it is skipped.")
        : h("div", { class: "help" }, "Get a key: ", h("a", { href: provider.link, target: "_blank", rel: "noopener" }, provider.link)),
    ) };
  });

  const save = () => {
    saveKeys(Object.fromEntries(rows.map((row) => [row.provider.id, row.input.value.trim()])));
    toast("Saved for this browser tab.");
  };

  mount(root,
    h("div", { class: "page-head" },
      h("div", {}, h("h1", {}, "Settings"),
        h("div", { class: "muted" }, "Provider keys for your runs."))),
    h("div", { class: "grid two" },
      h("div", { class: "panel" },
        h("h2", {}, "Provider keys"),
        ...rows.map((row) => row.element),
        h("div", { class: "row end" },
          h("button", { class: "ghost", onclick: () => { rows.forEach((r) => { r.input.value = ""; }); save(); } }, "Clear"),
          h("button", { onclick: save }, "Save"))),
      h("div", { class: "panel" },
        h("h2", {}, "How keys are handled"),
        h("ul", {},
          h("li", {}, "Keys you enter stay in this browser tab (sessionStorage) and disappear when it closes."),
          h("li", {}, "When you start a run they are sent once; the server encrypts them (Fernet) for that run only and deletes them when the run ends."),
          h("li", {}, "Keys never appear in logs, the run timeline, exports or Langfuse traces. The Go dispatcher never sees them."),
          h("li", {}, "Keys in ", h("code", {}, ".env"), " are used as defaults when a field here is empty.")),
        h("h3", {}, "What you need"),
        h("p", {}, "At least one LLM key (Gemini or OpenAI) and one search key (Tavily or Brave). Both of each gives the agent a fallback when a provider fails, and Brave adds result diversity."),
        h("p", { class: "muted small" }, "Testing a Brave key spends one search from its free monthly allowance; the other tests are free.")),
    ),
  );
}
