// #/prompts - which prompt version each step uses, and the research skills. Editing, diffs,
// labels and the playground live in Langfuse (v0.6 §20.2); this page links there.

import { api, h, mount, truncate } from "../lib.js";

export async function render(root) {
  const [prompts, skills] = await Promise.all([api("/api/prompts"), api("/api/skills")]);
  const lf = prompts.langfuse;
  const lfBadge = !lf.configured
    ? h("span", { class: "badge" }, "Langfuse not configured - using repo YAML seeds")
    : lf.reachable ? h("span", { class: "badge ok" }, "Langfuse connected")
      : h("span", { class: "badge warn" }, "Langfuse unreachable - using repo YAML seeds");

  mount(root,
    h("div", { class: "page-head" },
      h("div", {}, h("h1", {}, "Prompts & Skills"),
        h("div", { class: "muted" }, "Signatures fix each step's inputs and output schema in code; the wording is versioned in Langfuse, with the repo YAML as seed and fallback.")),
      h("div", { class: "row" }, lfBadge,
        lf.url ? h("a", { class: "button ghost", href: lf.url, target: "_blank", rel: "noopener" }, "Open Langfuse ↗") : null)),
    h("div", { class: "panel" },
      h("h2", {}, "Signatures"),
      h("div", { class: "table-wrap" }, h("table", {},
        h("thead", {}, h("tr", {}, h("th", {}, "Step"), h("th", {}, "Tier"), h("th", {}, "Active version"),
          h("th", {}, "Output contract"), h("th", {}, "Inputs"), h("th", {}))),
        h("tbody", {}, ...prompts.prompts.map((p) => h("tr", {},
          h("td", {}, h("strong", {}, p.id), h("div", { class: "muted small" }, p.description),
            p.uses_skills ? h("span", { class: "badge info small" }, "takes skill guidance") : null),
          h("td", {}, p.tier),
          h("td", {}, h("span", { class: `badge ${p.active.source === "langfuse" ? "violet" : ""}` }, `${p.active.source} v${p.active.version}`),
            h("div", { class: "muted small mono" }, p.active.content_hash)),
          h("td", {}, h("div", { class: "mono small" }, p.output),
            p.compatible ? h("span", { class: "badge ok small" }, `schema ${p.schema_hash.slice(0, 8)}`)
              : h("span", { class: "badge bad small", title: p.problem }, "Langfuse version refused")),
          h("td", { class: "small muted" }, p.inputs.join(", ")),
          h("td", {}, p.edit_url ? h("a", { class: "button ghost small", href: p.edit_url, target: "_blank", rel: "noopener" }, "Edit in Langfuse ↗") : null))))))),
    h("div", { class: "panel" },
      h("h2", {}, "Research skills"),
      h("p", { class: "muted" }, "The analyzer picks up to two per question from their descriptions. Skills add guidance and trusted domains; they cannot change budgets, termination or the gate."),
      ...skills.skills.map((skill) => h("details", { style: "border-top:1px solid var(--line); padding:10px 0" },
        h("summary", {}, h("strong", {}, skill.name), h("span", { class: "badge small", style: "margin-left:8px" }, `v${skill.version}`),
          h("div", { class: "muted", style: "margin-left:18px" }, skill.description)),
        h("div", { class: "grid two", style: "margin-top:8px" },
          h("div", {}, h("h3", {}, "Guidance"), h("pre", { style: "white-space:pre-wrap" }, skill.guidance)),
          h("div", {},
            h("h3", {}, "Added domains"),
            Object.keys(skill.domains).length ? h("ul", {}, ...Object.entries(skill.domains).map(([tier, domains]) =>
              h("li", {}, `Tier ${tier}: `, truncate(domains.join(", "), 400)))) : h("div", { class: "muted" }, "none"),
            h("h3", {}, "Query patterns"),
            skill.query_patterns.length ? h("ul", {}, ...skill.query_patterns.map((q) => h("li", { class: "mono small" }, q))) : h("div", { class: "muted" }, "none")))))),
  );
}
