"""Verification and hallucination detection for generated documents.

The metric check and the structured fact gate now live in
:mod:`jobscout.verification.facts`; this module keeps the cover-letter-specific
relevance check and exposes one result object for the generator.

Verdicts are ``pass`` / ``warn`` / ``block``.  A ``block`` must stop PDF
generation: an invented metric, an unsupported tool or a forbidden phrase is
not something a warning banner should paper over.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Sequence

from jobscout.cover_letter.bullets import Bullet
from jobscout.verification.facts import check_facts


@dataclass
class VerificationResult:
    passed: bool
    warnings: list[str]
    grounded_bullets_count: int
    unverified_metrics: list[str]
    verdict: str = "pass"
    forbidden_phrases: list[str] = field(default_factory=list)
    unsupported_facts: list[str] = field(default_factory=list)
    scope_issues: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)


def _relevance_warnings(
    letter_text: str,
    bullets: Sequence[Bullet],
) -> tuple[int, list[str]]:
    grounded = 0
    warnings: list[str] = []
    for bullet in bullets:
        keywords = [
            word.lower()
            for word in re.findall(r"\b\w{4,}\b", bullet.text)
            if word.lower()
            not in {
                "with", "that", "this", "from", "have", "were", "und", "oder",
                "dass", "eine", "einen", "einem", "sowie", "auch", "nach",
            }
        ]
        if not keywords:
            continue
        matches = sum(1 for keyword in keywords if keyword in letter_text.lower())
        if matches / len(keywords) >= 0.4:
            grounded += 1
    return grounded, warnings


def verify_letter(
    letter_text: str,
    available_bullets: Sequence[Bullet],
    selected_bullets: Sequence[Bullet] | None = None,
    *,
    evidence_text: str | None = None,
    forbidden_phrases: Sequence[str] | None = None,
) -> VerificationResult:
    """Verify that the generated letter is grounded in the applicant's CV."""
    if not letter_text or len(letter_text.strip()) < 100:
        return VerificationResult(
            passed=False,
            warnings=["Letter is too short or empty."],
            grounded_bullets_count=0,
            unverified_metrics=[],
            verdict="block",
            blockers=["Letter is too short or empty."],
        )

    evidence = evidence_text or " ".join(bullet.text for bullet in available_bullets)
    facts = check_facts(
        letter_text,
        evidence,
        forbidden_phrases=forbidden_phrases,
    )

    bullets_to_check = selected_bullets if selected_bullets else available_bullets
    grounded_count, _ = _relevance_warnings(letter_text, bullets_to_check)

    warnings = [*facts.warnings, *facts.blockers]
    return VerificationResult(
        passed=facts.verdict != "block",
        warnings=warnings,
        grounded_bullets_count=grounded_count,
        unverified_metrics=facts.invented_metrics,
        verdict=facts.verdict,
        forbidden_phrases=facts.forbidden_phrases,
        unsupported_facts=facts.unsupported_facts,
        scope_issues=facts.scope_issues,
        blockers=facts.blockers,
    )
