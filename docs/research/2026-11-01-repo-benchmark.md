# Repo Benchmark: ai-job-search & career-ops → JobScout Yol Haritası

**Tarih:** 2026-11-01
**Branch:** `analysis/repo-benchmark`
**İncelenen repolar:**
- `ai-job-search-dev/ai-job-search` — Claude Code tabanlı, fork-and-fill uygulama framework'ü (v1.7.4, ~315 dosya, 867 test).
- `career-ops-hq/career-ops` — AI CLI'lar içinde çalışan "job search agent" + dashboard (commit `7a2abd5`, ~2.992 dosya, 1.010 test).

**Kısa hüküm:** İki repo da *CV üretimini LLM'e bırakmıyor*; LLM'den yapılandırılmış veri alıp deterministik gate'lerle doğruluyor. Bizim projede eksik olan tam olarak bu katman: **fact gate, ATS kontrolü, ilan canlılık kontrolü ve CV üretimi.** Ayrıca iş bulma tarafında ikisi de LinkedIn/Indeed scraping'ini merkeze koymuyor; bizim en büyük stratejik zaafımız bu.

---

## 1. Repolar ne yapıyor?

### ai-job-search
- **Mimari:** 14 slash command (`/setup`, `/scrape`, `/rank`, `/apply`, `/interview`, `/outcome`), 3 skill, 3 subagent, 16 Python aracı.
- **CV:** Tek bir master `cv/main_example.tex` var. Her ilan için LLM sıfırdan LaTeX yazıyor.
- **ATS:** Gerçek ama kısmi. Sayfa sayısı, metin katmanı, Unicode/aksan katlama, ASCII tarih aralığı, layout boşluk/orphan kontrolleri var; **sayısal ATS skoru yok.**
- **İş bulma:** 6 portal CLI'ı (LinkedIn guest HTML, freehire JSON API, 4 Danimarka portalı). `expired` / `unreachable` ayrımı ve retry bütçesi çok iyi.
- **API key:** Scraping tamamen keyless. **LLM tarafı Claude Code aboneliği/API key istiyor; local model desteği yok.**

### career-ops
- **Mimari:** Node/ESM agent harness. 166 kök script, 139 iş boardu, 126 provider modülü, 20 dilde mode, Next.js dashboard, Go TUI.
- **CV:** `modes/pdf.md` 23 adımlı prosedür. LLM **HTML/LaTeX yazmıyor**; JSON render payload üretiyor, `build-cv-html.mjs` + `generate-pdf.mjs` çıktıyı üretiyor.
- **ATS:** İki aşamalı ve en olgun taraf: `ats-payload.mjs` (HTML öncesi düzeltme) + `verify-ats.mjs` (0–100 ağırlıklı skor) + `keyword-match.mjs` (kapsam) + YAML kural seti.
- **İş bulma:** LinkedIn/Indeed **core'da yok** (aggregator sayılıp "UNCONFIRMED" işaretleniyor). ATS API'ları, RSS, SSR parser'lar öncelikli; Playwright son çare.
- **API key:** Keyless yollar var: Ollama (`ollama-eval.mjs`), OpenAI-compatible local server (`openai-eval.mjs`), Antigravity CLI (`agy`, hesap yeterli), OpenCode+Ollama batch. OpenRouter/Gemini免费 ama key'li.

---

## 2. CV düzeltmeyi nasıl ele alıyorlar?

### Ortak desen: "LLM yazar, kod denetler"

| Katman | ai-job-search | career-ops |
|---|---|---|
| Gerçek kaynak | `01-candidate-profile.md` + master `.tex` + `CLAUDE.md` (üç kaynak birliği) | `cv.md` + `article-digest.md` + `config/cv-facts.json` |
| Üretim | LLM doğrudan LaTeX yazar | LLM JSON payload yazar; renderer HTML/PDF üretir |
| Denetim | Fresh-context `application-reviewer` → JSON edit listesi | `verify-cv-facts.mjs` → `pass / warn / block` |
| Son kontrol | `verify_pdf.py`, `verify_layout.py`, manuel PDF okuma | `cv-title-check`, `verify-cv-structure`, `verify-cv-facts`, `verify-ats` |

### ai-job-search'in değerli kuralları
- **Keyword enjeksiyonu yalnızca doğruysa:** "MLOps" başlığı "ML Deployment" paraphrase'inden daha iyi; ama profile'da yoksa asla eklenmiyor.
- **Interview backtrack testi:** Aday "aslında şunu demek istemiştim" demek zorunda kalacaksa o cümle yumuşatılır veya silinir.
- **Relevance-weighted cutting:** 1 sayfaya sığdırırken "en eski işi kes" yerine her satırı (relevantlik + benzersizlik + anlatı yükü) puanlayıp en düşüğü kesiyor.
- **Reviewer edit formatı:**
  ```json
  {"file":"...", "old_string":"...", "new_string":"...",
   "reason":"keyword match | company angle | reframing | style | grounding"}
  ```
  `grounding` reason'lı öneriler otomatik atlanıyor. Bu, halüsinasyonu "yumuşatmak" yerine reddetmeyi sağlıyor.
