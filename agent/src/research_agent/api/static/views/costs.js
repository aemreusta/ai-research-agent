// #/costs - where the money and the time went (D33).

import { api, duration, h, money, mount, number, percent, statusBadge, truncate, when } from "../lib.js";

function stat(label, value, hint) {
  return h("div", { class: "panel stat" }, h("div", { class: "label" }, label), h("div", { class: "value" }, value),
    hint ? h("div", { class: "hint" }, hint) : null);
}

function share(value, total) {
  const width = total ? Math.round((value / total) * 100) : 0;
  return h("div", { class: "bar", title: `${width}%` }, h("span", { style: `width:${width}%` }));
}

export async function render(root) {
  const data = await api("/api/costs");
  const t = data.totals;
  const modelTotal = data.by_model.reduce((sum, row) => sum + row.cost_usd, 0);
  const nodeTotal = data.by_node.reduce((sum, row) => sum + row.cost_usd, 0);

  mount(root,
    h("div", { class: "page-head" },
      h("div", {}, h("h1", {}, "Costs"),
        h("div", { class: "muted" }, "From llm_calls and search_calls in Postgres. Prices come from config/models.yaml, the same table Langfuse uses."))),
    h("div", { class: "grid cards" },
      stat("Total LLM cost", money(t.cost_usd), `${t.runs} run(s)`),
      stat("Average per run", money(t.avg_cost_per_run)),
      stat("LLM calls", number(t.llm_calls), `${number(t.tokens_in)} in / ${number(t.tokens_out)} out tokens`),
      stat("Searches", number(t.searches)),
      stat("Search cache hit rate", percent(t.search_cache_hit_rate), "cached searches cost nothing")),
    h("div", { class: "panel", style: "margin-top:16px" }, h("h2", {}, "By model"),
      data.by_model.length ? h("div", { class: "table-wrap" }, h("table", {},
        h("thead", {}, h("tr", {}, h("th", {}, "Model"), h("th", {}, "Tier"), h("th", { class: "num" }, "Calls"),
          h("th", { class: "num" }, "Tokens in"), h("th", { class: "num" }, "Tokens out"), h("th", { class: "num" }, "p50"),
          h("th", { class: "num" }, "p95"), h("th", { class: "num" }, "Failures"), h("th", { class: "num" }, "Cost"), h("th", {}, "Share"))),
        h("tbody", {}, ...data.by_model.map((r) => h("tr", {},
          h("td", {}, h("strong", {}, r.model), h("div", { class: "muted small" }, r.provider)),
          h("td", {}, r.tier), h("td", { class: "num" }, number(r.calls)),
          h("td", { class: "num" }, number(r.tokens_in)), h("td", { class: "num" }, number(r.tokens_out)),
          h("td", { class: "num" }, `${number(r.latency_p50_ms)} ms`), h("td", { class: "num" }, `${number(r.latency_p95_ms)} ms`),
          h("td", { class: "num" }, r.failures), h("td", { class: "num" }, money(r.cost_usd)), h("td", {}, share(r.cost_usd, modelTotal)))))))
        : h("div", { class: "empty" }, "No LLM calls yet.")),
    h("div", { class: "grid two", style: "margin-top:16px" },
      h("div", { class: "panel" }, h("h2", {}, "By node"),
        h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "Node"), h("th", { class: "num" }, "Calls"),
          h("th", { class: "num" }, "Avg latency"), h("th", { class: "num" }, "Cost"), h("th", {}, "Share"))),
          h("tbody", {}, ...data.by_node.map((r) => h("tr", {},
            h("td", {}, r.node), h("td", { class: "num" }, r.calls), h("td", { class: "num" }, `${number(r.avg_latency_ms)} ms`),
            h("td", { class: "num" }, money(r.cost_usd)), h("td", {}, share(r.cost_usd, nodeTotal))))))),
      h("div", { class: "panel" }, h("h2", {}, "Search providers"),
        h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "Provider"), h("th", { class: "num" }, "Calls"),
          h("th", { class: "num" }, "Cache hits"), h("th", { class: "num" }, "Failures"), h("th", { class: "num" }, "Avg latency"))),
          h("tbody", {}, ...data.by_search_provider.map((r) => h("tr", {},
            h("td", {}, r.provider), h("td", { class: "num" }, r.calls), h("td", { class: "num" }, r.cache_hits),
            h("td", { class: "num" }, r.failures), h("td", { class: "num" }, `${number(r.avg_latency_ms)} ms`))))))),
    h("div", { class: "panel", style: "margin-top:16px" }, h("h2", {}, "By run"), runTable(data.runs)),
  );
}

function runTable(runs) {
  const head = h("thead", {}, h("tr", {}, h("th", {}, "Started"), h("th", {}, "Question"), h("th", {}, "Status"),
    h("th", { class: "num" }, "Searches"), h("th", { class: "num" }, "Tokens"),
    h("th", { class: "num" }, "Duration"), h("th", { class: "num" }, "Cost")));
  const rows = runs.map((r) => h("tr", { class: "clickable", onclick: () => { window.location.hash = `#/runs/${r.run_id}`; } },
    h("td", { class: "muted" }, when(r.created_at)),
    h("td", {}, truncate(r.question, 80)),
    h("td", {}, statusBadge(r.status)),
    h("td", { class: "num" }, r.searches),
    h("td", { class: "num" }, number(r.tokens_in + r.tokens_out)),
    h("td", { class: "num" }, duration(r.duration_seconds)),
    h("td", { class: "num" }, money(r.cost_usd))));
  return h("div", { class: "table-wrap" }, h("table", {}, head, h("tbody", {}, ...rows)));
}
