# Claude Code Brief v1 — düzeltmeler, eval harness, dayanıklılık kanıtı

> Historical plan, superseded by [evaluation v2](evaluation_v2.md) and the [completed improvement status](improvement_status.md). Do not apply disproven v1 findings or redo completed work.

> Girdi: `docs/review/evaluation_v1.md` (bulguların gerekçesi ve kanıtı orada).
> Bu belge uygulama planıdır: ne değişecek, hangi dosyada, kabul kriteri ne, hangi komutla doğrulanacak.
> Sıra bağlayıcıdır: WP1 → WP2 → WP3 önce; WP4–WP6 sonra.

## 0. Çalışma kuralları

- Her WP kendi dalında: `fix/<wp-id>-<kısa-ad>`. Commit mesajı repodaki üslubu izlesin (`fix(ledger): ...`).
- Her düzeltmenin **önce başarısız olan bir testi** yazılsın, sonra kod. Testsiz düzeltme kabul edilmiyor.
- Doğrulama sırası: `make lint` → `make test` → `make test-integration` → ilgili canlı senaryo.
- Davranış değiştiren her madde için `TODO.md` karar günlüğüne bir satır, gerekiyorsa `docs/design/architecture_v0.6.md`'ye bir cümle.
- Canlı run gerektiren adımlarda maliyet ~$0.20/run. Gereksiz tekrar yok; `examples/` çıktıları ancak davranış değiştiyse yenilensin.

---

## WP1 — P0 düzeltmeleri (doğrulanmış, teslimi bloklar)

