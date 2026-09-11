# TODO — Apilex AI Research Agent Case

- **Teslim:** Pazartesi 14 Eylül 2026, 16:00 → muhammed.bilgin@apilex.ai
- **Mimari taslak:** [`docs/design/architecture_v0.5.md`](docs/design/architecture_v0.5.md) (önceki sürümler: `docs/design/_archive/`)
- **Case:** [`docs/reference/ai-eng-case-i.pdf`](docs/reference/ai-eng-case-i.pdf)
- **Ana ilkeler:** Kontrol akışı kodda, muhakeme LLM'de · Claim ledger merkezli · Kullanıcıya çıkmadan önce deterministik Gate · Her karar ve hata izlenebilir · Basit ama gerekçeli

Durum etiketleri: `[ ]` yapılacak · `[~]` devam ediyor · `[x]` bitti · **❓ karar bekliyor** · **✅ karar verildi** · `öneri` = onay bekleyen best-practice önerisi

---

## Karar Günlüğü

### Agent
| # | Konu | Karar / öneri | Durum |
|---|---|---|---|
| D1 | LLM provider + katmanlar | Kendi `LLMClient` arayüzü; `reasoning` + `fast`. **Gemini → OpenAI → Ollama.** Gemini'de Pydantic validation + repair retry kritik. Model ID'leri `models.yaml`'da | ✅ |
| D2 | Orchestration | LangGraph `StateGraph`; node'lar saf fonksiyon, router/termination saf Python | ✅ |
| D3 | Search | Tavily (birincil) + Brave (fallback/çeşitlilik) | ✅ |
| D4 | Dil | README + kod EN · Design Question cevapları TR · rapor = sorunun dili | ✅ |
| D6 | Claim embedding | Gemini embedding birincil; OpenAI / Ollama fallback. **Bir run içinde tek embedding modeli.** Hiçbiri yoksa lexical fallback | öneri |
| D7 | Tam içerik | Provider raw content; yoksa `httpx` + `trafilatura`; sadece top-K doküman | öneri |
| D11 | Sonlandırma parametreleri | `max_iterations=4`, `max_searches=30`, `max_wall_clock=300s`, cost cap, stagnation eşiği 2 — `settings.yaml`, UI'dan run başına override | öneri |
| D12 | Örnek sorgular (≥3) | (1) KVKK 2026 SaaS aksiyon planı (TR, regülasyon) · (2) ApilexAI ürün/partnerlik/strateji (az kaynak → gap) · (3) EU AI Act uygulama takvimindeki değişiklikler (tarih çelişkileri, EN) · (4 ops.) pazar büyüklüğü (sayısal çelişki → Gate G4 vitrini) | öneri |

