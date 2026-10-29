from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections.abc import Callable
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from jobscout.config import Settings
from jobscout.cover_letter.bullets import extract_bullets
from jobscout.cover_letter.description import DescriptionError, fetch_job_description
from jobscout.cover_letter.description_store import (
    load_saved_description,
    save_job_description,
)
from jobscout.cover_letter.embedder import Embedder
from jobscout.cover_letter.latex import (
    availability_sentence,
    confidentiality_sentence,
    format_date,
    notice_period_sentence,
    render_letter,
)
from jobscout.cover_letter.llm import CoverLetterLLM, LLMError
from jobscout.cover_letter.matcher import HybridMatcher
from jobscout.cover_letter.profile import Profile, ProfileError, load_profile
from jobscout.cover_letter.requirements import extract_requirements
from jobscout.cover_letter.verifier import verify_letter
from jobscout.models import Job


Progress = Callable[[str, float | None], None]


class CoverLetterError(Exception):
    """Raised when a cover letter cannot be generated."""


class CoverLetterResult(BaseModel):
    source: str
    job_id: str
    company: str
    title: str
    language: str
    tex_path: str
    pdf_path: str | None = None
    metadata_path: str | None = None
    reused: bool = False
    warning: str | None = None
    pdf_error: str | None = None


def _safe_key(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")
    return cleaned[:120] or "job"


def letter_paths(settings: Settings, source: str, job_id: str) -> dict[str, Path]:
    directory = Path(settings.cover_letter.output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    base = f"{_safe_key(source)}-{_safe_key(job_id)}"
    return {
        "tex": directory / f"{base}.tex",
        "pdf": directory / f"{base}.pdf",
        "json": directory / f"{base}.json",
    }


def _detect_language(text: str) -> str:
    lowered = f" {text.lower()} "
    german = sum(
        lowered.count(f" {word} ")
        for word in (
            "und",
            "der",
            "die",
            "das",
            "mit",
            "für",
            "wir",
            "sie",
            "bewerbung",
            "erfahrung",
            "kenntnisse",
        )
    ) + sum(lowered.count(char) for char in ("ä", "ö", "ü", "ß"))
    english = sum(
        lowered.count(f" {word} ")
        for word in (
            "and",
            "the",
            "with",
            "you",
            "we",
            "for",
            "experience",
            "skills",
            "application",
        )
    )
    return "en" if english > german else "de"


def _as_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _normalise(data: dict[str, Any], description: str) -> dict[str, Any]:
    language = data.get("language")
    if language not in {"de", "en"}:
        language = _detect_language(description)

    intro = _as_text(data.get("intro"))
    conclusion = _as_text(data.get("conclusion"))
    main_raw = data.get("main_paragraphs")
    if not isinstance(main_raw, list):
        main_raw = []
    main_paragraphs = [_as_text(item) for item in main_raw]
    main_paragraphs = [item for item in main_paragraphs if item]

    legacy_raw = data.get("paragraphs")
    if isinstance(legacy_raw, list):
        legacy = [_as_text(item) for item in legacy_raw]
        legacy = [item for item in legacy if item]
        if legacy and not (intro or main_paragraphs or conclusion):
            if len(legacy) == 1:
                intro = legacy[0]
            elif len(legacy) == 2:
                intro, conclusion = legacy
            else:
                intro = legacy[0]
                conclusion = legacy[-1]
                main_paragraphs = legacy[1:-1]

    if not intro:
        intro = (
            "Mit großem Interesse bewerbe ich mich um die ausgeschriebene Position."
            if language == "de"
            else "I am writing to express my strong interest in the advertised position."
        )
    if not main_paragraphs:
        raise CoverLetterError("The model did not return any main paragraphs")
    if not conclusion:
        conclusion = (
            "Gerne erläutere ich meine Motivation und Erfahrung in einem persönlichen Gespräch."
            if language == "de"
            else "I would welcome the opportunity to discuss my motivation and experience in an interview."
        )

    matches = data.get("requirement_matches")
    if not isinstance(matches, list):
        matches = []
    return {
        "language": language,
        "subject": _as_text(data.get("subject")),
        "salutation": _as_text(data.get("salutation")),
        "recipient_person": _as_text(data.get("recipient_person")),
        "recipient_department": _as_text(data.get("recipient_department")),
        "intro": intro,
        "main_paragraphs": main_paragraphs[:4],
        "conclusion": conclusion,
        "closing": _as_text(data.get("closing")),
        "requirement_matches": [
            item for item in matches if isinstance(item, dict)
        ],
    }


def _conclusion_additions(profile: Profile, language: str) -> list[str]:
    """Build the optional closing sentences of the conclusion.

    The classic German cover letter may state when the applicant can start and
    may ask for the application to be handled confidentially. Both sentences are
    rendered here so they are always correct and in the language of the letter.
    """
    sentences: list[str] = []
    if profile.notice_period_months > 0:
        sentences.append(notice_period_sentence(profile.notice_period_months, language))
    availability = availability_sentence(profile.earliest_start, language)
    if availability:
        sentences.append(availability)
    if profile.confidential_application:
        sentences.append(confidentiality_sentence(language))
    return sentences


def _compile_pdf(tex_path: Path) -> tuple[Path | None, str | None]:
    tectonic = shutil.which("tectonic")
    if not tectonic:
        return None, "tectonic is not installed"
    try:
        process = subprocess.run(
            [tectonic, tex_path.name],
            cwd=str(tex_path.parent),
            capture_output=True,
            text=True,
            timeout=180,
        )
    except Exception as exc:
        return None, f"PDF compilation failed: {exc}"
    pdf_path = tex_path.with_suffix(".pdf")
    if process.returncode != 0 or not pdf_path.exists():
        detail = (process.stderr or process.stdout or "").strip()[-1200:]
        return None, f"PDF compilation failed: {detail or 'unknown tectonic error'}"
    return pdf_path, None


def generate_cover_letter(
    settings: Settings,
    job: Job,
    *,
    force: bool = False,
    description: str | None = None,
    profile: Profile | None = None,
    llm: CoverLetterLLM | None = None,
    compile_pdf: bool | None = None,
    today: date | None = None,
    on_progress: Progress | None = None,
) -> CoverLetterResult:
    progress = on_progress or (lambda _task, _value=None: None)
    paths = letter_paths(settings, job.source.value, job.job_id)

    if paths["tex"].exists() and not force:
        language = "de"
        matches: list[dict[str, Any]] = []
        if paths["json"].exists():
            try:
                meta = json.loads(paths["json"].read_text(encoding="utf-8"))
                language = meta.get("language", language)
                matches = meta.get("requirement_matches", matches)
            except Exception:
                pass
        return CoverLetterResult(
            source=job.source.value,
            job_id=job.job_id,
            company=job.company,
            title=job.title,
            language=language,
            tex_path=str(paths["tex"]),
            pdf_path=str(paths["pdf"]) if paths["pdf"].exists() else None,
            metadata_path=str(paths["json"]) if paths["json"].exists() else None,
            reused=True,
        )

    config = settings.cover_letter
    if llm is None and not config.api_key:
        raise CoverLetterError(
            "COVER_LETTER_API_KEY is not set. Add it to .env before generating a letter."
        )
    if profile is None:
        try:
            profile = load_profile(config.profile_path)
        except ProfileError as exc:
            raise CoverLetterError(str(exc)) from exc

    warning: str | None = None
    progress("Reading job description", 0.08)
    if description is None:
        cached = load_saved_description(settings, job.source.value, job.job_id)
        if cached:
            description = cached
            progress("Loaded saved job description", 0.12)
        elif job.description and len(job.description.strip()) >= 20:
            description = job.description.strip()
            try:
                save_job_description(settings, job, description)
            except Exception as exc:
                warning = f"Sheet description could not be cached: {exc}"
            progress("Loaded job description from sheet", 0.12)
        else:
            try:
                description = fetch_job_description(settings, job)
            except DescriptionError as exc:
                fallback = (job.posted_text or "").strip()
                if len(fallback) >= 80:
                    description = fallback
                    warning = f"{exc} Used the saved posting snippet instead."
                else:
                    description = (
                        f"Position: {job.title} at {job.company}\n"
                        f"Location: {job.location or 'Germany / Remote'}\n\n"
                        f"About the Role:\n"
                        f"We are seeking a talented and motivated {job.title} to join our team at {job.company}. "
                        f"In this role, you will work on core projects, build reliable systems, and collaborate "
                        f"with cross-functional engineering teams.\n\n"
                        f"Requirements and Qualifications:\n"
                        f"- Proven background and relevant experience as a {job.title} or related technical discipline\n"
                        f"- Solid technical expertise, software best practices, and modern tooling\n"
                        f"- Strong problem-solving ability, proactive communication, and eagerness to learn\n"
                        f"- Fluent communication skills"
                    )
                    warning = f"Live posting was unreachable ({exc}). Generated tailored draft using role and company profile."
            else:
                try:
                    path = save_job_description(settings, job, description)
                    progress("Saved job description", 0.14)
                except Exception as exc:
                    warning = f"Job description fetched but could not be saved: {exc}"

    progress("Matching qualifications with job requirements", 0.20)
    bullets = extract_bullets(profile.model_dump(mode="python"))
    requirements = extract_requirements(description)

    embedder: Embedder | None = None
    if getattr(config, "embed_base_url", None) and getattr(config, "embed_model", None):
        embedder = Embedder(
            base_url=config.embed_base_url,
            model=config.embed_model,
            cache_db=getattr(config, "embedding_db", None),
            timeout_seconds=config.timeout_seconds,
            api_key=config.api_key,
        )

    matcher = HybridMatcher(
        embedder=embedder,
        semantic_weight=getattr(config, "semantic_weight", 0.7),
        keyword_weight=getattr(config, "keyword_weight", 0.3),
        threshold=getattr(config, "match_threshold", 0.25),
        max_matches=getattr(config, "max_matches", 8),
    )
    matched_bullets = matcher.match(bullets, requirements)

    progress("Writing cover letter", 0.35)
    client = llm or CoverLetterLLM(
        base_url=config.base_url,
        api_key=config.api_key or "ollama",
        model=config.model,
        temperature=config.temperature,
        max_tokens=config.max_tokens,
        timeout_seconds=config.timeout_seconds,
        num_ctx=getattr(config, "num_ctx", 4096),
    )
    try:
        raw = client.generate(profile, job, description, matched_bullets=matched_bullets)
    except TypeError:
        raw = client.generate(profile, job, description)
    except LLMError as exc:
        raise CoverLetterError(str(exc)) from exc
    data = _normalise(raw, description)
    language = data["language"]
    german = language == "de"

    if not data["subject"]:
        data["subject"] = (
            f"Bewerbung als {job.title}" if german else f"Application for {job.title}"
        )
    if not data["salutation"]:
        data["salutation"] = (
            "Sehr geehrte Damen und Herren," if german else "Dear Hiring Manager,"
        )
    if not data["closing"]:
        data["closing"] = (
            "Mit freundlichen Grüßen" if german else "Sincerely"
        )

    date_text = format_date(language, today)
    additions = _conclusion_additions(profile, language)
    conclusion = " ".join([data["conclusion"], *additions]).strip()

    # Verification and grounding check
    full_text = " ".join([data["intro"], *data["main_paragraphs"], conclusion])
    verification = verify_letter(
        full_text,
        available_bullets=bullets,
        selected_bullets=[m.bullet for m in matched_bullets],
    )
    if verification.warnings and not warning:
        warning = f"Verification check: {len(verification.warnings)} potential grounding notice(s)."

    context = {
        "profile": profile,
        "babel_language": "ngerman" if german else "english",
        "date_line": date_text,
        "recipient_company": job.company or ("das Unternehmen" if german else "the company"),
        "recipient_department": data.get("recipient_department"),
        "recipient_person": data.get("recipient_person"),
        "recipient_extra": "Personalabteilung" if german else "Recruiting Team",
        "subject": data["subject"],
        "salutation": data["salutation"],
        "intro": data["intro"],
        "main_paragraphs": data["main_paragraphs"],
        "conclusion": conclusion,
        "closing": data["closing"],
    }
    progress("Rendering LaTeX", 0.7)
    try:
        tex = render_letter(config.template_dir, context)
    except Exception as exc:
        raise CoverLetterError(f"Could not render the LaTeX template: {exc}") from exc
    paths["tex"].write_text(tex, encoding="utf-8")

    metadata = {
        "source": job.source.value,
        "job_id": job.job_id,
        "company": job.company,
        "title": job.title,
        "link": job.link,
        "language": language,
        "model": config.model,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "requirement_matches": data["requirement_matches"],
        "matched_bullets": [
            {
                "target": m.requirement.text,
                "bullet": m.bullet.text,
                "source": m.bullet.source,
                "score": round(m.total_score, 3),
            }
            for m in matched_bullets
        ],
        "verification": {
            "passed": verification.passed,
            "warnings": verification.warnings,
            "grounded_bullets_count": verification.grounded_bullets_count,
            "unverified_metrics": verification.unverified_metrics,
        },
        "warning": warning,
    }
    paths["json"].write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    do_compile = config.compile_pdf if compile_pdf is None else compile_pdf
    pdf_path: Path | None = None
    pdf_error: str | None = None
    if do_compile:
        progress("Compiling PDF", 0.85)
        pdf_path, pdf_error = _compile_pdf(paths["tex"])

    progress("Cover letter ready", 1.0)
    return CoverLetterResult(
        source=job.source.value,
        job_id=job.job_id,
        company=job.company,
        title=job.title,
        language=language,
        tex_path=str(paths["tex"]),
        pdf_path=str(pdf_path) if pdf_path else None,
        metadata_path=str(paths["json"]),
        reused=False,
        warning=warning,
        pdf_error=pdf_error,
    )
