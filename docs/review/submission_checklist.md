# Teslim kontrolü — 17 Eylül 2026

İstenen teslim kalemlerinin tamamı mevcut. README, uygulama kodu, testler ve saklanan gerçek
araştırma çıktıları birlikte kontrol edildi. Bu sonuç teslim kapsamına ve çalışabilirliğe
ilişkindir; araştırma raporlarının anlamsal olarak hatasız olduğu anlamına gelmez.

## Gereksinim–kanıt eşleştirmesi

| İstenen | Durum | Doğrudan kanıt |
|---|---|---|
| Projenin nasıl çalıştırılacağı | Tamam | [README §1](../../README.md#1-quickstart): Docker, anahtarlar, hafif kurulum, offline demo ve CLI |
| Kullanılan teknolojiler | Tamam | [README §2](../../README.md#2-technologies) |
| Agent architecture | Tamam | [README §3](../../README.md#3-architecture): servisler, LangGraph ve claim ledger |
| Search strategy | Tamam | [README §4](../../README.md#4-search-strategy) |
| Source evaluation | Tamam | [README §5](../../README.md#5-source-evaluation): ağırlıklar, birincillik, güncellik, iddia kabulü |
| Duplicate detection | Tamam | [README §6](../../README.md#6-duplicate-detection): URL, metin, alıntı, yayıncı, iddia ve sorgu |
| Follow-up search strategy | Tamam | [README §7](../../README.md#7-follow-up-search-and-termination): eksik maddeler ve tek çelişki takibi |
| Termination condition | Tamam | [README §7](../../README.md#7-follow-up-search-and-termination): yeterlilik, limitler ve ilerleme yokluğu |
| Contradiction handling | Tamam | [README §8](../../README.md#8-contradiction-handling) |
| Önemli design decisions | Tamam | [README §15](../../README.md#15-design-decisions) |
| Basit architecture diagram | Tamam | [README §3](../../README.md#3-architecture): servis diyagramı ve agent workflow için iki Mermaid diyagramı |
| En az 3 farklı gerçek araştırma | Tamam | [8 farklı soru](../../examples/2026-09-17-final/); [input / trace / output bağlantıları](../../examples/README.md#the-case-questions) |
| Kritik bileşen testleri | Tamam | [README §14](../../README.md#14-tests): beş istenen bileşenin doğrudan test dosyaları |
| 8 design question cevabı | Tamam | [README §16](../../README.md#16-design-questions): sekiz sorunun tamamına Türkçe cevap |

## Bu kontrolde çalıştırılan doğrulamalar

| Kontrol | Sonuç |
|---|---|
| `make verify` | Başarılı: **548 Python testi**, PostgreSQL entegrasyonu, Go testleri ve race detector, Ruff, strict mypy, gofmt ve go vet |
| Anahtarsız evidence fixtures | 20 vakanın veri/alıntı kontrolü geçti. Eski quote-only baseline precision değeri 0.45; bu bir canlı anlamsal doğruluk skoru değildir. |
| `uv run python evals/audit_exports.py examples/2026-09-17-final --out /tmp/apilex-submission-export-audit.json` | 8 rapor, 638 iddia; geçersiz alıntı, boşa çıkan atıf, kaynak listesi uyuşmazlığı, G4 hata veya G12 ihlali yok |
| Örnek dosya bütünlüğü | 8 klasörün tamamında input, trace, Markdown/JSON rapor, gate ve sıkıştırılmış state var; toplam 2.324 trace kaydı JSON olarak okunuyor; altı dosya türü de Git takibinde |
| Rapor–state uyumu | JSON raporlar state'ten yeniden render edilen içerikle aynı; gate çıktıları state ile aynı. Markdown'da yalnızca tarihsel `single source` / `tek kaynak` etiketinin bugünkü `single independent source` / `tek bağımsız kaynak` karşılığı farklı. |
| README / examples / evals yerel bağlantıları | Hedef dosya ve klasörler mevcut |
| `docker compose config --quiet` | Mevcut ayarlar ve `.env.example` ile başarılı |
| Çalışan API `/readyz` | Database, Presidio ve Langfuse erişilebilir; `ready=true` |
| Docker CLI offline çalıştırma | `sufficient`, gate `pass`, $0; altı çıktı dosyası host volume üzerinde oluşturuldu |
| `pre-commit run gitleaks --all-files` | Başarılı |

Yeni bir ücretli araştırma serisi çalıştırılmadı; en az üç gerçek çalıştırma şartı saklanan sekiz
farklı canlı örnekle doğrulandı. Son kontrol ayrıca güncel testleri ve anahtarsız Docker çalıştırmasını
içerir. GitHub Actions iş akışı mevcut; bu kontrolde uzak CI çalıştırması yapılmadı.

## Teslim öncesinde tamamlananlar

- CLI komutlarına host volume ve kullanıcı eşlemesi eklendi; `--rm` sonrasında çıktılar kaybolmaz.
- Yerel testler için uv, Python, Go, make ve bağımlılık kurulum adımı açıklandı.
- Sorgu üretimi için beş doğrudan test eklendi: modelin fazla sorgu üretmesi/tekrarlar/geçersiz
  hedefler, model erişilemezliği, yalnızca eksikler için takip ve tek çelişki denemesi, tekrar
  tükenmesi ve arama bütçesi bitince model çağrısının atlanması.
- Çelişki sonrası durma politikası kodla eşleştirildi: takip denemesi çözüm sayılmaz; bulgu
  tartışmalı kalır fakat yeni arama zorlamayı bırakır.
- Arşiv örneklerin son kod revizyonuyla yeniden çalıştırılmış olduğu izlenimi kaldırıldı.
- Tasarım sorularındaki kesin doğruluk ifadesi, ilk deadline'ın korunması ve maliyet/kapsam
  dengesi netleştirildi.

## Açıkça belirtilen kalite sınırları

[Sonuç notları](../../examples/2026-09-17-RESULTS.md) ve [evaluation v3](evaluation_v3.md)
teslimin parçasıdır. Özellikle KVKK bildirim rolü yorumunda ve Shopify/Wix karşılaştırmasının
kapsamında kayıtlı sorunlar var. Eski örnekler sonraki düzeltmelerden önceki çıktıları koruyor.
Gate geçişi yapısal denetim sonucudur; kaynakların doğruluğunu veya hukuki yorumu garanti etmez.
Uydurma şirket örneğindeki `no_evidence` / gate `fail` beklenen çekimser sonuçtur.

Bu sınırlamalar README'de de açıklanmıştır; kapsam maddelerinde eksik yoktur, ancak sistemi
production veya kusursuz doğruluk iddiasıyla sunmamak gerekir.