### Servis & altyapı
| # | Konu | Karar / öneri | Durum |
|---|---|---|---|
| D13 | Dağıtım | Docker Compose; Python imajı (`api` / `agent` / `migrate`) + Go `dispatcher` imajı; profiller `observability` (Langfuse, `COMPOSE_PROFILES` ile varsayılan açık), `local-llm`, `optimize` | ✅ |
| D14 | Veritabanı | PostgreSQL 16: runs/kuyruk, `run_events`, LangGraph checkpoint, search cache, preset | ✅ |
| D15 | Konfig | YAML ağırlıklı (`config/*.yaml`, prompt'lar dahil); `.env` sadece secret + altyapı; YAML < env (altyapı) < UI override; run başına config snapshot + hash | ✅ |
| D16 | Worker + kuyruk | **Go dispatcher** (control plane: claim, kapasite, agent seçimi, heartbeat watchdog, hard deadline, cancel, retry; iş mantığı/secret yok) + **Python agent servisi** (data plane, N replica, `202 + heartbeat`). PG kuyruğu `SKIP LOCKED` + `LISTEN/NOTIFY`. Kontrat `contracts/` (OpenAPI, run states, error codes). Not: öneri Python worker'dı; Go'nun kazancı izolasyon + dış deadline güvencesi | ✅ (Emre) |
| D17 | Frontend | API'nin servis ettiği statik HTML + vanilla JS + SSE; build adımı yok | öneri |
| D18 | API key yönetimi | UI → `sessionStorage` → run başına Fernet-şifreli `run_secrets` → run bitince silinir; `.env` varsayılan yedek; dispatcher key görmez; key'ler log/event/trace'e asla girmez (testli) | ✅ |
| D19 | Kullanıcıya çıkış kontrolü | Deterministik Output Gate G1–G11 (`gate.yaml`), deterministik remediation, sessiz başarısızlık yok | ✅ |
| D20 | PII | Microsoft Presidio (analyzer + anonymizer). Hassas tanımlayıcılar (TCKN, IBAN, kart, telefon, e-posta, IP) her zaman maskeli; web içeriğindeki kişi/kurum adları maskelenmez; 3 sınır: intake, telemetri, çıktı | ✅ |
| D21 | Trace backend | Postgres `run_events` birincil (UI buradan) + **Langfuse self-host** (LangGraph callback, generation ↔ prompt versiyonu bağlantısı, SDK `mask` ile redaction, trace URL `runs`'ta). LangSmith elendi: self-host Enterprise lisansı gerektiriyor | ✅ (revize) |
| D22 | Logging | `structlog` (Python) + `log/slog` (Go), ortak JSON alanları, `run_id`/`span_id` korelasyonu, redaction processor | ✅ |
| D23 | Hata izlenebilirliği | `AgentError` + `ErrorCode` taksonomisi (`contracts/error_codes.yaml`, Python + Go ortak); expected (degrade + hata→karar→sonuç) vs unexpected (stack trace, event ID) | ✅ |
| D24 | UI parametre ayarı | Pydantic şeması → `/api/config/schema` → otomatik form; run başına override, preset'ler DB'de, YAML'a yazılmaz; güvenlik-kritik ayarlar UI'dan kapatılamaz | öneri |
| D25 | Reasoning akışı | Her LLM şemasında `rationale` alanı → SSE ile UI zaman çizelgesi; Gemini thought summaries opsiyonel bayrak; ham CoT gösterilmez | öneri |
| D8 | Runtime | Python 3.12 (`python:3.12-slim`), uv, FastAPI, Pydantic v2, SQLAlchemy + alembic, asyncpg/psycopg, httpx, tenacity, structlog, pytest | öneri |
| D26 | Prompt tekniği | DSPy tarzı signature'lar (Pydantic I/O + talimat + demolar) kendi runtime'ımızda; **DSPy 3.x + GEPA offline optimize job'ı** (eval set = Langfuse dataset, koşu = experiment) → Langfuse'a `candidate` versiyon; Gate ihlalleri GEPA'ya textual feedback | ✅ |
| D27 | Prompt yönetimi | **Langfuse Prompt Management** (headless init ile hazır admin) = düzenleme arayüzü; repo YAML = seed + kanonik export; app `PromptRegistry`: Langfuse → YAML fallback; output şema hash + template değişken kontrolü; run başında versiyon sabitlenir | ✅ |
| D28 | Skills | **Agent Skills** (`SKILL.md`) formatı; `analyze_query` 0–2 skill seçer (progressive disclosure); script yok; bütçe/Gate'i değiştiremez. Saklama önerisi: repo `skills/` kanonik, Langfuse'ta `skill/<name>` prompt olarak versiyon/label (aç/kapa = `production`) | ✅ (saklama: öneri) |
| D29 | Ek araçlar | promptfoo (injection/regression, COULD) · BAML ve Instructor değerlendirildi, seçilmedi (gerekçe §20.4) | öneri |
| D9 | Repo teslimi | GitHub private + reviewer davet (veya public) | ❓ (sonra) |
| D10 | Bonus kapsamı | Artık mimarinin parçası: streaming (SSE), persistent state (PG checkpoint), detailed tracing, parallel search, caching, cost tracking, citation verification, multiple search providers, LLM fallback, semantic dedup, reranking. Zaman kalırsa: mini eval set. **Yok:** human-in-the-loop | öneri |

---

## Kapsam çizgisi
- **MUST:** compose (postgres, migrate, api, dispatcher, agent, presidio×2) · Go dispatcher (dar kapsam) + PG kuyruk + watchdog + deadline · Langfuse self-host (headless init) + trace + `PromptRegistry` + seed sync · signature katmanı · 2–3 seed skill + seçim · UI'da prompt/skill özet + Langfuse linki · `run_events` + SSE · minimal UI (key, soru, parametre, canlı akış, rapor) · agent çekirdeği · Output Gate · hata taksonomisi · structlog + redaction · testler · 3 örnek
- **SHOULD:** preset'ler · cancel · crash sonrası resume · export · DSPy/GEPA ile 1 node optimizasyonu + before/after
- **COULD:** Ollama profili · thought summaries · promptfoo · skill zip import/export · Go dispatcher span'lerini Langfuse OTLP'ye
- ⚠️ Değerlendirmenin %75'i agent kalitesi. Kapsam artık 4 günün sınırında → **kesme sırası:** önce COULD, sonra SHOULD'daki optimizasyon. MUST'a dokunulmaz.

---

## Faz 0 — Tasarım · Per 10 Eyl
- [x] Case'i oku, gereksinimleri çıkar
- [x] Mimari v0.1 → v0.2 (D1–D4) → v0.3 (servis mimarisi) → v0.4 (Go dispatcher, prompt katmanı + skills) → v0.5 (Langfuse self-host: trace + prompt + dataset)
- [x] 2. tur kararlar (D16, D18, D20, D21)
- [x] 3. tur kararlar: D26, D27 (Langfuse), D28
- [ ] Onay bekleyen öneriler: D6, D7, D8, D10, D11, D12, D17, D24, D25, D29
- [ ] API key'leri temin et: Gemini, OpenAI, Tavily, Brave (Langfuse key'leri headless init ile üretilir)

## Faz 1 — Altyapı iskeleti · Per akşam → Cum öğlen
- [ ] `git init`, `.gitignore`, `uv init`, `pyproject.toml` (ruff, mypy, pytest)
- [ ] `agent/Dockerfile` (Python, 3 entrypoint) + `dispatcher/Dockerfile` (Go multi-stage → distroless) + `docker-compose.yml` (postgres, migrate, api, dispatcher, agent, presidio×2 + `observability` profili: langfuse-web, langfuse-worker, clickhouse, redis, minio; postgres init'te ayrı `langfuse` DB; headless init env'leri; healthcheck'ler)
- [ ] `contracts/`: `agent-api.openapi.yaml`, `run_states.yaml`, `error_codes.yaml`
- [ ] `config/*.yaml` + `config/schema.py` (Pydantic, `ui` metadata) + loader + override doğrulama + snapshot/hash
- [ ] `.env.example` (sadece secret + altyapı)
- [ ] alembic: `runs`, `run_secrets`, `run_events`, `llm_calls`, `search_calls`, `run_artifacts`, `search_cache`, `presets`, `prompt_versions`, `skills`, `skill_versions`, `eval_sets`, `eval_runs`
- [ ] `errors.py` — `AgentError`, `ErrorCode`
- [ ] `observability/logging.py` — structlog JSON + correlation + redaction processor
- [ ] `observability/events.py` — `run_events` writer + `NOTIFY`
- [ ] **Go dispatcher:** `queue` (claim `SKIP LOCKED`, `LISTEN` + polling), `capacity` + agent seçimi, `agentclient` (execute/cancel/healthz), `watchdog` (heartbeat kaybı → requeue, deadline → cancel → failed), `events` (`run_events`'e `node=dispatcher`), `slog` JSON, `dispatcher.yaml`
- [ ] **Python agent_server:** `POST /v1/runs/{id}/execute` (202), `/cancel`, `/healthz`, `/capacity`; heartbeat döngüsü; node sınırında cancel kontrolü
- [ ] `api` iskeleti: `/healthz`, `/readyz`, `POST /api/runs`, `GET /api/runs/{id}`
- [ ] `docker compose up` ile uçtan uca "boş run": api → PG → dispatcher → agent (dummy graph) → event'ler DB'de; agent'ı öldür → heartbeat kaybı → requeue → resume
- [ ] ⛔ Bu noktadan sonra dispatcher'a yalnızca bug fix

## Faz 2 — Provider'lar & PII · Cum
- [ ] `providers/llm` — Gemini, OpenAI, Ollama adapter; `structured.py` (Pydantic + repair retry); `chain.py` (fallback, `LLM_FALLBACK_USED`); token/cost → `llm_calls`
- [ ] `providers/search` — Tavily, Brave; normalize `SearchResult`; PG cache; timeout/retry/fallback → `search_calls`
- [ ] `pii/` — Presidio HTTP client, TR ad-hoc recognizer'lar (TCKN + checksum post-filter, TR telefon), regex-only degrade, redaction yardımcıları
- [ ] Key çözümleme: `run_secrets` (Fernet) → `.env` varsayılanı; kaynak metadata'sı
- [ ] `FakeLLM` + `FakeSearchProvider` + `FakePresidio`
- [ ] `prompting/` — `Signature`, `Predict`/`Reasoned`, registry (YAML seed → DB, aktif versiyon çözümleme, run snapshot), `skills.py` (SKILL.md parse, seçim listesi, enjeksiyon)
- [ ] Langfuse: callback handler, `mask` redaction, `trace_id` ↔ `run_id`, trace URL; `PromptRegistry` (Langfuse → YAML fallback, şema hash, template değişken kontrolü); `seed_sync` (YAML → Langfuse, skills → `skill/<name>`) + export script

## Faz 3 — Agent çekirdeği · Cum akşam → Cmt
- [ ] `intake_guard` (doğrulama, dil, PII maskeleme)
- [ ] `analyze_query` (+ skill seçimi), `plan`, `generate_queries` — signature seed YAML'ları (her şemada `rationale`)
- [ ] Seed skill'ler: `regulatory-research-tr`, `company-research`, (ops.) `market-sizing`
- [ ] L4 query dedup · L1 URL canonicalization · snippet triage + top-K fetch · L2 MinHash → `origin_cluster`
- [ ] `evaluate_sources` (skor + gerekçe)
- [ ] `extract_claims` + quote doğrulama
- [ ] `cluster_and_corroborate` (L3) · `detect_contradictions`

## Faz 4 — Döngü, sentez, Gate · Cmt
- [ ] `assess_coverage` + `termination.py` + router
- [ ] LangGraph wiring + Postgres checkpointer (`thread_id = run_id`)
- [ ] Çelişki çözüm follow-up'ı
- [ ] `synthesize` + `verify_citations` (1 kez geri bildirimle yeniden sentez)
- [ ] `gate/` — G1–G11, sayı/tarih normalizer, deterministik remediation, `gate_result.json`
- [ ] `render_report` (Summary · Key Findings · Conflicting/Uncertain · Conclusion (+Action Plan) · Known Gaps · Sources · Metadata)
- [ ] Worker'a bağla: gerçek run uçtan uca compose içinde

## Faz 5 — API & UI · Paz
- [ ] SSE `/api/runs/{id}/events` (backfill + `Last-Event-ID` + `LISTEN`)
- [ ] `/api/config/schema`, `/api/presets`, `/api/keys/validate`, `/cancel`, `/export`
- [ ] UI: Ayarlar/API key ekranı (Test butonları)
- [ ] UI: Yeni araştırma + otomatik parametre formu + preset
- [ ] UI: Canlı run görünümü (iterasyon bazlı zaman çizelgesi, rationale'lar, skorlar, kodlu hatalar, Gate checklist, rapor)
- [ ] UI: Geçmiş listesi
- [ ] UI: **Prompts & Skills** özet sayfası — aktif versiyon/label, kaynak (Langfuse/YAML), şema uyumu, "Langfuse'ta düzenle" linki (düzenleme/diff/terfi/playground Langfuse admin'de)
- [ ] API: `GET /api/prompts`, `GET /api/skills`

## Faz 6 — Güvenilirlik & observability · Paz
- [ ] Hata matrisi uçtan uca (§13.2) — her kod için tetiklenebilir senaryo
- [ ] Langfuse payload'ında secret/PII olmadığını doğrula; Langfuse kapalıyken (hafif mod) sistemin tam çalıştığını doğrula
- [ ] (SHOULD) `optimize/`: DSPy 3.x + GEPA job'ı (kilitli bağımlılıklar, ayrı profil), eval set → Langfuse dataset, koşu → experiment, `generate_queries` veya `extract_claims` için 1 optimizasyon → `candidate` + before/after metrik tablosu
- [ ] Worker crash → lease → resume denemesi
- [ ] Prompt'ları gözden geçir (TR/EN, web içeriğini "untrusted data" olarak işaretle — prompt injection)

## Faz 7 — Test (Faz 2'den itibaren sürekli)
- [ ] Unit: URL canonicalization, MinHash, query dedup, query generation
- [ ] Unit: scoring, facet yeterlilik, contradiction adayları, termination/router
- [ ] Unit: Gate G1–G11 (ayrı ayrı), sayı/tarih normalizer
- [ ] Unit: PII redaction + TCKN checksum, secret redaction, structured output repair, config override sınırları
- [ ] Senaryo: sufficient · stagnation · aynı sorgu · timeout→fallback · invalid JSON · max iteration · gate fail→remediation
- [ ] Hata izlenebilirliği: her expected hata doğru `ErrorCode`/`decision`/`outcome` üretiyor
- [ ] API: run oluşturma, SSE resume, cancel, **key sızıntısı yok**
- [ ] Go: watchdog karar tablosu (table-driven), backoff, kapasite seçimi, durum geçişleri ↔ `run_states.yaml`
- [ ] Contract: Python agent server + Go client ↔ OpenAPI + `error_codes.yaml`
- [ ] Prompt/skill: versiyon çözümleme, UI'dan şema değişmez, skill seçimi + enjeksiyon, skill bütçe/Gate'i değiştiremez, YAML round-trip
- [ ] Integration (compose PG): iki dispatcher aynı işi almıyor; agent kill → resume; deadline → cancel

## Faz 8 — Örnekler & dokümantasyon · Paz akşam
- [ ] 3+ örnek → `examples/<slug>/{input.md, trace.jsonl, report.md, gate_result.json}`
- [ ] Eşik kalibrasyonu
- [ ] README (EN): kurulum (`docker compose up`), teknolojiler, servis mimarisi + agent diyagramı (Mermaid), search strategy, source evaluation, duplicate detection, follow-up, termination, contradiction, Output Gate, PII, observability, error handling, design decisions
- [ ] README: 8 Design Question'a kısa **Türkçe** cevaplar

## Faz 9 — Teslim · Pzt sabah (son: 16:00)
- [ ] Temiz clone → `cp .env.example .env` → `docker compose up` → UI'dan run → `docker compose run api pytest` + `go test ./...`
- [ ] Secret taraması (`.env` repoda yok)
- [ ] README ↔ kod tutarlılığı son okuma
- [ ] Repo linki + kısa açıklama ile mail

---

## Değerlendirme kriterleri → nerede karşılanıyor

| Alan | Ağırlık | Karşılık (v0.4) |
|---|---|---|
| Agent Orchestration & Reasoning Flow | 25% | §5 akış, §7 sonlandırma, alt soru durum makinesi, coverage-driven follow-up |
| Search & Retrieval Strategy | 20% | §5 sorgu çeşitliliği, paralel search, triage, hedefli follow-up, §16 |
| Reliability & Edge Case Handling | 15% | §13 hata matrisi + taksonomi, §2 heartbeat watchdog + dış deadline + checkpoint resume, §11 Gate |
| LLM / Prompt Engineering | 15% | §20 signature katmanı + registry + skills + GEPA optimizasyonu, structured output + repair, claim-ledger-only sentez |
| Software Architecture & Code Quality | 15% | §2 control/data plane ayrımı + kontratlar, §3 konfig, §17 yapı, provider soyutlamaları |
| Testing & Observability | 5% | §14, §18 |
| Documentation & Design Decisions | 5% | README + bu karar günlüğü |

## Design Questions → cevabın kaynağı (v0.4)

| # | Soru | Kaynak |
|---|---|---|
| 1 | Araştırma ne zaman sonlanıyor? | §7 |
| 2 | Sonsuz search loop nasıl engelleniyor? | §7 hard limits + stagnation, §8 L4 query dedup, §2 dispatcher dış deadline |
| 3 | Kaynak güvenilirliği nasıl değerlendiriliyor? | §9 |
| 4 | Aynı haberin farklı sitelerde yayınlanması? | §8 L2 MinHash → origin cluster |
| 5 | İki güvenilir kaynak çelişirse? | §10 |
| 6 | Desteklenmeyen claim final cevaba nasıl girmiyor? | §11 (defense in depth + Output Gate G2/G4) |
| 7 | Search sayısı / latency / token dengesi? | §16, §2 paralelizm |
| 8 | Production'a taşırken ne değişir? | §19 |
