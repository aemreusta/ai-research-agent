# Bağımsız Değerlendirme v1 — AI Research Agent

> Historical audit. Some findings were disproven in [evaluation v2](evaluation_v2.md). Current implementation and verification are in [evaluation v3](evaluation_v3.md).

> Tarih: 2026-09-16 · Kapsam: dört canlı run'ın çıktıları, agent çekirdeği, control plane, dokümantasyon
> Yöntem: sistemin kendi Gate'ine ve kendi analiz notlarına **güvenmeden** yeniden hesap; iddiaların bir kısmı web'den bağımsız olarak doğrulandı.
> Denetlenen commit: `f8fc007` · Girdi: `examples/*/state.json`, `report.md`, `gate_result.json`, kaynak kod

---

## 0. Özet

Sistem çalışıyor ve iddia ettiği şeylerin çoğunu gerçekten yapıyor. Dört run'da:

- **Sayısal destek kusursuz:** Rapor cümlelerindeki 0 sayı, atıf yapılan claim'lerde bulunmuyor. Öneri cümleleri dahil (G4 `finding_refs`'i de çözüyor). Halüsinasyon rakam yok.
- **Atıf bütünlüğü kusursuz:** Hiçbir cümle var olmayan bir cluster'a atıf yapmıyor, kaynak listesi atıflardan yeniden üretiliyor.
- **Alıntı temeli sağlam:** Claim'lerin %97–99'u kendi eşiklerinde (fuzzy ≥ 0.88) dokümanda bulunuyor.
- **Dürüstlük iyi:** ApilexAI run'ı kanıt bulamadığı yerde uyduruyor değil, `no_progress` ile durup Known Gaps yazıyor.

Buna karşılık üç sınıf sorun var ve ikisi **rapor içeriğini yanlış yapıyor**:

| # | Sınıf | En keskin örnek |
|---|---|---|
| A | Doğruluk: eski ama otoriter kaynak | KVKK raporu VERBİS eşiğini **25 milyon TL** diyor; 2026 eşiği **100 milyon TL** |
| B | Doğruluk: koşulun düşmesi | EU raporu "şeffaflık yükümlülükleri 2 Aralık 2026'ya ertelendi" diyor; gerçekte erteleme yok, **2 Ağustos 2026'dan önce piyasaya sürülmüş sistemler için** geçiş süresi var |
| C | Altyapı: dış garantinin delinmesi | Heartbeat atmaya devam eden takılı bir agent, dispatcher'ın hard deadline'ını süresiz atlatabiliyor (CAS her turda kaybediyor) |

Kaba reviewer tahmini: **~85/100**. Aşağıdaki P0 listesi kapanırsa 90+ bandına çıkar.

---

## 1. Bölüm A — Çıktı denetimi

### A1. (YÜKSEK, doğruluk) Eski birincil kaynak güncel olanı bastırdı — VERBİS eşiği

**Rapordaki cümle** (`kvkk-2026-saas-action-plan`, Temel Bulgular ve Öneri 5):
> "Yıllık çalışan sayısı 50'den veya yıllık mali bilanço toplamı **25 milyon TL**'den çok olan veri sorumluları VERBİS kayıt yükümlülüğüne tabidir."

**Gerçek:** 2026 için eşik **50 çalışandan az VE 100 milyon TL'den az**. Rakam eskimiş; kaynak `[15]` KVKK'nın eski bir "kayıt sürelerinin uzatılması" duyurusu (skor 0.73, birincil).

**Neden pipeline'dan geçti:**
- Skor formülünde otorite 0.35, güncellik 0.15. `kvkk.gov.tr` T1 olduğu için eski sayfa yüksek skor alıyor.
- Sorunun zaman kapsamı (2026) çıkarılıyor ama **claim'in `as_of`'u ile karşılaştırılmıyor**; kapsam dışı kalan bir değer için uyarı yok.
- Çelişki tespiti yalnızca *toplanan* claim'ler arasında çalışıyor. 100 milyon TL'lik güncel claim hiç toplanmadığı için ortada çelişki de yok.

Bu, sistemin en pahalı hata tipi: **yüksek skorlu, birincil, yanlış**. Hukuk alanında bir aksiyon planında doğrudan yanlış aksiyona götürür.

### A2. (YÜKSEK, doğruluk) Claim çıkarımı koşulu düşürüyor

