# JobScout

Two visible-browser scouts search LinkedIn and Indeed. A coordinator dispatches the work, and a registrar dedupes the reports into Google Sheets or a local CSV.

The UI is a live agent control room built to the monochrome blueprint in [`design.md`](design.md): eight agents (source scouts including the keyless **Boards Scout**, verifier, analyst, registrar, writer), animated communication signals, task text, pause/resume, and cancel.

## Quick start (Local-first with Docker)

The easiest way to run JobScout with zero cloud dependencies and free local AI inference:

```bash
docker compose up
```

This single command:
1. Starts **Ollama** locally.
2. Automatically pulls **`bge-m3`** (for multilingual semantic embeddings) and **`qwen2.5:7b`** (for cover letter generation and CV extraction).
3. Builds and launches the JobScout engine & blueprint dashboard on <http://localhost:8000>.

> **GPU Acceleration**: If you have an NVIDIA GPU on Linux, run:
> ```bash
> docker compose -f docker-compose.yml -f docker-compose.gpu.yml up
> ```

### Manual / Local Python Development

Requirements: Python 3.12, Node.js 20+, and Google Chrome.

```bash
make setup
make serve
```

Open <http://127.0.0.1:8000>. Toggle **CACHED** and press **RUN BOTH** for a deterministic demo using the fixtures in `data/fixtures/`.

For frontend development:

```bash
make dev
```

Vite serves the UI and proxies `/api` to FastAPI on port 8000. Run `make serve` in another terminal.

## Live search

Live mode opens a headed Chrome window using a persistent profile.

```bash
make warm-linkedin
```

Sign in manually, then close the browser. The session is stored in `.browser-profiles/linkedin`. Indeed runs logged out; if Indeed presents a verification challenge, JobScout brings the browser to the front and pauses up to 45s for you to complete it, then automatically clears stale cookies and retries.

You can also solve or reset Indeed manually at any time:
```bash
make warm-indeed    # Opens visible Chrome to solve challenge & save clean cookies
make reset-indeed   # Deletes stale profile/cookies, then opens fresh Chrome
```

```bash
uv run jobscout run --sources both
```

Queries can be comma-, semicolon-, newline-, or `OR`-separated; each term is searched separately and the results are deduped. There is no CAPTCHA solving, proxy rotation, stealth mode, or credential storage. The adapters read public/search result HTML, keep volume low (25 jobs per source by default), and stop at cooperative pause/cancel checkpoints.

## Keyless sources (no login, no scraping)

Beside the browser adapters, JobScout runs five keyless fetchers under a single **Boards Scout** agent. They talk to documented public JSON APIs (or your own feeds) with plain HTTP, so there is no CAPTCHA race and the jobs come straight from the publisher:

| Source | Endpoint | Needs |
| --- | --- | --- |
| `boards` | Greenhouse / Lever / Ashby employer boards | board tokens in config |
| `arbeitnow` | arbeitnow.com job-board API | – |
| `hn` | Hacker News "who is hiring" via Algolia | – |
| `ba` | Bundesagentur für Arbeit official API | free `JOBSCOUT_BA_API_KEY` |
| `rss` | any RSS/Atom job feed | feed URLs; honours robots.txt |

```yaml
# config/search.yaml
sources:
  keyless:
    max_jobs: 25
    boards:
      - greenhouse:acme      # token from app.greenhouse.io/<token>
      - lever:bees           # token from jobs.lever.co/<token>
      - ashby:startupsaas    # token from jobs.ashbyhq.com/<token>
    feeds:
      - https://acme.example/careers.rss
```

```bash
uv run jobscout run --sources keyless    # all five
uv run jobscout run --sources boards     # just one
```

The UI has a **KEYLESS** button for the same set. Empty `query`/`location` fall back to the LinkedIn search terms. Jobs keep per-fetcher provenance (`Source` = `boards`/`arbeitnow`/`hn`/`ba`/`rss`) plus `canonical_url` and `requisition_id`, so cross-source duplicates collapse in the sheet. Without a BA key the source reports status `blocked` with setup instructions instead of pretending it is empty.

## In-app Setup (bring your own keys)

