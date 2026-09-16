# Analiz v1 — Tasarım denetimi ve karar kapanışı

> Tarih: 2026-09-16 · Girdi: `architecture_v0.6.md`, `TODO.md`, case PDF (13 sayfa, s.13 referans mimari görseli)
> Bu doküman bir **denetim kaydıdır**: bulgu → karar → nereye yansıdı. Kararların kanonik listesi `TODO.md` → Karar Günlüğü'nde.
> Not: Analiz, uygulama kodu yazılmaya başlamadan önce spesifikasyon + plan + repo hijyeni üzerine yapıldı.
> Mimariye yansıması: `architecture_v0.6.md` (v0.5 → `_archive/`).

---

## 1. Gereksinim kapsaması

Case'in 8 fonksiyonel gereksinimi, 7 error-handling senaryosu, 5 deliverable'ı ve 8 Design Question'ının tamamı `architecture_v0.6.md`'de karşılığa sahip. Öne çıkanlar:

| Case gereksinimi | Tasarım karşılığı | Not |
|---|---|---|
| Duplicate detection + yaklaşımın açıklanması | 4 katman (L1 URL · L2 MinHash · L3 claim embedding · L4 query) + "corroboration = bağımsız origin sayısı" | Case'in "aynı basın açıklamasını aktaran 5 haber sitesi ≠ 5 doğrulama" örneğini doğrudan hedefliyor |
| Source quality evaluation | Ağırlıklı, bileşenleri trace'e yazılan açıklanabilir skor (§9) | Case kesin algoritma beklemiyor, yaklaşımın açıklanmasını bekliyor |
| Desteklenmeyen claim'in cevaba girmemesi | 5 katmanlı defense-in-depth + deterministik Output Gate G1–G11 (§11) | Rubric'teki "hallucination riski nasıl azaltıldı" maddesinin merkezi |
| Termination strategy | Hard limit + success + stagnation + query exhaustion; **karar kodda** (§7) | Design Question 1 ve 2'yi birlikte kapatıyor |
| Observability | Postgres `run_events` + case'in `[Planner] …` log formatının korunması (§14) | Case yalnızca log/trace görünürlüğü istiyor; Langfuse bunun üstüne maliyet/prompt versiyon boyutu ekliyor |

**Merkezi güç:** Claim ledger soyutlaması (§4) dört gereksinimi (dedup, corroboration, contradiction, citation) tek veri modelinden çözüyor. README'de bu tek başına anlatılmaya değer.

---

## 2. Bulgular ve kararlar

### 2.1 Kapatılan bloklayıcılar

| # | Bulgu | Karar |
|---|---|---|
| B1 | Planlanan teslim tarihi (14 Eyl) geçti, repoda kod yok | **Kabul edildi, takvim serbest.** Kapsam daraltılmıyor; sistem bütün olarak yazılacak. Gecikmenin karşılığı tamamlanmış ve gerekçelendirilmiş bir sistem olacak |
| B2 | Provider key'leri temin edilmemiş | Kod + `.env.example` + testler yeşil olduktan sonra key'ler `.env`'e eklenip gerçek run'lar alınacak. Geliştirme `FakeLLM` / `FakeSearchProvider` ile key'siz ilerler → key'ler kritik yolda değil |
| B3 | Case PDF repoda commit'li; PDF "paylaşılamaz" ibaresi taşıyor | **`docs/reference/` gitignore'a alındı, dosya git'ten çıkarıldı.** Repo private kalıyor, teslim = link + reviewer daveti (D31). Git geçmişi `filter-branch` ile yeniden yazıldı, blob GC ile silindi ve force-push edildi → **dosya hiçbir commit'te yok**. PDF yalnızca lokalde |

### 2.2 Kapsam ve altyapı (H1, H2, M5)

**Bulgu:** Rubric'in %75'i agent kalitesinde; MUST listesinin yarısı altyapı. Case'in kendi referans mimarisi (PDF s.13) tek bir LangGraph uygulaması ve storage/monitoring katmanlarını açıkça "Optional" işaretliyor. `docker compose up` 12 servis ayağa kaldırıyor.

**Karar: altyapı kapsamı korunuyor.** Gerekçe: bu case'de altyapı, gösterilmek istenen mühendislik yetkinliğinin parçası — control/data plane ayrımı, kuyruk + watchdog + deadline, self-host observability, prompt yönetimi ve PII sınırları bilinçli birer tercih olarak sunulacak. Reviewer'ın sistemi Docker ile ayağa kaldırması bekleniyor ve `COMPOSE_PROFILES=observability` **varsayılan açık** kalıyor; Langfuse'un maliyet/latency panoları ürünün parçası (D33).

**Karşılığında alınan önlemler:**
- Agent kalitesi ayrıca takviye edilecek: LLM yargısının yanında **deterministik heuristic kontroller** (facet yeterlilik, skor matematiği, çelişki adayları, G1–G11) — yani %75'lik kalem koda gömülü kurallarla da desteklenir.
- `README.md`'de "hafif mod" (`COMPOSE_PROFILES=`) ayrı bir bölüm olarak belgelenir: Langfuse'suz, Postgres trace'i ve YAML prompt'larıyla tam çalışan kurulum. Reviewer'ın makinesi yetmezse çıkış yolu var.
- CLI yolu MUST'a alındı (aşağıda).