**Rapordaki cümle** (`eu-ai-act-timeline`, Temel Bulgular):
> "The AI Omnibus postponed transparency obligations for AI-generated synthetic content to 2 December 2026."

**Gerçek:** Madde 50 şeffaflık yükümlülükleri ertelenmedi. 2 Ağustos 2026'dan **önce** piyasaya sürülmüş sistemler 2 Aralık 2026'ya kadar uyum için süre aldı; o tarihten sonra sürülenler için 2 Ağustos 2026'dan itibaren geçerli.

Aynı desen ikinci kez: yeni yasak için "applying from 2 December 2026" deniyor, oysa bu da geçiş süresi; yasağın kendisi yürürlükle birlikte geliyor.

**Mekanizma:** `Claim` modelinde `entity/attribute/value/unit/as_of` var ama **koşul/kapsam alanı yok**. "X, Y koşuluyla Z tarihinde" cümlesi "X, Z tarihinde" olarak defterlenip sentezde koşulsuz bir olguya dönüşüyor. Alıntı doğrulaması bunu yakalamaz: alıntı doğru, özet yanlış.

### A3. (ORTA) Tartışmalı tarih kesinmiş gibi sunuluyor

Rapor "Digital Omnibus … 29 Haziran 2026'da kabul edildi" diyor (kaynak: `euaicompass.com`, skor 0.35 + europa.eu). Bağımsız hukuk bültenleri **8 Temmuz 2026** kabul tarihini veriyor; yürürlük tarihi (27 Temmuz 2026) ve regülasyon numarası (2026/1744) ise **doğru** — EUR-Lex'te teyit ettim.

Cümle `_(single source)_` etiketli, yani sistem belirsizliği tamamen gizlemiyor. Ama etiket "tek kaynak" diyor, "kaynaklar ayrışıyor" demiyor; kullanıcı farkı anlayamaz.

### A4. (ORTA, doğruluk beyanı) "Verbatim" denilen alıntı aslında bulanık eşleşme

`quotes.py:20` → `FUZZY_THRESHOLD = 0.88` (kelime düzeyinde `SequenceMatcher`, rakamlar zorunlu). Mimari ve README ise "verbatim alıntı" diyor.

Ölçtüm: claim'lerin **%1–3'ü** kendi 0.88 eşiğinin altında kalıyor (74'te 2, 84'te 1, 33'te 1, 54'te 1 — sınırda, 0.87). Yani mekanizma çalışıyor; sorun **beyan**: reviewer bir alıntıyı `Ctrl-F` ile aradığında kayda değer bir kısmını harfiyen bulamaz ve "verbatim" iddiasına güveni sarsılır.

Ayrıca `state.json`'daki claim kaydı eşleşme tipini ve oranını taşımıyor; artefakt kendi kendini doğrulayamıyor.

### A5. (ORTA) Bağımsızlık sayımı facet düzeyinde yayıncıyı yok sayıyor

"Bir yayıncı tek sestir" kuralı cluster **içinde** uygulanıyor; facet yeterliliği ise cluster'lardan gelen `origin_ids`'i birleştirip sayıyor. Canlı veride kanıt:

- `eu-ai-act-timeline` / `s3f2`: `sufficient`, 2 origin, **tek yayıncı** (`europa.eu`).
- `apilexai` / `s4f2`: `sufficient`, 2 origin, yayıncılar `apilex.ai` + `apple.com` — ikincisi App Store listesi, yani **şirketin kendi metni üçüncü taraf barındırıcıda**. Teknik olarak farklı yayıncı, bilgi olarak aynı ses.

İlki yüksek skorlu birincil kaynak olduğu için sonuç yanlış değil, ama kural amaçlandığı gibi çalışmıyor: iki farklı sayfa = iki bağımsız doğrulama sayılabiliyor.

### A6. (DÜŞÜK) "Tek kaynak" etiketi ile gösterilen atıflar tutarsız görünüyor

`eu-ai-act-timeline`'da 8 cümle `single_source` etiketli olmasına rağmen 2–3 farklı yayıncıya atıf veriyor (etiket, cümlenin dayandığı cluster'lardan **herhangi biri** tek kaynaklıysa düşüyor). Davranış muhafazakâr yönde yanlış, yani zararsız; ama reviewer "3 kaynak var, neden tek kaynak diyor?" diye takılır. Etiketi cümle düzeyinde değil **bulgu düzeyinde** göstermek ya da "bir bulgusu tek kaynak" ifadesini kullanmak gerekir.