- **Üç kaynak kuralı:** Eski taslaklar asla fact kaynağı değil; sadece yapı/üslup için okunur.

### career-ops'un değerli kuralları
- **Payload şeması:** `candidate`, `sections`, `summary`, `competencies[]`, `experience[{company,role,dates,bullets}]`, `projects`, `education`, `skills[]`. Renderer bilinmeyen alanı sessizce düşürmüyor, uyarıyor.
- **Fact gate çıktısı:**
  ```js
  { verdict:'pass'|'warn'|'block',
    invented:[], unsupportedFacts:[], advisoryFacts:[],
    forbidden:[], warnings:[] }
  ```
  `invented` / `unsupportedFacts` / `forbidden` doluysa **PDF üretimi durur.**
- **Yakaladığı hata sınıfları:** uydurma metrik, desteklenmeyen işveren/unvan/araç, "kullandı" yerine "inşa etti" (delegated authorship), "led" yerine "contributed" (scope inflation), "3 takım kullandı" gibi adoption iddiaları, forbidden phrase listesi.
- **ATS payload düzeltmesi:** `competencies` CSS `gap` ile ayrı tag'ler halinde render edilirse metin katmanında kelimeler birbirine yapışıyor. `ats-payload.mjs` bunları `Skills / Kategori: a, b, c` formatına katlıyor. Bu, "temiz HTML skoru = temiz ATS" yanılgısını çözen gerçek bir bug fix.

### Bizde eksik olan
JobScout şu an **sadece cover letter üretiyor**; CV üretimi/tailoring'i yok. `verifier.py` yalnızca metrik + bullet relevance kontrolü yapıyor; işveren/unvan/araç/scope kontrolü, forbidden phrase, block verdict'i yok. `cv_import.py` var ama sadece profil dolduruyor.

---

## 3. ATS uyumunu nasıl ele alıyorlar?

### career-ops: 0–100 skor + ayrı kapsam metriği

`verify-ats.mjs` ağırlıkları (toplam 100):

| Ağırlık | Kontrol |
|---:|---|
| 15 | Gerçek, seçilebilir metin |
| 20 | Standart/yerelleşmiş bölüm başlıkları |
| 15 | Gövdede e-posta (+ telefon) |
| 20 | Tek kolon; layout table / multi-column CSS yok |
| 10 | CV metni görsel içine gömülü değil |
| 10 | Standart, gömülebilir fontlar |
| 5 | UTF-8 beyanı |
| 5 | Gizli metin / keyword stuffing yok |

Not: ≥90 A, ≥80 B, ≥70 C, ≥60 D, <60 F. Varsayılan eşik 70 ve `critical` issue olmaması.

**Kritik ayrım:** `keyword-match.mjs` kapsamı **skora dahil edilmiyor.** Skor yapısal; kapsam tanısal. `{total, presentCount, coveragePct, present[], thin[], missing[]}` ve `k8s ↔ Kubernetes`, `postgresql ↔ Postgres`, ama `Java ↛ JavaScript` synonym tablosu var. `thin` = ilanda sadece 1 kez geçen kelime.

**Kural seti YAML:** Her kural `severity`, `scope`, `detect`, `must_not_flag`, `source`, `cannot_catch` alanlarına sahip. `must_not_flag` sayesinde `lang=de` iken Almanca başlıklar flag'lenmiyor. `cannot_catch` ise ölçülen kör noktaları kaydediyor — "temiz sonuç = doğruluk kanıtı değil" disiplini.

### ai-job-search: geometri + metin katmanı, skor yok

| Kontrol | Kural |
|---|---|
| Sayfa sayısı | CV 2, cover 1 (exit 1) |
| Metin çıkarma | `pypdf` → `pdftotext -layout` fallback |
| Unicode katlama | NFC + `' ' " " – — nbsp` → ASCII, **karşılaştırmada**, dump'ta değil |
| Aksan | `\ifpdftex\usepackage[T1]{fontenc}\fi` yoksa `è` ayrışır, keyword eşleşmez |
| Garbage | `(cid:NNN)` ve `�` yasak |
| İletişim | E-posta **literal metin** olmalı; icon/hyperlink-only görünmez |
| Okuma sırası | Tek kolon çıkarımı görsel sırayla aynı olmalı |
| Tarih | `2019–2023` (en-dash) yasak; ASCII hyphen zorunlu |
| Layout | >100pt boşluk, sayfa sonu %25 erken, son sayfa %35 boş, footer bandı 90pt, orphan başlık |
| Keyword | LLM tablosu: `covered / synonym-only / missing (have it) / missing (gap)` |

