// #/runs - history, refreshed while anything is still active.

import { ACTIVE, api, duration, gateBadge, h, money, mount, statusBadge, truncate, when } from "../lib.js";

export async function render(root) {
  let timer = null;

  async function draw() {
    const data = await api("/api/runs?limit=100");
    const body = data.runs.length
      ? h("div", { class: "table-wrap" }, h("table", {},
          h("thead", {}, h("tr", {},
            h("th", {}, "Started"), h("th", {}, "Question"), h("th", {}, "Status"),
            h("th", {}, "Stop reason"), h("th", {}, "Gate"), h("th", { class: "num" }, "Rounds"),
            h("th", { class: "num" }, "Searches"), h("th", { class: "num" }, "Cost"),
            h("th", { class: "num" }, "Duration"))),
          h("tbody", {}, ...data.runs.map((run) => h("tr", {
            class: "clickable", onclick: () => { window.location.hash = `#/runs/${run.run_id}`; },
          },
            h("td", { class: "muted", title: run.created_at }, when(run.created_at)),
            h("td", {}, truncate(run.question, 90),
              run.simulated ? h("span", { class: "badge violet small", style: "margin-left:6px" }, "offline demo") : null,
              run.error_code ? h("div", { class: "code" }, run.error_code) : null),
            h("td", {}, statusBadge(run.status)),
            h("td", {}, run.stop_reason ? h("code", {}, run.stop_reason) : "–"),
            h("td", {}, gateBadge(run.gate_status)),
            h("td", { class: "num" }, run.iteration),
            h("td", { class: "num" }, run.searches_used),
            h("td", { class: "num" }, money(run.cost_usd)),
            h("td", { class: "num" }, duration(run.duration_seconds)),
          )))))
      : h("div", { class: "empty" }, "No runs yet. ", h("a", { href: "#/new" }, "Start one →"));

    mount(root,
      h("div", { class: "page-head" },
        h("div", {}, h("h1", {}, "Runs"), h("div", { class: "muted" }, `${data.total} in total`)),
        h("a", { class: "button", href: "#/new" }, "New research")),
      h("div", { class: "panel" }, body));

    clearTimeout(timer);
    if (data.runs.some((run) => ACTIVE.has(run.status))) timer = setTimeout(draw, 4000);
  }

  await draw();
  return () => clearTimeout(timer);
}