### A7. Denetimin doğruladığı güçlü yanlar

| Kontrol | Sonuç |
|---|---|
| Rapordaki sayıların atıflı claim'lerde bulunması | 4 run, **0 ihlal** (öneri cümleleri dahil) |
| Var olmayan cluster'a atıf | 0 |
| Kaynak listesi ↔ atıf tutarlılığı | tam |
| Alıntı temeli (kendi 0.88 eşiği) | %97–99 |
| Çelişki sınıflandırması | `true_conflict` yalnızca gerçek olanda (legal-tech 2030: 10.31 vs 11.58 mlr USD), `different_time`/`different_scope`/`consistent` yerinde |
| Kanıt yoksa durma | ApilexAI: `no_progress`, uydurma yok, Known Gaps dolu |
| Birincil kaynak oranı | KVKK 7/15, EU 4/9 atıflı doküman birincil |

---

## 2. Bölüm B — Sistem denetimi (doğrulanmış bulgular)

Ağırlık sırasına göre. "Doğrulandı" = kodu okuyup teyit ettim; "olası" = kod okumasına dayanıyor, canlı tekrarlanmadı.

### P0 — teslim öncesi kapanmalı

**B1. Hard deadline, heartbeat atan takılı agent'ta devre dışı kalıyor** (doğrulandı — `dispatcher/internal/store/store.go:207`)
`transition()` her CAS'a `heartbeat_at IS NOT DISTINCT FROM $3` koşulunu ekliyor. Deadline'ı aşmış `running` bir run'ı `failed` yapmak da aynı yoldan geçiyor. Agent 10 sn'de bir heartbeat atıyor, watchdog 5 sn'de bir bakıyor: gözlenen heartbeat ile CAS anındaki heartbeat farklıysa `ErrLostRace` dönüyor ve `WatchOnce` sessizce `continue` ediyor. Sonuç: canlı ama takılmış bir agent, "hiçbir agent bug'ının aşamayacağı" denen sınırı süresiz aşabilir. Heartbeat koruması `Requeue` için doğru, deadline kararı için gereksiz.

**B2. SSE, run 200'den fazla olay biriktirip biterse kuyruğu düşürüyor** (doğrulandı — `agent/src/research_agent/api/sse.py:121-127`)
Backfill döngüsü `len(batch) < _BATCH` olana kadar dönüyor; canlı döngü dönmüyor. Tek bir 200'lük batch yayınlanıp durum terminal ise `run_closed` gönderilip stream kapanıyor — aradaki olaylar (raporun hazır olduğu olayı dahil) hiç gitmiyor.

**B3. Heartbeat'te sahiplik çiti yok** (olası — `db/repository.py:213-218`)
Heartbeat kaybı sonrası requeue edilen bir run'ı ikinci replika alırken, birinci replikanın heartbeat görevi hâlâ aynı satırı damgalıyor. `ExecuteRequest` yalnızca `attempt` taşıyor, fencing token yok. İkinci replika ölürse birincinin damgaları bunu maskeler.

**B4. Kaybeden çift çalıştırma yine artefakt yazıyor** (olası — `agent_server/executor.py:287-311`)
`_settle` artefaktları `finish()` CAS'ından **önce** commit ediyor. CAS kaybedilince istisna yutuluyor ama artefaktlar kalıyor; `export_run` `(run_id, kind)` üzerinde `scalar_one_or_none()` yaptığı için `MultipleResultsFound` → export 500.

**B5. Gate remediation bir tur fazla çalışıyor** (doğrulandı — `gate/runner.py:278-288`)
`while violations and rounds < max_rounds + 1` + döngü sonundaki `break`: `max_remediation_rounds=1` iken iki tur, `0` iken bir tur çalışıyor. Yani Gate hiçbir konfigürasyonda "yalnızca raporla, düzeltme" moduna alınamıyor — dokümandaki "en fazla N tur" ifadesi de yanlış.