The app is local-first: nothing leaves your machine. A user who never touches a terminal configures everything on the **SETUP** tab (`jobscout serve` → http://127.0.0.1:8000/#setup):

- **Model** - provider, base URL, model and API key for the writer. `Test` sends one tiny chat request; the key is stored only in `config/settings.local.json` (0600, git-ignored) and every read endpoint returns it masked (`****abcd`).
- **Google Sheet** - paste the share link of your own sheet and upload or paste the service-account JSON. The job id is parsed from the link, the key is stored as `config/google-service-account.json` (0600, git-ignored), and the UI shows the `client_email` to share the sheet with as Editor. `Test` opens the sink and reads a row.
- **Optional keys** - Browse AI, for one more keyless source.

Everything the Setup view writes lands in `config/settings.local.json`, which loads **after** `config/search.yaml` and **before** the environment - `.env` and CI overrides keep working exactly as before.

## Model providers

All LLM calls (fit scoring, cover letters, CV tailoring) go through one OpenAI-shaped transport in `jobscout/llm.py`. `COVER_LETTER_PROVIDER=auto` infers the provider from `COVER_LETTER_BASE_URL` (`api.anthropic.com` → Anthropic, `generativelanguage.googleapis.com` → Gemini, loopback → ollama, anything else → OpenAI-compatible), or set it explicitly. Safety rule: a **non-loopback ollama URL is refused** unless you opt in with `OLLAMA_ALLOW_REMOTE=1`, because ollama is unauthenticated and would receive your whole profile. Ollama-only knobs (`num_ctx`) are only sent to local servers.

```dotenv
COVER_LETTER_PROVIDER=openai
COVER_LETTER_BASE_URL=https://api.openai.com/v1
COVER_LETTER_API_KEY=sk-...
COVER_LETTER_MODEL=gpt-4o-mini
```

## Google Sheets

1. Create a Google Cloud service account and enable the Google Sheets API.
2. Create a sheet and share it with the service account `client_email` as **Editor**.
3. Copy `.env.example` to `.env` and set:

```dotenv
GOOGLE_SHEET_ID=your-sheet-id
GOOGLE_SERVICE_ACCOUNT_JSON=credentials/service-account.json
GOOGLE_SHEET_URL=https://docs.google.com/spreadsheets/d/your-sheet-id/edit
SINK=auto
```

`SINK=auto` uses Google Sheets when credentials are present and falls back to `data/jobs.csv` otherwise. `SINK=local` always writes CSV.

The `Jobs` tab receives job rows and the `Runs` tab receives one row per run.

## Applicant profile

Cover letters are generated from `config/profile.yaml` (copy `config/profile.example.yaml` to start).
The **Applicant Profile** panel in the UI edits the same file.

### Upload a CV (Text-First + OCR + Vision Fallback)

The **Upload CV** button below the *Applicant Profile* title reads a CV and fills the form for you:
1. **Direct PDF text extraction**: Fast, zero image tokens, extracts readable text in seconds.
2. **Local Tesseract OCR fallback**: Used for scanned documents or image uploads (`.png`, `.jpg`, `.webp`).
3. **Vision Model fallback**: Used as a last resort if OCR cannot extract sufficient text.

The import never overwrites what you typed by hand: only empty fields are filled, and existing skills are extended instead of replaced.

### Smart Matching & Anti-Hallucination Pipeline

Unlike naive prompts that dump the whole profile into a large LLM:
1. **Bullet Granularity**: Every achievement is an independent embeddable unit.
2. **Hybrid Matching**: Combines dense semantic cosine similarity (`bge-m3`) with sparse BM25 lexical relevance and MMR diversity filtering.
3. **Constrained Generation**: The LLM is provided only with top-matched evidence bullets for the role's specific requirements.
4. **Factual Verification**: The generated letter is audited against the profile for hallucinated metrics, unverified numbers, and grounding integrity.
5. **Hard Fact Gate**: A letter that invents a metric, names an unsupported tool, or uses a forbidden phrase is marked `blocked`. The `.tex` and metadata are still written for review, but no PDF is produced.
6. **Posting Guard**: Scraped job descriptions are stripped of hidden HTML, invisible Unicode tag characters, and instruction-shaped text before they reach the model.

### ATS check

Every compiled cover letter is scored on its PDF text layer (selectable text, section headings, contact details, single-column layout, images, embedded fonts, charset, hidden text). The score is advisory and stored in the letter metadata. Any PDF can be checked from the CLI:

```bash
uv run jobscout ats check data/cover_letters/linkedin-123.pdf --pages 1
```

The same check is available as `POST /api/ats/check` (multipart PDF upload) and returns both the ATS score and the text-layer issues.

### CV pipeline

The CV is generated from data, never written by the model. `config/profile.yaml` is the master
record: every achievement becomes an addressable fact with an id, the tools and numbers it
evidences, and how strongly the candidate was involved. An optional `config/cv.master.yaml`
overlay can annotate facts (`skills`, `metrics`, `scope`, `sources`) or add new ones - it can
never rewrite the text of an existing fact.

For one saved job (or any job description file) the pipeline builds a strict JSON payload,
tailors it deterministically to the posting (keyword ordering, bold labels only for terms a
bullet already proves, page fitting), runs the same fact gate as the cover letter - including
title-drift and CV/letter consistency checks - and only then renders LaTeX to a PDF:

```bash
uv run jobscout cv build --source linkedin --id 123   # tailor to one saved job
uv run jobscout cv build --jd jobs/ad.txt --no-llm    # deterministic, from a text file
uv run jobscout cv gaps                               # requirements the master CV cannot evidence
```

Requirements the candidate cannot prove are reported as **gaps** - they never end up in the
document, and phrasings like "fast learner" for a missing tool are refused. The model (the same
endpoint as the cover letter) may only choose, order and label what the master CV already
states; a fresh-context reviewer may propose typed edits, but `grounding` objections are only
reported and `reframing` suggestions only shown. Outputs land in `data/cv/` as `.tex`, `.pdf`,
the payload JSON and a metadata JSON with the verification verdict; a `block` verdict writes
the source for review but no PDF. `JOBSCOUT_CV_ENABLED`, `JOBSCOUT_CV_LLM`,
`JOBSCOUT_CV_REVIEWER` and `JOBSCOUT_CV_COMPILE_PDF` gate the steps.

In the web UI every row in the Job Offers view has a **Build CV** button that runs this exact
pipeline for that one job (`POST /api/cv`), streams progress to the Writer agent, and then
offers the resulting **CV TEX / CV PDF** links. The job description is fetched lazily — only
when you build for the selected job — exactly like the cover letter.

The **Batch CVs** button tailors the top-fit saved jobs that do not have a CV yet, sequentially
on a background thread (fit score first, will-apply filter, `1-25` jobs, cancel anytime) with a
live progress card in the UI.

Before building for a job that has no CV yet, the UI asks a **JD-similarity reuse check**
(career-ops `jd:similarity`): the posting's description is compared, token-set overlap plus a
seniority gate, against the descriptions behind every CV already in `data/cv/`. A near-duplicate
answers **reuse** (45-72% answers **reuse with edits**, anything else **regenerate**), and the
seniority gate forces **regenerate** when the two postings name two different levels - a Junior
CV is never suggested for a Lead posting. It is a recommendation only: declining builds a fresh
CV as before, and a reuse copies the matching artifacts under the new job key with a
`reused_from` provenance entry instead of calling the model. CLI equivalents:

```bash
uv run jobscout cv reuse --source linkedin --id 123          # just the recommendation
uv run jobscout cv reuse --source linkedin --id 123 --apply  # copy the best candidate
uv run jobscout cv reuse --source linkedin --id 123 --from arbeitnow-xyz --apply  # override
```

```yaml
name: "Your Name"
headline: "AI Engineer"
email: "you@example.com"
phone: "+49 000 000000"
address: "Street 1"        # street and house number
postal_code: "12345"       # rendered as "Street 1, 12345 Berlin, Germany"
city: "Berlin"
country: "Germany"
linkedin: "https://www.linkedin.com/in/your-profile"
github: "https://github.com/your-profile"
website: ""
summary: "Short professional summary focused on your strongest experience."
motivation: |                     # your own argument, becomes the introduction
  Two to four sentences: why this kind of role fits you, what you enjoy
  about it and what you bring that other applicants do not.
skills: ["Python", "n8n"]
soft_skills: ["Communication", "Teamwork"]   # used when a posting asks for soft skills
experience:
  - company: "Example GmbH"
    role: "AI Engineer"
    start: "2023"
    end: "present"
    location: "Berlin"
    achievements: ["Built an automation workflow that reduced manual work by 30%."]
education:
  - institution: "Example University"
    degree: "M.Sc. Computer Science"
    start: "2020"
    end: "2022"
    location: "Berlin"
languages: ["German — fluent", "English — professional"]
certifications: ["Example certification"]
earliest_start: "2026-03-01"        # stated in the conclusion of the letter
notice_period_months: 1             # mentioned next to the start date
confidential_application: false      # asks for the application to be handled confidentially
cv_text: |                          # optional fallback when you do not fill every section
  Optional: paste the full CV text here.
```

### Letter structure

Every letter follows the classic German structure:

1. **Introduction** (2-3 sentences) — the door opener: the position, the motivation and what makes
   the applicant stand out. Built from the `motivation` and `summary` fields.
2. **Main part** (2-3 paragraphs) — the company (products, location, mission, where the applicant
   found the posting), the requirements of the posting (qualifications, specialist knowledge, IT
   and language skills, soft skills), and the proof that the profile fits, with concrete milestones.
3. **Conclusion** (1-2 sentences) — the motivation once more and the willingness to talk in person.
   The template appends the start date, the notice period and the confidentiality request here when
   the profile provides them, always in the language of the letter.
4. **Closing** — `Mit freundlichen Grüßen` / `Sincerely`.

The profile is deliberately free of photo, date of birth, marital status, nationality, salary
expectation, and references. Those fields must never be sent to an employer in Germany.

## Commands

```bash
make serve          # serve the built UI and API
make dev            # Vite dev server
make build          # typecheck and build the UI
make test           # backend tests
make check          # tests + TypeScript typecheck
make run-cached     # CLI cached run, no browser
make run            # CLI live run
make warm-linkedin  # manual LinkedIn login
make warm-indeed    # open Indeed and accept cookies
```

## Interface

The top bar switches between three views, and the choice is stored in the URL hash so a
view can be bookmarked and shared:

- `#run` — the agent control room with the run controls, the agent graph, and the live feed.
- `#cover-letters` — only the cover letter list for the scraped jobs.
- `#profile` — only the applicant profile form, open for editing.

The theme switch on the right of the top bar toggles between the light and the dark theme. The
first visit follows `prefers-color-scheme`; the choice is remembered for later visits. The
brand colors never change — the dark theme is a pure inversion of the canvas and the ink,
as described in `design.md`.

## Artifact archive and search

Every new job, its job description and the letters/CVs generated for it are archived verbatim under `data/artifacts/{source}/{job_id}/` (`job.json`, `jd.md`, `letter.json`, `documents.json` links to tex/pdf). The sheet/CSV flow is untouched; the archive is an additive, inspectable record.

`data/artifacts/index.sqlite` is only an index — delete it and rebuild:

```bash
uv run jobscout index rebuild
uv run jobscout index search "kafka" --source hn
uv run jobscout index search "Berlin" --json
```

Set `JOBSCOUT_ARTIFACTS=0` to disable archiving entirely.

## How it works

- `jobscout/orchestrator.py` runs the deterministic coordinator/scout/registrar workflow.
- `jobscout/sources/` contains cached and live source adapters.
- `jobscout/parsers/` normalizes LinkedIn/Indeed cards, links, and relative dates.
- `jobscout/sinks/` writes to local CSV or Google Sheets.
- `jobscout/run_manager.py` owns run state, pause/cancel control, and the event history.
- `jobscout/artifacts.py` writes the verbatim artifact archive and the rebuildable SQLite/FTS index.
- `jobscout/llm.py` holds the provider-agnostic transport (ollama, OpenAI-compatible, Anthropic, Gemini).
- `jobscout/ui/server.py` exposes the REST/SSE API and serves `web/dist`.
- `web/src/` contains the React control room and the SVG communication graph.

Dedupe is by `(source, job_id)` within a source. Cross-source fingerprint dedupe is optional and disabled by default (`dedupe_cross_source: false`).

A search run never sweeps job descriptions after saving. The full JD is fetched only for the job you open or generate documents for (UI job detail, cover letter, `cv build`) and is then cached under `data/job_descriptions/`. Opt back into a bounded post-run sweep with `COVER_LETTER_AUTO_DESCRIPTIONS=1` and `COVER_LETTER_AUTO_DESCRIPTION_LIMIT` (default 10).

## Verification

```bash
uv run jobscout run --cached
uv run pytest -q
npm --prefix web run build
```

The cached run should report `found=12 new=12` on a fresh CSV, then `found=12 new=0 skipped=12` on a repeat run.