### WP1.1 Hard deadline'ı heartbeat yarışından kurtar
**Dosya:** `dispatcher/internal/store/store.go` (`transition`, ~satır 196-207), çağıranlar `Fail`/`Cancel`/`Requeue`.
**Sorun:** CAS her zaman `heartbeat_at IS NOT DISTINCT FROM $3` içeriyor. Deadline'ı aşmış ama heartbeat atmaya devam eden bir run'ı `failed` yapmak imkânsız hale geliyor; `WatchOnce` `ErrLostRace`'i yutuyor (`dispatcher.go:271`).
**Yapılacak:**
- `transition`'a "heartbeat guard" parametresi ekle. `Requeue` (heartbeat kaybı) guard'lı kalsın; **deadline kaynaklı `Fail` ve `Cancel` guard'sız** olsun (`WHERE id=$1 AND status=$2` yeterli, durum makinesi zaten koruyor).
- `WatchOnce`'ta `ErrLostRace`'i sessiz geçme: `level=warn`, `error_code=DISPATCH_LOST_RACE` olayı yaz (yeni kod `contracts/error_codes.yaml`'a eklensin, Python tarafı da tanısın).
**Test:** `store_test.go` — deadline'ı geçmiş, her turda `heartbeat_at`'i değişen bir run; watchdog tek turda `failed (DEADLINE_EXCEEDED)` yazmalı. Ayrıca `decide_test.go`'da guard seçiminin karar tipine bağlı olduğunu doğrula.
**Kabul:** `go test ./...` yeşil; WP6.8 canlı senaryosunda run 1 dakika içinde `DEADLINE_EXCEEDED` ile kapanıyor.

### WP1.2 SSE kuyruk düşürmesi
**Dosya:** `agent/src/research_agent/api/sse.py:105-127`.
**Yapılacak:** Canlı döngüde de backfill mantığı: `while True: batch=_fetch(...); yield...; if len(batch)==_BATCH: continue` — terminal kontrolü ancak `len(batch) < _BATCH` olduğunda yapılsın.
**Test:** `tests/api/test_api.py` — 250 olay + terminal durum yazılmış bir run'a bağlan; tüm `seq`'lerin geldiğini ve `run_closed`'ın **en sonda** geldiğini doğrula.

### WP1.3 Gate remediation tur sayısı
**Dosya:** `agent/src/research_agent/gate/runner.py:278-288`.
**Yapılacak:** `while violations and rounds < max_rounds`. `max_remediation_rounds=0` → hiç düzeltme yapılmasın, ihlaller raporlansın (report-only mod).
**Test:** `tests/unit/gate/test_gate.py` — `max_rounds=0` (0 tur, verdict `fail`, cümle silinmemiş) ve `max_rounds=1` (tam 1 tur) vakaları.

### WP1.4 Heartbeat sahiplik çiti
**Dosyalar:** `contracts/agent-api.openapi.yaml` (`ExecuteRequest`'e `lease_id`), `dispatcher/internal/store/store.go` (claim'de `lease_id = gen_random_uuid()`), `agent/src/research_agent/db/repository.py` (`mark_running`, `heartbeat`, `finish` → `WHERE lease_id = $x`), `agent_server/executor.py`.
**Yapılacak:** Her claim yeni bir `lease_id` üretsin; agent yalnızca kendi lease'i ile heartbeat/finish yazabilsin. Eski lease'in yazması sessizce 0 satır etkilesin ve agent'ta görev iptal edilsin.
**Test:** `tests/integration/test_repository.py` — requeue sonrası eski lease ile heartbeat 0 satır; yeni lease çalışıyor. Migration: `runs.lease_id` (nullable, index'siz).

### WP1.5 Çift çalıştırmada artefakt sızıntısı
**Dosya:** `agent_server/executor.py:287-327`.
**Yapılacak:** `finish()` CAS'ı **önce** çalışsın; kazanılırsa aynı transaction içinde artefaktlar + sayaçlar yazılsın, kaybedilirse hiçbir şey yazılmasın. `RunArtifact` için `(run_id, kind)` üzerine **unique constraint** ekle (migration) — ikinci savunma hattı.
**Test:** İki eşzamanlı `_settle` → tek artefakt seti, `export_run` 200.

### WP1.6 Sahipli run'a gelen `execute` → 409
**Dosyalar:** `agent_server/app.py` (AgentException handler, `IllegalTransitionError` → 409 `ALREADY_RUNNING`), `dispatcher/internal/agentclient/client.go:135` (409 → `ErrAlreadyOwned`, başka replikaya geçme, olay `level=info`).
**Test:** `tests/api/test_agent_server.py` + `client_test.go` + contract testi.

---

## WP2 — Çıktı doğruluğu

### WP2.1 Eski kaynak tuzağı (VERBİS vakası)
**Hedef:** Sorunun zaman kapsamı dışında kalan bir kaynaktan gelen **değer** claim'i, sırf domain otoritesi yüksek diye rapora girmesin.
**Yapılacak:**
1. `Claim`'e `source_published_at` (dokümandan) zaten var → `agent/scoring.py` ve `agent/clustering.py` içinde **staleness sinyali**: `analysis.time_scope.start` biliniyorsa ve doküman tarihi ondan eskiyse cluster'a `stale_candidate=True`.
2. `assess_facets`: bir facet yalnızca `stale_candidate` cluster'larla `sufficient` olmasın (en az bir kapsam içi kaynak ya da kapsam içi doğrulama gerekli); aksi halde follow-up sorgusu üret (`purpose=FRESHNESS`).
3. `synthesize` girdisinde her bulgunun tarihi zaten var; prompt'a "kapsam dışı tarihli değerleri 'X tarihi itibarıyla' diye niteleyerek yaz" kuralı eklensin.
4. Yeni Gate kuralı **G12 (warn)**: sorunun zaman kapsamı varsa ve cümledeki değer yalnızca kapsam dışı kaynaklara dayanıyorsa uyarı + etiket.
**Regresyon fixture'ı:** `kvkk-2026-saas-action-plan/state.json`'daki `VERBİS 25 milyon TL` claim'i. Beklenen yeni davranış: ya "2021 duyurusuna göre" niteleyicisiyle çıkması ya da tazelik follow-up'ı sonrası 100 milyon TL ile değişmesi.
**Kabul:** `make test` yeşil + KVKK sorusu canlı tekrar çalıştırıldığında rapor 25 milyon TL'yi niteliksiz bir 2026 kuralı olarak sunmuyor.

### WP2.2 Koşul düşmesi (EU şeffaflık vakası)
**Dosyalar:** `agent/prompting/schemas.py` (`ExtractedClaim`), `config/prompts/extract_claims.yaml`, `config/prompts/synthesize.yaml`, `agent/state.py` (`Claim.conditions: str | None`), `agent/nodes/report.py`.
**Yapılacak:**
1. `ExtractedClaim`'e `conditions` alanı: "değerin geçerli olduğu kapsam/koşul, kaynakta yazıyorsa; yoksa null" (ör. "2 Ağustos 2026'dan önce piyasaya sürülmüş sistemler için").
2. Şema değişikliği → `output_schema_hash` değişir → `PromptRegistry` otomatik olarak repo seed'ini yeniden yayınlar (mevcut mekanizma, `prompting/registry.py:80-93`). Seed sürümünü artır.
3. `synthesize` prompt'u: koşullu bir bulgu koşulsuz yazılamaz; koşul cümleye ya da parantez içine taşınır.
4. Gate **G13 (warn)**: cümlenin dayandığı cluster'lardan biri `conditions` taşıyorsa ve cümlede o koşulun anahtar kelimeleri yoksa uyarı.
**Regresyon fixture'ı:** `eu-ai-act-timeline` — "transparency obligations postponed to 2 December 2026" cümlesi. Beklenen: koşul cümlede görünsün.

### WP2.3 Facet düzeyinde yayıncı bağımsızlığı
**Dosya:** `agent/coverage.py:38-50`.
**Yapılacak:** `origins` kümesini doğrudan saymak yerine, cluster'lardaki dokümanların **yayıncı kimliğine** (`site_of`) göre tekilleştir; "≥2 bağımsız origin" kuralı "≥2 farklı yayıncı" anlamına gelsin. `has_primary and score ≥ 0.7` dalı olduğu gibi kalsın (tek birincil kaynak yeterliliği bilinçli).
**Test:** `tests/unit/agent/test_ledger_logic.py` — aynı yayıncının iki sayfası tek sayılıyor; `examples/eu-ai-act-timeline` replay'inde `s3f2` artık "2 bağımsız origin" dalından geçmiyor.
**Not:** `apple.com` App Store listesi gibi "üçüncü tarafta barındırılan birinci taraf içerik" için `config/domain_tiers.yaml`'a `first_party_hosts` listesi ekle (apps.apple.com, play.google.com, linkedin.com, crunchbase.com): bu hostlardaki içerik, konu olan şirketin kendi sesi sayılsın.

### WP2.4 "Tek kaynak" etiketinin anlamı
**Dosya:** `agent/nodes/report.py` (etiketleme), `config/prompts/synthesize.yaml`.
**Yapılacak:** Etiket cümle düzeyinde değil bulgu düzeyinde uygulansın ("bir bulgusu tek kaynak") ya da metin "kısmen tek kaynak" olsun. `README §9`'da da açıklansın.

---

## WP3 — Dokümantasyon (en yüksek puan/efor)

1. **README §1:** `cp .env.example .env` zorunlu adım olsun; altına "Langfuse `COMPOSE_PROFILES=observability` ile gelir; `.env` kopyalamazsanız hafif modda çalışırsınız" cümlesi.
2. **README §1'e bir satır:** "Tipik run: 10–20 arama, ~2 dakika, $0.20'nin altında." + `examples/ANALYSIS.md` linki.
3. **README §1 sonuna 3 adımlık reviewer yolu:** key gir → şu soruyu yapıştır → canlı akışı izle; altına "keysiz bakmak için `make demo`".
4. **README §13:** `report.json` beyanını düzelt (yalnızca offline demoda var); `examples/README.md` ve `examples/ANALYSIS.md`'ye açık link.
5. **README başına 6 satırlık içindekiler**, Design Questions'a (§16) doğrudan bağlantı.
6. **"verbatim" → "alıntı doğrulaması (tam eşleşme ya da ≥0.88 bulanık eşleşme, rakamlar zorunlu)"** — README, `architecture_v0.6.md`, `quotes.py` docstring.
7. `architecture_v0.6.md:17` dosya adlarını düzelt; `TODO.md`'deki Design Questions tablosunu v0.6 bölüm numaralarına güncelle.
8. **Yeni:** `state.json`'daki claim kaydına `quote_match: {type: exact|fuzzy, ratio: 0.93}` ekle — artefakt kendi kendini doğrulasın (`agent/quotes.py` zaten üretiyor, sadece taşınmıyor).

---

## WP4 — P1 sağlamlaştırma

| # | Dosya | Düzeltme | Test |
|---|---|---|---|
| 4.1 | `agent/nodes/intake.py`, `agent/research.py` | Resume'da `state.skills` / `state.skill_domains` geri yüklensin (skill guidance + tier genişletmesi) | `test_research_graph.py` resume testine skill assertion'ı |
| 4.2 | `agent/nodes/report.py:379` | Yeniden sentez başarısızsa `unsupported` indeksleri **atılsın**, fallback rapordan cümle silinmesin | Yeni senaryo testi |
| 4.3 | `agent/nodes/search.py:282` | Sayaç semafor içinde ve rezervasyonla artsın (`reserve → search → release`) | Paralel fake provider ile `max_searches` aşılmıyor |
| 4.4 | `agent/coverage.py:38` | `facet_id` boş gelen claim'ler için fallback: sorgunun `facet_id`'si üzerinden eşleştir | `facet_id=None` üreten fake extractor ile test |
| 4.5 | `agent/text.py:176` | Dil tespiti tek Türkçe karaktere değil, stopword oylarına dayansın (TR karakter = ağırlıklı oy) | "Türkiye" geçen İngilizce metin `en` |
| 4.6 | `config/dispatcher.yaml`, `config/schema.py` | `cancel_grace_seconds` pozitiflik doğrulamasına girsin; `max_wall_clock_seconds` için `hard_deadline`'ı aşamaz kuralı **ya da** README'de "hard deadline, run kendi bütçesini büyütürse büyür" düzeltmesi | Config testi |
| 4.7 | `observability/events.py:255` | `**redacted_data` splat yerine `data=redacted_data` | Anahtar çakışması testi |
| 4.8 | `api/routes/runs.py:104` | B1 sınırında `PII_ENGINE_DEGRADED` olayı + yanıt bayrağı | API testi |
| 4.9 | `dispatcher/internal/store/store.go:126` | Watchlist sorgusu için kısmi indeks (`status IN ('dispatched','running')`) | Migration + `EXPLAIN` notu |
| 4.10 | `dispatcher/internal/dispatcher/listen.go:399` | Reconnect sonrası `Wake()` | Birim test |
| 4.11 | `dispatcher/internal/store/store.go:263` | `expected` bayrağı `error_codes.yaml`'dan okunsun | Contract testi |
| 4.12 | `db/repository.py:159` | `create_local` run'ları watchdog'un görmeyeceği bir işaretle (`agent_id='cli'` + `deadline_at NULL` + watchlist filtresi) | Integration testi |

---

## WP5 — Eval harness (yeni yetenek)

**Amaç:** "sistem çalışıyor" yerine "sistem şu metriklerde şu seviyede, ve regresyon CI'da yakalanıyor" diyebilmek. Case'in bonus listesindeki *evaluation dataset* + *automated agent evaluation* maddelerini kapatır.

### 5.1 Paket yapısı
```
eval/
├── dataset/cases.yaml          # canlı değerlendirme soruları + beklentiler
├── fixtures/                   # commit'li state.json'lar (regresyon, ağsız)
├── metrics.py                  # tüm metrikler, tek girdi: ResearchState + Report + GateResult
├── runner.py                   # offline (fixture) ve live (API) modları
├── thresholds.yaml             # CI kapısı
└── report.py                   # markdown + JSON çıktı
```
CLI: `research eval --offline` (varsayılan, ağsız) · `research eval --live --cases 4 --max-cost 1.00`.
Make: `eval` (offline), `eval-live`.

### 5.2 Metrikler (hepsi deterministik, LLM gerektirmez)
| Metrik | Tanım | Hedef |
|---|---|---|
| `citation_coverage` | atıflı fact cümlesi / tüm fact cümleleri | 1.00 |
| `unsupported_number_rate` | remediation **öncesi** G4 ihlali / cümledeki toplam sayı | ≤ 0.02 |
| `quote_exact_rate` / `quote_fuzzy_rate` | alıntının dokümanda tam / ≥0.88 bulunma oranı | fuzzy ≥ 0.98 |
| `independent_publisher_ratio` | raporda kullanılan bulgulardan ≥2 farklı yayıncıya dayananların oranı | ≥ 0.40 |
| `publisher_concentration` | atıflı dokümanlarda tek yayıncının en yüksek payı | ≤ 0.50 |
| `primary_source_ratio` | atıflı dokümanlarda birincil oranı | regülasyon sorularında ≥ 0.40 |
| `stale_value_rate` | zaman kapsamı dışı kaynağa dayanan değer claim'i oranı | ≤ 0.05 (WP2.1 sonrası) |
| `single_source_share` | tek kaynak etiketli cümle oranı | raporlanır |
| `gate_verdict`, `removed_sentences`, `remediation_rounds` | Gate çıktısı | `fail` = 0 |
| `contradiction_kinds` | fixture'larda etiketli beklenen türle karşılaştırma | precision ≥ 0.8 |
| `budget` | rounds, searches, tokens, cost_usd, elapsed | bütçe içinde |
| `stop_reason` dağılımı | — | raporlanır |

### 5.3 Vakalar (`dataset/cases.yaml`)
Mevcut dördü + üç yeni tuzak vakası:
- **stale-trap:** cevabı son 12 ayda değişmiş, eski otoriter sayfası hâlâ üst sıralarda olan bir mevzuat sorusu (VERBİS eşiği doğal aday). Beklenti: güncel değer ya da niteleyici.
- **conditional-trap:** cevabı koşullu bir yükümlülük tarihi (EU şeffaflık vakası). Beklenti: koşul raporda.
- **no-evidence:** kasıtlı olarak kaynak bulunmayan soru ("… şirketinin 2027 ciro hedefi"). Beklenti: `no_progress`/Known Gaps, uydurma yok, `citation_coverage` bozulmadan.
Her vakada `expect`: `must_contain_entities`, `must_not_state`, `min_primary_ratio`, `expected_stop_reason`, `max_cost_usd`.

### 5.4 Offline regresyon
`eval/fixtures/` içine commit'li `state.json`'lar (mevcut dört run + WP2 vakaları). `research eval --offline` bunları yeniden değerlendirir: Gate'i baştan koşturur, metrikleri hesaplar, `thresholds.yaml` ile karşılaştırır. **Ağ ve key gerektirmez**, CI'da çalışır.
`make lint test eval` = PR kapısı.

### 5.5 Langfuse bağlantısı
`--live` modunda: vakalar Langfuse **dataset**'i olarak yüklensin, her koşu **experiment** olsun, metrikler `score` olarak yazılsın. Böylece prompt versiyonu ↔ metrik ilişkisi Langfuse'ta görünür ve WP2'deki prompt değişikliklerinin etkisi ölçülebilir.

### 5.6 Kabul kriteri
- `make eval` ağsız çalışıyor, çıktı `eval/runs/<ts>/report.md`.
- Dört mevcut fixture eşikleri geçiyor; `stale_value_rate` ve `conditions` metrikleri WP2 öncesi **kırmızı**, sonrası yeşil (yani harness gerçekten bir şey ölçüyor).
- README'ye 10 satırlık "Evaluation" bölümü + örnek tablo.

---

## WP6 — Dayanıklılık matrisi (kanıt üretimi)

Her senaryo için: komut → beklenen `error_code` ve olay → run'ın son durumu. Çıktılar `examples/resilience/<senaryo>/{command.txt, events.jsonl, note.md}` altına kaydedilsin, README'den linklensin. TODO'da açık kalan tek madde (`DEADLINE_EXCEEDED` canlı gösterimi) burada kapanır.

| # | Senaryo | Nasıl tetiklenir | Beklenen |
|---|---|---|---|
| 6.1 | Search timeout | `search.yaml`'da Tavily `base_url`'u yutan bir porta çevir (`http://127.0.0.1:9`), tek run | `SEARCH_TIMEOUT` → retry → Brave fallback → run tamamlanıyor |
| 6.2 | Geçersiz search key | `.env`'de Tavily key'i boz | `SEARCH_AUTH`, fail-fast, UI'da "key geçersiz" |
| 6.3 | LLM auth | Gemini key'i boz, OpenAI dursun | `LLM_AUTH` → `LLM_FALLBACK_USED` → run tamamlanıyor |
| 6.4 | Bozuk structured output | `FakeLLM` ile 1. denemede geçersiz JSON | `LLM_INVALID_OUTPUT` → repair → başarılı |
| 6.5 | Boş sonuç | Anlamsız sorgu (`"zxqw qweor"`) | `SEARCH_EMPTY` → broaden → `no_progress`, Known Gaps |
| 6.6 | Max iteration | `max_iterations=1`, geniş soru | `MAX_ITERATIONS`, rapor + Known Gaps |
| 6.7 | Agent çöküşü | Run ortasında `docker compose kill agent` | 30 sn içinde `AGENT_HEARTBEAT_LOST` → requeue → ikinci replika checkpoint'ten devam |
| 6.8 | Deadline | `hard_deadline_seconds=60`, uzun soru; ayrıca **WP1.1 regresyonu**: agent'ı `SIGSTOP` yerine heartbeat atar halde takılı bırakan bir test kancası | `DEADLINE_EXCEEDED`, run `failed`, kısmi state saklı |
| 6.9 | Presidio kapalı | `docker compose stop presidio-analyzer` | `PII_ENGINE_DEGRADED`, regex maskeleme, run devam |
| 6.10 | Langfuse kapalı | `docker compose stop langfuse-web` | `PROMPT_REGISTRY_DEGRADED`, YAML seed'e düşüş, run devam |
| 6.11 | İptal | Run ortasında UI'dan cancel | `cancelled`, node sınırında duruyor, `run_secrets` silinmiş |
| 6.12 | Çift dispatcher | `docker compose up --scale dispatcher=2` | Aynı run iki kez dispatch edilmiyor (`SKIP LOCKED`), olay zaman çizelgesi tek |

Her senaryodan sonra `SELECT status, stop_reason, error_code FROM runs ORDER BY created_at DESC LIMIT 1;` çıktısı da not'a eklensin.

---

## WP7 — Teslim kontrol listesi

1. Temiz klon → `cp .env.example .env` → key'ler → `make up` → `/readyz` üç bağımlılık yeşil.
2. `make test` + `make test-integration` + `go test ./...` + `make eval` yeşil.
3. Dört örnek WP2 sonrası **bir kez** yenilendi; `examples/ANALYSIS.md`'ye "v2 → final" satırı eklendi (nelerin değiştiği).
4. `examples/resilience/` dolu, README'den linkli.
5. Secret taraması: `git log -p | grep -iE "AIza|sk-|tvly-|BSA"` boş.
6. README'de içindekiler, maliyet/süre satırı, reviewer yolu, doğru `report.json` ifadesi.
7. Repo private + reviewer daveti; mail'de: repo linki, 3 satırlık "ne yaptım", `make up` + ilk soru, `examples/ANALYSIS.md` ve `docs/review/evaluation_v1.md`'ye işaret.

> Not: `docs/review/evaluation_v1.md` reviewer'a açık bırakılmalı mı, bu bir tercih. Lehine: kendi sisteminin zayıf yerlerini bulup sıraya koymuş bir mühendis izlenimi güçlü bir sinyaldir. Aleyhine: kapanmamış maddeler dikkat çeker. Kapanmayanları "bilinen sınırlar" başlığına taşımak ikisinin ortası.