**B6. `execute` çakışmasında 500 ve yanlış hata kodu** (olası — `agent_server/app.py:142-158`)
Sahipli bir run'a gelen geç `execute`, `IllegalTransitionError` → handler yok → 500. Go istemcisi bunu `ErrUnreachable`'a eşliyor, zaman çizelgesine `AGENT_UNREACHABLE` düşüyor. Doğrusu 409 ve "zaten çalışıyor".

### P1 — kalite ve doğruluk

**B7.** `agent/coverage.py:44` — facet yeterliliğinde origin'ler yayıncıya göre tekilleştirilmiyor (Bölüm A5'in kod tarafı).
**B8.** `agent/nodes/intake.py` — skill seçimi ve skill'in eklediği domain tier'ları yalnızca `analyze_query` içinde `deps`'e yazılıyor; `state.skills` yazılıyor ama geri okunmuyor. Checkpoint'ten devam eden run skill enjeksiyonunu ve tier'ları **kaybediyor**; aynı doküman crash öncesi ve sonrası farklı skorlanır.
**B9.** `agent/nodes/report.py:379-382` — yeniden sentez `LLMFailure` verirse, eski rapora ait cümle indeksleriyle yeni rapordan cümle siliniyor: desteklenen bir bulgu silinebilir.
**B10.** `agent/nodes/search.py:282-283` — `searches_left()` semafor içinde kontrol ediliyor, sayaç sonra artıyor: `max_searches` en fazla `max_parallel_searches-1` kadar aşılabilir.
**B11.** `agent/coverage.py:38-43` — facet eşleşmesi claim'in `facet_id`'sine bağlı; model bu alanı boş bırakırsa çok facet'li alt soru asla `sufficient` olamaz. **Canlı veride tetiklenmiyor** (4 run, 245 claim, `facet_id` boş olan 0), ama simüle LLM tek facet ürettiği için test de bunu görmüyor. Fallback + test gerekir.
**B12.** `agent/text.py:176` — metinde tek bir Türkçe karakter varsa dil "tr". "Türkiye"den söz eden İngilizce rapor G10 uyarısı alır.
**B13.** `config/schema.py:66` — `budget.max_wall_clock_seconds` (≤7200) `tunable`; dispatcher deadline'ı `GREATEST(hard_deadline, wall_clock+margin)` ile kurduğu için run başına override belgelenen 1800 sn tavanını 7260 sn'ye çıkarabiliyor. Davranış savunulabilir, **doküman** yanlış.
**B14.** `dispatcher/internal/config/config.go:74-85` — `watchdog.cancel_grace_seconds` pozitiflik doğrulamasında yok; 0 verilirse nazik iptal yolu tamamen ölür.

### P2 — küçük

`events.py:255-263` (`**redacted_data` splat → `data` içinde `node`/`run_id` varsa satır yazıldıktan sonra `TypeError`) · `runs.py:104` (B1 sınırında `PII_ENGINE_DEGRADED` olayı üretilmiyor) · `store.go:126-131` (watchlist sorgusu indeks kullanamıyor, 5 sn'de bir seq scan) · `sse.py:92` (izleyici başına bir PG bağlantısı, global kanal) · `listen.go:399` (reconnect sonrası `Wake()` yok) · `store.go:263` (`expected` sabit `true`, taksonomi okunmuyor) · `repository.py:159` (`--persist` CLI run'ı öldürülürse watchdog onu gerçek agent'a kuyruklar).

### Dokümantasyon bulguları

| # | Bulgu | Etki |
|---|---|---|
| D1 | Quickstart `.env`'i opsiyonel gösteriyor ama Langfuse dahil tüm observability servisleri `observability` profilinde ve `COMPOSE_PROFILES` yalnızca `.env.example`'da. README'yi harfiyen izleyen reviewer `localhost:3000`'de bağlantı hatası alır | Yüksek — %20 ağırlıklı iki başlığın vitrini |
| D2 | Maliyet ve süre README'de hiçbir yerde yok ($0.08–0.20, 100–140 sn `examples/ANALYSIS.md`'de duruyor) | Yüksek — key yapıştıracak reviewer ne onayladığını bilmiyor |
| D3 | 15 dakikalık yol işaretlenmemiş; `make demo` (keysiz tur) ve `make examples` gömülü | Yüksek |
| D4 | README §13 her örnekte `report.json` olduğunu söylüyor; yalnızca offline demoda var | Orta — ilk bakılan yerde yanlış beyan |
| D5 | Design Questions 437. satırda, içindekiler yok | Orta |
| D6 | `examples/ANALYSIS.md` (reponun en güçlü belgesi) yalnızca dipnottan linkli | Orta |
| D7 | "verbatim alıntı" ifadesi gerçekte 0.88 fuzzy (A4) | Orta |
| D8 | `architecture_v0.6.md:17` olmayan dosya adları sayıyor (`gate/remediation.py`, `prompting/signature.py`) | Düşük |
| D9 | TODO'daki "Design Questions → kaynak" tablosu hâlâ v0.4 bölüm numaralarına işaret ediyor | Düşük |