### 2.3 CLI ve UI'ın rolü (H3)

**Karar (D30):** `research` CLI MUST. Aynı agent çekirdeğini servis katmanı olmadan koşar (`cli.py`): örnek üretimi (`examples/<slug>/`), eşik kalibrasyonu, debug ve testler buradan gider. **Ancak ürünün birincil yüzü UI'dır** — CLI geliştirici aracıdır, teslimde vitrin değil. UI çok ekranlı bir takip çerçevesine genişletiliyor (D17).

### 2.4 Output Gate G4 kalibrasyonu (H4)

**Bulgu:** "Cümledeki her sayı/tarih/yüzde/para tutarı atıflı claim metninde geçmeli" kuralı türetilmiş sayıları (sayım, ordinal, toplam, oran, yuvarlama, para birimi çevrimi, yıl aralığı) yanlış pozitif olarak yakalar. Remediation "cümle çıkarılır" olduğu için sonuç sessizce budanmış rapor — tasarımın "sessiz başarısızlık yok" ilkesiyle çelişir.

**Karar:** G4 üç kovaya ayrılır:
1. **Çıplak olgusal sayı** (claim'den gelmesi gereken değer) → claim metninde/alıntısında normalize edilmiş eşleşme yok ise `error` → cümle çıkarılır.
2. **Ledger'dan türetilmiş sayı** (cluster sayımı, "üç alt başlıktan ikisi", toplam) → Gate bunu ledger'dan **yeniden hesaplar**; eşleşiyorsa geçer, eşleşmiyorsa `error`.
3. **Tolerans dahilindeki varyant** (yuvarlama, birim/para birimi dönüşümü, tarih granülaritesi) → `config/gate.yaml`'daki toleransla karşılaştırılır; sınırdaysa `warn` + "yaklaşık" etiketi.

Her ihlal, çıkarılan cümle ve gerekçesiyle `gate_result.json` ve trace'e yazılır; rapor "N cümle kanıt yetersizliğinden çıkarıldı" satırı olmadan kullanıcıya gitmez.

### 2.5 Bütçe ve sonlandırma (M1, M2)

**Bulgu:** 3–6 alt soru × 2–3 sorgu = ilk turda 9–18 arama; `max_searches=30` ile `max_iterations=4` fiilen erişilemez. `max_wall_clock=300s` tam içerik fetch + entailment ile çoğu run'ı erken keser.

**Karar (D11 revize):**
- `max_searches = 45`
- Tur genişliği: **tur 1** → açık alt soru başına 2–3 sorgu; **tur ≥ 2** → yalnızca eksik facet / çözülmemiş çelişki başına 1 sorgu, tur başına en fazla 8
- `max_wall_clock = null` (**kapalı başlar**, `settings.yaml` + UI'dan açılabilir parametre)
- `max_cost_usd = null` (kapalı başlar) — fakat **token/maliyet sayacı her zaman çalışır** ve UI'da canlı görünür
- `max_iterations = 4`, stagnation eşiği 2 (kalibre edilecek)

Süre ve maliyet kapıları kapalı başlasa da `stop_reason=budget` yolu ve testleri kodda durur; UI'dan açılıp senaryo testinde doğrulanır.

### 2.6 Diğer teknik düzeltmeler

| # | Bulgu | Karar |
|---|---|---|
| M3 | `verify_citations` cümle başına LLM çağrısı → 30 cümlelik raporda 30 çağrı | **Batch'lenecek:** tek çağrıda N cümle + atıflı claim'ler, `asyncio` ile 2–3 batch paralel |
| M4 | Prompt injection yapısal olarak ele alınmamış | **Tasarım maddesine yükseltildi.** Web içeriği her yerde untrusted data; enjekte edilen talimat claim'e dönüşemez (verbatim quote doğrulaması), sayı G4'ü geçemez, Gate LLM içermez. README'de Design Question 6'nın yanında anlatılacak |
| M6 | Claim embedding (D6) açık; L3 cluster ve L4 dedup ona bağlı | **D6 kapatıldı:** embedding birincil, run içinde tek model, provider yoksa lexical fallback (token Jaccard + entity eşleşmesi). Embedding'ler `sha256(model+text)` ile Postgres'te cache'lenir |
| M7 | CI yok | **Şimdilik eklenmiyor** (ücretsiz CI dakikası yok). Kalite kapısı = `pre-commit` (ruff, gitleaks, gofmt/go vet) + lokal `pytest` / `go test`. İleride bakılacak |
| M8 | README son güne planlı | **Şimdi iskelet kuruldu** (`README.md`): başlıklar + 8 Design Question yer tutucuları + Mermaid diyagram bölümü. Her faz sonunda ilgili bölüm doldurulur |

### 2.7 Düşük seviye notlar

- `.gitignore`'daki `*.csv` / `*.xml` kalıpları ileride eval set veya test fixture'ını sessizce yutabilir → o an hedefli negasyon (`!optimize/evalset.jsonl` benzeri) eklenecek.
- `mypy strict` doğru seçim; `langgraph`, `langfuse`, `presidio_analyzer` gibi stub'sız paketler için `ignore_missing_imports` override'ı gerekecek.
- Case PDF git geçmişinden de silindi (history rewrite + GC + force-push, 2026-09-16). GitHub tarafında eski object'ler sunucu GC'sine kadar doğrudan SHA ile erişilebilir kalabilir; repo private olduğu için kabul edilen risk.

---

## 3. Kapatılan açık kararlar (D6–D29)

Tümü `TODO.md` → Karar Günlüğü'ne ✅ olarak işlendi. Özet:

| # | Konu | Karar |
|---|---|---|
| D6 | Claim embedding | Embedding birincil (Gemini → OpenAI → Ollama), **run içinde tek model**, τ + aynı entity koşulu, provider yoksa lexical fallback, Postgres cache |
| D7 | Tam içerik | Provider raw content → yoksa `httpx` + `trafilatura`; yalnızca snippet triage'ı geçen top-K (tur 1: 5, follow-up: 3); timeout 10 sn + 1 retry; içerik boyutu cap'li; başarısızsa snippet ile devam (`FETCH_FAILED`) |
| D8 | Runtime | **Python 3.13** (`python:3.13-slim`), uv, FastAPI, Pydantic v2, SQLAlchemy 2 + alembic, asyncpg, httpx, tenacity, structlog, pytest + pytest-asyncio, trafilatura, MinHash kütüphanesi, LangGraph + Postgres checkpointer, Langfuse SDK, `cryptography` (Fernet) |
| D10 | Bonus kapsamı | Streaming, persistent state, tracing, parallel search, caching, cost tracking, citation verification, çoklu search provider, LLM fallback, semantic dedup, reranking → mimarinin parçası. Mini eval set = SHOULD. Human-in-the-loop = COULD (UI zaten çok ekranlı; plan onayı opsiyonel bayrak olarak eklenebilir) |
| D11 | Sonlandırma parametreleri | §2.5 |
| D12 | Örnek sorgular | 4 örnek: (1) KVKK 2026 SaaS aksiyon planı — TR, regülasyon, Recommendation/G11 vitrini · (2) ApilexAI ürün/partnerlik/strateji — case'in kendi örneği, az kaynak → Known Gaps · (3) EU AI Act uygulama takvimi — EN, tarih çelişkisi · (4) pazar büyüklüğü — sayısal çelişki → G4 + Conflicting bölümü |
| D17 | Frontend | Statik HTML + vanilla JS + SSE, build adımı yok, **çok ekranlı** hash router: `#/new` · `#/runs` · `#/runs/:id` · `#/costs` · `#/prompts` · `#/settings` |
| D24 | UI parametre ayarı | Pydantic şeması → `/api/config/schema` → otomatik gruplu form; run başına override; preset'ler DB'de; güvenlik-kritik alanlar `ui: false` ve sunucuda reddedilir; override'lar `config_snapshot`'a yazılır |
| D25 | Reasoning akışı | Her şemada `rationale` → SSE timeline; provider thought summaries opsiyonel bayrak (varsayılan kapalı); ham CoT gösterilmez ve saklanmaz |
| D29 | Ek araçlar | promptfoo = SHOULD (injection + regression, lokal script). BAML / Instructor yok. DSPy runtime'da değil — **birincil gerekçe:** kontrol akışı LangGraph'ta ve tek LLM erişim katmanı (fallback + redaction + tracing tek yerde); LiteLLM tedarik zinciri notu ikincil gerekçe |

### Yeni kararlar

| # | Konu | Karar |
|---|---|---|
| D30 | CLI | `research` CLI MUST — örnek üretimi, kalibrasyon, debug. Ürünün birincil yüzü UI |
| D31 | Repo teslimi | GitHub **private** + reviewer daveti, mail'de link. Case PDF repoda tutulmuyor. Reviewer `docker compose up` ile ayağa kaldırır |
| D32 | Gate G4 kovaları | §2.4 |
| D33 | Maliyet takibi | `llm_calls` + `runs` toplamı (birincil) **ve** Langfuse generation usage/cost. Model fiyatları `models.yaml`'da tek kaynak, Langfuse model pricing tanımıyla hizalanır. UI `#/costs` ekranı: run başına ve toplam maliyet, model/provider kırılımı, token, latency, cache hit oranı |

---

## 4. Sıradaki adımlar

1. `architecture_v0.6.md`: bu kararların mimariye yansıtılması (§7 bütçe, §11.2 G4, §15.2 çok ekranlı UI, §16 maliyet panosu, §17 yapıya `cli.py`, Python 3.13).
2. Faz 1 — altyapı iskeleti (`TODO.md`).
3. README bölümleri faz sonlarında doldurulur; 8 Design Question en son.
