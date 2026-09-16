# AI Research Agent

From a complex natural-language question to a well-sourced, citation-verified report.

Control flow lives in code, judgement lives in the LLM. The system plans a research strategy,
searches the web in parallel, scores sources, extracts atomic claims with verbatim evidence,
detects duplicates and contradictions, decides for itself whether it knows enough — and passes the
final report through a **deterministic output gate** before a user ever sees it.

> Status: **in development.** Sections marked _TBD_ are filled in as the corresponding phase lands
> (see [`TODO.md`](TODO.md)). Design rationale: [`docs/design/architecture_v0.5.md`](docs/design/architecture_v0.5.md),
> audit + decisions: [`docs/design/analysis_v1.md`](docs/design/analysis_v1.md).

---

## 1. Quickstart

```bash
cp .env.example .env          # add your provider keys (Gemini, OpenAI, Tavily, Brave)
docker compose up             # full stack, including self-hosted Langfuse
# → UI:       http://localhost:8000
# → Langfuse: http://localhost:3000   (admin user is provisioned headlessly)
```

Lightweight mode — no Langfuse, no ClickHouse/Redis/MinIO. The system is fully functional:
traces come from Postgres, prompts from the repo YAML seeds.

```bash
COMPOSE_PROFILES= docker compose up
```

CLI (used to produce the examples and to debug without the service layer):

```bash
docker compose run --rm agent research "Your research question" --out examples/my-run
```

<!-- TBD: scaling agents (--scale agent=N), running tests, resetting the DB -->

## 2. Technologies

| Layer | Choice |
|---|---|
| Orchestration | LangGraph `StateGraph` — nodes are pure functions, router and termination are plain Python |
| LLM | Gemini → OpenAI → Ollama fallback chain, `reasoning` and `fast` tiers |
| Search | Tavily (primary) + Brave (fallback and diversity) |
| Runtime | Python 3.13, uv, FastAPI, Pydantic v2, SQLAlchemy 2 + Alembic, asyncpg, httpx, structlog |
| Control plane | Go dispatcher — queue claim, capacity, heartbeat watchdog, hard deadline, cancel, retry |
| Storage | PostgreSQL 16 — run queue, event store, LangGraph checkpoints, search cache, presets |
| PII | Microsoft Presidio (analyzer + anonymizer) |
| Observability | Postgres `run_events` (primary) + self-hosted Langfuse (traces, prompts, cost, datasets) |
| Frontend | Static HTML + vanilla JS + SSE, no build step |

## 3. Architecture

### 3.1 Services

<!-- TBD: Mermaid service diagram — port from architecture_v0.5.md §2 -->

### 3.2 Agent workflow

<!-- TBD: Mermaid agent flow — port from architecture_v0.5.md §5 -->

### 3.3 The central abstraction: the claim ledger

<!-- TBD: SearchResult → Document → Claim (+verbatim quote) → ClaimCluster → Finding, and how one
     data model answers duplicate detection, corroboration, contradiction and citation at once -->

## 4. Search strategy

<!-- TBD: sub-question decomposition, query diversity (official/news, TR+EN, broad/specific),
     parallel execution, caching, snippet-first triage, targeted follow-up queries -->

## 5. Source evaluation

<!-- TBD: weighted score (authority · primary · recency · relevance), domain tiers, why the
     components and the one-line rationale are written to the trace -->

## 6. Duplicate detection

<!-- TBD: the four layers (URL canonicalization, MinHash near-dup, claim embedding, query dedup)
     and the core rule: corroboration counts independent origins, not URLs -->

## 7. Follow-up search and termination

<!-- TBD: facet sufficiency rule, coverage assessment, the four termination rules
     (hard limits, success, stagnation, query exhaustion), stop_reason surfaced in the report -->

## 8. Contradiction handling

<!-- TBD: rule-based candidates + LLM judge, classification, resolution, and what happens when a
     conflict cannot be resolved -->

## 9. Output gate

<!-- TBD: why it is deterministic, rules G1–G11, the three G4 buckets, remediation, and why there
     is no silent failure -->

## 10. PII handling

<!-- TBD: the three boundaries (intake, telemetry, output), what is masked and what deliberately
     is not, and the degraded regex-only mode -->

## 11. Observability

<!-- TBD: run_events as the primary store, trace format, the case's [Planner]/[Search] log lines,
     Langfuse traces + prompt versions + cost dashboard, reproducibility (config hash) -->

## 12. Error handling

<!-- TBD: AgentError taxonomy, expected vs unexpected, the error → decision → outcome chain,
     the error matrix -->

## 13. Examples

| # | Question | Why this one |
|---|---|---|
| 1 | KVKK 2026 action plan for Turkish SaaS companies | Regulatory research in Turkish, produces a recommendation-style action plan |
| 2 | ApilexAI products, partnerships and strategic direction | Thin source coverage → exercises the "known gaps" path |
| 3 | Changes to the EU AI Act implementation timeline | English, conflicting dates → contradiction handling |
| 4 | Market size estimate | Numeric conflict → gate rule G4 and the "conflicting / uncertain" section |

Each example ships as `examples/<slug>/{input.md, trace.jsonl, report.md, gate_result.json}`.

## 14. Tests

<!-- TBD: how to run them; unit / scenario / api / contract / integration layers and what each covers -->

## 15. Design decisions

<!-- TBD: the short version of the decision log — the handful of choices worth defending:
     control flow in code, claim ledger, deterministic gate, control/data plane split,
     prompts as versioned artifacts, DSPy offline only -->

Full decision log with rationale: [`TODO.md`](TODO.md) · [`docs/design/analysis_v1.md`](docs/design/analysis_v1.md)

---

## 16. Design Questions

> Bu bölümün cevapları Türkçedir (case gereği).

### 1. Agent araştırmayı ne zaman sonlandırıyor?

_TBD_

### 2. Sistemin sonsuz search loop'una girmesini nasıl engelliyorsunuz?

_TBD_

### 3. Bir kaynağın güvenilirliğini nasıl değerlendiriyorsunuz?

_TBD_

### 4. Aynı haberin farklı sitelerde yayınlanmasını nasıl duplicate olarak tespit ediyorsunuz?

_TBD_

### 5. İki güvenilir kaynak birbiriyle çelişiyorsa sistem nasıl davranıyor?

_TBD_

### 6. LLM tarafından üretilmiş fakat hiçbir kaynak tarafından desteklenmeyen bir claim'in final cevaba girmesini nasıl engelliyorsunuz?

_TBD_

### 7. Search sayısı, latency ve LLM token maliyeti arasında nasıl bir denge kuruyorsunuz?

_TBD_

### 8. Bu sistemi production ortamına taşımanız gerekse hangi parçaları değiştirir veya geliştirirdiniz?

_TBD_