Doğrulanan beyanlar (alt denetimde teyitli): 476 Python testi, 70 Go testi, dispatcher'ın key tutmayı reddetmesi, G1–G11'in gerçekten 11 kural olması, `ANALYSIS.md`'deki tüm run metriklerinin `state.json` ile birebir tutması.

---

## 3. Bölüm C — Reviewer simülasyonu

| Alan | Ağırlık | Tahmin | Gerekçe |
|---|---|---|---|
| Agent Orchestration & Reasoning Flow | 25 | **22** | Saf router + `RouteDecision` kaydı, aritmetikle türetilen `recursion_limit`, facet tabanlı kapsama, alt soru durum makinesi. Kayıp: facet düzeyinde yayıncı sızıntısı (A5/B7), durgunluk semantiği dokümandan farklı |
| Search & Retrieval Strategy | 20 | **17** | Tur genişliği, snippet triage, her üçüncü sorgu Brave, altı katmanlı dedup, hedefli follow-up. Kayıp: eski kaynak filtresi yok (A1), `max_searches` aşımı (B10) |
| Reliability & Edge Case Handling | 15 | **11.5** | Ayrı control plane, CAS geçişler, checkpoint resume, tablo testli watchdog. Kayıp: B1 (dış garanti delinebiliyor), B2, B3, B4 |
| LLM / Prompt Engineering | 15 | **13** | Signature katmanı, şema hash korumalı registry, repair retry, skill enjeksiyonu, GEPA. Kayıp: koşul düşmesi (A2), remediation off-by-one (B5) |
| Software Architecture & Code Quality | 15 | **13.5** | İki dilli kontrat tek kaynakta, `locked()/tunable()` konfig, temiz modül sınırları, distroless imaj |
| Testing & Observability | 5 | **4.5** | 546 test, PG'de olay deposu, Langfuse, run başına config hash. Kayıp: simüle LLM idealize (B11'i görünmez kılıyor) |
| Documentation & Design Decisions | 5 | **4** | Kapsam tam, 8 sorunun hepsi cevaplı; ama Quickstart tuzağı ve yanlış `report.json` beyanı |
| **Toplam** | 100 | **~85.5** | P0 listesi kapanırsa 90+ |

**Reviewer'ın ilk 15 dakikası:** `git clone` → README §1 → `docker compose up` → `localhost:8000`. Şu an bu yolda üç sürtünme var: `.env` kopyalanmadığı için Langfuse yok (D1), ne kadar süreceği ve ne kadar tutacağı yazmıyor (D2), ilk soruyu nereye yazacağı söylenmiyor (D3). Üçü de birer cümlelik düzeltme ve puanın en ucuz kısmı.

**Reviewer'ı en çok etkileyecek üç şey:** `examples/ANALYSIS.md`'deki "canlı veride şu 8 sorunu bulduk, şöyle düzelttik, testi bu" tablosu; Output Gate'in G4 kovaları; Go/Python kontrat paylaşımı ve `decide.go`'nun saf karar fonksiyonu.

---

## 4. Öncelik sırası

1. **B1, B2, B5** — doğrulanmış, tek dosyalık düzeltmeler; ikisi mimarinin en çok reklamı yapılan garantilerine dokunuyor.
2. **A1, A2** — çıktının doğruluğu. Güncellik cezası + `conditions` alanı. İkisi için de elimizde birer regresyon vakası var.
3. **D1, D2, D3, D4, D7** — beş cümlelik doküman düzeltmeleri, en yüksek puan/efor oranı.
4. **B3, B4, B6, B7** — yarış durumları ve bağımsızlık sayımı.
5. **B8–B14** + eval harness + dayanıklılık matrisi (bkz. `claude-code-brief_v1.md`).
