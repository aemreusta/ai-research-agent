# TODO — Apilex AI Research Agent Case

- **Teslim:** planlanan tarih (Pzt 14 Eyl 2026) geçti. **Takvim serbest** — kapsam daraltılmıyor, sistem bütün olarak yazılıyor. Alıcı: muhammed.bilgin@apilex.ai (D31)
- **Mimari taslak:** [`docs/design/architecture_v0.6.md`](docs/design/architecture_v0.6.md) (önceki sürümler: `docs/design/_archive/`) · **Denetim + karar kapanışı:** [`docs/design/analysis_v1.md`](docs/design/analysis_v1.md)
- **Case:** `docs/reference/ai-eng-case-i.pdf` — Apilex telifli, **repoda tutulmuyor** (gitignore, D31); lokalde durur
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
| D6 | Claim embedding | Gemini embedding birincil; OpenAI / Ollama fallback. **Bir run içinde tek embedding modeli** (`runs.models_used`'a yazılır). L3 eşiği: cosine ≥ τ **ve** aynı normalize entity. Provider yoksa lexical fallback (token Jaccard + entity eşleşmesi). Embedding cache: `sha256(model+text)` → Postgres | ✅ |
| D7 | Tam içerik | Provider raw content → yoksa `httpx` + `trafilatura`. Yalnızca snippet triage'ı geçen top-K (tur 1: K=5, follow-up: K=3); timeout 10 sn + 1 retry; içerik boyutu cap'li; başarısızsa snippet ile devam (`FETCH_FAILED`) | ✅ |
| D11 | Sonlandırma parametreleri | `max_iterations=4`, **`max_searches=45`**, stagnation eşiği 2. **Tur genişliği:** tur 1 → açık alt soru başına 2–3 sorgu; tur ≥2 → eksik facet / çözülmemiş çelişki başına 1 sorgu, tur başına ≤8. **`max_wall_clock=null` ve `max_cost_usd=null` — kapalı başlar**, parametrik (`settings.yaml` + UI). Sayaçlar (token/maliyet/süre) her zaman çalışır ve UI'da canlı | ✅ |
| D12 | Örnek sorgular (4) | (1) KVKK 2026 SaaS aksiyon planı — TR, regülasyon, Recommendation/G11 vitrini · (2) ApilexAI ürün/partnerlik/strateji — case'in kendi örneği, az kaynak → Known Gaps · (3) EU AI Act uygulama takvimi — EN, tarih çelişkisi · (4) pazar büyüklüğü — sayısal çelişki → G4 + Conflicting | ✅ |

### Servis & altyapı
| # | Konu | Karar / öneri | Durum |
|---|---|---|---|
| D13 | Dağıtım | Docker Compose; Python imajı (`api` / `agent` / `migrate`) + Go `dispatcher` imajı; profiller `observability` (Langfuse, `COMPOSE_PROFILES` ile varsayılan açık), `local-llm`, `optimize` | ✅ |
| D14 | Veritabanı | PostgreSQL 16: runs/kuyruk, `run_events`, LangGraph checkpoint, search cache, preset | ✅ |
| D15 | Konfig | YAML ağırlıklı (`config/*.yaml`, prompt'lar dahil); `.env` sadece secret + altyapı; YAML < env (altyapı) < UI override; run başına config snapshot + hash | ✅ |
| D16 | Worker + kuyruk | **Go dispatcher** (control plane: claim, kapasite, agent seçimi, heartbeat watchdog, hard deadline, cancel, retry; iş mantığı/secret yok) + **Python agent servisi** (data plane, N replica, `202 + heartbeat`). PG kuyruğu `SKIP LOCKED` + `LISTEN/NOTIFY`. Kontrat `contracts/` (OpenAPI, run states, error codes). Not: öneri Python worker'dı; Go'nun kazancı izolasyon + dış deadline güvencesi | ✅ (Emre) |
| D17 | Frontend | API'nin servis ettiği statik HTML + vanilla JS + SSE, build adımı yok. **Çok ekranlı** hash router: `#/new` · `#/runs` · `#/runs/:id` (canlı timeline + rapor + Gate checklist) · `#/costs` · `#/prompts` · `#/settings`. **Ürünün birincil yüzü UI'dır** | ✅ |
| D18 | API key yönetimi | UI → `sessionStorage` → run başına Fernet-şifreli `run_secrets` → run bitince silinir; `.env` varsayılan yedek; dispatcher key görmez; key'ler log/event/trace'e asla girmez (testli) | ✅ |
| D19 | Kullanıcıya çıkış kontrolü | Deterministik Output Gate G1–G11 (`gate.yaml`), deterministik remediation, sessiz başarısızlık yok | ✅ |
| D20 | PII | Microsoft Presidio (analyzer + anonymizer). Hassas tanımlayıcılar (TCKN, IBAN, kart, telefon, e-posta, IP) her zaman maskeli; web içeriğindeki kişi/kurum adları maskelenmez; 3 sınır: intake, telemetri, çıktı | ✅ |
| D21 | Trace backend | Postgres `run_events` birincil (UI buradan) + **Langfuse self-host** (LangGraph callback, generation ↔ prompt versiyonu bağlantısı, SDK `mask` ile redaction, trace URL `runs`'ta). LangSmith elendi: self-host Enterprise lisansı gerektiriyor | ✅ (revize) |
| D22 | Logging | `structlog` (Python) + `log/slog` (Go), ortak JSON alanları, `run_id`/`span_id` korelasyonu, redaction processor | ✅ |
| D23 | Hata izlenebilirliği | `AgentError` + `ErrorCode` taksonomisi (`contracts/error_codes.yaml`, Python + Go ortak); expected (degrade + hata→karar→sonuç) vs unexpected (stack trace, event ID) | ✅ |
| D24 | UI parametre ayarı | Pydantic şeması → `/api/config/schema` → otomatik gruplu form; run başına override, preset'ler DB'de, YAML'a yazılmaz; güvenlik-kritik alanlar `ui: false` ve sunucuda reddedilir; override'lar `config_snapshot`'a yazılır | ✅ |
| D25 | Reasoning akışı | Her LLM şemasında `rationale` alanı → SSE ile UI zaman çizelgesi; provider thought summaries opsiyonel bayrak (varsayılan kapalı); ham CoT gösterilmez ve saklanmaz | ✅ |
| D8 | Runtime | **Python 3.13** (`python:3.13-slim`), uv, FastAPI, Pydantic v2, SQLAlchemy 2 + alembic, asyncpg, httpx, tenacity, structlog, pytest + pytest-asyncio, trafilatura, MinHash kütüphanesi, LangGraph + Postgres checkpointer, Langfuse SDK, `cryptography` (Fernet) | ✅ |
| D26 | Prompt tekniği | DSPy tarzı signature'lar (Pydantic I/O + talimat + demolar) kendi runtime'ımızda; **DSPy 3.x + GEPA offline optimize job'ı** (eval set = Langfuse dataset, koşu = experiment) → Langfuse'a `candidate` versiyon; Gate ihlalleri GEPA'ya textual feedback | ✅ |
| D27 | Prompt yönetimi | **Langfuse Prompt Management** (headless init ile hazır admin) = düzenleme arayüzü; repo YAML = seed + kanonik export; app `PromptRegistry`: Langfuse → YAML fallback; output şema hash + template değişken kontrolü; run başında versiyon sabitlenir | ✅ |
| D28 | Skills | **Agent Skills** (`SKILL.md`) formatı; `analyze_query` 0–2 skill seçer (progressive disclosure); script yok; bütçe/Gate'i değiştiremez. Saklama önerisi: repo `skills/` kanonik, Langfuse'ta `skill/<name>` prompt olarak versiyon/label (aç/kapa = `production`) | ✅ (saklama: öneri) |
| D29 | Ek araçlar | promptfoo = SHOULD (injection + regression, lokal script) · BAML ve Instructor değerlendirildi, seçilmedi (§20.4) · DSPy runtime'da değil — **birincil gerekçe:** kontrol akışı LangGraph'ta + tek LLM erişim katmanı (fallback/redaction/tracing tek yerde); LiteLLM tedarik zinciri notu ikincil | ✅ |
| D9 | Repo teslimi | GitHub **private** + reviewer daveti; mail'de repo linki. Case PDF repoda değil. Reviewer `docker compose up` ile ayağa kaldırır | ✅ (bkz. D31) |
| D10 | Bonus kapsamı | Mimarinin parçası: streaming (SSE), persistent state (PG checkpoint), detailed tracing, parallel search, caching, cost tracking, citation verification, multiple search providers, LLM fallback, semantic dedup, reranking. **SHOULD:** mini eval set. **COULD:** human-in-the-loop plan onayı (UI zaten çok ekranlı, opsiyonel bayrak) | ✅ |

### Analiz v1 sonrası (2026-09-16)
| # | Konu | Karar / öneri | Durum |
|---|---|---|---|
| D30 | CLI | `research` CLI MUST — aynı agent çekirdeğini servis katmanı olmadan koşar: `examples/` üretimi, eşik kalibrasyonu, debug. **Ürünün birincil yüzü UI'dır**, CLI geliştirici aracı | ✅ |
| D31 | Teslim | GitHub **private** + reviewer daveti, mail'de link. Case PDF gitignore'da (Apilex telifli). Reviewer `docker compose up` ile ayağa kaldırır | ✅ |
| D32 | Gate G4 kovaları | (1) çıplak olgusal sayı → claim'de normalize eşleşme yok ise `error`, cümle çıkar · (2) ledger'dan türetilmiş sayı (sayım/ordinal/toplam) → Gate **yeniden hesaplar** · (3) tolerans dahilindeki varyant (yuvarlama, birim/kur, tarih granülaritesi) → `warn` + "yaklaşık". Her ihlal `gate_result.json` + trace'e; rapor "N cümle çıkarıldı" satırı olmadan gitmez | ✅ |
| D33 | Maliyet takibi | `llm_calls` + `runs` toplamı (birincil) **ve** Langfuse generation usage/cost. Fiyatlar `models.yaml`'da tek kaynak, Langfuse model pricing ile hizalı. UI `#/costs`: run başına + toplam maliyet, model/provider kırılımı, token, latency, cache hit oranı | ✅ |
| D34 | Citation verification maliyeti | `verify_citations` batch'li: tek çağrıda N cümle + atıflı claim'ler, 2–3 batch `asyncio` ile paralel | ✅ |
| D36 | DB sürücüsü | **psycopg 3 tek sürücü** (`postgresql+psycopg://`). Gerekçe: LangGraph Postgres checkpointer psycopg istiyor; alembic (sync) + SQLAlchemy (async) + `LISTEN/NOTIFY` aynı sürücüyle çalışınca tek bağlantı dizesi yetiyor. asyncpg (D8) yerine bu seçildi; `DATABASE_URL` hangi yazımla verilirse verilsin `db/session.py` normalize ediyor (testli) | ✅ |
| D37 | Şema kapsamı | Tablolar: `runs` (kuyruk + sayaçlar + `event_seq`), `run_secrets`, `run_events`, `llm_calls`, `search_calls`, `run_artifacts`, `search_cache`, **`embedding_cache`** (D6), `presets`. **prompt/skill/eval tablosu yok** — versiyonlar Langfuse'ta (v0.6 §6). LangGraph checkpoint tablolarını kütüphane kendi açar | ✅ |
| D35 | Prompt injection | Web içeriği her katmanda untrusted data. Yapısal savunma: enjekte talimat claim'e dönüşemez (verbatim quote doğrulaması), uydurma sayı G4'ü geçemez, Gate LLM içermez. README'de Design Question 6 ile birlikte anlatılır | ✅ |

---

## Kapsam çizgisi
- **MUST:** compose (postgres, migrate, api, dispatcher, agent, presidio×2) · Go dispatcher (dar kapsam) + PG kuyruk + watchdog + deadline · Langfuse self-host (headless init) + trace + maliyet + `PromptRegistry` + seed sync · signature katmanı · 2–3 seed skill + seçim · `run_events` + SSE · **çok ekranlı UI** (key, soru, parametre, canlı akış, rapor, maliyet panosu, prompt/skill özeti) · **`research` CLI** (örnek üretimi + debug, D30) · agent çekirdeği + deterministik heuristic kontroller · Output Gate (G4 üç kovalı, D32) · hata taksonomisi · structlog + redaction · testler · 4 örnek · README + 8 Design Question
- **SHOULD:** preset'ler · cancel · crash sonrası resume · export · DSPy/GEPA ile 1 node optimizasyonu + before/after · mini eval set · promptfoo injection/regression
- **COULD:** Ollama profili · thought summaries · skill zip import/export · Go dispatcher span'lerini Langfuse OTLP'ye · human-in-the-loop plan onayı
- ⚠️ Değerlendirmenin %75'i agent kalitesi, altyapı %15'lik kalemde. Karar: **altyapı kapsamı korunuyor** (bilinçli mühendislik vitrini), agent tarafı LLM yargısının yanına **deterministik heuristic kontrollerle** takviye edilir. Takvim serbest; kesme yapılmıyor. Gerekçe: [`analysis_v1.md`](docs/design/analysis_v1.md) §2.2.
- 🧰 **Hafif mod** README'de belgelenir: `COMPOSE_PROFILES=` boş → Langfuse'suz, Postgres trace'i + YAML prompt'larıyla tam çalışan kurulum (reviewer makinesi yetmezse çıkış yolu).

---

## Faz 0 — Tasarım · Per 10 Eyl
- [x] Case'i oku, gereksinimleri çıkar
- [x] Mimari v0.1 → v0.2 (D1–D4) → v0.3 (servis mimarisi) → v0.4 (Go dispatcher, prompt katmanı + skills) → v0.5 (Langfuse self-host) → **v0.6 (kararların kapanışı + denetim bulguları)**
- [x] 2. tur kararlar (D16, D18, D20, D21)
- [x] 3. tur kararlar: D26, D27 (Langfuse), D28
- [x] Onay bekleyen öneriler kapatıldı: D6, D7, D8, D10, D11, D12, D17, D24, D25, D29 (+ yeni D30–D33) → `analysis_v1.md`
- [x] Tasarım denetimi: `docs/design/analysis_v1.md` (bulgu → karar)
- [x] Repo: `git init`, `.gitignore`, pre-commit (ruff + gitleaks + gofmt/go vet), `pyproject.toml` (ruff/mypy/pytest), GitHub private + `main`
- [x] README iskeleti (8 Design Question yer tutucuları)
- [ ] API key'leri: kod + `.env.example` + testler yeşil olduktan sonra `.env`'e eklenecek. Geliştirme `FakeLLM`/`FakeSearchProvider` ile key'siz ilerler (Langfuse key'leri headless init ile üretilir)

## Faz 1 — Altyapı iskeleti · Per akşam → Cum öğlen
- [x] uv workspace (kök = tooling + `[tool.uv.workspace]`, üye = `agent/`), `agent/pyproject.toml`, `uv.lock`
- [ ] `agent/Dockerfile` (Python, 3 entrypoint) + `dispatcher/Dockerfile` (Go multi-stage → distroless) + `docker-compose.yml` (postgres, migrate, api, dispatcher, agent, presidio×2 + `observability` profili: langfuse-web, langfuse-worker, clickhouse, redis, minio; postgres init'te ayrı `langfuse` DB; headless init env'leri; healthcheck'ler)
- [x] `contracts/error_codes.yaml` (30 kod) + `contracts/run_states.yaml` (durum makinesi, `max_attempts`)
- [x] `research_agent/contracts.py` — YAML → Pydantic; enum ↔ YAML eşitliği ve `dispatcher.yaml` ↔ `run_states.yaml` retry sayısı testli
- [x] `contracts/agent-api.openapi.yaml` (execute/cancel/healthz/capacity) + Python tarafıyla yapısal contract testi (path/method/status/required alan/`additionalProperties: false`)
- [x] `config/*.yaml` (settings, models, search, domain_tiers, pii, gate, dispatcher)
- [x] `config/schema.py` — `tunable()` / `locked()`, grup + açıklama, sınırlar; `tunable_fields()` → `/api/config/schema`
- [x] `config/loader.py` — YAML < env allowlist < run override; kilitli alan reddi, sınır doğrulaması, `config_hash` (kanonik JSON)
- [ ] `config/prompts/*.yaml` (signature seed'leri — Faz 2)
- [x] `.env.example` (sadece secret + altyapı)
- [x] `db/models.py` + alembic `0001`: `runs`, `run_secrets`, `run_events`, `llm_calls`, `search_calls`, `run_artifacts`, `search_cache`, `embedding_cache`, `presets` (D37). Model ↔ migration drift testi (`compare_metadata`) yeşil
- [x] `errors.py` — `ErrorCode`, `ErrorCategory`, `AgentError` (code → decision → outcome), `AgentException`
- [x] `observability/logging.py` — structlog JSON + contextvar korelasyon + redaction processor
- [x] `observability/redaction.py` — provider key / bearer / DSN / IBAN / kart (Luhn) / TCKN (checksum) / e-posta / telefon / IP + hassas alan adları; pipeline'dan geçtiği testli
- [x] `observability/events.py` — `run_events` writer (run satırındaki sayaçtan boşluksuz `seq`, kendi bağlantısında commit), `EventType` sözlüğü, `pg_notify` + `listen()` yardımcısı, redaction'dan geçen payload
- [ ] **Go dispatcher:** `queue` (claim `SKIP LOCKED`, `LISTEN` + polling), `capacity` + agent seçimi, `agentclient` (execute/cancel/healthz), `watchdog` (heartbeat kaybı → requeue, deadline → cancel → failed), `events` (`run_events`'e `node=dispatcher`), `slog` JSON, `dispatcher.yaml`
- [x] **Python agent_server:** `execute` (202) / `cancel` / `healthz` / `capacity`; `RunExecutor` (slot, heartbeat, tek terminal yazım, kooperatif cancel, drain); `db/repository.py` (claim `SKIP LOCKED`, kontratla doğrulanan geçişler, requeue + `max_attempts`, stale/overdue taramaları, `run_secrets` yaşam döngüsü); `keys.py` (Fernet + UI>env çözümleme, `repr` sızdırmıyor)
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
- [~] `cli.py` — `research config` / `research contracts` çalışıyor; `research run "soru"` graph bağlanınca (D30)
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
- [ ] `synthesize` + `verify_citations` (**batch'li**, 1 kez geri bildirimle yeniden sentez — D34)
- [ ] `gate/` — G1–G11, sayı/tarih normalizer, **G4 üç kovalı + tolerans (D32)**, deterministik remediation, `gate_result.json`
- [ ] `render_report` (Summary · Key Findings · Conflicting/Uncertain · Conclusion (+Action Plan) · Known Gaps · Sources · Metadata)
- [ ] Worker'a bağla: gerçek run uçtan uca compose içinde

## Faz 5 — API & UI · Paz
- [ ] SSE `/api/runs/{id}/events` (backfill + `Last-Event-ID` + `LISTEN`)
- [ ] `/api/config/schema`, `/api/presets`, `/api/keys/validate`, `/cancel`, `/export`
- [ ] UI: Ayarlar/API key ekranı (Test butonları)
- [ ] UI: Yeni araştırma + otomatik parametre formu + preset
- [ ] UI: Canlı run görünümü (iterasyon bazlı zaman çizelgesi, rationale'lar, skorlar, kodlu hatalar, Gate checklist, rapor)
- [ ] UI: Geçmiş listesi (`#/runs`)
- [ ] UI: Maliyet panosu (`#/costs`) — run başına + toplam, model/provider kırılımı, token, latency, cache hit (D33)
- [ ] UI: **Prompts & Skills** özet sayfası — aktif versiyon/label, kaynak (Langfuse/YAML), şema uyumu, "Langfuse'ta düzenle" linki (düzenleme/diff/terfi/playground Langfuse admin'de)
- [ ] API: `GET /api/prompts`, `GET /api/skills`

## Faz 6 — Güvenilirlik & observability · Paz
- [ ] Hata matrisi uçtan uca (§13.2) — her kod için tetiklenebilir senaryo
- [ ] Langfuse payload'ında secret/PII olmadığını doğrula; Langfuse kapalıyken (hafif mod) sistemin tam çalıştığını doğrula
- [ ] (SHOULD) `optimize/`: DSPy 3.x + GEPA job'ı (kilitli bağımlılıklar, ayrı profil), eval set → Langfuse dataset, koşu → experiment, `generate_queries` veya `extract_claims` için 1 optimizasyon → `candidate` + before/after metrik tablosu
- [ ] Worker crash → lease → resume denemesi
- [ ] Prompt'ları gözden geçir (TR/EN, web içeriğini "untrusted data" olarak işaretle — prompt injection)

## Faz 7 — Test (Faz 2'den itibaren sürekli)
- [x] Unit: kontrat eşitliği, config loader + override sınırları + kilitli alanlar + `config_hash` kararlılığı, redaction + TCKN checksum, log pipeline (40 test yeşil)
- [ ] Unit: URL canonicalization, MinHash, query dedup, query generation
- [ ] Unit: scoring, facet yeterlilik, contradiction adayları, termination/router
- [ ] Unit: Gate G1–G11 (ayrı ayrı), sayı/tarih normalizer, **G4 üç kova + yanlış pozitif senaryoları** (türetilmiş sayı, yuvarlama, kur, tarih granülaritesi)
- [ ] Unit: PII redaction + TCKN checksum, secret redaction, structured output repair, config override sınırları
- [ ] Senaryo: sufficient · stagnation · aynı sorgu · timeout→fallback · invalid JSON · max iteration · gate fail→remediation
- [ ] Senaryo: **`max_wall_clock` ve `max_cost_usd` açıkken** `stop_reason=budget` (kapılar varsayılan kapalı olduğu için bu yol yalnızca testle korunur — D11)
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
- [ ] Repo private + reviewer daveti → link + kısa açıklama ile mail (D31)
- [ ] Case PDF'in repoda olmadığını doğrula (`git ls-files | grep reference`)

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
