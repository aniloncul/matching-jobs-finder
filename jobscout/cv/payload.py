"""Strict render schema for a CV.

The template consumes this object and nothing else.  ``extra="forbid"`` is the
point: a model that returns ``{"score": 99, "hired": true}`` or tries to add an
invented section fails here instead of silently reaching the PDF.  Gaps are part
of the schema but are deliberately excluded from :func:`plain_text` and from the
template - a missing skill is a report, never a claim on the document.
"""

from __future__ import annotations

from typing import Any, Iterator, Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator

from jobscout.cv.master import CVFact, MasterCV, MasterRole
from jobscout.verification.text import normalize_text

MAX_SUMMARY_CHARS = 900
MAX_HEADLINE_CHARS = 120
MAX_COMPETENCIES = 12
MAX_BULLETS_PER_ROLE = 10
MAX_LABEL_CHARS = 48
MAX_GAPS = 20

GapPriority = Literal["required", "preferred", "normal"]


class CVSchemaError(Exception):
    """Raised when a payload does not match the render contract."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CVContact(_Strict):
    name: str = ""
    email: str = ""
    phone: str = ""
    address: str = ""
    postal_code: str = ""
    city: str = ""
    country: str = ""
    linkedin: str = ""
    github: str = ""
    website: str = ""

    def one_line(self) -> str:
        place = " ".join(
            part
            for part in (self.postal_code.strip(), self.city.strip())
            if part
        )
        return " | ".join(
            part
            for part in (
                self.email.strip(),
                self.phone.strip(),
                place.strip(),
                self.linkedin.strip(),
                self.github.strip(),
                self.website.strip(),
            )
            if part
        )


class CVBullet(_Strict):
    """One bullet of experience.

    ``fact_ids`` is what makes the CV auditable: every sentence must trace back
    to the master CV.  ``label`` is a bold prefix; it is only allowed when the
    exact term already appears in ``text`` (the tailor step enforces that).
    """

    text: str = ""
    label: str = ""
    fact_ids: list[str] = Field(default_factory=list)
    relevance: float = 0.0

    @field_validator("text", "label", mode="before")
    @classmethod
    def _clean_text(cls, value: Any) -> str:
        return normalize_text(str(value)) if value is not None else ""

    @field_validator("label")
    @classmethod
    def _label_is_short(cls, value: str) -> str:
        if len(value) > MAX_LABEL_CHARS:
            raise ValueError(f"bullet label longer than {MAX_LABEL_CHARS} characters")
        return value

    def display(self) -> str:
        if self.label and self.label.strip():
            return f"{self.label.strip()}: {self.text}"
        return self.text


class CVRole(_Strict):
    company: str = ""
    role: str = ""
    location: str = ""
    start: str = ""
    end: str = ""
    bullets: list[CVBullet] = Field(default_factory=list)

    @field_validator("bullets")
    @classmethod
    def _cap_bullets(cls, value: list[CVBullet]) -> list[CVBullet]:
        if len(value) > MAX_BULLETS_PER_ROLE:
            raise ValueError(f"more than {MAX_BULLETS_PER_ROLE} bullets in one role")
        return value

    def period(self) -> str:
        if self.start and self.end:
            return f"{self.start} - {self.end}"
        return self.start or self.end


class CVEducation(_Strict):
    institution: str = ""
    degree: str = ""
    location: str = ""
    start: str = ""
    end: str = ""
    fact_id: str = ""


class CVGap(_Strict):
    """A requirement the master CV cannot evidence.  Never rendered."""

    term: str
    priority: GapPriority = "normal"
    reason: str = "not_in_evidence"

    def to_dict(self) -> dict[str, Any]:
        return {"term": self.term, "priority": self.priority, "reason": self.reason}


class CVMeta(_Strict):
    """Provenance.  Shown in the report and the metadata JSON, not the PDF."""

    target_role: str = ""
    company: str = ""
    language: str = "de"
    keywords: list[str] = Field(default_factory=list)
    coverage_pct: float | None = None
    dropped_bullets: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    model: str = ""
    generated_at: str = ""


class CVRenderPayload(_Strict):
    contact: CVContact = Field(default_factory=CVContact)
    headline: str = ""
    summary: str = ""
    competencies: list[str] = Field(default_factory=list)
    experience: list[CVRole] = Field(default_factory=list)
    education: list[CVEducation] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    gaps: list[CVGap] = Field(default_factory=list)
    meta: CVMeta = Field(default_factory=CVMeta)

    @field_validator("summary")
    @classmethod
    def _summary_length(cls, value: str) -> str:
        text = normalize_text(value)
        if len(text) > MAX_SUMMARY_CHARS:
            raise ValueError(f"summary longer than {MAX_SUMMARY_CHARS} characters")
        return text

    @field_validator("headline")
    @classmethod
    def _headline_length(cls, value: str) -> str:
        text = normalize_text(value)
        if len(text) > MAX_HEADLINE_CHARS:
            raise ValueError(f"headline longer than {MAX_HEADLINE_CHARS} characters")
        return text

    @field_validator("competencies")
    @classmethod
    def _cap_competencies(cls, value: list[str]) -> list[str]:
        cleaned = [str(item).strip() for item in value if str(item).strip()]
        if len(cleaned) > MAX_COMPETENCIES:
            raise ValueError(f"more than {MAX_COMPETENCIES} competencies")
        return cleaned

    @field_validator("gaps")
    @classmethod
    def _cap_gaps(cls, value: list[CVGap]) -> list[CVGap]:
        return value[:MAX_GAPS]


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def bullet_from_fact(fact: CVFact) -> CVBullet:
    return CVBullet(text=fact.text, fact_ids=[fact.id])


def from_master(master: MasterCV, *, target_role: str = "", company: str = "") -> CVRenderPayload:
    """Untailored payload: the master CV as it stands, in CV order."""
    experience: list[CVRole] = []
    for block in master.roles:
        bullets: list[CVBullet] = []
        for fact_id in block.fact_ids:
            fact = master.fact(fact_id)
            if fact is None:
                continue
            bullets.append(bullet_from_fact(fact))
        experience.append(
            CVRole(
                company=block.company,
                role=block.role,
                location=block.location,
                start=block.start,
                end=block.end,
                bullets=bullets[:MAX_BULLETS_PER_ROLE],
            )
        )
    return CVRenderPayload(
        contact=CVContact(name=master.name, **master.contact),
        headline=master.headline,
        summary=master.summary[:MAX_SUMMARY_CHARS],
        competencies=master.supported_skills()[:MAX_COMPETENCIES],
        experience=experience,
        education=[
            CVEducation(
                institution=item.institution,
                degree=item.degree,
                location=item.location,
                start=item.start,
                end=item.end,
                fact_id=item.fact_id,
            )
            for item in master.education
        ],
        languages=list(master.languages),
        certifications=list(master.certifications),
        meta=CVMeta(
            target_role=target_role,
            company=company,
            notes=["Untailored master CV."],
        ),
    )


def parse_payload(data: dict[str, Any]) -> CVRenderPayload:
    """Validate a dict produced by a model or read back from disk."""
    if not isinstance(data, dict):
        raise CVSchemaError("The CV payload must be a JSON object")
    try:
        return CVRenderPayload.model_validate(data)
    except Exception as exc:  # pydantic ValidationError
        raise CVSchemaError(str(exc)) from exc


# ---------------------------------------------------------------------------
# Views used by verification and rendering
# ---------------------------------------------------------------------------


def iter_bullets(payload: CVRenderPayload) -> Iterator[tuple[int, int, CVRole, CVBullet]]:
    for role_index, role in enumerate(payload.experience):
        for bullet_index, bullet in enumerate(role.bullets):
            yield role_index, bullet_index, role, bullet


def bullet_count(payload: CVRenderPayload) -> int:
    return sum(len(role.bullets) for role in payload.experience)


def all_fact_ids(payload: CVRenderPayload) -> list[str]:
    ids: list[str] = []
    for _, _, _, bullet in iter_bullets(payload):
        for fact_id in bullet.fact_ids:
            if fact_id not in ids:
                ids.append(fact_id)
    for item in payload.education:
        if item.fact_id and item.fact_id not in ids:
            ids.append(item.fact_id)
    return ids


def plain_text(payload: CVRenderPayload) -> str:
    """The document as a recruiter would read it - gaps are not part of it."""
    lines: list[str] = [
        payload.contact.name,
        payload.headline,
        payload.contact.one_line(),
        payload.summary,
    ]
    if payload.competencies:
        lines.append(" | ".join(payload.competencies))
    for role in payload.experience:
        lines.append(f"{role.role} - {role.company} {role.period()} {role.location}".strip())
        lines.extend(bullet.display() for bullet in role.bullets)
    for item in payload.education:
        lines.append(f"{item.degree} - {item.institution} {item.start} {item.end}".strip())
    lines.extend(payload.certifications)
    lines.extend(payload.languages)
    return "\n".join(line for line in lines if line and line.strip())


def to_dict(payload: CVRenderPayload) -> dict[str, Any]:
    return payload.model_dump(mode="json")


def merge_notes(payload: CVRenderPayload, notes: Sequence[str]) -> CVRenderPayload:
    """Append report notes without touching the rendered content."""
    fresh = payload.model_copy(deep=True)
    for note in notes:
        text = str(note).strip()
        if text and text not in fresh.meta.notes:
            fresh.meta.notes.append(text)
    return fresh
