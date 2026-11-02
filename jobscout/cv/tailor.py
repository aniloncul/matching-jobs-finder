"""Deterministic CV tailoring: order, label, cut - and never invent.

Everything here is pure code so the result is reproducible and testable:

* keywords come from :mod:`jobscout.verification.keywords` (no LLM),
* a *gap* is a job requirement the master CV cannot evidence; it is reported and
  never written into the document,
* bullets are only reordered, trimmed or given a bold label, never rewritten,
* a bold label is allowed only when that exact term already appears in the
  bullet, which is what keeps "keyword optimisation" from becoming fabrication.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Sequence

from jobscout.cv.master import MasterCV
from jobscout.cv.payload import (
    CVGap,
    CVRenderPayload,
    MAX_GAPS,
    MAX_SUMMARY_CHARS,
    MAX_COMPETENCIES,
    bullet_count,
    plain_text,
)
from jobscout.verification.keywords import (
    DEFAULT_SYNONYMS,
    Keyword,
    KeywordCoverage,
    coverage,
    extract_keywords,
)
from jobscout.verification.text import contains_keyword, fold_for_match, normalize_text

DEFAULT_TARGET_KEYWORDS = 18
DEFAULT_MAX_BULLETS_PER_ROLE = 6
GRID_MIN = 6
GRID_MAX = 8

_PRIORITY_WEIGHT = {"required": 3.0, "preferred": 2.0, "normal": 1.2}
_METRIC_HINT = re.compile(r"\d")


@dataclass
class TailorPlan:
    """What the tailor did and what the candidate still lacks."""

    keywords: list[Keyword] = field(default_factory=list)
    gaps: list[CVGap] = field(default_factory=list)
    coverage: KeywordCoverage | None = None
    reordered: int = 0
    relabeled: int = 0
    dropped: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def coverage_pct(self) -> float:
        return self.coverage.coverage_pct if self.coverage else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "keywords": [item.to_dict() for item in self.keywords],
            "gaps": [item.to_dict() for item in self.gaps],
            "coverage": self.coverage.to_dict() if self.coverage else None,
            "reordered": self.reordered,
            "relabeled": self.relabeled,
            "dropped": list(self.dropped),
            "notes": list(self.notes),
        }


_TAILOR_KEYS = {"summary", "competencies", "labels", "bullet_order", "notes"}


def unsupported_terms(text: str, evidence: str) -> list[str]:
    """Tool names or numbers in ``text`` that the evidence cannot support.

    Used by the LLM tailoring step and the reviewer: both may re-phrase, neither
    may introduce a fact.
    """
    from jobscout.verification.facts import TECH_FACT_TERMS, extract_metrics

    found: list[str] = []
    for term in sorted(TECH_FACT_TERMS):
        if contains_keyword(text, term) and not contains_keyword(evidence, term):
            found.append(term)
    evidence_metrics = {claim.key for claim in extract_metrics(evidence)}
    for claim in extract_metrics(text):
        if claim.key not in evidence_metrics and claim.raw not in found:
            found.append(claim.raw)
    return found


def apply_llm_tailoring(
    payload: CVRenderPayload,
    master: MasterCV,
    data: dict[str, Any],
    *,
    max_summary_chars: int = MAX_SUMMARY_CHARS,
) -> tuple[CVRenderPayload, list[str]]:
    """Apply the safe subset of a model's tailoring JSON.

    Accepted keys are exactly ``summary``, ``competencies``, ``labels``,
    ``bullet_order`` and ``notes``.  The model may re-phrase the summary within
    the evidence, pick and order what is already in the CV, and bold terms a
    bullet already proves.  It cannot add a bullet, a tool or a number; anything
    like that is dropped and reported.
    """
    from jobscout.cv.payload import MAX_SUMMARY_CHARS, MAX_COMPETENCIES

    if not isinstance(data, dict):
        raise ValueError("The tailoring response must be a JSON object")
    unknown = sorted(set(data) - _TAILOR_KEYS)
    notes: list[str] = []
    if unknown:
        notes.append(
            "Ignored unsupported tailoring keys: " + ", ".join(unknown) + "."
        )

    fresh = payload.model_copy(deep=True)
    evidence = master.evidence_text()
    limit = min(max_summary_chars, MAX_SUMMARY_CHARS)

    summary = data.get("summary")
    if isinstance(summary, str) and summary.strip():
        candidate = normalize_text(summary)[:limit]
        blockers = unsupported_terms(candidate, evidence)
        if blockers:
            notes.append(
                "Rejected the model summary (unsupported: "
                + ", ".join(blockers[:5])
                + ")."
            )
        else:
            fresh.summary = candidate
            notes.append("Summary rewritten by the model.")

    competencies = data.get("competencies")
    if isinstance(competencies, list):
        supported = {fold_for_match(item) for item in master.supported_skills()}
        items: list[str] = []
        for item in competencies:
            text = normalize_text(str(item))
            if not text or fold_for_match(text) not in supported:
                continue
            if text not in items:
                items.append(text)
        if items:
            fresh.competencies = items[:MAX_COMPETENCIES]
            notes.append(f"Competency grid taken from the model ({len(fresh.competencies)} items).")
        else:
            notes.append("Rejected the model competency grid: nothing was in the master CV.")

    labels = data.get("labels")
    if isinstance(labels, list):
        applied = 0
        for item in labels:
            if not isinstance(item, dict):
                continue
            role_index = item.get("role")
            bullet_index = item.get("index")
            label = normalize_text(str(item.get("label") or ""))
            if not isinstance(role_index, int) or not isinstance(bullet_index, int):
                continue
            if not (0 <= role_index < len(fresh.experience)):
                continue
            role = fresh.experience[role_index]
            if not (0 <= bullet_index < len(role.bullets)):
                continue
            bullet = role.bullets[bullet_index]
            if not label or len(label) > 48:
                continue
            if not contains_keyword(bullet.text, label):
                notes.append(
                    f"Dropped label '{label}': the bullet does not contain that term."
                )
                continue
            bullet.label = label
            applied += 1
        if applied:
            notes.append(f"{applied} bold label(s) set from the model.")

    order = data.get("bullet_order")
    if isinstance(order, list):
        moved = 0
        for item in order:
            if not isinstance(item, dict):
                continue
            role_index = item.get("role")
            sequence = item.get("order")
            if not isinstance(role_index, int) or not (0 <= role_index < len(fresh.experience)):
                continue
            role = fresh.experience[role_index]
            if not isinstance(sequence, list):
                continue
            indices = [value for value in sequence if isinstance(value, int)]
            if sorted(indices) != list(range(len(role.bullets))):
                notes.append(
                    f"Ignored bullet_order for {role.company}: not a permutation of its bullets."
                )
                continue
            role.bullets = [role.bullets[value] for value in indices]
            moved += 1
        if moved:
            notes.append(f"{moved} role block(s) reordered by the model.")

    model_notes = data.get("notes")
    if isinstance(model_notes, list):
        for note in model_notes:
            text = normalize_text(str(note))
            if text and text not in notes:
                notes.append(text)

    fresh.meta.model = fresh.meta.model or "llm-tailored"
    return fresh, notes


def target_keywords(description: str, limit: int = DEFAULT_TARGET_KEYWORDS) -> list[Keyword]:
    """15-20 terms that decide whether this CV passes a keyword screen."""
    return extract_keywords(description or "", limit=limit)


def _count(text: str, term: str, synonyms: dict[str, tuple[str, ...]]) -> int:
    folded = fold_for_match(term)
    variants = [term, *synonyms.get(folded, ())]
    return sum(_plain_count(text, variant) for variant in variants)


def _plain_count(text: str, term: str) -> int:
    haystack = f" {fold_for_match(text)} "
    needle = fold_for_match(term)
    if not needle:
        return 0
    return haystack.count(f" {needle} ")


def relevance_score(text: str, keywords: Sequence[Keyword]) -> float:
    """Weighted keyword hits; metrics get a small, honest bonus."""
    synonyms = DEFAULT_SYNONYMS
    score = 0.0
    for item in keywords:
        hits = _count(text, item.term, synonyms)
        if not hits:
            continue
        score += _PRIORITY_WEIGHT.get(item.priority, 1.0) * min(hits, 2)
    if _METRIC_HINT.search(text or ""):
        score += 0.5
    return round(score, 3)


# Boilerplate from the structure of a job ad, never a real requirement.
_GAP_NOISE = {
    "erforderlich", "vorausgesetzt", "zwingend", "muss", "sollten", "required", "must",
    "essential", "minimum", "wuenschenswert", "wünschenswert", "idealerweise", "vor",
    "vorteil", "preferred", "nice", "have", "plus", "bonus",
    "aufgaben", "aufgabenbereich", "verantwortlichkeiten", "anforderungen",
    "qualifikationen", "kenntnisse", "erfahrung", "jahre", "jahren", "team",
    "teams", "unternehmen", "position", "rolle", "stelle", "skills", "experience",
    "role", "candidate", "applicant", "working", "work", "good", "strong", "proven",
    "solid", "ability", "capacity", "understanding", "knowledge", "bereitschaft",
    "faehigkeit", "fähigkeit", "sowie", "und", "oder", "mit", "fuer", "für", "sehr",
    "gute", "guter", "gutes", "mehrere", "jahrelange", "idealerweise", "ihr", "ihre",
    "deine", "unser", "unsere", "job", "stellenangebot", "deutschland", "remote",
}


def _gap_core_words(term: str) -> list[str]:
    """Words of a keyword that actually name a requirement."""
    words = [word for word in fold_for_match(term).split() if len(word) >= 3]
    return [word for word in words if word not in _GAP_NOISE]


def find_gaps(
    keywords: Sequence[Keyword],
    master: MasterCV,
    limit: int = MAX_GAPS,
) -> list[CVGap]:
    """Requirements the master CV cannot evidence.  Never added to the CV.

    Ad boilerplate ("Erforderlich: ...", "3+ years of experience") and phrases
    whose individual parts are all present in the CV are not gaps; reporting
    them would train the candidate to ignore the list.
    """
    evidence = master.evidence_text()
    evidence_folded = f" {fold_for_match(evidence)} "
    gaps: list[CVGap] = []
    core_sets: list[set[str]] = []
    seen: set[str] = set()
    for item in keywords:
        folded = fold_for_match(item.term)
        if not folded or folded in seen:
            continue
        if _count(evidence, item.term, DEFAULT_SYNONYMS):
            seen.add(folded)
            continue
        core = _gap_core_words(item.term)
        if not core:
            continue
        if all(f" {word} " in evidence_folded for word in core):
            seen.add(folded)
            continue
        core_set = set(core)
        if any(known <= core_set for known in core_sets):
            # A shorter gap already names the missing tool or skill.
            seen.add(folded)
            continue
        seen.add(folded)
        gaps.append(CVGap(term=item.term, priority=item.priority))
        core_sets.append(set(core))
        if len(gaps) >= limit:
            break
    return gaps


def bold_label(text: str, keywords: Sequence[Keyword]) -> str:
    """Return the strongest JD term the bullet already proves, else ``""``."""
    best: tuple[float, str] | None = None
    for item in keywords:
        if not _count(text, item.term, DEFAULT_SYNONYMS):
            continue
        weight = _PRIORITY_WEIGHT.get(item.priority, 1.0) * min(
            _count(text, item.term, DEFAULT_SYNONYMS), 2
        )
        term = normalize_text(item.term)
        if len(term) > 48:
            continue
        if best is None or weight > best[0]:
            best = (weight, term)
    return best[1] if best else ""


def pick_competencies(
    master: MasterCV,
    keywords: Sequence[Keyword],
    *,
    minimum: int = GRID_MIN,
    maximum: int = GRID_MAX,
) -> list[str]:
    """6-8 competency grid entries, JD-relevant first, master order as tie-break."""
    candidates = master.supported_skills()
    if not candidates:
        return []
    ranked = sorted(
        enumerate(candidates),
        key=lambda pair: (-relevance_score(pair[1], keywords), pair[0]),
    )
    relevant = [skill for _, skill in ranked if relevance_score(skill, keywords) > 0.5]
    rest = [skill for _, skill in ranked if skill not in relevant]
    count = max(minimum, min(maximum, len(candidates)))
    picked = (relevant + rest)[:count]
    return picked


def _carrier_map(
    payload: CVRenderPayload,
    keywords: Sequence[Keyword],
) -> dict[str, int]:
    """How many bullets in the payload carry each ``required`` keyword."""
    carriers: dict[str, int] = {}
    for _, _, _, bullet in _iter(payload):
        for item in keywords:
            if item.priority != "required":
                continue
            folded = fold_for_match(item.term)
            if _count(bullet.display(), item.term, DEFAULT_SYNONYMS):
                carriers[folded] = carriers.get(folded, 0) + 1
    return carriers


def _is_sole_carrier(
    bullet: CVBullet,
    carriers: dict[str, int],
    keywords: Sequence[Keyword],
) -> bool:
    for item in keywords:
        if item.priority != "required":
            continue
        folded = fold_for_match(item.term)
        if carriers.get(folded, 0) == 1 and _count(
            bullet.display(), item.term, DEFAULT_SYNONYMS
        ):
            return True
    return False


def _iter(payload: CVRenderPayload):
    for role_index, role in enumerate(payload.experience):
        for bullet_index, bullet in enumerate(role.bullets):
            yield role_index, bullet_index, role, bullet


def order_bullets(
    payload: CVRenderPayload,
    keywords: Sequence[Keyword],
) -> tuple[CVRenderPayload, int]:
    """Most relevant bullet first, stable for equal scores."""
    fresh = payload.model_copy(deep=True)
    moved = 0
    for role in fresh.experience:
        before = [bullet.text for bullet in role.bullets]
        ranked = sorted(
            enumerate(role.bullets),
            key=lambda pair: (-relevance_score(pair[1].display(), keywords), pair[0]),
        )
        role.bullets = [bullet for _, bullet in ranked]
        if [bullet.text for bullet in role.bullets] != before:
            moved += 1
    return fresh, moved


def cut_bullets(
    payload: CVRenderPayload,
    keywords: Sequence[Keyword],
    *,
    max_bullets_per_role: int | None = None,
    max_total_bullets: int | None = None,
) -> tuple[CVRenderPayload, list[str]]:
    """Relevance-weighted trimming to fit the page.

    A bullet is protected while it is the only one that carries a *required*
    keyword; the last bullets to go are low-relevance ones from the oldest
    roles (the payload keeps CV order, so a higher role index is older).
    """
    if not max_bullets_per_role and not max_total_bullets:
        return payload, []

    fresh = payload.model_copy(deep=True)
    dropped: list[str] = []
    carriers = _carrier_map(fresh, keywords)

    def trim_role(role_index: int, limit: int) -> None:
        role = fresh.experience[role_index]
        while len(role.bullets) > limit:
            ordered = sorted(
                enumerate(role.bullets),
                key=lambda pair: (
                    _is_sole_carrier(pair[1], carriers, keywords),
                    relevance_score(pair[1].display(), keywords),
                    -pair[0],
                ),
            )
            victim_index, victim = ordered[0]
            if _is_sole_carrier(victim, carriers, keywords):
                # Every remaining bullet carries a required keyword on its own.
                break
            role.bullets.pop(victim_index)
            dropped.append(f"{role.company}: {victim.text}")
            for item in keywords:
                if item.priority == "required" and _count(
                    victim.display(), item.term, DEFAULT_SYNONYMS
                ):
                    folded = fold_for_match(item.term)
                    carriers[folded] = max(0, carriers.get(folded, 1) - 1)

    if max_bullets_per_role:
        for role_index, role in enumerate(fresh.experience):
            trim_role(role_index, max_bullets_per_role)

    if max_total_bullets:
        while bullet_count(fresh) > max_total_bullets:
            candidates = [
                (role_index, bullet_index, bullet)
                for role_index, bullet_index, role, bullet in _iter(fresh)
                if not _is_sole_carrier(bullet, carriers, keywords)
            ]
            if not candidates:
                break
            worst = min(
                candidates,
                key=lambda item: (
                    relevance_score(item[2].display(), keywords),
                    -item[0],
                    -item[1],
                ),
            )
            role = fresh.experience[worst[0]]
            victim = role.bullets.pop(worst[1])
            dropped.append(f"{role.company}: {victim.text}")
            for item in keywords:
                if item.priority == "required" and _count(
                    victim.display(), item.term, DEFAULT_SYNONYMS
                ):
                    folded = fold_for_match(item.term)
                    carriers[folded] = max(0, carriers.get(folded, 1) - 1)

    return fresh, dropped


def build_summary(
    master: MasterCV,
    keywords: Sequence[Keyword] = (),
    *,
    language: str = "de",
    include_years: bool = False,
    skill_count: int = 4,
) -> str:
    """Grounded summary for profiles that have none.

    Every token comes from the master CV.  A number is only used when
    ``include_years`` is set *and* the master can derive it from real dates;
    :func:`jobscout.cv.verify.verify_cv` puts exactly that phrase on the fact
    gate's allow-list, so nothing else can slip through as a metric.
    """
    role = master.headline or master.name
    if not role:
        return ""
    skills: list[str] = []
    ranked = sorted(
        master.skills,
        key=lambda skill: (-relevance_score(skill, keywords), master.skills.index(skill)),
    )
    for skill in ranked:
        if skill not in skills:
            skills.append(skill)
        if len(skills) >= skill_count:
            break

    german = language != "en"
    parts: list[str] = []
    if include_years and master.years_experience:
        years = int(master.years_experience)
        if german:
            parts.append(f"{role} mit {years} Jahren Berufserfahrung")
        else:
            unit = "year" if years == 1 else "years"
            parts.append(f"{role} with {years} {unit} of experience")
    elif role:
        parts.append(role)
    if skills:
        label = "Schwerpunkte" if german else "Focus"
        parts.append(f"{label}: {', '.join(skills)}")
    return normalize_text(". ".join(parts))


def derived_metric_claims(master: MasterCV) -> list[str]:
    """The only derived numbers the fact gate may accept for this CV."""
    if not master.years_experience:
        return []
    years = int(master.years_experience)
    return [f"{years} Jahren", f"{years} Jahre", f"{years} years", f"{years} year"]


def tailor_payload(
    payload: CVRenderPayload,
    master: MasterCV,
    keywords: Sequence[Keyword],
    *,
    language: str = "de",
    max_bullets_per_role: int | None = DEFAULT_MAX_BULLETS_PER_ROLE,
    max_total_bullets: int | None = None,
    reorder: bool = True,
    label_bullets: bool = True,
    fill_summary: bool = True,
    grid_min: int = GRID_MIN,
    grid_max: int = GRID_MAX,
) -> tuple[CVRenderPayload, TailorPlan]:
    """Apply every deterministic tailoring step in one pass."""
    plan = TailorPlan(keywords=list(keywords))
    plan.gaps = find_gaps(keywords, master)

    fresh = payload.model_copy(deep=True)
    if reorder:
        fresh, plan.reordered = order_bullets(fresh, keywords)
    fresh, plan.dropped = cut_bullets(
        fresh,
        keywords,
        max_bullets_per_role=max_bullets_per_role,
        max_total_bullets=max_total_bullets,
    )

    if label_bullets:
        for _, _, _, bullet in _iter(fresh):
            if bullet.label:
                continue
            label = bold_label(bullet.text, keywords)
            if label:
                bullet.label = label
                plan.relabeled += 1

    competencies = pick_competencies(
        master, keywords, minimum=grid_min, maximum=grid_max
    )
    if competencies:
        fresh.competencies = competencies

    if fill_summary and not fresh.summary.strip():
        fresh.summary = build_summary(master, keywords, language=language)

    fresh.gaps = list(plan.gaps)
    fresh.meta.keywords = [item.term for item in keywords]
    fresh.meta.dropped_bullets = list(plan.dropped)
    plan.coverage = coverage(plain_text(fresh), list(keywords))
    fresh.meta.coverage_pct = plan.coverage_pct

    plan.notes.append(
        f"{len(keywords)} target keywords, coverage {plan.coverage_pct}%."
    )
    if plan.gaps:
        plan.notes.append(
            f"{len(plan.gaps)} requirement(s) not evidenced in the master CV "
            f"({', '.join(gap.term for gap in plan.gaps[:5])})."
        )
    if plan.dropped:
        plan.notes.append(f"{len(plan.dropped)} bullet(s) cut for length.")
    fresh.meta.notes = [
        note for note in [*fresh.meta.notes, *plan.notes] if note not in fresh.meta.notes
    ]
    return fresh, plan
