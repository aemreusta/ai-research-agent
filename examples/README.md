# Examples

Each example is one run of `research run`, written as:

| File | Content |
|---|---|
| `input.md` | The question, date, providers, overrides and config hash |
| `trace.jsonl` | The full run timeline - every node, decision, rationale, coded error and LLM/search call |
| `report.md` | The report as a user sees it, after the output gate |
| `report.json` | The same report, structured, with findings and source numbers |
| `gate_result.json` | The verdict and each of the rules G1-G11: what was found, what was fixed |
| `state.json` | The claim ledger: plan, queries, documents and scores, claims with quotes, findings |

## The four case examples

The committed runs were started through the API (the same path as the UI) with Gemini
(`gemini-3.8-flash` / `gemini-3.1-flash-lite`) and Tavily; each `input.md` records the run id,
models, config hash and outcome. The UI export writes `report.md`, `trace.jsonl`,
`gate_result.json` and `state.json` (the structured report is inside `state.json`).
See [`ANALYSIS.md`](ANALYSIS.md) for what they show and what they changed in the code.

To regenerate them with your own keys:

```bash
cp .env.example .env     # add GEMINI_API_KEY (or OPENAI_API_KEY) and TAVILY_API_KEY (or BRAVE_API_KEY)
docker compose up -d     # the examples use the same image
make examples            # writes examples/<slug>/ for each question below
```

| Slug | Question | What it exercises |
|---|---|---|
| `kvkk-2026-saas-action-plan` | Türkiye'deki SaaS şirketleri için 2026 KVKK uyum aksiyon planı nedir? | Turkish regulatory research, the `regulatory-research-tr` skill, recommendations tied to findings (G11) |
| `apilexai-products-partnerships` | ApilexAI'ın ürünleri, iş ortaklıkları ve stratejik yönü nedir? | Thin coverage, syndicated press releases, Known Gaps |
| `eu-ai-act-timeline` | What changed in the EU AI Act implementation timeline? | English, conflicting dates, contradiction handling |
| `legal-tech-market-size` | What is the size of the European legal tech market and how fast is it growing? | Numeric disagreement, the `market-sizing` skill, gate rule G4 |

Runs started from the UI can be exported in the same format from the run page.

## `offline-demo-eu-ai-act`

Produced with `research run --simulate`: a rule-based stand-in model and a three-page built-in
corpus, no keys, zero cost. It is **not** a research result - it exists to show the file format
and the pipeline end to end (a contradiction found, one targeted follow-up round, gate passed).
