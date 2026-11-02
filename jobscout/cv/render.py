"""Deterministic LaTeX renderer.

The model never produces LaTeX or HTML in this project.  It may only change the
text inside a :class:`~jobscout.cv.payload.CVRenderPayload`; this module is the
only place that knows what the document looks like.  Escaping is done here, so a
bullet containing a percent sign, an ampersand or a dollar sign cannot break the
build or inject markup.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from jobscout.cover_letter.latex import compile_pdf, detect_language, tex_escape
from jobscout.cv.payload import CVRenderPayload
from jobscout.verification.text import normalize_text

CV_TEMPLATE = "cv_modern.tex.j2"


def tex_text(value: object) -> str:
    """Escape for LaTeX *after* normalising typography.

    The ATS text-layer check wants ASCII, and pdflatex has no business deciding
    what an em dash means, so both are handled before escaping.
    """
    return tex_escape(normalize_text(value))

# Rough geometry of the rendered page, used only to decide whether the cutter
# has to trim before we even call LaTeX.
CHARS_PER_LINE = 92
LINES_PER_PAGE = 52

HEADINGS: dict[str, dict[str, str]] = {
    "de": {
        "profile": "Profil",
        "competency": "Kernkompetenzen",
        "experience": "Berufserfahrung",
        "education": "Ausbildung",
        "certifications": "Zertifikate",
        "languages": "Sprachkenntnisse",
    },
    "en": {
        "profile": "Profile",
        "competency": "Core skills",
        "experience": "Experience",
        "education": "Education",
        "certifications": "Certifications",
        "languages": "Languages",
    },
}


def template_context(payload: CVRenderPayload, language: str | None = None) -> dict[str, Any]:
    headings = HEADINGS[language if language in HEADINGS else "de"]
    return {
        "babel_language": "ngerman" if language != "en" else "english",
        "name": payload.contact.name,
        "headline": payload.headline,
        "contact_line": payload.contact.one_line(),
        "summary": payload.summary,
        "competencies": payload.competencies,
        "experience": [
            {
                "role": role.role,
                "company": role.company,
                "location": role.location,
                "period": role.period(),
                "bullets": [
                    {"label": bullet.label, "text": bullet.text}
                    for bullet in role.bullets
                ],
            }
            for role in payload.experience
        ],
        "education": [
            {
                "degree": item.degree,
                "institution": item.institution,
                "location": item.location,
                "period": " - ".join(part for part in (item.start, item.end) if part),
            }
            for item in payload.education
        ],
        "certifications": payload.certifications,
        "languages": payload.languages,
        "profile_heading": headings["profile"],
        "competency_heading": headings["competency"],
        "experience_heading": headings["experience"],
        "education_heading": headings["education"],
        "certifications_heading": headings["certifications"],
        "languages_heading": headings["languages"],
    }


def detect_payload_language(payload: CVRenderPayload) -> str:
    """The language of the CV: the recorded one, or the text itself."""
    if payload.meta.language in {"de", "en"}:
        return payload.meta.language
    return detect_language(payload.summary or " ".join(payload.competencies))


def render_cv(
    template_dir: str | Path,
    payload: CVRenderPayload,
    *,
    language: str | None = None,
    template_name: str = CV_TEMPLATE,
) -> str:
    """Render the payload to a LaTeX source string."""
    resolved = language or payload.meta.language or detect_payload_language(payload)
    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=select_autoescape(enabled_extensions=()),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["tex"] = tex_text
    template = env.get_template(template_name)
    return template.render(**template_context(payload, resolved))


def estimate_lines(payload: CVRenderPayload) -> int:
    """Line estimate used to decide whether the cutter has to run."""
    lines = 3  # name, headline, contact
    lines += math.ceil(len(payload.summary) / CHARS_PER_LINE) if payload.summary else 0
    if payload.competencies:
        joined = " ".join(payload.competencies)
        lines += max(1, math.ceil(len(joined) / CHARS_PER_LINE))
    for role in payload.experience:
        lines += 1
        for bullet in role.bullets:
            lines += max(1, math.ceil(len(bullet.display()) / (CHARS_PER_LINE - 4)))
    lines += len(payload.education)
    lines += len(payload.certifications)
    lines += len(payload.languages)
    lines += 5  # section headings and vertical space
    return lines


def estimate_pages(payload: CVRenderPayload) -> int:
    return max(1, math.ceil(estimate_lines(payload) / LINES_PER_PAGE))


def compile_cv_pdf(tex_path: Path, timeout: int = 180) -> tuple[Path | None, str | None]:
    """Same tectonic path as the cover letter."""
    return compile_pdf(tex_path, timeout=timeout)
