// #/runs/:id - the live run: timeline over SSE, then the report, gate checklist and ledger.

import {
  ACTIVE, api, clock, duration, gateBadge, h, money, mount, number, statusBadge, toast, truncate,
} from "../lib.js";
import { renderMarkdown } from "../markdown.js";

const NODE_LABELS = {
  intake_guard: "intake", analyze_query: "analyze", plan: "plan", generate_queries: "queries",
  search: "search", process_results: "sources", evaluate_sources: "evaluate",
  extract_claims: "extract", cluster_and_corroborate: "ledger", detect_contradictions: "conflicts",
  assess_coverage: "coverage", synthesize: "synthesize", verify_citations: "verify",
  output_gate: "gate", dispatcher: "dispatcher", agent: "agent", prompts: "prompts",
  check_plan: "plan checks", review_evidence: "heuristics",
};

export async function render(root, runId) {
  const state = { run: null, events: [], ledger: null, tab: "timeline", showDebug: false, errorsOnly: false };
  let source = null;
  let poll = null;

  const header = h("div");
  const tabs = h("div", { class: "tabs" });
  const content = h("div");

  async function loadRun() {
    state.run = await api(`/api/runs/${runId}`);
    if (!ACTIVE.has(state.run.status) && !state.ledger) {
      state.ledger = await api(`/api/runs/${runId}/ledger`).catch(() => null);
      if (state.tab === "timeline" && state.run.report_md && !state.visitedReport) {
        state.tab = "report";
        state.visitedReport = true;
      }
    }
    drawHeader();
    drawTabs();
    drawContent();
  }

  function elapsed(run) {
    const start = run.created_at && new Date(run.created_at).getTime();
    const end = run.finished_at ? new Date(run.finished_at).getTime() : Date.now();
    return start ? (end - start) / 1000 : null;
  }

  function drawHeader() {
    const run = state.run;
    const active = ACTIVE.has(run.status);
    const cancel = active ? h("button", { class: "danger small", onclick: async () => {
      try { await api(`/api/runs/${runId}/cancel`, { method: "POST" }); toast("Cancellation requested."); loadRun(); }
      catch (error) { toast(error.body?.message || error.message, "bad"); }
    } }, "Cancel") : null;
    const exports = run.artifacts.length ? h("div", { class: "row" },
      ...[["report", "report.md"], ["report_json", "report.json"], ["trace", "trace.jsonl"], ["gate", "gate_result.json"], ["state", "state.json"]]
        .map(([artifact, label]) => h("a", { class: "button ghost small", href: `/api/runs/${runId}/export?artifact=${artifact}` }, label))) : null;

    mount(header,
      h("div", { class: "panel run-head" },
        h("div", { class: "page-head", style: "margin-bottom:0" },
          h("div", { style: "min-width:0" },
            h("div", { class: "row", style: "margin-bottom:6px" }, statusBadge(run.status), gateBadge(run.gate_status),
              run.stop_reason ? h("span", { class: "badge violet" }, `stop: ${run.stop_reason}`) : null,
              run.attempts > 1 ? h("span", { class: "badge warn" }, `attempt ${run.attempts}`) : null,
              run.simulated ? h("span", { class: "badge violet", title: "Rule-based stand-in model over a built-in corpus - not a research result" }, "offline demo") : null,
              ...(run.skills_used || []).map((skill) => h("span", { class: "badge info" }, `skill: ${skill}`))),
            h("h1", {}, run.question)),
          h("div", { class: "row" }, cancel,
            run.langfuse_trace_url ? h("a", { class: "button ghost small", href: run.langfuse_trace_url, target: "_blank", rel: "noopener" }, "Langfuse trace ↗") : null)),
        run.error_message ? h("div", { class: "banner bad", style: "margin-top:12px" }, h("span", { class: "code" }, run.error_code), run.error_message,
          run.error_code === "LLM_AUTH" || run.error_code === "SEARCH_AUTH" ? h("span", {}, " ", h("a", { href: "#/settings" }, "Open Settings →")) : null) : null,
        h("div", { class: "muted small", style: "margin-top:10px" },
          `Requested: planning ${run.config_snapshot?.llm?.reasoning_model || "automatic"} · extraction ${run.config_snapshot?.llm?.fast_model || "automatic"} · fallback ${run.config_snapshot?.llm?.allow_fallback === false ? "off" : "on"}`),
        Object.keys(run.models_used || {}).length ? h("div", { class: "muted small" },
          `Used: ${Object.entries(run.models_used).map(([tier, model]) => `${tier}: ${model}`).join(" · ")}`) : null,
        h("div", { class: "meters" },
          meter("Round", run.iteration),
          meter("Searches", `${run.searches_used} / ${run.config_snapshot?.budget?.max_searches ?? "?"}`),
          meter("Tokens in / out", `${number(run.tokens_in)} / ${number(run.tokens_out)}`),
          meter("Cost", money(run.cost_usd)),
          meter("Elapsed", duration(elapsed(run))),
          meter("Agent", run.agent_id ? truncate(run.agent_id, 14) : "–"),
        ),
        h("div", { class: "row", style: "margin-top:12px; justify-content:space-between" },
          h("span", { class: "muted small mono", title: "config hash" }, `config ${run.config_hash.slice(0, 12)} · run ${runId.slice(0, 8)}`),
          exports)));
  }

  function meter(label, value) {
    return h("div", { class: "meter" }, h("div", { class: "label" }, label), h("div", { class: "value" }, value));
  }

  function drawTabs() {
    const ledger = state.ledger;
    const items = [
      ["report", "Report", null],
      ["timeline", "Timeline", state.events.length],
      ["gate", "Gate", null],
      ["heuristics", "Heuristic checks", ledger?.heuristic_checks?.length],
      ["plan", "Plan & queries", ledger?.queries?.length],
      ["sources", "Sources", ledger?.documents?.length],
      ["findings", "Findings", ledger?.clusters?.length],
    ];
    mount(tabs, ...items.map(([key, label, count]) => h("button", {
      class: state.tab === key ? "active" : "",
      onclick: () => { state.tab = key; drawTabs(); drawContent(); },
    }, label, count ? h("span", { class: "count" }, count) : null)));
  }

  function drawContent() {
    const view = { report, timeline, gate, heuristics, plan, sources, findings }[state.tab];
    mount(content, view());
  }

  // --- tabs -------------------------------------------------------------------------------------

  function heuristics() {
    const checks = state.ledger?.heuristic_checks || state.events
      .filter((event) => event.data?.heuristic).map((event) => event.data.heuristic);
    return h("div", { class: "panel" }, h("h2", {}, "Heuristic checks"),
      h("p", { class: "muted" }, "Deterministic review of the plan, evidence and report. Warnings identify limitations; a pass does not certify factual accuracy. Report checks run before gate remediation."),
      checks.length ? checks.map((check) => h("div", { class: "check" },
        h("strong", {}, check.id), h("span", { class: `badge ${check.status === "pass" ? "ok" : "warn"}` }, check.status),
        h("div", {}, h("strong", {}, check.name), h("div", {}, check.summary),
          check.related_ids?.length ? h("details", {}, h("summary", {}, "Affected evidence"),
            h("div", { class: "mono small" }, check.related_ids.join(", "))) : null)))
        : h("div", { class: "empty" }, ACTIVE.has(state.run.status) ? "Checks appear as research progresses." : "This run predates heuristic checks."));
  }

  function report() {
    const run = state.run;
    if (!run.report_md) {
      return h("div", { class: "panel empty" }, ACTIVE.has(run.status)
        ? [h("span", { class: "spinner" }), " The report appears here when the run finishes. Follow the timeline meanwhile."]
        : "This run produced no report.");
    }
    const body = h("div", { class: "report" });
    body.innerHTML = renderMarkdown(run.report_md);  // escaped by the renderer
    return h("div", { class: "panel" }, body);
  }

  function timeline() {
    const visible = state.events.filter((event) =>
      (state.showDebug || event.level !== "debug") && (!state.errorsOnly || event.error_code));
    const groups = [];
    for (const event of visible) {
      const key = event.iteration ? `Round ${event.iteration}` : (groups.length && groups.at(-1).key.startsWith("Round") ? "Report" : "Setup");
      if (!groups.length || groups.at(-1).key !== key) groups.push({ key, events: [] });
      groups.at(-1).events.push(event);
    }
    const filters = h("div", { class: "filters" },
      h("label", { class: "inline" }, h("input", { type: "checkbox", checked: state.errorsOnly, onchange: (e) => { state.errorsOnly = e.target.checked; drawContent(); } }), "coded errors only"),
      h("label", { class: "inline" }, h("input", { type: "checkbox", checked: state.showDebug, onchange: (e) => { state.showDebug = e.target.checked; drawContent(); } }), "show debug (LLM calls, node spans)"),
      ACTIVE.has(state.run.status) ? h("span", { class: "badge info live" }, "live") : null);
    return h("div", { class: "panel" }, filters,
      visible.length ? h("div", { class: "timeline" }, ...groups.map((group) => [
        h("div", { class: "round" }, group.key),
        ...group.events.map(eventRow),
      ])) : h("div", { class: "empty" }, ACTIVE.has(state.run.status) ? "Waiting for the first event…" : "No events."));
  }

  function eventRow(event) {
    const data = event.data || {};
    const rationale = data.rationale;
    const extra = Object.keys(data).filter((k) => !["display", "rationale"].includes(k));
    return h("div", { class: `event ${event.level}` },
      h("div", { class: "time" }, clock(event.ts)),
      h("div", { class: `node ${event.node}` }, NODE_LABELS[event.node] || event.node),
      h("div", {},
        h("div", { class: "text" },
          event.error_code ? h("span", { class: `code ${event.expected ? "expected" : ""}` }, event.error_code) : null,
          data.display || event.message,
          event.cost_usd ? h("span", { class: "muted small" }, `  ${money(event.cost_usd)}`) : null),
        rationale ? h("div", { class: "rationale" }, rationale) : null,
        extra.length ? h("details", {}, h("summary", {}, "details"),
          h("pre", {}, JSON.stringify(Object.fromEntries(extra.map((k) => [k, data[k]])), null, 2))) : null));
  }

  function gate() {
    const result = state.run.gate_result;
    if (!result) return h("div", { class: "panel empty" }, "The gate runs after synthesis.");
    const status = { pass: ["ok", "pass"], remediated: ["info", "remediated"], warn: ["warn", "warning"], fail: ["bad", "fail"] };
    return h("div", { class: "panel" },
      h("div", { class: "panel-head" },
        h("h2", {}, "Output gate"),
        h("div", { class: "row" }, gateBadge(result.verdict),
          h("span", { class: "badge" }, `${result.removed_sentences} sentence(s) removed`),
          h("span", { class: "badge" }, `${result.rounds} remediation round(s)`))),
      result.banner ? h("div", { class: "banner bad" }, result.banner) : null,
      h("p", { class: "muted small" }, "Deterministic checks - no LLM involved. The same report and ledger always produce the same verdict."),
      ...Object.entries(result.checks).map(([rule, check]) => {
        const [kind, label] = status[check.status] || ["", check.status];
        const issues = [...check.found.map((v) => ({ ...v, stage: "found" })), ...check.remaining.map((v) => ({ ...v, stage: "remaining" }))];
        return h("div", { class: "check" },
          h("div", { class: "rule" }, rule),
          h("div", {}, h("span", { class: `badge ${kind}` }, label)),
          h("div", {}, check.description,
            issues.length ? h("ul", {}, ...issues.slice(0, 12).map((v) => h("li", {},
              `${v.stage === "remaining" ? "still: " : ""}${v.message}`,
              v.bucket ? h("span", { class: "badge small", style: "margin-left:6px" }, v.bucket) : null,
              v.text ? h("div", { class: "muted" }, `“${truncate(v.text, 140)}”`) : null))) : null));
      }),
      result.remediations?.length ? h("details", {}, h("summary", {}, `${result.remediations.length} remediation step(s)`),
        h("pre", {}, JSON.stringify(result.remediations, null, 2))) : null);
  }

  function needLedger() {
    return h("div", { class: "panel empty" }, ACTIVE.has(state.run.status)
      ? "Available when the run finishes. The timeline shows progress live."
      : "No ledger was stored for this run.");
  }

  function plan() {
    const ledger = state.ledger;
    if (!ledger) return needLedger();
    const statusKind = { sufficient: "ok", exhausted: "bad", searching: "info", pending: "" };
    return h("div", {},
      h("div", { class: "panel" },
        h("div", { class: "panel-head" }, h("h2", {}, "Plan"),
          h("span", { class: "badge violet" }, `stop: ${ledger.stop_reason} - ${ledger.stop_detail}`)),
        ledger.analysis?.rationale ? h("p", { class: "muted" }, ledger.analysis.rationale) : null,
        ...(ledger.plan || []).map((subq) => h("div", { style: "margin-bottom:12px" },
          h("div", { class: "row" }, h("strong", {}, `${subq.id}.`), subq.text,
            h("span", { class: `badge ${statusKind[subq.status] || ""}` }, subq.status),
            h("span", { class: "badge" }, subq.priority)),
          h("div", { class: "row", style: "margin-top:4px" }, ...subq.facets.map((facet) =>
            h("span", { class: `badge ${facet.status === "sufficient" ? "ok" : facet.status === "contested" ? "warn" : ""}`, title: facet.description }, facet.name))),
          subq.exhausted_reason ? h("div", { class: "muted small" }, subq.exhausted_reason) : null))),
      h("div", { class: "panel" }, h("h2", {}, "Queries"),
        h("div", { class: "table-wrap" }, h("table", {},
          h("thead", {}, h("tr", {}, h("th", {}, "Round"), h("th", {}, "Query"), h("th", {}, "For"), h("th", {}, "Purpose"),
            h("th", {}, "Provider"), h("th", { class: "num" }, "Results"), h("th", {}, "Status"))),
          h("tbody", {}, ...(ledger.queries || []).map((q) => h("tr", {},
            h("td", {}, q.iteration), h("td", {}, q.text, q.rationale ? h("div", { class: "muted small" }, q.rationale) : null),
            h("td", {}, q.subq_id, q.facet_id ? h("span", { class: "muted" }, ` / ${q.facet_id}`) : null),
            h("td", {}, q.purpose), h("td", {}, q.provider || "–", q.cache_hit ? h("span", { class: "badge small" }, "cache") : null),
            h("td", { class: "num" }, q.result_count),
            h("td", {}, h("span", { class: `badge ${q.status === "done" ? "ok" : q.status === "failed" ? "bad" : "warn"}` }, q.status),
              q.error_code ? h("div", { class: "code" }, q.error_code) : null))))))));
  }

  function sources() {
    const ledger = state.ledger;
    if (!ledger) return needLedger();
    const origins = {};
    for (const doc of ledger.documents) origins[doc.origin_id] = (origins[doc.origin_id] || 0) + 1;
    return h("div", { class: "panel" },
      h("div", { class: "panel-head" }, h("h2", {}, "Sources"),
        h("span", { class: "muted small" }, "score = 0.35·authority + 0.25·primary + 0.15·recency + 0.25·relevance")),
      h("div", { class: "table-wrap" }, h("table", {},
        h("thead", {}, h("tr", {}, h("th", {}, "Source"), h("th", { class: "num" }, "Score"), h("th", {}, "Components"),
          h("th", {}, "Origin"), h("th", {}, "Read"))),
        h("tbody", {}, ...ledger.documents.map((doc) => {
          const s = doc.score || {};
          return h("tr", {},
            h("td", {}, h("a", { href: doc.url, target: "_blank", rel: "noopener" }, truncate(doc.title || doc.url, 70)),
              h("div", { class: "muted small" }, doc.domain, doc.published_at ? ` · ${doc.published_at}` : "",
                s.rationale ? ` · ${truncate(s.rationale.split(" - ").slice(1).join(" - "), 60)}` : "")),
            h("td", { class: "num" }, h("strong", {}, (s.total ?? 0).toFixed(2)),
              h("div", { class: "bar" }, h("span", { style: `width:${Math.round((s.total || 0) * 100)}%` }))),
            h("td", {}, h("div", { class: "score" },
              h("span", {}, `T${s.tier}`), h("span", {}, s.is_primary ? "primary" : "secondary"),
              h("span", {}, `auth ${(s.authority ?? 0).toFixed(2)}`), h("span", {}, `rec ${(s.recency ?? 0).toFixed(2)}`),
              h("span", {}, `rel ${(s.relevance ?? 0).toFixed(2)}`))),
            h("td", {}, h("span", { class: "mono small" }, doc.origin_id),
              origins[doc.origin_id] > 1 ? h("span", { class: "badge warn small", style: "margin-left:4px" }, `${origins[doc.origin_id]} copies`) : null),
            h("td", {}, doc.extracted ? h("span", { class: "badge ok" }, "extracted")
              : doc.fetch_error ? h("span", { class: "badge warn", title: doc.fetch_error }, "snippet only")
              : h("span", { class: "badge" }, "triaged out")));
        })))));
  }

  function findings() {
    const ledger = state.ledger;
    if (!ledger) return needLedger();
    const kinds = { supported: "ok", single_source: "warn", contested: "bad" };
    const claimsById = Object.fromEntries(ledger.claims.map((c) => [c.id, c]));
    const clusters = [...ledger.clusters].sort((a, b) => b.confidence - a.confidence);
    return h("div", {},
      (ledger.contradictions || []).length ? h("div", { class: "panel" }, h("h2", {}, "Contradictions"),
        ...ledger.contradictions.map((x) => h("div", { style: "margin-bottom:10px" },
          h("div", { class: "row" }, h("span", { class: `badge ${x.kind === "true_conflict" ? "bad" : "info"}` }, x.kind.replaceAll("_", " ")),
            x.resolved ? h("span", { class: "badge ok" }, x.preferred_cluster_id ? `prefer ${x.preferred_cluster_id}` : "resolved") : h("span", { class: "badge warn" }, "unresolved"),
            h("span", { class: "muted small" }, x.cluster_ids.join(" vs "))),
          h("div", {}, x.summary), x.rationale ? h("div", { class: "muted small" }, x.rationale) : null))) : null,
      h("div", { class: "panel" },
        h("div", { class: "panel-head" }, h("h2", {}, "Findings (claim ledger)"),
          h("span", { class: "muted small" }, `${ledger.claims.length} candidate claims → ${clusters.length} findings`)),
        ...clusters.map((cluster) => h("details", { style: "border-bottom:1px solid var(--line); padding:8px 0" },
          h("summary", {},
            h("span", { class: "mono small" }, cluster.id), " ",
            h("span", { class: `badge ${kinds[cluster.status] || ""}` }, cluster.status.replaceAll("_", " ")), " ",
            h("span", { class: "badge" }, `${cluster.origin_ids.length} origin(s)`), " ",
            h("span", { class: "badge" }, `conf ${cluster.confidence.toFixed(2)}`), " ",
            cluster.requires_fresh_confirmation ? h("span", { class: "badge warn" }, "needs current evidence") : null,
            cluster.statement),
          h("ul", {}, ...cluster.claim_ids.map((id) => claimsById[id]).filter(Boolean).map((claim) => h("li", {},
            h("div", {}, claim.text),
            h("div", { class: "muted small" }, `${claim.validation_status || "legacy"} · ${claim.freshness || "date not checked"}`),
            (claim.conditions || []).length ? h("div", { class: "small" }, `Applies when: ${claim.conditions.join("; ")}`) : null,
            h("div", { class: "muted small" }, `“${truncate(claim.quote, 220)}” - ${claim.doc_id}`,
              claim.attributed_to ? ` (according to ${claim.attributed_to})` : ""))))))));
  }

  // --- wiring --------------------------------------------------------------------------------------

  mount(root, header, tabs, content);
  await loadRun();

  const seen = new Set();
  source = new EventSource(`/api/runs/${runId}/events`);
  source.addEventListener("run_event", (message) => {
    const event = JSON.parse(message.data);
    if (seen.has(event.seq)) return;
    seen.add(event.seq);
    state.events.push(event);
    if (state.tab === "timeline") {
      drawTabs();
      drawContent();
    }
  });
  source.addEventListener("run_closed", () => {
    source.close();
    loadRun();
  });
  source.onerror = () => { /* EventSource reconnects with Last-Event-ID on its own */ };

  poll = setInterval(() => {
    if (state.run && ACTIVE.has(state.run.status)) loadRun().catch(() => {});
  }, 3000);

  return () => {
    source?.close();
    clearInterval(poll);
  };
}
