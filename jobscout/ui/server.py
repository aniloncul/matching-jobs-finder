from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from jobscout.config import load_settings
from jobscout.control import RunConflict
from jobscout.cover_letter import (
    CoverLetterError,
    generate_cover_letter,
    letter_paths,
)
from jobscout.cover_letter.description import DescriptionError, fetch_job_description
from jobscout.cover_letter.description_store import (
    has_saved_description,
    load_saved_description,
    save_job_description as persist_job_description,
)
from jobscout.cover_letter.cv_import import (
    CVImportError,
    import_cv_into_profile,
    images_from_upload,
)
from jobscout.cover_letter.profile import Profile, inspect_profile, save_profile
from jobscout.events import EventBus
from jobscout.exclusions import delete_job_files
from jobscout.models import AgentStatus, Job, Source
from jobscout.run_manager import RunManager
from jobscout.sinks import build_sink


settings = load_settings()
bus = EventBus()
manager = RunManager(settings, bus)
WEB_DIST = settings.root / "web" / "dist"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    bus.set_loop(asyncio.get_running_loop())
    yield


app = FastAPI(title="JobScout", version="0.1.0", lifespan=lifespan)


class RunRequest(BaseModel):
    sources: list[Literal["linkedin", "indeed"]] = Field(
        default_factory=lambda: ["linkedin", "indeed"]
    )
    cached: bool = False
    query: str | None = Field(default=None, max_length=300)


class CoverLetterRequest(BaseModel):
    source: Literal["linkedin", "indeed", "custom"]
    job_id: str = Field(min_length=1, max_length=200)
    force: bool = False
    company: str | None = None
    title: str | None = None
    description: str | None = None


class JobAppliedRequest(BaseModel):
    applied: bool


@app.get("/api/health")
async def health() -> dict:
    return {"ok": True, "run": manager.state.run_id, "status": manager.state.status}


@app.get("/api/proxy")
async def proxy_page(url: str) -> HTMLResponse:
    """Proxy external job page so it can be embedded in an iframe.

    Strips X-Frame-Options and CSP headers and rewrites relative
    URLs to absolute so images, CSS and JS still load.
    """
    import re
    from urllib.parse import urlparse

    try:
        import httpx
    except ImportError:
        raise HTTPException(
            status_code=500,
            detail="httpx is required for the proxy endpoint. Install it with: pip install httpx",
        )

    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        raise HTTPException(status_code=400, detail="Invalid URL")

    origin = f"{parsed.scheme}://{parsed.netloc}"

    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=20.0,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
            },
        ) as client:
            resp = await client.get(url)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Proxy fetch failed: {exc}") from exc

    content_type = resp.headers.get("content-type", "text/html")
    if "text/html" not in content_type:
        return HTMLResponse(content=resp.content, status_code=resp.status_code)

    html = resp.text

    # Rewrite relative URLs → absolute so resources still load
    base_tag = f'<base href="{origin}/" target="_blank">'
    html = re.sub(
        r"(<head[^>]*>)",
        rf"\1{base_tag}",
        html,
        count=1,
        flags=re.IGNORECASE,
    )

    return HTMLResponse(content=html, status_code=resp.status_code)


@app.get("/api/state")
async def state() -> dict:
    return manager.snapshot_event()


