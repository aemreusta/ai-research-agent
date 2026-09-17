# Examples

The live examples are actual provider runs; `offline-demo-eu-ai-act` is explicitly simulated.
The folders hold these files:

| File | Content |
|---|---|
| `input.md` | The question, date, providers, models, overrides and config hash |
| `trace.jsonl` | The full run timeline - every node, decision, rationale, coded error and LLM/search call |
| `report.md` | The report as a user sees it, after the output gate |
| `report.json` | The same report, structured, with findings and source numbers |
| `gate_result.json` | The verdict and each of the rules G1-G12: what was found, what was fixed |
| `state.json` / `state.json.gz` | The claim ledger: plan, queries, documents and scores, claims with quotes, findings |

`state.json.gz` is a lossless gzip of `state.json`, used for the larger snapshots (`gzip -dc` to read it).

## Where to start

| Folder | What it shows |
|---|---|
| [`2026-09-17-final/`](2026-09-17-final/) | **Start here.** Eight distinct questions (the four case questions plus a Turkish/English GDPR–KVKK comparison, a PostgreSQL version comparison, a revenue comparison and a made-up company); these retained runs predate the later legal-authority and Turkish-letter refinements |
| [`2026-09-17-legal-authority/`](2026-09-17-legal-authority/) | EU AI Act and KVKK rerun after the rule that legal obligations need a primary source |
| [`2026-09-17-model-selection/`](2026-09-17-model-selection/) | A run started from the browser with Gemini 3.1 Pro and provider fallback switched off |
| [`2026-09-17-legal-final/`](2026-09-17-legal-final/), [`2026-09-17/`](2026-09-17/) | Earlier iterations, kept with their failures |
| `apilexai-products-partnerships/`, `eu-ai-act-timeline/`, `kvkk-2026-saas-action-plan/`, `legal-tech-market-size/` | The first live runs (2026-09-16); [`ANALYSIS.md`](ANALYSIS.md) lists the eight defects they exposed and the fixes |
| [`offline-demo-eu-ai-act/`](offline-demo-eu-ai-act/) | An offline run with no keys (see below) |

[`2026-09-17-RESULTS.md`](2026-09-17-RESULTS.md) has, for every batch:

- the duration, LLM cost, number of searches, gate verdict and stop reason of each run;
- what was still wrong in each report.

Keep in mind when reading the reports:

- **A gate pass means the report is structurally sound** (citations, numbers, labels), **not that it is true.** The results file names the reports that are cited correctly but still reach too broad a conclusion.
- **The made-up company is supposed to fail.** Its `fail` verdict with stop reason `no_evidence` is the intended outcome: the agent declines to invent an answer.
- **Folder names are iteration labels.** "final" does not mean certified.
- **Some reports predate later fixes.** Reports written before the check for Turkish letters can contain ASCII-only Turkish (for example the ApilexAI report in `2026-09-17-final`); these reports are not edited after the fact.

## The case questions

| Slug | Question | What it exercises |
|---|---|---|
| `kvkk-2026-saas-action-plan` | Türkiye'deki SaaS şirketleri için 2026 KVKK uyum aksiyon planı nedir? | Turkish regulatory research, the `regulatory-research-tr` skill, recommendations tied to findings (G11) |
| `apilexai-products-partnerships` | ApilexAI'ın ürünleri, iş ortaklıkları ve stratejik yönü nedir? | Thin coverage, syndicated press releases, Known Gaps |
| `eu-ai-act-timeline` | What changed in the EU AI Act implementation timeline? | English, conflicting dates, contradiction handling |
| `legal-tech-market-size` | What is the size of the European legal tech market and how fast is it growing? | Numeric disagreement, the `market-sizing` skill, gate rule G4 |

Direct links to the required input, agent trace and final output (recorded repeat batch):

| Example | Input | Agent trace | Final output |
|---|---|---|---|
| KVKK action plan | [input](2026-09-17-final/kvkk-2026-saas-action-plan/input.md) | [trace](2026-09-17-final/kvkk-2026-saas-action-plan/trace.jsonl) | [report](2026-09-17-final/kvkk-2026-saas-action-plan/report.md) |
| ApilexAI company research | [input](2026-09-17-final/apilexai-products-partnerships/input.md) | [trace](2026-09-17-final/apilexai-products-partnerships/trace.jsonl) | [report](2026-09-17-final/apilexai-products-partnerships/report.md) |
| EU AI Act timeline | [input](2026-09-17-final/eu-ai-act-timeline/input.md) | [trace](2026-09-17-final/eu-ai-act-timeline/trace.jsonl) | [report](2026-09-17-final/eu-ai-act-timeline/report.md) |
| European legal tech market | [input](2026-09-17-final/legal-tech-market-size/input.md) | [trace](2026-09-17-final/legal-tech-market-size/trace.jsonl) | [report](2026-09-17-final/legal-tech-market-size/report.md) |

The newer legal reruns and their remaining interpretation errors are listed in
[the results index](2026-09-17-RESULTS.md). These exports are historical evidence, not freshly
regenerated outputs from the final code revision.

To regenerate them with your own keys:

```bash
cp .env.example .env     # add GEMINI_API_KEY (or OPENAI_API_KEY) and TAVILY_API_KEY (or BRAVE_API_KEY)
docker compose up -d     # the examples use the same image
make examples            # writes examples/<slug>/ for each question above
```

You can also export a run started from the UI, in the same format, from its run page.

## `offline-demo-eu-ai-act`

This folder was made with `research run --simulate`:

- a rule-based stand-in model instead of an LLM;
- a built-in corpus of three pages instead of web search;
- no keys and no cost.

It is **not** a research result. It shows the file format and the whole pipeline end to end: a contradiction is found, one targeted follow-up round runs, and the gate passes.
