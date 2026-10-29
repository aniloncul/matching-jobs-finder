# JobScout

Two visible-browser scouts search LinkedIn and Indeed. A coordinator dispatches the work, and a registrar dedupes the reports into Google Sheets or a local CSV.

The UI is a live agent control room built to the monochrome blueprint in [`design.md`](design.md): four agents, animated communication signals, task text, pause/resume, and cancel.

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

Sign in manually, then close the browser. The session is stored in `.browser-profiles/linkedin`. Indeed runs logged out; if Indeed presents a verification challenge, the source reports an error and the run continues with the other source.

```bash
uv run jobscout run --sources both
```

Queries can be comma-, semicolon-, newline-, or `OR`-separated; each term is searched separately and the results are deduped. There is no CAPTCHA solving, proxy rotation, stealth mode, or credential storage. The adapters read public/search result HTML, keep volume low (25 jobs per source by default), and stop at cooperative pause/cancel checkpoints.

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

## How it works

- `jobscout/orchestrator.py` runs the deterministic coordinator/scout/registrar workflow.
- `jobscout/sources/` contains cached and live source adapters.
- `jobscout/parsers/` normalizes LinkedIn/Indeed cards, links, and relative dates.
- `jobscout/sinks/` writes to local CSV or Google Sheets.
- `jobscout/run_manager.py` owns run state, pause/cancel control, and the event history.
- `jobscout/ui/server.py` exposes the REST/SSE API and serves `web/dist`.
- `web/src/` contains the React control room and the SVG communication graph.

Dedupe is by `(source, job_id)` within a source. Cross-source fingerprint dedupe is optional and disabled by default (`dedupe_cross_source: false`).

## Verification

```bash
uv run jobscout run --cached
uv run pytest -q
npm --prefix web run build
```

The cached run should report `found=12 new=12` on a fresh CSV, then `found=12 new=0 skipped=12` on a repeat run.