@app.get("/api/agents/{agent_id}/logs")
def agent_logs(agent_id: str) -> dict:
    payload = manager.snapshot_event()["state"]
    agent = payload["agents"].get(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    messages = [
        message
        for message in payload["messages"]
        if message.get("from") == agent_id or message.get("to") == agent_id
    ]
    return {
        "agent": agent,
        "logs": payload["agent_logs"].get(agent_id, []),
        "history": payload["agent_status_history"].get(agent_id, []),
        "messages": messages,
    }


@app.get("/api/profile")
def profile_status() -> dict:
    return _profile_payload(*inspect_profile(settings.cover_letter.profile_path))


@app.post("/api/profile/import")
async def import_profile(file: UploadFile = File(...)) -> dict:
    """Read an uploaded CV and return the profile fields it contains."""
    limit = settings.cover_letter.cv_import_max_upload_mb * 1024 * 1024
    data = await file.read()
    if len(data) > limit:
        raise HTTPException(
            status_code=413,
            detail=(
                f"The file is larger than {settings.cover_letter.cv_import_max_upload_mb} MB. "
                "Upload a smaller CV or raise CV_IMPORT_MAX_UPLOAD_MB."
            ),
        )
    if not data:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")

    try:
        pages = images_from_upload(
            data,
            file.filename or "upload",
            file.content_type,
            max_pages=settings.cover_letter.cv_import_max_pages,
        )
    except CVImportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    current, _issues = inspect_profile(settings.cover_letter.profile_path)
    base = current or Profile()
    try:
        updated = import_cv_into_profile(
            base,
            data,
            file.filename or "upload",
            file.content_type,
            base_url=settings.cover_letter.base_url,
            api_key=settings.cover_letter.api_key or "ollama",
            model=settings.cover_letter.model,
            vision_model=settings.cover_letter.vision_model,
            max_pages=settings.cover_letter.cv_import_max_pages,
            timeout_seconds=settings.cover_letter.timeout_seconds,
            pages=pages,
        )
    except CVImportError as exc:
        import traceback
        print(f"[ERROR /api/profile/import] {exc}")
        traceback.print_exc()
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {
        "profile": updated.model_dump(mode="json"),
        "filled": _changed_fields(base, updated),
    }


def _changed_fields(before: Profile, after: Profile) -> list[str]:
    """List the field names the import actually filled in."""
    old = before.model_dump(mode="json")
    new = after.model_dump(mode="json")
    changed = [
        key
        for key, value in new.items()
        if value not in ("", [], None) and old.get(key) != value
    ]
    return changed


@app.put("/api/profile")
def update_profile(payload: Profile) -> dict:
    try:
        save_profile(settings.cover_letter.profile_path, payload)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not save profile: {exc}") from exc
    return _profile_payload(*inspect_profile(settings.cover_letter.profile_path))


def _profile_payload(profile: Profile | None, issues: list[str]) -> dict:
    return {
        "ready": bool(profile and not issues),
        "name": profile.name if profile else None,
        "path": str(settings.cover_letter.profile_path),
        "issues": issues,
        "profile": profile.model_dump(mode="json") if profile else None,
    }


@app.get("/api/jobs")
def list_jobs(limit: int = 100) -> dict:
    try:
        sink = build_sink(settings)
        jobs = sink.list_jobs(limit=max(1, min(limit, 500)))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not read jobs: {exc}") from exc
    return {"jobs": [_job_payload(job) for job in jobs]}


@app.delete("/api/jobs/{source}/{job_id}")
def delete_job(
    source: str,
    job_id: str,
) -> dict:
    try:
        sink = build_sink(settings)
        sink.delete_job(source, job_id)
        delete_job_files(settings, source, job_id)
        return {"status": "deleted", "source": source, "job_id": job_id}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to delete job: {exc}") from exc


@app.patch("/api/jobs/{source}/{job_id}/applied")
def set_job_applied(
    source: str,
    job_id: str,
    payload: JobAppliedRequest,
) -> dict:
    try:
        sink = build_sink(settings)
        ok = sink.update_job_applied(source, job_id, payload.applied)
        return {
            "status": "ok",
            "updated": ok,
            "source": source,
            "job_id": job_id,
            "applied": payload.applied,
        }
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update applied status: {exc}",
        ) from exc


@app.get("/api/jobs/{source}/{job_id}/description")
def read_job_description(
    source: str,
    job_id: str,
) -> PlainTextResponse:
    text = load_saved_description(settings, source, job_id)
    if not text:
        try:
            sink = build_sink(settings)
            job = _find_job(sink.list_jobs(limit=500), source, job_id)
        except Exception:
            job = None
        text = (job.description or "").strip() if job else ""
    if not text:
        try:
            sink = build_sink(settings)
            job = _find_job(sink.list_jobs(limit=500), source, job_id)
            if job and job.source in {Source.linkedin, Source.indeed}:
                fetched = fetch_job_description(settings, job)
                if fetched:
                    text = fetched
                    persist_job_description(settings, job, text)
                    try:
                        sink.save_description(source, job_id, text)
                    except Exception:
                        pass
        except Exception:
            pass
    if not text:
        raise HTTPException(status_code=404, detail="No description available for this job")
    return PlainTextResponse(text, media_type="text/plain; charset=utf-8")


