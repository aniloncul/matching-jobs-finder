"""Build a tailored CV: master -> payload -> tailor -> verify -> PDF.

Order matters here.  Tailoring happens before the gate, so the gate sees exactly
the text that is about to be rendered.  A ``block`` verdict still writes the
``.tex`` and the metadata - the candidate has to be able to read *why* it was
refused - but no PDF is produced.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from jobscout.config import Settings
from jobscout.artifacts import store_for
from jobscout.cover_letter.description import fetch_job_description
from jobscout.cover_letter.description_store import (
    load_saved_description,
    save_job_description,
)
from jobscout.cover_letter.generator import letter_paths
from jobscout.cover_letter.latex import detect_language
from jobscout.cover_letter.profile import Profile
from jobscout.cv.llm import CVLLM
from jobscout.cv.master import MasterCV, load_master
from jobscout.cv.payload import CVRenderPayload, from_master, to_dict
from jobscout.cv.render import (
    compile_cv_pdf,
    estimate_pages,
    render_cv,
)
from jobscout.cv.reviewer import ReviewOutcome, ReviewerError, apply_edits, parse_reviewer_payload
from jobscout.cv.tailor import TailorPlan, apply_llm_tailoring, tailor_payload, target_keywords
from jobscout.cv.verify import CVVerification, verify_cv
from jobscout.models import Job
from jobscout.security import guard_text

Progress = Callable[[str, float | None], None]


class CVBuildError(Exception):
    """Raised when a CV cannot be built at all (missing master CV, bad job)."""


@dataclass
class CVBuildResult:
    key: str
    tex_path: str
    payload_path: str
    metadata_path: str
    verdict: str = "pass"
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    gaps: list[dict[str, Any]] = field(default_factory=list)
    coverage_pct: float = 0.0
    pdf_path: str | None = None
    pdf_error: str | None = None
    pages: int | None = None
    ats_score: int | None = None
    ats_grade: str | None = None
    reused: bool = False
    llm_used: bool = False

    @property
    def blocked(self) -> bool:
        return self.verdict == "block"

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "verdict": self.verdict,
            "blocked": self.blocked,
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
            "gaps": list(self.gaps),
            "coverage_pct": self.coverage_pct,
            "tex_path": self.tex_path,
            "pdf_path": self.pdf_path,
            "pdf_error": self.pdf_error,
            "payload_path": self.payload_path,
            "metadata_path": self.metadata_path,
            "pages": self.pages,
            "ats_score": self.ats_score,
            "ats_grade": self.ats_grade,
            "reused": self.reused,
            "llm_used": self.llm_used,
        }


def _safe_key(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value)).strip("-")
    return cleaned[:120] or "job"


def cv_key(job: Job | None) -> str:
    if job is None:
        return "master"
    return f"{_safe_key(job.source.value)}-{_safe_key(job.job_id)}"


def cv_paths(settings: Settings, key: str) -> dict[str, Path]:
    directory = Path(settings.cv.output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    return {
        "tex": directory / f"{key}.tex",
        "pdf": directory / f"{key}.pdf",
        "payload": directory / f"{key}.payload.json",
        "json": directory / f"{key}.json",
    }


def _description_for(settings: Settings, job: Job | None, description: str) -> tuple[str, str | None]:
    """Prefer an archived JD, then the sheet text, then the posting snippet."""
    if description and description.strip():
        return description.strip(), None
    if job is None:
        return "", None
    cached = load_saved_description(settings, job.source.value, job.job_id)
    if cached:
        return cached, None
    if job.description and len(job.description.strip()) >= 20:
        return job.description.strip(), None
    # Lazy fetch: only the job the candidate actually selected reaches the
    # network, never a bulk sweep after a search run.  Best effort - a blocked
    # or dead posting falls back to whatever snippet we already have.
    if job.link:
        try:
            fetched = fetch_job_description(settings, job).strip()
        except Exception:
            fetched = ""
        if len(fetched) >= 80:
            try:
                save_job_description(settings, job, fetched)
            except Exception:
                pass
            store_for(settings).archive_description(
                job.source.value, job.job_id, fetched
            )
            return fetched, None
    if job.posted_text and len(job.posted_text.strip()) >= 40:
        return (
            job.posted_text.strip(),
            "Only the short posting snippet was available; keyword targeting is limited.",
        )
    return (
        f"Position: {job.title} at {job.company}\nLocation: {job.location or 'not specified'}",
        "No job description was available; the CV is the untailored master.",
    )


def _letter_text(settings: Settings, job: Job | None) -> str | None:
    """Existing cover letter for the same job, for the consistency pass."""
    if job is None:
        return None
    try:
        paths = letter_paths(settings, job.source.value, job.job_id)
        tex = Path(paths["tex"])
        if not tex.exists():
            return None
        return tex.read_text(encoding="utf-8", errors="ignore")
    except Exception:  # noqa: BLE001 - the consistency pass is advisory
        return None


def build_cv(
    settings: Settings,
    *,
    job: Job | None = None,
    description: str = "",
    master: MasterCV | None = None,
    profile: Profile | None = None,
    llm: CVLLM | None = None,
    use_llm: bool | None = None,
    use_reviewer: bool | None = None,
    compile_pdf: bool | None = None,
    force: bool = False,
    language: str | None = None,
    on_progress: Progress | None = None,
) -> CVBuildResult:
    """Build one CV.  Deterministic unless an LLM is explicitly available."""
    progress = on_progress or (lambda _task, _value=None: None)
    config = settings.cv
    key = cv_key(job)
    paths = cv_paths(settings, key)

    if paths["tex"].exists() and not force and job is not None:
        metadata: dict[str, Any] = {}
        if paths["json"].exists():
            try:
                metadata = json.loads(paths["json"].read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                metadata = {}
        return CVBuildResult(
            key=key,
            tex_path=str(paths["tex"]),
            payload_path=str(paths["payload"]),
            metadata_path=str(paths["json"]),
            verdict=str(metadata.get("verdict", "pass")),
            pdf_path=str(paths["pdf"]) if paths["pdf"].exists() else None,
            reused=True,
        )

    progress("Loading the master CV", 0.05)
    if master is not None:
        resolved_master = master
    elif profile is not None:
        resolved_master = MasterCV.from_profile(profile)
    else:
        try:
            resolved_master = load_master(config_profile_path(settings), config.master_path)
        except Exception as exc:  # MasterCVError, ProfileError
            raise CVBuildError(str(exc)) from exc

    raw_description, note = _description_for(settings, job, description)
    # Every job description that can reach a model goes through the guard.
    guarded = guard_text(raw_description)
    working_description = guarded.clean_text
    language = language or (detect_language(working_description) if working_description else "de")

    progress("Building the payload", 0.2)
    payload: CVRenderPayload = from_master(
        resolved_master,
        target_role=job.title if job else "",
        company=job.company if job else "",
    )
    payload.meta.language = language
    notes: list[str] = [note] if note else []
    if guarded.flagged:
        notes.append(guarded.summary())

    progress("Tailoring to the job description", 0.35)
    keywords = target_keywords(working_description, limit=config.keywords)
    payload, plan = tailor_payload(
        payload,
        resolved_master,
        keywords,
        language=language,
        max_bullets_per_role=config.max_bullets_per_role,
        max_total_bullets=config.max_total_bullets or None,
    )
    notes.extend(plan.notes)

    want_llm = config.llm_tailoring if use_llm is None else use_llm
    want_reviewer = config.reviewer_enabled if use_reviewer is None else use_reviewer
    client = llm
    if client is None and (want_llm or want_reviewer) and settings.cover_letter.api_key:
        client = CVLLM(
            base_url=settings.cover_letter.base_url,
            api_key=settings.cover_letter.api_key or "ollama",
            model=settings.cover_letter.model,
            temperature=settings.cover_letter.temperature,
            max_tokens=settings.cover_letter.max_tokens,
            timeout_seconds=settings.cover_letter.timeout_seconds,
            num_ctx=settings.cover_letter.num_ctx,
            provider=getattr(settings.cover_letter, "provider", "auto"),
            allow_remote=getattr(settings.cover_letter, "allow_remote", False),
        )
    llm_used = False

    if client is not None and want_llm:
        progress("Model tailoring (choice and order only)", 0.5)
        try:
            raw = client.tailor(
                resolved_master, payload, keywords, description=working_description, language=language
            )
            payload, tailor_notes = apply_llm_tailoring(payload, resolved_master, raw)
            notes.extend(tailor_notes)
            llm_used = True
            payload.meta.model = settings.cover_letter.model
        except Exception as exc:  # noqa: BLE001 - tailoring is optional by design
            notes.append(f"Model tailoring skipped: {exc}")

    review_outcome: ReviewOutcome | None = None
    if client is not None and want_reviewer:
        progress("Fresh-context review", 0.62)
        review_outcome = _review(client, resolved_master, payload, keywords, working_description, language, notes)
        if review_outcome is not None:
            llm_used = True
            notes.extend(review_outcome.notes)

    # Cutting may still be needed after the model reordered blocks.
    if config.max_pages and estimate_pages(payload) > config.max_pages:
        payload, extra_dropped = _fit_to_pages(payload, resolved_master, keywords, config.max_pages)
        if extra_dropped:
            notes.append(f"Cut {len(extra_dropped)} more bullet(s) to fit {config.max_pages} pages.")

    progress("Checking every claim against the master CV", 0.72)
    verification: CVVerification = verify_cv(
        payload,
        resolved_master,
        letter_text=_letter_text(settings, job),
        target_company=(job.company if job else ""),
        forbidden_phrases=settings.cover_letter.forbidden_phrases or None,
    )
    payload.meta.coverage_pct = plan.coverage_pct
    payload.meta.notes = [item for item in dict.fromkeys([*payload.meta.notes, *notes]) if item]

    progress("Rendering LaTeX", 0.8)
    try:
        tex = render_cv(config.template_dir, payload, language=language)
    except Exception as exc:  # noqa: BLE001
        raise CVBuildError(f"Could not render the CV template: {exc}") from exc
    paths["tex"].write_text(tex, encoding="utf-8")
    paths["payload"].write_text(
        json.dumps(to_dict(payload), ensure_ascii=False, indent=2), encoding="utf-8"
    )

    result = CVBuildResult(
        key=key,
        tex_path=str(paths["tex"]),
        payload_path=str(paths["payload"]),
        metadata_path=str(paths["json"]),
        verdict=verification.verdict,
        blockers=list(verification.blockers),
        warnings=list(verification.warnings),
        gaps=[item.to_dict() for item in plan.gaps],
        coverage_pct=plan.coverage_pct,
        llm_used=llm_used,
    )

    do_compile = config.compile_pdf if compile_pdf is None else compile_pdf
    pdf_path: Path | None = None
    if verification.verdict == "block":
        result.pdf_error = "Blocked by the CV checks. Fix the facts, then rebuild."
    elif do_compile:
        progress("Compiling PDF", 0.88)
        pdf_path, result.pdf_error = compile_cv_pdf(paths["tex"])
        result.pdf_path = str(pdf_path) if pdf_path else None

    if pdf_path is not None:
        progress("Reading the PDF back", 0.94)
        result.pages, result.ats_score, result.ats_grade = _analyse_pdf(
            pdf_path, min_score=config.ats_min_score
        )

    metadata = {
        "key": key,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "job": None if job is None else {"source": job.source.value, "job_id": job.job_id,
                                         "company": job.company, "title": job.title, "link": job.link},
        "language": language,
        "llm_used": llm_used,
        "model": payload.meta.model,
        "keywords": [item.to_dict() for item in plan.keywords],
        "coverage": plan.coverage.to_dict() if plan.coverage else None,
        "gaps": [item.to_dict() for item in plan.gaps],
        "tailoring": plan.to_dict(),
        "review": review_outcome.to_dict() if review_outcome else None,
        "verification": verification.to_dict(),
        "posting_guard": guarded.to_dict(),
        "notes": payload.meta.notes,
        "pages": result.pages,
        "estimated_pages": estimate_pages(payload),
        "ats": {"score": result.ats_score, "grade": result.ats_grade},
        "pdf_error": result.pdf_error,
    }
    paths["json"].write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    if job is not None:
        artifacts = store_for(settings)
        artifacts.link_document(job.source.value, job.job_id, "cv-tex", paths["tex"])
        artifacts.link_document(job.source.value, job.job_id, "cv-json", paths["json"])
        if result.pdf_path:
            artifacts.link_document(job.source.value, job.job_id, "cv-pdf", result.pdf_path)
    progress("CV ready", 1.0)
    return result


def config_profile_path(settings: Settings) -> Path:
    return settings.cover_letter.profile_path


def _review(
    client: CVLLM,
    master: MasterCV,
    payload: CVRenderPayload,
    keywords,
    description: str,
    language: str,
    notes: list[str],
) -> ReviewOutcome | None:
    """One reviewer call with one retry on an unparsable response."""
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            raw = client.review(
                master, payload, keywords, description=description, language=language
            )
            edits = parse_reviewer_payload(raw)
        except Exception as exc:  # noqa: BLE001 - review is optional by design
            last_error = exc
            if attempt == 0:
                notes.append("Reviewer response was invalid; retrying once.")
            continue
        _, outcome = apply_edits(payload, edits, master)
        return outcome
    notes.append(f"Reviewer skipped: {last_error}")
    return None


def _fit_to_pages(payload, master, keywords, max_pages: int):
    """Progressive cutting until the estimate fits."""
    from jobscout.cv.tailor import cut_bullets

    fresh = payload
    dropped: list[str] = []
    total = 12
    while estimate_pages(fresh) > max_pages and total < 40:
        fresh, more = cut_bullets(
            fresh,
            keywords,
            max_bullets_per_role=max(2, total // 2),
            max_total_bullets=total,
        )
        if not more:
            break
        dropped.extend(more)
        total += 2
    return fresh, dropped


def _analyse_pdf(pdf_path: Path, *, min_score: int) -> tuple[int | None, int | None, str | None]:
    """Pages and ATS score from the real PDF; advisory, never fatal."""
    try:
        from jobscout.verification import analyze_pdf

        report = analyze_pdf(pdf_path, min_score=min_score)
        return report.pages, report.score, report.grade
    except Exception:  # noqa: BLE001 - the ATS check is advisory
        try:
            from jobscout.verification.pdf import read_pdf

            return read_pdf(pdf_path).page_count, None, None
        except Exception:  # noqa: BLE001
            return None, None, None
