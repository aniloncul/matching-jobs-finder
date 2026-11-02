"""The master CV: one human-editable record of what is actually true.

``config/profile.yaml`` already holds the candidate's data, but it stores
achievements as plain strings.  A CV generator needs more than the sentence: it
needs to know *which employer* a bullet belongs to, *which tools* it evidences,
*which numbers* it may contain and *how strong* the candidate's role in it was.
This module turns the profile into addressable facts and lets an optional
``config/cv.master.yaml`` overlay add that metadata.

Two rules hold for everything else in :mod:`jobscout.cv`:

1. A fact is only ever *derived from* the master.  A rendered CV, a PDF or a
   generated payload is never used as evidence for the next document.
2. The overlay can only annotate or explicitly add facts.  An overlay entry
   that points at an unknown fact id is reported, never guessed at, and it can
   never overwrite the text of an existing fact.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal, Sequence

import yaml

from jobscout.cover_letter.profile import Profile, inspect_profile
from jobscout.verification.facts import extract_metrics
from jobscout.verification.text import contains_keyword, fold_for_match, normalize_text

FactKind = Literal[
    "achievement",
    "responsibility",
    "education",
    "certification",
    "language",
    "skill",
]
Scope = Literal["led", "contributed", "used", "unknown"]

FACT_KINDS: tuple[str, ...] = (
    "achievement",
    "responsibility",
    "education",
    "certification",
    "language",
    "skill",
)
SCOPES: tuple[str, ...] = ("led", "contributed", "used", "unknown")

_LED_MARKERS = (
    "leitete",
    "fuehrte",
    "verantwortete",
    "verantwortung fuer",
    "baute",
    "entwickelte",
    "entwarf",
    "konzipierte",
    "implementierte",
    "gruendete",
    "skalierte",
    "optimierte",
    "led ",
    "managed",
    "managed ",
    "built",
    "developed",
    "designed",
    "architected",
    "implemented",
    "owned",
    "scaled",
    "founded",
)
_CONTRIBUTED_MARKERS = (
    "unterstuetzte",
    "beteiligte",
    "wirkte",
    "arbeitete an",
    "beitragte",
    "assisted",
    "supported",
    "contributed",
    "participated",
    "helped",
    "collaborated",
)
_USED_MARKERS = (
    "nutzte",
    "verwendete",
    "setzte ein",
    "arbeitete mit",
    "used",
    "using",
    "worked with",
    "operated",
    "maintained",
)

_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")


class MasterCVError(Exception):
    """Raised when the master CV cannot be built at all."""


def fact_id(kind: str, *parts: str) -> str:
    """Stable id for a fact: the same text always yields the same id."""
    digest = hashlib.sha1("|".join(part.strip() for part in parts).encode("utf-8"))
    prefix = {"achievement": "ach", "responsibility": "resp"}.get(kind, kind[:4])
    return f"{prefix}-{digest.hexdigest()[:12]}"


def infer_scope(text: str) -> Scope:
    """Guess how strongly the candidate was involved from the verb alone."""
    folded = f" {fold_for_match(text)} "
    if any(marker in folded for marker in _LED_MARKERS):
        return "led"
    if any(marker in folded for marker in _CONTRIBUTED_MARKERS):
        return "contributed"
    if any(marker in folded for marker in _USED_MARKERS):
        return "used"
    return "unknown"


def infer_skills(text: str, known_skills: Sequence[str]) -> tuple[str, ...]:
    """Which declared skills does this bullet actually evidence?"""
    found: list[str] = []
    for skill in known_skills:
        name = str(skill).strip()
        if len(name) < 2 or not contains_keyword(text, name):
            continue
        if name not in found:
            found.append(name)
    return tuple(found)


def infer_metrics(text: str) -> tuple[str, ...]:
    """Numbers the bullet is allowed to contain, verbatim from the source."""
    seen: list[str] = []
    for claim in extract_metrics(normalize_text(text)):
        raw = claim.raw.strip()
        if raw and raw not in seen:
            seen.append(raw)
    return tuple(seen)


def _clean(value: Any) -> str:
    return normalize_text(str(value)) if value is not None else ""


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


@dataclass(frozen=True)
class CVFact:
    """One addressable piece of evidence about the candidate."""

    id: str
    kind: str
    text: str
    company: str = ""
    role: str = ""
    period: str = ""
    skills: tuple[str, ...] = ()
    metrics: tuple[str, ...] = ()
    scope: Scope = "unknown"
    sources: tuple[str, ...] = ()
    location: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "text": self.text,
            "company": self.company,
            "role": self.role,
            "period": self.period,
            "skills": list(self.skills),
            "metrics": list(self.metrics),
            "scope": self.scope,
            "sources": list(self.sources),
            "location": self.location,
        }


@dataclass
class MasterRole:
    """One employment block in CV order (CV order is part of the truth)."""

    company: str
    role: str
    location: str = ""
    start: str = ""
    end: str = ""
    fact_ids: list[str] = field(default_factory=list)

    @property
    def period(self) -> str:
        if self.start and self.end:
            return f"{self.start} - {self.end}"
        return self.start or self.end

    def to_dict(self) -> dict[str, Any]:
        return {
            "company": self.company,
            "role": self.role,
            "location": self.location,
            "start": self.start,
            "end": self.end,
            "period": self.period,
            "fact_ids": list(self.fact_ids),
        }


@dataclass
class MasterEducation:
    institution: str
    degree: str = ""
    location: str = ""
    start: str = ""
    end: str = ""
    fact_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "institution": self.institution,
            "degree": self.degree,
            "location": self.location,
            "start": self.start,
            "end": self.end,
            "fact_id": self.fact_id,
        }


@dataclass
class MasterCV:
    """Everything the generator is allowed to claim about the candidate."""

    name: str = ""
    headline: str = ""
    contact: dict[str, str] = field(default_factory=dict)
    summary: str = ""
    motivation: str = ""
    skills: list[str] = field(default_factory=list)
    soft_skills: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    certifications: list[str] = field(default_factory=list)
    roles: list[MasterRole] = field(default_factory=list)
    education: list[MasterEducation] = field(default_factory=list)
    facts: dict[str, CVFact] = field(default_factory=dict)
    years_experience: float | None = None
    warnings: list[str] = field(default_factory=list)

    # -- facts ------------------------------------------------------------
    def add_fact(self, fact: CVFact) -> CVFact:
        """Register a fact; an identical text re-uses the existing id."""
        for existing in self.facts.values():
            if existing.kind == fact.kind and fold_for_match(
                existing.text
            ) == fold_for_match(fact.text):
                return existing
        self.facts[fact.id] = fact
        return fact

    def fact(self, fact_id: str) -> CVFact | None:
        return self.facts.get(fact_id)

    def facts_for(self, company: str, role: str = "") -> list[CVFact]:
        for block in self.roles:
            if fold_for_match(block.company) != fold_for_match(company):
                continue
            if role and fold_for_match(block.role) != fold_for_match(role):
                continue
            return [self.facts[item] for item in block.fact_ids if item in self.facts]
        return []

    def find_facts(self, term: str, kinds: Sequence[str] | None = None) -> list[CVFact]:
        """Facts that actually mention ``term`` (grounded keyword lookup)."""
        result: list[CVFact] = []
        for fact in self.facts.values():
            if kinds and fact.kind not in kinds:
                continue
            if contains_keyword(fact.text, term) or contains_keyword(
                " ".join(fact.skills), term
            ):
                result.append(fact)
        return result

    def evidence_text(self) -> str:
        """The single evidence string every fact check compares against."""
        parts: list[str] = [self.summary, self.motivation, *self.skills]
        parts.extend(self.soft_skills)
        for block in self.roles:
            parts.append(f"{block.role} bei {block.company}" if block.company else block.role)
            for item in block.fact_ids:
                fact = self.facts.get(item)
                if fact:
                    parts.append(fact.text)
        for item in self.education:
            parts.append(f"{item.degree} {item.institution}")
        parts.extend(self.certifications)
        parts.extend(self.languages)
        return "\n".join(part for part in parts if part and part.strip())

    def allowed_metrics(self) -> list[str]:
        values: list[str] = []
        for fact in self.facts.values():
            for metric in fact.metrics:
                if metric not in values:
                    values.append(metric)
        return values

    def known_years(self) -> set[int]:
        years: set[int] = set()
        for block in self.roles:
            years.update(int(match) for match in _YEAR_RE.findall(block.period))
        for item in self.education:
            years.update(
                int(match) for match in _YEAR_RE.findall(f"{item.start} {item.end}")
            )
        return years

    def supported_skills(self) -> list[str]:
        """Skills the master actually lists; nothing else may be claimed."""
        merged: list[str] = []
        for skill in [*self.skills, *self.soft_skills]:
            if skill and skill not in merged:
                merged.append(skill)
        return merged

    # -- derived views ----------------------------------------------------
    @classmethod
    def from_profile(cls, profile: Profile) -> "MasterCV":
        master = cls(
            name=profile.name.strip(),
            headline=profile.headline.strip(),
            contact={
                key: value.strip()
                for key, value in {
                    "email": profile.email,
                    "phone": profile.phone,
                    "address": profile.address,
                    "postal_code": profile.postal_code,
                    "city": profile.city,
                    "country": profile.country,
                    "linkedin": profile.linkedin,
                    "github": profile.github,
                    "website": profile.website,
                }.items()
                if value and value.strip()
            },
            summary=normalize_text(profile.summary),
            motivation=normalize_text(profile.motivation),
            skills=[item.strip() for item in profile.skills if item.strip()],
            soft_skills=[item.strip() for item in profile.soft_skills if item.strip()],
            languages=[item.strip() for item in profile.languages if item.strip()],
            certifications=[
                item.strip() for item in profile.certifications if item.strip()
            ],
        )
        known_skills = [*master.skills, *master.soft_skills]

        for index, experience in enumerate(profile.experience):
            company = experience.company.strip()
            role = experience.role.strip()
            block = MasterRole(
                company=company,
                role=role,
                location=experience.location.strip(),
                start=experience.start.strip(),
                end=experience.end.strip(),
            )
            for achievement_index, achievement in enumerate(experience.achievements):
                text = normalize_text(achievement)
                if len(text) < 8:
                    continue
                fact = master.add_fact(
                    CVFact(
                        id=fact_id("achievement", company, role, text),
                        kind="achievement",
                        text=text,
                        company=company,
                        role=role,
                        period=block.period,
                        skills=infer_skills(text, known_skills),
                        metrics=infer_metrics(text),
                        scope=infer_scope(text),
                        sources=(f"profile:experience[{index}].achievements[{achievement_index}]",),
                    )
                )
                block.fact_ids.append(fact.id)
            if company or role:
                master.roles.append(block)

        for index, item in enumerate(profile.education):
            institution = item.institution.strip()
            degree = item.degree.strip()
            if not institution and not degree:
                continue
            text = " - ".join(part for part in (degree, institution) if part)
            fact = master.add_fact(
                CVFact(
                    id=fact_id("education", institution, degree),
                    kind="education",
                    text=text,
                    period=f"{item.start} - {item.end}".strip(" -"),
                    location=item.location.strip(),
                    sources=(f"profile:education[{index}]",),
                )
            )
            master.education.append(
                MasterEducation(
                    institution=institution,
                    degree=degree,
                    location=item.location.strip(),
                    start=item.start.strip(),
                    end=item.end.strip(),
                    fact_id=fact.id,
                )
            )

        for index, certification in enumerate(master.certifications):
            master.add_fact(
                CVFact(
                    id=fact_id("certification", certification),
                    kind="certification",
                    text=certification,
                    skills=infer_skills(certification, known_skills),
                    sources=(f"profile:certifications[{index}]",),
                )
            )
        for index, language in enumerate(master.languages):
            master.add_fact(
                CVFact(
                    id=fact_id("language", language),
                    kind="language",
                    text=language,
                    sources=(f"profile:languages[{index}]",),
                )
            )

        master.years_experience = _years_experience(master)
        return master

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "headline": self.headline,
            "contact": dict(self.contact),
            "summary": self.summary,
            "skills": list(self.skills),
            "soft_skills": list(self.soft_skills),
            "languages": list(self.languages),
            "certifications": list(self.certifications),
            "roles": [item.to_dict() for item in self.roles],
            "education": [item.to_dict() for item in self.education],
            "facts": [fact.to_dict() for fact in self.facts.values()],
            "years_experience": self.years_experience,
            "warnings": list(self.warnings),
        }


def _years_experience(master: MasterCV) -> float | None:
    """Whole years between the earliest role and the latest one (or today)."""
    from datetime import date

    starts: list[int] = []
    ends: list[int] = []
    for block in master.roles:
        start_years = [int(item) for item in _YEAR_RE.findall(block.start)]
        if start_years:
            starts.append(min(start_years))
        end_text = block.end.lower()
        end_years = [int(item) for item in _YEAR_RE.findall(block.end)]
        if end_years:
            ends.append(max(end_years))
        elif any(word in end_text for word in ("present", "heute", "aktuell", "now")):
            ends.append(date.today().year)
    if not starts:
        return None
    latest = max(ends) if ends else date.today().year
    return float(max(0, latest - min(starts)))


# ---------------------------------------------------------------------------
# Optional overlay: config/cv.master.yaml
# ---------------------------------------------------------------------------


def _apply_overlay(master: MasterCV, overlay: dict[str, Any]) -> list[str]:
    """Annotate or add facts.  Returns warnings; never mutates fact text."""
    warnings: list[str] = []
    annotations = overlay.get("facts")
    if isinstance(annotations, dict):
        items = [{"id": key, **value} for key, value in annotations.items() if isinstance(value, dict)]
    elif isinstance(annotations, list):
        items = [item for item in annotations if isinstance(item, dict)]
    else:
        items = []

    for item in items:
        target_id = str(item.get("id") or "").strip()
        match_text = str(item.get("match") or "").strip()
        target = master.facts.get(target_id) if target_id else None
        if target is None and match_text:
            folded = fold_for_match(match_text)
            candidates = [
                fact for fact in master.facts.values() if fold_for_match(fact.text) == folded
            ]
            if len(candidates) == 1:
                target = candidates[0]
        if target is None:
            warnings.append(
                f"cv.master.yaml: no fact matches id '{target_id or match_text}'; "
                "the annotation was ignored."
            )
            continue
        updated = CVFact(
            id=target.id,
            kind=target.kind,
            text=target.text,
            company=target.company,
            role=target.role,
            period=target.period,
            skills=tuple(_as_list(item.get("skills"))) or target.skills,
            metrics=tuple(_as_list(item.get("metrics"))) or target.metrics,
            scope=(
                item["scope"]
                if str(item.get("scope", "")) in SCOPES
                else target.scope
            ),
            sources=tuple([*target.sources, *_as_list(item.get("sources"))]),
            location=target.location,
        )
        master.facts[target.id] = updated
        for block in master.roles:
            if target.id in block.fact_ids:
                for index, fact_id_value in enumerate(block.fact_ids):
                    if fact_id_value == target.id:
                        block.fact_ids[index] = updated.id

    additions = overlay.get("extra_facts")
    if isinstance(additions, list):
        for item in additions:
            if not isinstance(item, dict):
                continue
            text = normalize_text(str(item.get("text") or ""))
            if len(text) < 8:
                warnings.append("cv.master.yaml: an extra fact had no usable text.")
                continue
            kind = str(item.get("kind") or "achievement")
            if kind not in FACT_KINDS:
                warnings.append(f"cv.master.yaml: unknown fact kind '{kind}'.")
                kind = "achievement"
            company = str(item.get("company") or "").strip()
            role = str(item.get("role") or "").strip()
            source = str(item.get("source") or "cv.master.yaml").strip()
            fact = master.add_fact(
                CVFact(
                    id=fact_id(kind, company, role, text),
                    kind=kind,
                    text=text,
                    company=company,
                    role=role,
                    skills=tuple(_as_list(item.get("skills"))),
                    metrics=tuple(_as_list(item.get("metrics"))),
                    scope=(
                        item["scope"]
                        if str(item.get("scope", "")) in SCOPES
                        else "unknown"
                    ),
                    sources=(source,),
                )
            )
            if company or role:
                for block in master.roles:
                    if fold_for_match(block.company) == fold_for_match(company) and (
                        not role or fold_for_match(block.role) == fold_for_match(role)
                    ):
                        if fact.id not in block.fact_ids:
                            block.fact_ids.append(fact.id)
                        break
    return warnings


def _load_overlay(path: Path | None) -> dict[str, Any]:
    if path is None or not Path(path).exists():
        return {}
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001 - reported as a warning downstream
        raise MasterCVError(f"Cannot read {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise MasterCVError(f"{path} must contain a YAML mapping")
    return raw


def inspect_master(
    profile_path: str | Path,
    overlay_path: str | Path | None = None,
) -> tuple[MasterCV | None, list[str]]:
    """Build the master CV and return it with the issues worth showing."""
    profile, issues = inspect_profile(profile_path)
    if profile is None:
        return None, issues
    master = MasterCV.from_profile(profile)
    overlay_file = Path(overlay_path) if overlay_path else None
    try:
        overlay = _load_overlay(overlay_file)
    except MasterCVError as exc:
        return master, [*issues, str(exc)]
    if overlay:
        master.warnings.extend(_apply_overlay(master, overlay))
    return master, [*issues, *master.warnings]


def load_master(
    profile_path: str | Path,
    overlay_path: str | Path | None = None,
) -> MasterCV:
    """Strict loader: a profile the cover letter would refuse is refused here too.

    Overlay *warnings* do not block: an annotation that points at a stale fact id
    is reported by :func:`inspect_master` and simply ignored.
    """
    profile, readiness = inspect_profile(profile_path)
    if profile is None:
        raise MasterCVError(" ".join(readiness) or "Profile could not be read.")
    if readiness:
        raise MasterCVError(" ".join(readiness))
    master = MasterCV.from_profile(profile)
    overlay_file = Path(overlay_path) if overlay_path else None
    try:
        overlay = _load_overlay(overlay_file)
    except MasterCVError as exc:
        master.warnings.append(str(exc))
        overlay = {}
    if overlay:
        master.warnings.extend(_apply_overlay(master, overlay))
    if not master.facts:
        raise MasterCVError(
            "The master CV has no facts. Add at least one achievement under "
            "experience in config/profile.yaml, or an entry under extra_facts in "
            "config/cv.master.yaml."
        )
    return master


def iterable_terms(values: Iterable[str]) -> list[str]:
    """Small helper used by the tailor step to de-duplicate term lists."""
    result: list[str] = []
    for value in values:
        text = str(value).strip()
        if text and text not in result:
            result.append(text)
    return result
