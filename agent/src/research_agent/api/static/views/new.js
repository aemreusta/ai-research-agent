// #/new - ask a question; the advanced panel is generated from /api/config/schema (D24).

import { ApiError, api, h, keyReadiness, loadKeys, mount, toast } from "../lib.js";

const EXAMPLES = [
  "Türkiye'deki SaaS şirketleri için 2026 KVKK uyum aksiyon planı nedir?",
  "ApilexAI'ın ürünleri, iş ortaklıkları ve stratejik yönü nedir?",
  "What changed in the EU AI Act implementation timeline?",
  "What is the size of the European legal tech market and how fast is it growing?",
];

function control(field, overrides) {
  const id = `f-${field.path}`;
  const current = overrides[field.path] ?? field.default;
  const help = h("div", { class: "help" }, field.description,
    field.minimum !== null || field.maximum !== null
      ? ` (${field.minimum ?? "…"} – ${field.maximum ?? "…"})` : "");

  if (field.options) {
    const select = h("select", { id, onchange: () => { overrides[field.path] = select.value; } },
      ...field.options.map((option) => h("option", { value: option.value }, option.label)));
    select.value = current ?? "";
    const label = field.path === "llm.reasoning_model" ? "Planning & verification" : "Extraction & search";
    return h("div", { class: "field" }, h("label", { for: id }, label), select, help);
  }

  if (field.type === "boolean") {
    const box = h("input", { type: "checkbox", id, checked: Boolean(current),
      onchange: () => { overrides[field.path] = box.checked; } });
    return h("div", { class: "field" }, h("label", { class: "inline", for: id }, box,
      field.path === "llm.allow_fallback" ? "Allow provider fallback" : field.path.split(".").pop()), help);
  }
  if (field.type === "integer" || field.type === "number") {
    const input = h("input", {
      type: "number", id, value: current ?? "",
      step: field.type === "integer" ? "1" : "any",
      min: field.minimum ?? undefined, max: field.maximum ?? undefined,
      placeholder: field.nullable ? "disabled" : "",
      oninput: () => {
        const raw = input.value.trim();
        if (raw === "") {
          if (field.nullable) overrides[field.path] = null; else delete overrides[field.path];
        } else {
          overrides[field.path] = field.type === "integer" ? parseInt(raw, 10) : parseFloat(raw);
        }
      },
    });
    return h("div", { class: "field" },
      h("label", { for: id }, field.path.split(".").pop(),
        field.nullable ? h("span", { class: "source-tag" }, "optional gate") : null),
      input, help);
  }
  const input = h("input", { type: "text", id, value: current ?? "",
    oninput: () => { overrides[field.path] = input.value; } });
  return h("div", { class: "field" }, h("label", { for: id }, field.path.split(".").pop()), input, help);
}

export async function render(root) {
  const [schema, presetList, readiness] = await Promise.all([
    api("/api/config/schema"),
    api("/api/presets").catch(() => ({ presets: [] })),
    keyReadiness(),
  ]);
  const overrides = {};
  const question = h("textarea", { placeholder: "Ask a research question - in Turkish or English.", maxlength: "2000" });
  const presetSelect = h("select", {},
    h("option", { value: "" }, "No preset"),
    ...presetList.presets.map((p) => h("option", { value: p.name }, p.name)));

  const modelControls = h("div", { class: "form-grid" });
  const advancedControls = h("div");
  function drawControls() {
    mount(modelControls, ...schema.fields.filter((f) => f.group === "Models").map((f) => control(f, overrides)));
    mount(advancedControls, ...schema.groups.filter((group) => group !== "Models").map((group) => h("fieldset", {},
      h("legend", {}, group), h("div", { class: "form-grid" },
        ...schema.fields.filter((f) => f.group === group).map((f) => control(f, overrides))))));
  }
  presetSelect.addEventListener("change", () => {
    for (const key of Object.keys(overrides)) delete overrides[key];
    Object.assign(overrides, presetList.presets.find((p) => p.name === presetSelect.value)?.overrides || {});
    drawControls();
  });
  drawControls();

  const submit = h("button", {}, "Start research");
  const warning = readiness.ready ? null : h("div", { class: "banner warn" },
    "No usable keys yet. ",
    !readiness.llm ? "Add a Gemini or OpenAI key. " : "",
    !readiness.search ? "Add a Tavily or Brave key. " : "",
    h("a", { href: "#/settings" }, "Open Settings →"));

  submit.addEventListener("click", async () => {
    const text = question.value.trim();
    if (!text) { toast("Write a question first."); question.focus(); return; }
    submit.disabled = true;
    submit.textContent = "Starting…";
    try {
      const run = await api("/api/runs", { method: "POST", body: {
        question: text, overrides, keys: loadKeys(), preset: presetSelect.value || null,
      } });
      if (Object.keys(run.pii_masked || {}).length) {
        toast(`Masked before storage: ${Object.entries(run.pii_masked).map(([k, v]) => `${v} ${k}`).join(", ")}`);
      }
      window.location.hash = `#/runs/${run.run_id}`;
    } catch (error) {
      const detail = error instanceof ApiError ? error.body?.message : error.message;
      toast(`Could not start: ${detail}`, "bad");
      submit.disabled = false;
      submit.textContent = "Start research";
    }
  });

  const presetName = h("input", { type: "text", placeholder: "preset name" });
  const savePreset = h("button", { class: "ghost small", onclick: async () => {
    if (!presetName.value.trim()) { toast("Name the preset first."); return; }
    try {
      await api("/api/presets", { method: "POST", body: { name: presetName.value.trim(), overrides } });
      toast("Preset saved.");
      presetSelect.append(h("option", { value: presetName.value.trim() }, presetName.value.trim()));
      presetList.presets.push({ name: presetName.value.trim(), overrides: { ...overrides } });
    } catch (error) {
      toast(error.body?.message || error.message, "bad");
    }
  } }, "Save current settings as preset");

  mount(root,
    h("div", { class: "page-head" },
      h("div", {}, h("h1", {}, "New research"),
        h("div", { class: "muted" }, "The agent plans, searches, scores sources, extracts quote-backed claims, and passes the report through a deterministic gate."))),
    warning,
    h("div", { class: "panel" },
      h("div", { class: "field" }, h("label", {}, "Question"), question,
        h("div", { class: "help" }, "Identifiers such as national ids, IBANs, cards, emails and phone numbers are masked before anything is stored or sent.")),
      h("div", { class: "row", style: "margin-bottom:14px" }, h("span", { class: "muted small" }, "Try:"),
        ...EXAMPLES.map((example) => h("span", { class: "chip", onclick: () => { question.value = example; question.focus(); } }, example))),
      h("fieldset", {}, h("legend", {}, "Models"), modelControls,
        h("p", { class: "help" }, "Choose each stage independently, or leave Automatic. A selected provider needs its API key in Settings. OpenAI models use API billing; a ChatGPT subscription is separate. Local models must be installed in Ollama.")),
      h("details", { class: "advanced" },
        h("summary", {}, "Advanced settings"),
        h("p", { class: "muted small" }, "Defaults come from config/settings.yaml. Changes apply to this run only and are recorded in its config snapshot. Wall-clock and cost budgets are disabled unless you set them."),
        h("div", { class: "row", style: "margin-bottom:12px" }, h("label", { class: "inline" }, "Preset"), presetSelect, presetName, savePreset),
        advancedControls),
      h("div", { class: "row end" }, submit)),
  );
  question.focus();
}