@app.get("/api/jobs/{source}/{job_id}/preview", response_class=HTMLResponse)
def preview_job_page(
    source: str,
    job_id: str,
) -> HTMLResponse:
    import html as html_lib

    job = None
    try:
        sink = build_sink(settings)
        job = _find_job(sink.list_jobs(limit=500), source, job_id)
    except Exception:
        pass

    text = load_saved_description(settings, source, job_id)
    if not text and job:
        text = (job.description or "").strip()

    title = job.title if job else f"{source.upper()} Job"
    company = job.company if job else ""
    location = job.location or "" if job else ""
    link = job.link if job else "#"
    status_applied = bool(job and job.applied)

    esc_title = html_lib.escape(title)
    esc_company = html_lib.escape(company)
    esc_location = html_lib.escape(location)
    esc_link = html_lib.escape(link)
    esc_source = html_lib.escape(source.upper())

    if text:
        paragraphs = [f"<p>{html_lib.escape(p.strip())}</p>" for p in text.split("\n\n") if p.strip()]
        body_content = "".join(paragraphs) if paragraphs else f"<p>{html_lib.escape(text)}</p>"
    else:
        body_content = f"""
        <div class="empty-state">
            <div class="spinner"></div>
            <p><strong>Job Description is not loaded yet.</strong></p>
            <p class="sub">Click below to fetch the full text or open directly on {esc_source}:</p>
            <div class="actions">
                <a class="btn primary" href="{esc_link}" target="_blank" rel="noopener noreferrer">Open Original Posting on {esc_source} ↗</a>
            </div>
        </div>
        """

    html_page = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{esc_title} — {esc_company}</title>
    <style>
        :root {{
            --bg: #0c1017;
            --surface: #141b24;
            --surface-raised: #1c2533;
            --border: #243042;
            --text-primary: #e6edf3;
            --text-secondary: #8b949e;
            --accent: #5695F5;
            --accent-rgb: 86, 149, 245;
            --success: #238636;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            background: var(--bg);
            color: var(--text-primary);
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            font-size: 14px;
            line-height: 1.6;
            padding: 1.5rem;
        }}
        .header {{
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 1rem 1.25rem;
            margin-bottom: 1.25rem;
            display: flex;
            align-items: center;
            justify-content: space-between;
            flex-wrap: wrap;
            gap: 1rem;
        }}
        .header-left {{
            display: flex;
            flex-direction: column;
            gap: 0.25rem;
        }}
        .badge {{
            display: inline-flex;
            align-items: center;
            gap: 0.35rem;
            font-size: 11px;
            font-weight: 700;
            letter-spacing: 0.05em;
            text-transform: uppercase;
            color: var(--accent);
            background: rgba(var(--accent-rgb), 0.12);
            padding: 2px 8px;
            border-radius: 4px;
            width: fit-content;
        }}
        .title {{
            font-size: 1.2rem;
            font-weight: 600;
            color: #ffffff;
        }}
        .meta {{
            font-size: 13px;
            color: var(--text-secondary);
        }}
        .meta strong {{ color: var(--text-primary); }}
        .actions {{
            display: flex;
            gap: 0.5rem;
            align-items: center;
        }}
        .btn {{
            display: inline-flex;
            align-items: center;
            gap: 0.4rem;
            padding: 0.45rem 0.9rem;
            font-size: 13px;
            font-weight: 500;
            border-radius: 6px;
            text-decoration: none;
            cursor: pointer;
            transition: all 0.15s ease;
            border: 1px solid var(--border);
            background: var(--surface-raised);
            color: var(--text-primary);
        }}
        .btn:hover {{
            background: var(--border);
            color: #fff;
        }}
        .btn.primary {{
            background: var(--accent);
            border-color: var(--accent);
            color: #ffffff;
        }}
        .btn.primary:hover {{
            opacity: 0.92;
        }}
        .content-card {{
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 1.5rem;
        }}
        .content-card h3 {{
            font-size: 13px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: var(--text-secondary);
            margin-bottom: 1rem;
            border-bottom: 1px solid var(--border);
            padding-bottom: 0.5rem;
        }}
        .description {{
            font-size: 14px;
            color: #c9d1d9;
            word-break: break-word;
        }}
        .description p {{
            margin-bottom: 1rem;
            white-space: pre-wrap;
        }}
        .empty-state {{
            text-align: center;
            padding: 3rem 1rem;
            color: var(--text-secondary);
        }}
        .empty-state p strong {{
            color: var(--text-primary);
            font-size: 15px;
        }}
        .empty-state .sub {{
            margin: 0.5rem 0 1.25rem;
            font-size: 13px;
        }}
    </style>