**Bizim için en değerli iki parça:** `normalize_text()` (~20 satır, Almanca umlaut/ß/tırnak/tire sorununu tek yerde çözer) ve `--ascii-dates` kontrolü. Almanca CV'lerde Word/LibreOffice varsayılanı en-dash olduğu için bu gerçek bir ATS kaybı sebebi.

---

## 4. İş bulmayı nasıl ele alıyorlar?

### ai-job-search
- Portal CLI'ları glob ile keşfediliyor (`.agents/skills/*/SKILL.md`), `enabled: false` ile kapatılabiliyor.
- Sözleşme: `search` / `detail`, `--format json|table|plain`, JSON `{meta:{count,page}, results:[{id,title,company,location,date,url}]}`, hata stderr'e `{error,code}` + exit 1, dürüst UA, sıfır runtime dependency, 429/5xx exponential backoff.
- Akış: **önce dedupe, sonra detail fetch.** `scrape_state.py candidates` → `{selected, already_seen, already_applied, repeated_in_batch, invalid}`. Bu, gereksiz detail fetch'ini kesiyor.
- Dedupe anahtarı: normalize URL → normalize company+title → tracker'daki company+role → batch içi tekrar. `job_key.py` slug + SHA-1 truncation ile çakışmasız.
- **Liveness ayrımı:**
  - `expired` = pozitif kanıt (kapalı banner, geçmiş deadline, 410, listing'e redirect)
  - `unreachable` = erişim hatası (401/403/404/timeout/5xx). **404 expired değildir.**
  - 3 başarısız deneme + ≥48 saat → `expired (reason: unreachable)`, `--all` ile geri açılabilir.
- **Skor kodda hesaplanıyor:** LLM 4 adet 0–100 sayı döndürüyor; Python ağırlıklı toplamı `Fraction` ile yapıyor.
  ```python
  WEIGHTS = {"technical": 0.30, "experience": 0.25, "behavioral": 0.15, "career": 0.30}
  BANDS = ((75,"Strong Fit"),(60,"Good Fit"),(45,"Moderate Fit"),(30,"Weak Fit"),(0,"Poor Fit"))
  ```
  Location FAIL ve language gate FAIL skordan bağımsız veto. Deadline ≤7 gün 🔥 ve tie-breaker. 30 günden eski ilan ⚠ (veto değil).
- `robots_check.py`: RFC 9309'a yakın, redirect hedeflerinde de kontrol, okunamayan policy = deny. **Ama LinkedIn CLI'ı robots'u bilinçli olarak atlıyor** (ToS uyarısı taşıyor).

### career-ops
- **LinkedIn/Indeed core'da yok.** Aggregator ilanı varsayılan olarak `UNCONFIRMED`; aynı rolü işverenin ATS/kariyer sayfasında bulup canonical URL'yi işveren URL'si yapıyorlar.
- 4 seviyeli keşif: (0) local parser, (1/2) public ATS API / RSS / SSR, (3) Playwright + WebSearch, (4) WebSearch fallback (liveness doğrulaması zorunlu).
- Provider sözleşmesi: `{id, detect(entry), async fetch(entry, ctx)}`, `Job` = `title,url,company,location` zorunlu; `ctx.fetchJson/fetchText` zorunlu, `redirect:'error'` default (SSRF), pagination `ctx.maxPages`, **empty vs broken ayrımı** (boş container = boş board; eksik container = throw).
- Dedupe: URL → company+role → pipeline; ayrıca **SimHash** (64-bit) ile 90 gün içinde farklı şirket altında aynı JD uyarısı (ajans re-post'unu yakalar).
- Liveness: önce ATS API (`greenhouse, lever, ashby, workday, smartrecruiters, zoho, workable`), sonra Playwright. **"Yanlış expired, mevcut durumdan daha kötüdür"** — sadece kesin sinyalde expired, diğer her şey `uncertain`.
- `scan-history.tsv` 15 kolon, append-only; `skipped_title/location/age/...` **seen sayılmıyor** (ayarı değiştirince ilan geri gelir). `skipped_dup/expired/error/added` seen sayılıyor.

### Bizde durum
- Sadece LinkedIn + Indeed, Playwright ile headed browser. `Source` enum'unda `custom` var ama adapter yok.
- Dedupe: `(source, job_id)`; cross-source fingerprint opsiyonel ve kapalı. URL normalize / company+role / SimHash yok.
- Liveness yok: ilan kapanmış olsa da sheet'te kalıyor.
- Fit scoring/ranking yok: tüm ilanlar eşit.
- Deadline sweep yok.
- `SourceResult` yok: blocked/login/layout change ile "0 ilan" aynı görünüyor.

---

## 5. API key istemeyen çözümler

| Yetenek | ai-job-search | career-ops | JobScout (mevcut) |
|---|---|---|---|
| İş verisi | Keyless (public HTML/JSON/RSS) | Keyless (ATS API/RSS) + keyed plugin escape hatch | Keyless ama ToS riskli scraping |
| CV/letter üretimi | **Claude zorunlu** | Ollama / local OpenAI-compatible / `agy` hesabı | **Ollama (qwen2.5:7b) — keyless** |
| Embedding | — | — | **bge-m3 local — keyless** |
| Free-tier keyed | — | OpenRouter `:free`, Gemini free | Opsiyonel NVIDIA/cloud |
| Batch runner | — | `claude / opencode / gemini / qwen / grok` | — |

### Keyless kaynaklar (Almanya odaklı, öneri)
- **Bundesagentur für Arbeit Jobsuche API** — resmi, ücretsiz; topluluk tarafından yayınlanan public client key ile çalışır. Almanya için en yüksek sinyal.
- **Greenhouse / Lever / Ashby / Workable / SmartRecruiters public board API'ları** — key yok, ilanın "gerçekten açık" olduğunu doğrulamak için de kullanılır.
- **Arbeitnow API** — key yok, Almanya/remote.
- **Hacker News "Who is hiring"** — key yok.
- **Berlin Startup Jobs / WeWorkRemotely RSS** — key yok.
- **freehire.me** — ~50 ATS'i tek JSON API'de topluyor, self-host edilebilir.

### Önemli ayrım
"Ücretsiz" ≠ "key'siz". OpenRouter `:free` ve Gemini free tier **key ister**. Ollama, LM Studio, llama.cpp, vLLM key istemez. Bizim proje bu konuda iki repodan da avantajlı; eksik olan **model kalitesi** (7B Almanca Anschreiben için zayıf) ve **provider soyutlaması**.

---

## 6. Boşluk analizi

| Yetenek | ai-job-search | career-ops | JobScout | Öncelik |
|---|---|---|---|---|
| CV tailoring | ✅ spec | ✅ payload+renderer | ❌ | **P2 (en büyük fark)** |
| Fact gate / hallucination block | ✅ reviewer | ✅ `pass/warn/block` | ⚠️ sadece metrik | **P0** |
| ATS skoru | ❌ | ✅ 0–100 | ❌ | **P0** |
| PDF metin katmanı kontrolü | ✅ | ✅ | ❌ | **P0** |
| Keyword coverage | ✅ LLM tablosu | ✅ deterministik | ❌ | **P0** |
| Posting sanitization (prompt injection) | ✅ `posting_guard` | ⚠️ | ❌ | **P0** |
| Liveness `expired/unreachable` | ✅ | ✅ | ❌ | **P1** |
| ATS API ile doğrulama | ❌ | ✅ | ❌ | **P1** |
| SimHash dedupe | ❌ | ✅ | ❌ | **P1** |
| Fit scoring (kodla toplama) | ✅ | ✅ (1–5 holistic) | ❌ | **P1** |
| Deadline sweep | ✅ | ✅ | ❌ | **P1** |
| Source `empty vs broken` | ⚠️ | ✅ | ❌ | **P1** |
| Keyless LLM | ❌ | ✅ | ✅ | — |
| Keyless yeni kaynaklar | ⚠️ | ✅ | ❌ | **P2** |
| Reviewer JSON edit loop | ✅ | ⚠️ | ❌ | **P2** |
| Prompt-as-code testleri | ✅ | ⚠️ | ❌ | **P3** |

---

## 7. Yol haritası

### P0 — Deterministik güvenlik ve doğrulama (1–2 gün)

> **Durum: tamamlandı (2026-11-01).** `jobscout/verification/` (text, pdf, ats, keywords, facts) ve `jobscout/security/posting_guard.py` eklendi; fact gate cover letter üretimine bağlandı, `jobscout ats check` CLI'ı ve `POST /api/ats/check` endpoint'i çalışıyor. Testler: `tests/test_verification.py`.

Amaç: LLM çıktısını denetleyen, modelden bağımsız, test edilebilir katman.

**1. `jobscout/verification/pdf.py`**
- `normalize_text()` (NFC + tırnak/tire/nbsp → ASCII) — ai-job-search `verify_pdf.py:89-112` portu.
- `check_pdf(path, pages=1) -> PdfCheck`: sayfa sayısı, metin uzunluğu, `(cid:` / `�`, e-posta literal, okuma sırası uyarısı.
- `check_ascii_dates(text)` — `2019–2023` gibi en-dash/Unicode tire aralıklarını yakala; miktar aralıklarını (`600-1.000 €`, `2000-5000 req/s`) istisna tut.
- Bağımlılık: `pypdf` (yeni) veya mevcut `pypdfium2` ile metin çıkarma. `pdftotext` fallback opsiyonel.

**2. `jobscout/verification/ats.py`**
- career-ops ağırlıklarıyla 0–100 skor + `critical` issue kontrolü.
- Almanca başlık sözlüğü: `Berufserfahrung, Ausbildung, Kenntnisse, Zertifikate, Projekte, Zusammenfassung` + İngilizce karşılıkları. `lang=de` iken Almanca başlık flag'lenmez.
- Kontroller: seçilebilir metin, başlıklar, e-posta/telefon, tek kolon (PDF'te x-koordinat dağılımı), görsel içine gömülü metin, font gömme, UTF-8, gizli metin.
- Çıktı: `{score, grade, issues:[{id,severity,message}], checks:[...]}`.
- CLI: `jobscout ats check <pdf>`; API: `POST /api/ats/check`.

**3. `jobscout/verification/keywords.py`**
- JD'den 15–20 anahtar terim; CV/letter metninde `present / thin / missing` + synonym tablosu (DE/EN: `k8s↔Kubernetes`, `Postgres↔PostgreSQL`, `JavaScript↛Java`).
- Skora dahil **edilmez**; ayrı rapor.

**4. `jobscout/security/posting_guard.py`**
- Gizli HTML/CSS (`display:none`, `visibility:hidden`, `font-size:0`, `opacity:0`, `text-indent:-9999px`), Unicode tag karakterleri (`U+E0000–E007F`), homoglyph, instruction-like satır tespiti.
- Playwright'tan gelen ham HTML ve `fetch_job_description` çıktısı LLM'e gitmeden önce temizlenir.
- Çıktı: `{clean_text, hidden_blocks, instruction_lines, suspicious_words}`; UI'da uyarı rozeti.

**5. Mevcut `verifier.py`'yi fact gate'e yükselt**
- `VerificationResult` → `verdict: pass|warn|block`, `invented_metrics`, `unsupported_facts`, `forbidden_phrases`, `scope_issues`.
- Almanca sayı sözcükleri (`zwei, drei, zwölf, hundert`) ve ondalık virgül (`3,5`) desteği.
- `config/facts.yaml`: `allow_metrics`, `allow_facts`, `forbidden_phrases` (`Fortune 500`, `patentiert`, `weltweit führend`).
- `block` ise PDF yazılmaz; kullanıcıya hangi cümlenin hangi kaynakla çeliştiği gösterilir.

**Kabul kriterleri:** Uydurma metrik içeren bir letter PDF üretemiyor; en-dash tarihli CV ATS check'te fail ediyor; gizli talimat içeren fixture posting LLM'e temiz metin olarak gidiyor; hepsi pytest'te.

---

### P1 — İş bulma güvenilirliği (3–5 gün)

**6. `SourceResult` sözleşmesi**
```python
class SourceResult(BaseModel):
    jobs: list[Job]
    status: Literal["ok","empty","blocked","login_required","layout_changed","error"]
    message: str | None = None
    pages_fetched: int = 0
```
- `linkedin.py` / `indeed.py` bunu döndürür. CAPTCHA/login = `blocked`, boş sonuç = `empty`, selector kaybı = `layout_changed`.
- UI'da "0 ilan" ile "bloklandı" ayrımı görünür.

**7. Dedupe cascade**
- Sıra: `source_job_id` → canonical URL (tracking param temizle, fragment sil, path lowercase) → normalize company+role (GmbH/AG/Inc. suffix temizle) → SimHash (90 gün, sadece uyarı).
- `Job` modeline `canonical_url`, `requisition_id`, `fingerprint`, `first_seen`, `last_seen`, `status` ekle.
- `skipped_*` durumları seen sayılmaz (ayar değişince ilan geri gelir).

**8. Liveness**
- `jobscout/liveness.py`: ilandaki işveren URL'sinden ATS tipini tespit et (greenhouse/lever/ashby/workable/smartrecruiters/workday), public API ile doğrula.
- Durumlar: `active / expired / uncertain`. **403, 404, login wall, CAPTCHA = uncertain.** Sadece kesin kapanış kanıtı `expired`.
- 3 deneme + 48 saat kuralı; `expired_reason` sakla; `--all` ile geri aç.
- UI: her ilanda "işveren ATS'inde doğrulandı" rozeti.

**9. Fit scoring**
- LLM'den 4 tam sayı + `strengths[]` + `gaps[]` + `language_gate` + `location_verdict` al (Pydantic, `extra="forbid"`).
- Python'da hesapla: `technical .30`, `experience .25`, `behavioral .15`, `career .30`; bantlar `75/60/45/30`; `Fraction` ile yuvarlama kaymasını önle.
- Veto: location FAIL, language FAIL. Deadline ≤7 gün `closing_soon` + tie-breaker.
- `rank_score`, `rank_verdict`, `ranked_at` alanları; sheet'e yeni kolonlar.

**10. Deadline sweep**
- LLM'siz, günde bir çalışan endpoint/CLI: `deadline < today → expired`, `≤7 gün → closing_soon`, parse edilemeyen → `unparseable` (tahmin yok).

---

### P2 — CV pipeline (1–2 hafta)

Bu, iki repodan alınacak en büyük yetenek. Amaç: **CV'yi LLM'e yazdırmadan, veriden üretmek.**

> **Uygulandı** (`analysis/repo-benchmark`): `jobscout/cv/` paketi — `master.py` (facts +
> `config/cv.master.yaml` overlay; örnek: `config/cv.master.example.yaml`), `payload.py`
> (`extra="forbid"` şema; gap'ler `plain_text`'e girmez), `tailor.py` (deterministik keyword/
> gap/sıralama/cut; bold label yalnız bullet'ta geçen terim), `llm.py` + `reviewer.py`
> (JSON-only tailoring, reason-enum edit'ler; `grounding` asla uygulanmaz), `verify.py`
> (structure/title-drift/fact gate/CV-letter tutarlılık), `render.py` + `templates/
> cv_modern.tex.j2` (tectonic), `pipeline.py` + CLI `cv build` / `cv gaps`. Testler:
> `tests/test_cv_*.py`.

**11. Master CV'yi veriye çevir**
- `config/profile.yaml`'ı genişlet: her achievement'a `id`, `metrics[]`, `skills[]`, `scope` (`led|contributed|used`), `sources[]`.
- Alternatif: `config/cv.master.yaml` + `data/cv/master.md` (insan okunur kaynak) + `profile.yaml` (iletişim/tercihler). Tek doğruluk kaynağı kuralı: türetilmiş CV asla fact kaynağı değil.

**12. Render payload + deterministik renderer**
- `jobscout/cv/payload.py` → Pydantic `CVRenderPayload` (career-ops şeması).
- `templates/cv/moderncv.tex.j2` veya HTML+WeasyPrint. LLM **sadece JSON üretir**, HTML/LaTeX üretmez.
- `jobscout/cv/render.py` → PDF; mevcut `pdflatex` altyapısıyla uyumlu.
- `cv-title-check`: unvan `profile`'daki unvandan senior'a kaydırılamaz (warn).
- `verify-cv-structure`: kaynak CV'deki employer/role/bullet kaybı veya sıra değişimi (warn).

**13. Tailoring adımları (LLM + kod)**
- JD'den 15–20 keyword çıkar (deterministik + LLM doğrulaması).
- Skill gap: JD'de istenip profilde olmayan araçlar → **asla eklenmez**, raporda `gap` olarak görünür.
- Summary'yi yeniden yaz (5–7 satır), competency grid 6–8 öğe, deneyim bullet'larını relevansa göre sırala.
- Keyword'ü yalnızca `profile` destekliyorsa mevcut bullet'ın bold etiketine koy.
- 1 sayfa için relevance-weighted cutting.

**14. Reviewer loop**
- Draft → fresh-context reviewer (aynı model, ayrı çağrı, düşük temperature) → JSON edit listesi (`reason` enum).
- `grounding` reason'lı edit otomatik atlanır; `reframing` kullanıcıya gösterilir.
- Pydantic response model + 1 retry.

**15. Fact gate'i CV'ye de uygula**
- Aynı `verification/facts.py`; CV PDF üretiminden önce `block` ise dur.
- CV + cover letter arasında tarih/şirket/unvan tutarlılığı kontrolü.

---

### P3 — Kaynak çeşitliliği ve operasyon (opsiyonel)

**16. Keyless kaynak adaptörleri**
- `sources/ba_jobsuche.py` (Almanya, resmi API), `sources/ats_board.py` (Greenhouse/Lever/Ashby board token ile), `sources/arbeitnow.py`, `sources/hn_hiring.py`, `sources/rss.py`.
- Her biri `BaseSource` + `SourceResult` sözleşmesine uyar; `make_source` registry'si genişler.
- Stratejik etki: LinkedIn/Indeed scraping bağımlılığı ve ToS riski azalır; ilanlar işveren kaynağından geldiği için `confirmed` olur.

> **Uygulandı** (`analysis/repo-benchmark`): beş adaptör de aynı dosya adlarıyla yazıldı
> (`keyless.py` ortak taban: httpx + hata taksonomisi 401/403/429→`blocked`, ağ→`error`;
> `robots.py` yalnız RSS için fail-open robots kapısı — JSON API'leri yayınlandığı şekilde
> kullanılıyor). UI'da tek yeni ajan **Boards Scout** (`boards`); işler kaynak bazlı `Source`
> değerlerini (`boards`/`arbeitnow`/`hn`/`ba`/`rss`) ve `requisition_id`/`canonical_url`
> kimliklerini koruyor. `sources.keyless` config bloğu + `--sources keyless` + UI KEYLESS
> düğmesi. BA ucu ücretsiz `JOBSCOUT_BA_API_KEY` ister; anahtar yoksa durum `blocked`.

**17. Provider soyutlaması**
- `LLMClient` protocol: `ollama` (mevcut), `openai-compatible` (`base_url`), `anthropic`, `gemini`.
- `OLLAMA_ALLOW_REMOTE` benzeri güvenlik: non-loopback base URL için açık izin.
- Model profili: `fast` (7B), `balanced` (14B), `quality` (32B+). Almanca Anschreiben için 32B+ önerilir; 7B sadece taslak.

> **Uygulandı** (`analysis/repo-benchmark`): `jobscout/llm.py` — `LLMClient` protokolü tek
> `post(payload)` imzası (OpenAI şekli); `OpenAICompatTransport` (ollama/openai/NIM),
> `AnthropicTransport`, `GeminiTransport`; `detect_provider` base_url'den `auto` çözüyor.
> Güvenlik: `provider=ollama` + loopback-dışı adres `OLLAMA_ALLOW_REMOTE` /
> `COVER_LETTER_ALLOW_REMOTE` olmadan hata verir; `num_ctx` yalnız loopback'e gönderilir.
> `CoverLetterConfig.provider` + dört çağrı noktasından (CLI rank, orchestrator, generator,
> CV pipeline) taşınır; `LLMError` artık `jobscout.llm`'de kanonik.

**18. Prompt-as-code testleri**
- Her prompt template'inde grounding kuralı, dil kuralı ve JSON şeması olduğunu assert et.
- Her LLM'e giden posting yolunun `posting_guard`'dan geçtiğini assert et.
- ATS/fact gate için fixture'lar: uydurma metrik, en-dash tarih, gizli talimat, CSS-ayrık skill tag'leri.

> **Uygulandı** (`analysis/repo-benchmark`): `tests/test_prompts_as_code.py` — dört system
> prompt'unun (Anschreiben, CV tailor, CV reviewer, fit) grounding/dil/JSON-şeması
> sözleşmeleri sabitlendi; letter ve CV yollarında `guard_text` çağrıları spy'la doğrulandı.
> Benchmark'ın adversarial fixture'ları teste dönüştü: uydurma `%` metrik → fact gate
> `block`, en-dash tarih aralığı → `check_ascii_dates`, zero-width talimat taşıyıcısı →
> guard temizler+flag'ler, CSS-ayrık "P y t h o n" → guard artık kelimeleri birleştiriyor
> (`_join_spaced_words`). Bu arada gerçek bir bug yakalandı: `CV_REVIEW_SYSTEM_PROMPT`
> içindeki kaçışsız `{"edits"}` süslü parantezleri `.format()`'ı patlatıyordu; kaçırıldı.

**19. Artefakt + index depolama**
- İlan/letter/CV'yi `data/artifacts/{source}/{job_id}/` altında JSON/MD olarak sakla; JD'yi verbatim arşivle.
- SQLite sadece index/FTS için; artefaktlardan yeniden inşa edilebilir.
- Sheet/CSV export korunur (mevcut kullanıcı akışı bozulmaz).

> **Uygulandı** (`analysis/repo-benchmark`): `jobscout/artifacts.py` — `ArtifactStore`
> her yeni ilanı `job.json` + verbatim `jd.md` olarak arşivler (orchestrator write ve
> description-fetch hook'ları), letter/CV üretimleri `letter.json` + `documents.json`
> linkleriyle bağlanır (pipeline'lar asla doğrudan doküman yazmaz). `index.sqlite`
> FTS5 (+ LIKE fallback) sadece indistir; `jobscout index rebuild` klasörlerden yeniden
> kurar, `jobscout index search` arar. `JOBSCOUT_ARTIFACTS=0` ile kapanır;
> `data/artifacts/` gitignore'da. Sheet/CSV akışı değişmedi.

---

## 8. Ne kopyalanmalı, ne kopyalanmamalı

### Kopyala
- `pass / warn / block` fact gate ve `invented / unsupported / forbidden` sınıflandırması.
- ATS ağırlıklı skor + ayrı keyword coverage.
- `expired` vs `unreachable` ayrımı ve 3 deneme/48 saat kuralı.
- Dedupe cascade + SimHash uyarısı.
- `SourceResult` empty-vs-broken sözleşmesi.
- LLM'in sayı döndürüp Python'un skoru hesaplaması.
- Reviewer JSON edit formatı + reason enum.
- `normalize_text()` ve ASCII tarih kontrolü.
- Kural setinde `must_not_flag` ve `cannot_catch`.

### Kopyalama
- career-ops'un 2.992 dosyalık yüzeyi (20 locale, 139 board, Go TUI, Next.js dashboard).
- ai-job-search'in Claude'a bağımlı komut ağacı.
- LLM'e doğrudan LaTeX/HTML yazdırma (her iki repoda da bunun acı çekilerek öğrenildiği belli).
- Otomatik başvuru gönderme. İkisi de human-in-the-loop; biz de öyle kalmalıyız.
- LinkedIn'i birincil kaynak yapmak. ToS riski + kırılganlık + düşük sinyal.

---

## 9. Riskler ve hukuki not

- **LinkedIn/Indeed scraping:** Her iki repodan biri (ai-job-search) LinkedIn ToS uyarısını açıkça taşıyor; career-ops core'a almıyor. Bizim proje headed browser + düşük hacim + manuel login ile riski azaltıyor ama sıfırlamıyor. P3'teki keyless kaynaklar bu riski stratejik olarak azaltır.
- **robots.txt:** Yeni kaynaklarda `robots_check` uygulanmalı; LinkedIn için uygulanırsa scraping tamamen durur. Karar ürün kararı; en azından dokümante edilmeli.
- **Almanya'da CV:** Fotoğraf, doğum tarihi, medeni hal, uyruk, maaş beklentisi, referans gönderilmez. Mevcut `profile.py` bu alanları prompt dışında tutuyor — korunmalı.
- **AGG:** İlanda yaş/cinsiyet/uyruk filtresi kullanılmamalı; fit skorunda bu alanlar yer almamalı.
- **Halüsinasyon:** Almanca Anschreiben'de uydurma metrik en yüksek itibar riski. Fact gate P0 olmasının sebebi bu.
- **Model kalitesi:** 7B model ile fact gate'i geçmek zor; 32B+ veya bulut fallback gerekebilir. Keyless kalma hedefi ile çelişirse kullanıcıya açık tercih sunulmalı.

---

## 10. Önerilen sıra (tek cümle)

**Önce P0 fact gate + ATS + posting guard** (modelden bağımsız, hemen değer, düşük risk) → **P1 liveness + dedupe + fit score** (iş bulma kalitesi) → **P2 CV pipeline** (en büyük ürün farkı) → **P3 keyless kaynaklar + provider soyutlaması** (ölçek ve dayanıklılık).

---

## Ek: Referans dosyalar

**ai-job-search**
- `tools/verify_pdf.py` — normalize_text, ascii-dates, sayfa/metin kontrolü
- `tools/verify_layout.py` — geometri eşikleri (100pt, %25, %35, 90pt)
- `tools/posting_guard.py` — gizli metin / prompt injection temizliği
- `tools/robots_check.py` — RFC 9309 yakın uygulama
- `tools/job_key.py`, `tools/scrape_state.py`, `tools/rank_state.py` — dedupe, ağırlıklar, sweep
- `.claude/commands/apply.md` — draft → review → revise → verify akışı
- `.claude/skills/job-application-assistant/05-cv-templates.md` — CV kuralları
- `.claude/skills/job-application-assistant/04-job-evaluation.md` — rubrik ve gate'ler

**career-ops**
- `verify-cv-facts.mjs` — fact gate (`pass/warn/block`)
- `verify-ats.mjs` — 0–100 ATS skoru
- `ats-payload.mjs` — HTML öncesi competency fold
- `keyword-match.mjs` — kapsam + synonym tablosu
- `modes/pdf.md` — 23 adımlı CV pipeline ve JSON şeması
- `templates/ats-rules.yml` — `must_not_flag` / `cannot_catch` kural seti
- `providers/ADDING_A_PROVIDER.md` — provider sözleşmesi
- `check-liveness.mjs`, `liveness-api.mjs` — ATS API + Playwright liveness
- `DATA_CONTRACT.md` — artefakt-gerçek / SQLite-index modeli
- `docs/RUNNING_ON_A_BUDGET.md` — keyless/local model yolları
