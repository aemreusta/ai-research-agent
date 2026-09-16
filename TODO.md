# TODO — Apilex AI Research Agent Case

- **Durum (2026-09-16):** sistem uçtan uca çalışıyor — `docker compose up -d` + provider key'leri (README §1). Uygulama notları: mimari §22.
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
| D38 | Kurulum sürtünmesi | Reviewer'ın tek adımı provider key'leri olmalı: `APP_SECRET_KEY` boşsa `migrate` üretir (paylaşılan volume), Langfuse init değerleri `.env.example`'da lokal-dev varsayılanlarıyla dolu, compose `.env` yoksa da kalkar. Key'ler UI'dan da girilebildiği için `.env` düzenlemek bile opsiyonel | ✅ |
| D39 | Eşzamanlı yazım | Agent ve watchdog aynı `runs` satırına yazıyor → **her durum geçişi compare-and-set** (Python: `WHERE status = kaynak`; Go: ek olarak gözlenen `heartbeat_at`). Kaybeden taraf sessizce üzerine yazmıyor, `IllegalTransitionError` / `ErrLostRace` alıyor. Requeue backoff'u için `runs.available_at` (migration `0002`); agent'a hiç ulaşmamış claim denemeyi geri veriyor | ✅ |
| D40 | Model seçimi (2026-09-16 doğrulandı) | reasoning: `gemini-3.8-flash` / `gpt-5.6-terra` / `qwen3:14b` · fast: `gemini-3.1-flash-lite` / `gpt-5.6-luna` / `qwen3:8b` · embedding: `gemini-embedding-001` (metin başına vektör + `SEMANTIC_SIMILARITY`; `-2` girdileri tek vektörde birleştiriyor) / `text-embedding-3-small`. Fiyatlar `models.yaml`'da, Gemini 3.8 Flash tanıtım fiyatı 2026 sonuna kadar. Tavily `basic` derinlik (1 kredi) + raw content: 45 aramalık bütçede 45 vs 90 kredi | ✅ |
| D41 | UI teslimi | Build'siz ES modülleri + `Cache-Control: no-cache` (ETag ile yeniden doğrulama): sezgisel önbellek, güncellemeden sonra eski modülü sunup UI'ı kırıyordu (Playwright doğrulamasında yakalandı). `research run --simulate --persist` çevrimdışı, sıfır fiyatlı bir run'ı veritabanına yazar (kuyruğa girmez, `agent_id=cli`), UI'da görülebilir | ✅ |
| D42 | Presidio anonymizer | Kaldırıldı: yalnızca analyzer kullanılıyor; placeholder'lar kodda atanıyor çünkü aynı değerin her seferinde aynı numarayı alması (`<TCKN_1>`) gerekiyor. Presidio sonuçları her zaman regex bulgularıyla birleştiriliyor | ✅ |
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
- [x] `agent/Dockerfile` (uv workspace, multi-stage, non-root, `runtime` + `test` target'ları) + `dispatcher/Dockerfile` (Go → distroless, `-healthcheck` bayrağı) + `docker-compose.yml` (postgres, migrate, api, dispatcher, agent, presidio×2 + `observability`: Langfuse **v4** web/worker, clickhouse, redis, minio; `local-llm`: ollama; `test`: suite). Env servis başına açıkça veriliyor (`env_file` yok): dispatcher'a key gitmiyor, Langfuse'un `DATABASE_URL`'i bizimkiyle çakışmıyor. Postgres init'te `langfuse` + `research_test` DB'leri. `Makefile` kısayolları
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
- [x] **Go dispatcher:** `store` (claim `SKIP LOCKED` + deadline'ı ilk claim'de sabitleme, CAS geçişler — gözlenen heartbeat dahil, terminalde `run_secrets` silme, `run_events`'e `node=dispatcher`), `capacity` (compose DNS ile replica keşfi → `--scale agent=N` ayarsız çalışır; en boş sağlıklı replica), `agentclient` (sentinel hatalar, OpenAPI contract testi), `watchdog` (saf karar fonksiyonu, tablo testli: heartbeat kaybı/dispatch timeout/deadline→önce agent'a sor→grace→fail/cancel sonrası sessizlik), `listen` (hijack edilmiş LISTEN bağlantısı + polling yedeği), `slog` JSON (structlog ile aynı alan adları), provider key'i verilirse **başlamayı reddediyor**, `-healthcheck` (distroless'ta curl yok)
- [x] **Python agent_server:** `execute` (202) / `cancel` / `healthz` / `capacity`; `RunExecutor` (slot, heartbeat, tek terminal yazım, kooperatif cancel, drain); `db/repository.py` (claim `SKIP LOCKED`, kontratla doğrulanan geçişler, requeue + `max_attempts`, stale/overdue taramaları, `run_secrets` yaşam döngüsü); `keys.py` (Fernet + UI>env çözümleme, `repr` sızdırmıyor)
- [x] `api`: `/healthz` (bağımlılıksız) · `/readyz` (bağımlılık başına ok/required) · `POST/GET /api/runs` · `GET /api/runs/{id}` · `/cancel` (kuyruktaysa hemen `cancelled`, çalışıyorsa bayrak) · **SSE** (önce `LISTEN`, sonra backfill, `Last-Event-ID`, keepalive, terminal durumda kapanış) · `/export` (report/trace/state/gate) · `/api/config/schema` (etkin varsayılanlar + sınırlar) · `/api/presets` · `/api/keys/validate` + `/status`. Doğrulama hataları girdiyi geri yansıtmıyor (key sızıntısı)
- [x] Uçtan uca "boş run": lokal süreçlerle (agent `kill -9` → 30 sn → `AGENT_HEARTBEAT_LOST` → backoff → ikinci agent 2. denemede bitirdi, deadline korundu) **ve compose içinde** (tüm servisler healthy, `--scale agent=3` DNS keşfiyle ayarsız dağıtım, `docker compose --profile test run tests` → 167/167, Langfuse headless init: org/proje/key'ler hazır, `/readyz` üç bağımlılığı yeşil)
- [ ] ⛔ Bu noktadan sonra dispatcher'a yalnızca bug fix

## Faz 2 — Provider'lar & PII · Cum
- [x] `providers/llm` — REST adaptörleri (SDK yok): Gemini (`generateContent` + `responseJsonSchema` + `thinkingLevel`, `batchEmbedContents`) ve OpenAI-uyumlu (OpenAI Chat Completions strict `json_schema` + **Ollama `/v1` aynı adaptörle**); `schema.py` (Pydantic → strict şema: tüm alanlar required+nullable, `additionalProperties:false`, `$ref` yalın, şema hash'i); `structured.py` (tek repair turu, `null`→varsayılan); `gateway.py` (katalog sırasıyla zincir, retryable→backoff, auth→sıradaki, `LLM_FALLBACK_USED`, her deneme `llm_calls`'a, maliyet sayaca, `Tracer` kancası); `FakeLLMProvider` (şema adına göre senaryo)
- [x] `providers/search` — Tavily (`/search`, raw content markdown, dil→ülke) + Brave (`/web/search`, extra snippets, HTML temizliği); `SearchGateway` (önbellek, retry/backoff, auth'ta retry yok, boş sonuçta diğer sağlayıcı, `preferred` ile çeşitlilik, **her mantıksal arama önbellekten gelse de bütçeden 1 düşer** → soğuk/sıcak koşu aynı kararları verir); `ContentFetcher` (httpx stream + byte cap + trafilatura, önbellekli); `FakeSearchProvider` (korpus)
- [x] `pii/` — `RegexMasker` (kararlı numaralı placeholder, checksum/Luhn) + **`PresidioMasker`** (analyzer `/analyze` + TR ad-hoc recognizer'lar TCKN/telefon, checksum post-filter; sonuç **her zaman regex bulgularıyla birleşimi** — Presidio yalnızca kapsama ekleyebilir; erişilemezse regex + `degraded`). Compose'da doğrulandı: TCKN + e-posta maskelendi, kişi adı korundu
- [x] Key çözümleme: `run_secrets` (Fernet) → `.env` varsayılanı; kaynak metadata'sı. `APP_SECRET_KEY` boşsa `migrate` paylaşılan volume'a bir kez üretir (`APP_SECRET_KEY_FILE`, 0600, asla döndürülmez) — kullanıcı Fernet anahtarı üretmek zorunda değil (D38). Key probe'ları: Tavily `/usage`, OpenAI/Gemini model listesi (key header'da, URL'de değil), Ollama `/api/tags`, Brave 1 arama
- [~] `FakeLLMProvider` + `FakeSearchProvider` hazır; `agent/runtime.py`: `EventSink` / `CallRecorder` / `Cache` protokolleri — DB ve bellek implementasyonları (agent çekirdeği SQLAlchemy import etmez; CLI ve senaryo testleri aynı kodu koşar). Kalan: `FakePresidio`
- [x] `prompting/` — 9 signature (çıktı şeması kodda, her birinde `rationale`), YAML seed'lerde `output_schema_hash` (kod değişip seed güncellenmezse test kırılır), registry (şema hash + şablon değişkenleri **tam eşitlik**: `{ledger}`'ı düşüren prompt reddedilir), run başına sabitleme, `Predictor` (ortak güvenlik önsözü, `<untrusted_source>` çiti — içerik çiti erken kapatamaz), `skills.py` (script'li / tier-3 ekleyen skill reddedilir, en fazla 2 skill)
- [x] Langfuse **v4**: self-host v4 eski batch ingestion'ı reddettiği için **OTLP/JSON** (`/api/public/otel/v1/traces`, `x-langfuse-ingestion-version: 4`): run başına kök span + her LLM çağrısı için generation (usage/cost/prompt adı+sürümü); payload'lar redaction'dan geçiyor (testli); trace URL `runs`'ta. `PromptRegistry` Langfuse → YAML fallback, **ilk kullanımda seed** (migrate Langfuse'tan önce koştuğu için) — compose içinde 9 prompt `production` etiketiyle oluştu. Kalan: skill'lerin `skill/<name>` olarak senkronu, export script

## Faz 3 — Agent çekirdeği · Cum akşam → Cmt
- [x] `cli.py` — `research run "soru" [--simulate] [--override] [--out]`: servis katmanı olmadan aynı graf (bellek içi event/kayıt/önbellek), zaman çizelgesini case formatında basar, `input.md/report.md/report.json/gate_result.json/state.json/trace.jsonl` yazar; anahtar yoksa `LLM_AUTH` ile anlaşılır çıkış (D30)
- [x] `intake_guard` (doğrulama, dil, idempotent PII maskeleme, degrade uyarısı)
- [x] `analyze_query` (+ skill seçimi, skill domain'leri run için tier listesine eklenir), `plan` (normalize: tekrar alt soru/facet ayıklama, cap'ler, en az bir `must`), `generate_queries` (tur genişliği kuralı, bütçeye göre kota, L4 dedup, eksik kalan hedefe şablon sorgu, **sorgu tükenmesi → exhausted**) — hepsinde deterministik fallback
- [x] Seed skill'ler: `regulatory-research-tr` (+ tier-1 domain'ler), `company-research`, `market-sizing` (+ araştırma firması domain'leri)
- [x] L1 URL canonicalization (şema, www/m/amp, tracking, AMP son eki, sıralı query) · L2 normalize hash + MinHash LSH → `origin_id` (checkpoint'ten yeniden kurulabilir) · L4 token-Jaccard query dedup · ortak `text.py` (eşleştirme için tüm i-varyantları tek `i`: "ApilexAI" = "apilexaı" sorunu testle yakalandı)
- [x] `evaluate_sources`: 8'lik batch'ler paralel, LLM relevance/primary/±0.1 düzeltme + `scoring.py` (tier atlatılamaz); LLM yoksa kural skoru; her kaynak için `[Evaluator] domain → skor (T1, primary, tarih, relevance)` satırı
- [x] `extract_claims`: doküman başına paralel, içerik `<untrusted_source>` içinde; **alıntı doğrulaması** (rakam + sayı kelimesi birebir) + **enjeksiyon tripwire'ı** ("ignore previous instructions" / "önceki talimatları yok say" gibi modele hitap eden cümle kanıt sayılmaz — senaryo testinde yakalandı)
- [x] `cluster_and_corroborate` (tek embedding modeli, önbellekli; sağlayıcı düşerse run'ın geri kalanı lexical) · `detect_contradictions` (kural adayları + LLM judge; tercih ancak ledger destekliyorsa — birincil ve daha yüksek skor — kabul, çelişki yine raporlanır; judge yoksa muhafazakâr: gerçek çelişki). Eşleştirme düzeltmeleri: baştaki artikel ("The EU AI Act" = "EU AI Act"), attribute için içerme katsayısı

## Faz 4 — Döngü, sentez, Gate · Cmt
- [x] `assess_coverage` (facet'ler kodda; LLM yalnızca eksik facet ekleyebilir + not yazar, `[Research State] Missing information: …`) + router (`[Router] Continue/Stop …`, `BUDGET_EXCEEDED` / `MAX_ITERATIONS` / `NO_EVIDENCE` olayları)
- [x] LangGraph wiring (durum tek JSON dokümanı → checkpoint'te pickle yok) + Postgres checkpointer (`thread_id = run_id`); her node sınırında cancel kontrolü, kendi span'i, canlı sayaçlar; `recursion_limit` iterasyon sayısından türetilir; resume'da bütçe sayaçları geri yüklenir
- [x] Çelişki çözüm follow-up'ı (çelişki başına 1 hedefli sorgu, sonra raporlanır)
- [x] `synthesize` (yalnızca ledger; atıfsız cümle yalnızca açıkça çerçeve cümlesiyse META, değilse FACT kalır ve G2 düşürür; etiketler ledger'dan deterministik; Known Gaps kodda) + `verify_citations` (batch'li, 3 paralel, 1 kez geri bildirimle yeniden sentez, kalan desteksiz cümle düşer — D34); model yoksa ledger'ı doğrudan listeleyen rapor
- [x] `gate/` — `numeric.py` (TR+EN sayı/para/yüzde/tarih, çoklu okuma, **yazıldığı hassasiyete yuvarlama** toleransı, para birimi farkı = eşleşmez, tarih granülaritesi = kapsama) · `rules.py` G1–G11 (G4: soru bağlamı → B2 ledger'dan yeniden hesap → B1 birebir → B3 tolerans=warn; G1 ayrıca "hiç bulgu kalmadıysa" remediation'sız hata — boş raporun "None" ile temiz görünmesini engeller) · `runner.py` (değerlendir → düzelt → yeniden değerlendir, kaynak listesi her zaman atıflardan türetilir, "N cümle çıkarıldı" notu, fail'de banner, deterministik `to_dict`)
- [x] `render.py` (Markdown + JSON: numaralı atıflar, TR/EN etiketler, banner + kalan ihlaller, kaynak skoru/primary/tarih, metadata: durma nedeni, tur, arama, token, maliyet, gate, config hash, modeller, prompt sürümleri)
- [~] Worker'a bağlandı (`AGENT_RUNNER=graph` varsayılan): compose içinde anahtarsız run `LLM_AUTH` ile anlaşılır mesajla bitiyor, prompt'lar Langfuse'a seed ediliyor. Kalan: gerçek anahtarlarla uçtan uca (kullanıcının adımı)

## Faz 5 — API & UI · Paz
- [x] SSE `/api/runs/{id}/events` (backfill + `Last-Event-ID` + `LISTEN`)
- [x] `/api/config/schema`, `/api/presets`, `/api/keys/validate`, `/cancel`, `/export`, `/api/costs`, `/api/runs/{id}/ledger` (sayfa metni tarayıcıya gönderilmez)
- [x] UI `#/settings`: sağlayıcı başına alan + Test + durum rozeti, `.env` varsayılanı rozeti, key politikası açıklaması; üst barda "keys ready/missing" rozeti
- [x] UI `#/new`: soru, 4 örnek soru, key eksikse uyarı, şemadan otomatik gruplu form (sınırlar, kapalı kapılar için boş=disabled), preset seç/kaydet, maskeleme bildirimi
- [x] UI `#/runs/:id`: canlı sayaçlar, cancel, export, Langfuse linki; sekmeler: Rapor (güvenli markdown, tıklanabilir atıflar, kırmızı banner) · Zaman çizelgesi (SSE, tur gruplaması, rationale, kodlu hatalar, dispatcher ayrı renk, filtreler) · Gate checklist · Plan & sorgular · Kaynaklar (skor bileşenleri, origin kopyaları) · Bulgular (çelişkiler + claim/alıntılar)
- [x] UI `#/runs` (aktif run varken otomatik yenilenir)
- [x] UI `#/costs` — toplamlar, model bazında p50/p95 + hata + pay, node bazında, arama sağlayıcıları + cache, run bazında (D33)
- [x] UI `#/prompts` — aktif sürüm + kaynak (Langfuse/YAML) + içerik hash'i, şema uyumu (reddedilen Langfuse sürümü görünür), girdiler, "Edit in Langfuse"; skill'ler (rehber, eklenen domain'ler, sorgu kalıpları)
- [x] API: `GET /api/prompts`, `GET /api/skills`

## Faz 6 — Güvenilirlik & observability · Paz
- [~] Hata matrisi: kodların büyük kısmı senaryo/unit testlerle tetikleniyor; gerçek API'lere geçersiz anahtarla canlı doğrulama yapıldı (auth gövdeden tanınıyor, devre kesici, hepsi reddedilince fail-fast). Kalan: `DEADLINE_EXCEEDED`'in compose içinde canlı gösterimi (watchdog tablo testli)
- [x] Langfuse payload'ında secret/PII olmadığı testli; hafif modda sistem kalkıyor ve çalışıyor (compose, `COMPOSE_PROFILES=`)
- [ ] (SHOULD) `optimize/`: DSPy 3.x + GEPA job'ı (kilitli bağımlılıklar, ayrı profil), eval set → Langfuse dataset, koşu → experiment, `generate_queries` veya `extract_claims` için 1 optimizasyon → `candidate` + before/after metrik tablosu
- [x] Worker crash → heartbeat → requeue → resume: lokal süreçlerle canlı, graf seviyesinde Postgres checkpointer ile entegrasyon testi
- [x] Prompt'lar: ortak güvenlik önsözü, `<untrusted_source>` çiti, çıkarıcıda "talimatları izleme" kuralı, enjeksiyon tripwire'ı

## Faz 7 — Test (Faz 2'den itibaren sürekli)
- [x] Unit: kontrat eşitliği, config loader + override sınırları + kilitli alanlar + `config_hash` kararlılığı, redaction + TCKN checksum, log pipeline (40 test yeşil)
- [x] Unit: URL canonicalization, MinHash, query dedup (query generation node ile gelecek)
- [x] Unit: scoring, facet yeterlilik, contradiction adayları, termination/router
- [x] Unit: Gate G1–G11 (ayrı ayrı), sayı/tarih normalizer, **G4 üç kova + yanlış pozitif senaryoları** (türetilmiş sayı, yuvarlama, kur, tarih granülaritesi, soru bağlamı)
- [x] Unit: PII redaction + TCKN checksum (+Presidio), secret redaction, structured output repair, config override sınırları
- [x] Senaryo (`simulated.py` ile çevrimdışı tam graf): sufficient · stagnation/known gaps · max iteration · arama bütçesi · timeout→fallback provider · invalid JSON→repair→fallback plan · birincil LLM ölü→ikinci provider · kanıt yok→NO_EVIDENCE+banner · enjeksiyon · PII sağlayıcıya gitmiyor · cancel · **crash→checkpoint'ten resume (bitmiş node'lar tekrar koşmuyor)** · TR başlıklar · preflight
- [x] **`max_wall_clock` ve `max_cost_usd` açıkken** `stop_reason=budget` — router seviyesinde **ve tam graf senaryosunda** testli; kapalıyken asla durdurmadığı da testli (D11)
- [x] Hata izlenebilirliği: gateway/arama/node testleri `ErrorCode` + `decision` + `outcome` zincirini doğruluyor
- [x] API: run oluşturma, SSE backfill/resume/canlı, cancel, export, maliyet, **key sızıntısı yok**
- [x] Go: watchdog karar tablosu, backoff, kapasite seçimi + DNS keşfi, durum geçişleri ↔ `run_states.yaml`, store (Postgres)
- [x] Contract: Python agent server + Go client ↔ OpenAPI + `error_codes.yaml`
- [x] Prompt/skill: versiyon çözümleme + run başına sabitleme, uyumsuz Langfuse sürümü reddi, skill seçimi + rehber enjeksiyonu, script'li/tier-3 skill reddi, Langfuse seed round-trip
- [x] Integration (PG): eşzamanlı claim'de çift alım yok (Python + Go); CAS yarışları; agent servis yolu uçtan uca; checkpoint'ten resume; deadline kararları (Go tablo testi)

## Faz 8 — Örnekler & dokümantasyon · Paz akşam
- [~] Örnek altyapısı hazır: `make examples` 4 soruyu kullanıcının anahtarlarıyla `examples/<slug>/`'a yazar (input/trace/report/report.json/gate_result/state); `examples/offline-demo-eu-ai-act` çevrimdışı format örneği. Kalan: gerçek anahtarlarla üretim (kullanıcı adımı)
- [ ] Eşik kalibrasyonu
- [x] README (EN): kurulum (tek adım: key'ler; hafif mod; port override; demo), teknolojiler, servis + agent diyagramları (Mermaid), claim ledger, search strategy, source evaluation, duplicate detection, follow-up/termination, contradiction, Output Gate (G4 kovaları), PII, observability, error handling + matris, testler, design decisions, bilinen kısıtlar
- [x] README: 8 Design Question'a **Türkçe** cevaplar

## Faz 9 — Teslim · Pzt sabah (son: 16:00)
- [x] Temiz clone → `cp .env.example .env` → `docker compose up -d` → tüm servisler sağlıklı → UI → anahtarsız/geçersiz anahtarlı run'lar anlaşılır mesajla biter → `docker compose --profile test run tests` yeşil. Geçerli anahtarla run: kullanıcı adımı
- [x] Secret taraması: pre-commit gitleaks her commit'te; `.env` gitignore'da
- [x] README ↔ kod tutarlılığı: README iddiaları testlerle desteklendi (resume, simüle etiket, bellek ölçümü)
- [ ] Repo private + reviewer daveti → link + kısa açıklama ile mail (D31)
- [x] Case PDF repoda yok (`git ls-files | grep reference` boş)

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