</head>
<body>
    <header class="header">
        <div class="header-left">
            <span class="badge">{esc_source}</span>
            <h1 class="title">{esc_title}</h1>
            <div class="meta">
                <strong>{esc_company}</strong> {f"· {esc_location}" if esc_location else ""}
            </div>
        </div>
        <div class="actions">
            <a class="btn primary" href="{esc_link}" target="_blank" rel="noopener noreferrer">
                Open in New Tab ↗
            </a>
        </div>
    </header>

    <main class="content-card">
        <h3>Job Description</h3>
        <div class="description">
            {body_content}
        </div>
    </main>
</body>
</html>
"""
    return HTMLResponse(content=html_page)


@app.post("/api/jobs/{source}/{job_id}/description")
def save_description(
    source: Literal["linkedin", "indeed"],
    job_id: str,
    refresh: bool = False,
) -> dict:
    try:
        sink = build_sink(settings)
        job = _find_job(sink.list_jobs(limit=500), source, job_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not read jobs: {exc}") from exc
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    cached = load_saved_description(settings, source, job_id)
    if cached and not refresh:
        sheet_saved = False
        try:
            sheet_saved = sink.save_description(source, job_id, cached)
        except Exception:
            sheet_saved = False
        return {
            "saved": True,
            "cached": True,
            "characters": len(cached),
            "sheet_saved": sheet_saved,
            "url": f"/api/jobs/{source}/{job_id}/description",
        }

    if not refresh and job.description and len(job.description.strip()) >= 20:
        description = job.description.strip()
        path = None
        cached = True
    else:
        try:
            description = fetch_job_description(settings, job)
        except DescriptionError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        cached = False
        try:
            path = persist_job_description(settings, job, description)
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Description fetched but could not be saved: {exc}",
            ) from exc
    try:
        sheet_saved = sink.save_description(source, job_id, description)
    except Exception:
        sheet_saved = False
    return {
        "saved": True,
        "cached": cached,
        "characters": len(description),
        "path": str(path) if path else None,
        "sheet_saved": sheet_saved,
        "url": f"/api/jobs/{source}/{job_id}/description",
    }


@app.post("/api/cover-letters")
def create_cover_letter(payload: CoverLetterRequest) -> dict:
    try:
        description = payload.description
        sink = None
        if payload.source == "custom":
            job = Job(
                company=payload.company or "Unternehmen",
                title=payload.title or "Position",
                link="",
                source=Source.custom,
                job_id=payload.job_id,
            )
        else:
            sink = build_sink(settings)
            job = _find_job(sink.list_jobs(limit=500), payload.source, payload.job_id)
            if job is None:
                raise HTTPException(status_code=404, detail="Job not found")

        manager.writer_status(
            AgentStatus.working,
            f"Writing {job.company} · {job.title}",
            0.02,
        )
        manager.writer_message(f"Preparing cover letter for {job.company}")
        manager.writer_log(f"Received request to draft cover letter for {job.company} · {job.title}")

        def progress(task: str, value: float | None) -> None:
            manager.writer_status(AgentStatus.working, task, value)
            if value is not None:
                manager.writer_log(f"{task} ({int(value * 100)}%)")
            else:
                manager.writer_log(task)

        result = generate_cover_letter(
            settings,
            job,
            force=payload.force,
            description=description,
            on_progress=progress,
        )
        if sink is not None:
            saved = load_saved_description(settings, job.source.value, job.job_id)
            if saved:
                try:
                    sink.save_description(job.source.value, job.job_id, saved)
                except Exception:
                    pass
    except HTTPException:
        raise
    except CoverLetterError as exc:
        manager.writer_status(AgentStatus.error, str(exc))
        manager.writer_message(str(exc), kind="error")
        manager.writer_log(f"Cover letter generation error: {exc}", level="error")
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        manager.writer_status(AgentStatus.error, str(exc))
        manager.writer_message(str(exc), kind="error")
        manager.writer_log(f"Cover letter generation failed: {exc}", level="error")
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    manager.writer_status(
        AgentStatus.done,
        f"Letter ready · {job.company}",
        1.0,
    )
    manager.writer_message(
        f"Cover letter ready · {job.company} · {job.title}",
        kind="result",
    )
    manager.writer_log(f"Successfully generated cover letter and compiled PDF for {job.company}")
    return {
        "letter": result.model_dump(mode="json"),
        "urls": _letter_urls(result.source, result.job_id),
    }


@app.get("/api/cover-letters/{source}/{job_id}/{kind}")
def download_cover_letter(source: str, job_id: str, kind: str) -> FileResponse:
    if source not in {"linkedin", "indeed", "custom"} or kind not in {"tex", "pdf"}:
        raise HTTPException(status_code=404, detail="Cover letter not found")
    paths = letter_paths(settings, source, job_id)
    path = paths[kind]
    if not path.exists():
        raise HTTPException(status_code=404, detail="Cover letter not found")
    media_type = "application/pdf" if kind == "pdf" else "application/x-tex"
    return FileResponse(
        path,
        media_type=media_type,
        filename=f"cover-letter-{source}-{job_id}.{kind}",
        content_disposition_type="inline",
    )


@app.get("/api/events")
async def events(request: Request):
    queue = bus.subscribe()

    async def generate():
        try:
            snapshot = manager.snapshot_event()
            last_seq = int(snapshot.get("seq", 0))
            yield _sse(snapshot)
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                seq = int(event.get("seq", 0))
                if seq <= last_seq:
                    continue
                last_seq = seq
                yield _sse(event)
        finally:
            bus.unsubscribe(queue)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/runs")
async def start_run(payload: RunRequest) -> dict:
    if not payload.sources:
        raise HTTPException(status_code=422, detail="Select at least one source")
    try:
        manager.start(
            payload.sources,
            cached=payload.cached,
            query=payload.query,
        )
    except RunConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return manager.snapshot_event()


@app.post("/api/runs/{run_id}/pause")
async def pause_run(run_id: str) -> dict:
    _require_run(run_id)
    try:
        manager.pause()
    except RunConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return manager.snapshot_event()


@app.post("/api/runs/{run_id}/resume")
async def resume_run(run_id: str) -> dict:
    _require_run(run_id)
    try:
        manager.resume()
    except RunConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return manager.snapshot_event()


@app.post("/api/runs/{run_id}/cancel")
async def cancel_run(run_id: str) -> dict:
    _require_run(run_id)
    try:
        manager.cancel()
    except RunConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return manager.snapshot_event()


def _find_job(jobs: list[Job], source: str, job_id: str) -> Job | None:
    for job in jobs:
        if job.source.value == source and job.job_id == job_id:
            return job
    return None


def _job_payload(job: Job) -> dict:
    paths = letter_paths(settings, job.source.value, job.job_id)
    urls = _letter_urls(job.source.value, job.job_id)
    has_description = bool((job.description or "").strip()) or has_saved_description(
        settings,
        job.source.value,
        job.job_id,
    )
    return {
        "source": job.source.value,
        "job_id": job.job_id,
        "company": job.company,
        "title": job.title,
        "link": job.link,
        "posted_date": job.posted_date,
        "location": job.location,
        "search_query": job.search_query,
        "scraped_at": job.scraped_at,
        "has_tex": paths["tex"].exists(),
        "has_pdf": paths["pdf"].exists(),
        "has_description": has_description,
        "description_url": (
            f"/api/jobs/{job.source.value}/{job.job_id}/description"
            if has_description
            else None
        ),
        "applied": bool(getattr(job, "applied", False)),
        "urls": urls,
    }


def _letter_urls(source: str, job_id: str) -> dict[str, str | None]:
    paths = letter_paths(settings, source, job_id)
    return {
        "tex": (
            f"/api/cover-letters/{source}/{job_id}/tex"
            if paths["tex"].exists()
            else None
        ),
        "pdf": (
            f"/api/cover-letters/{source}/{job_id}/pdf"
            if paths["pdf"].exists()
            else None
        ),
    }


def _require_run(run_id: str) -> None:
    if manager.state.run_id != run_id:
        raise HTTPException(status_code=404, detail="Run not found")


def _sse(event: dict) -> str:
    return (
        f"id: {event.get('seq', 0)}\n"
        f"data: {json.dumps(event, ensure_ascii=False, separators=(',', ':'))}\n\n"
    )


if WEB_DIST.exists():
    app.mount("/", StaticFiles(directory=str(WEB_DIST), html=True), name="web")
else:

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return """
        <!doctype html>
        <html>
          <head><meta charset="utf-8"><title>JobScout</title></head>
          <body style="font-family:Menlo,monospace;padding:40px">
            <h1>JobScout API is running.</h1>
            <p>Build the UI with <code>npm --prefix web install &amp;&amp; npm --prefix web run build</code>,
            or run the Vite dev server with <code>npm --prefix web run dev</code>.</p>
          </body>
        </html>
        """


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "jobscout.ui.server:app",
        host=settings.ui.host,
        port=settings.ui.port,
        reload=False,
    )
