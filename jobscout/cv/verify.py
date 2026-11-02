"""Gate a CV before it can become a PDF.

Three deterministic layers, cheapest first:

1. **structure** - does the payload still describe the same career the master CV
   describes?  Invented employers or job titles block; trimmed bullets warn.
2. **title** - a CV may not promote you.  If the master says "AI Engineer" the
   generated document cannot present you as "Head of AI".
3. **facts** - the same fact gate as the cover letter
   (:mod:`jobscout.verification.facts`) over the rendered text, plus a CV/letter
   consistency pass so the two documents cannot disagree about dates.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Sequence

from jobscout.cv.master import MasterCV
from jobscout.cv.payload import CVRenderPayload, iter_bullets, plain_text
from jobscout.cv.tailor import derived_metric_claims
from jobscout.verification.facts import FactCheckResult, check_facts, extract_metrics
from jobscout.verification.text import fold_for_match, normalize_text

SENIORITY_MARKERS: dict[str, int] = {
    "praktikant": 0,
    "intern": 0,
    "working student": 0,
    "student": 0,
    "junior": 1,
    "trainee": 1,
    "senior": 2,
    "staff": 3,
    "principal": 3,
    "lead": 3,
    "teamleiter": 3,
    "group lead": 3,
    "manager": 3,
    "head": 4,
    "leiter": 4,
    "director": 4,
    "direktor": 4,
    "vp": 5,
    "vice president": 5,
    "chief": 6,
    "chef": 6,
    "partner": 6,
}
_C_LEVEL = ("cto", "ceo", "coo", "cfo", "cro", "cio")
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")


@dataclass
class CVVerification:
    verdict: str = "pass"
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    structure_issues: list[str] = field(default_factory=list)
    title_issues: list[str] = field(default_factory=list)
    cross_document_issues: list[str] = field(default_factory=list)
    facts: FactCheckResult | None = None

    @property
    def passed(self) -> bool:
        return self.verdict != "block"

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
            "structure": list(self.structure_issues),
            "title": list(self.title_issues),
            "cross_document": list(self.cross_document_issues),
            "facts": self.facts.to_dict() if self.facts else None,
        }


def title_rank(value: str) -> int:
    """Seniority rank of a role string; unknown titles rank as a peer (1)."""
    folded = f" {fold_for_match(value)} "
    rank = 1
    for marker, level in SENIORITY_MARKERS.items():
        if f" {marker} " in folded:
            rank = max(rank, level)
    for marker in _C_LEVEL:
        if f" {marker} " in folded or folded.strip() == marker:
            return max(rank, 6)
    return rank


def check_title_drift(master: MasterCV, payload: CVRenderPayload) -> list[str]:
    """Warn when the generated CV is more senior than the master CV."""
    issues: list[str] = []
    source_rank = title_rank(master.headline)
    if payload.headline and fold_for_match(payload.headline) != fold_for_match(master.headline):
        target_rank = title_rank(payload.headline)
        if target_rank > source_rank:
            issues.append(
                f"Title drift: the master CV says '{master.headline}' but the CV "
                f"is headlined '{payload.headline}'. Keep the honest title."
            )
    for role in payload.experience:
        source = next(
            (
                block
                for block in master.roles
                if fold_for_match(block.company) == fold_for_match(role.company)
            ),
            None,
        )
        if source is None:
            continue
        if title_rank(role.role) > title_rank(source.role):
            issues.append(
                f"Title drift at {role.company}: '{source.role}' became '{role.role}'."
            )
    return issues


def check_structure(master: MasterCV, payload: CVRenderPayload) -> tuple[list[str], list[str]]:
    """Return ``(blockers, warnings)`` comparing the payload to the master CV."""
    blockers: list[str] = []
    warnings: list[str] = []
    master_roles = {fold_for_match(block.company): block for block in master.roles}

    seen_order: list[str] = []
    for role in payload.experience:
        company = fold_for_match(role.company)
        source = master_roles.get(company)
        if source is None:
            blockers.append(
                f"Employer '{role.company}' is not in the master CV and was not rendered "
                "from evidence."
            )
            continue
        seen_order.append(company)
        known_titles = {fold_for_match(source.role)}
        known_titles.update(
            fold_for_match(fact.role) for fact in master.facts_for(role.company) if fact.role
        )
        if role.role and fold_for_match(role.role) not in known_titles:
            blockers.append(
                f"Role '{role.role}' at {role.company} is not in the master CV."
            )
        expected = len(source.fact_ids)
        if len(role.bullets) < expected:
            warnings.append(
                f"{role.company}: {expected - len(role.bullets)} of {expected} "
                "achievement(s) from the master CV were cut."
            )
        elif len(role.bullets) > expected:
            blockers.append(
                f"{role.company}: the CV has more bullets than the master CV has facts."
            )

    # CV order is part of the truth: the payload may drop roles, not reorder them.
    order = [fold_for_match(block.company) for block in master.roles]
    index = -1
    for company in seen_order:
        try:
            position = order.index(company, index + 1)
        except ValueError:
            warnings.append("Experience order differs from the master CV.")
            break
        index = position

    for _, _, role, bullet in iter_bullets(payload):
        if not bullet.fact_ids:
            warnings.append(
                f"Bullet at {role.company} has no master CV fact id: '{bullet.text[:60]}'."
            )
    return blockers, warnings


def cross_document_checks(
    payload: CVRenderPayload,
    master: MasterCV,
    letter_text: str | None,
    *,
    target_company: str = "",
    today_year: int | None = None,
) -> list[str]:
    """The letter and the CV must not contradict each other."""
    if not letter_text or not letter_text.strip():
        return []
    from datetime import date

    issues: list[str] = []
    allowed_years = set(master.known_years())
    allowed_years.add((today_year or date.today().year))
    cv_text = plain_text(payload)
    allowed_years.update(int(match) for match in _YEAR_RE.findall(cv_text))

    letter_folded = fold_for_match(letter_text)
    for year_text in _YEAR_RE.findall(letter_text):
        year = int(year_text)
        if year not in allowed_years:
            issues.append(
                f"The letter mentions {year}; neither the master CV nor the CV PDF "
                "supports that year."
            )

    evidence_metrics = {claim.key for claim in extract_metrics(master.evidence_text())}
    for claim in extract_metrics(letter_text):
        if claim.key in evidence_metrics:
            continue
        if claim.raw and claim.raw in cv_text:
            continue
        issues.append(
            f"The letter states '{claim.raw}' which the master CV cannot support; "
            "the CV and the letter must use the same numbers."
        )

    # A company the candidate never worked for cannot be verified from the CV,
    # so the letter is only checked for the numbers and dates it can contradict.
    if target_company and fold_for_match(target_company) not in letter_folded:
        issues.append(
            f"The letter never names {target_company}, the company on the CV header."
        )
    return issues


def verify_cv(
    payload: CVRenderPayload,
    master: MasterCV,
    *,
    letter_text: str | None = None,
    target_company: str = "",
    forbidden_phrases: Sequence[str] | None = None,
    check_cross_document: bool = True,
) -> CVVerification:
    """Run every CV gate.  A ``block`` verdict must stop PDF generation."""
    structure_blockers, structure_warnings = check_structure(master, payload)
    title_warnings = check_title_drift(master, payload)

    facts = check_facts(
        plain_text(payload),
        master.evidence_text(),
        forbidden_phrases=forbidden_phrases,
        supported_terms=master.supported_skills(),
        allow_metrics=[*master.allowed_metrics(), *derived_metric_claims(master)],
    )
    cross = (
        cross_document_checks(
            payload, master, letter_text, target_company=target_company
        )
        if check_cross_document
        else []
    )

    blockers = [*structure_blockers, *facts.blockers]
    warnings = [
        *structure_warnings,
        *title_warnings,
        *cross,
        *facts.warnings,
    ]
    verdict = "block" if blockers else ("warn" if warnings else "pass")
    return CVVerification(
        verdict=verdict,
        blockers=blockers,
        warnings=warnings,
        structure_issues=structure_warnings,
        title_issues=title_warnings,
        cross_document_issues=cross,
        facts=facts,
    )


def format_verification(verification: CVVerification) -> str:
    lines = [f"CV check: {verification.verdict.upper()}"]
    for label, items in (
        ("Blockers", verification.blockers),
        ("Warnings", verification.warnings),
    ):
        if items:
            lines.append(f"{label}:")
            lines.extend(f"- {item}" for item in items)
    if verification.verdict == "pass":
        lines.append("No blockers: every claim traces back to the master CV.")
    return normalize_text("\n".join(lines))
