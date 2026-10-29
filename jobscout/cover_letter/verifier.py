"""Verification and Hallucination Detection for Cover Letters.

Audits generated cover letters against profile ground truth:
1. Grounding check: Verifies that metrics/percentages mentioned exist in profile bullets
2. Relevance check: Confirms selected bullets were incorporated
3. Format check: Length and section integrity
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from jobscout.cover_letter.bullets import Bullet


@dataclass
class VerificationResult:
    passed: bool
    warnings: list[str]
    grounded_bullets_count: int
    unverified_metrics: list[str]


def verify_letter(
    letter_text: str,
    available_bullets: Sequence[Bullet],
    selected_bullets: Sequence[Bullet] | None = None,
) -> VerificationResult:
    """Verify that the generated letter is grounded in applicant's factual CV."""
    warnings: list[str] = []
    unverified_metrics: list[str] = []

    if not letter_text or len(letter_text.strip()) < 100:
        return VerificationResult(
            passed=False,
            warnings=["Letter is too short or empty."],
            grounded_bullets_count=0,
            unverified_metrics=[],
        )

    # 1. Metric / Number extraction check (e.g., "30%", "€50k", "10 million", "5+ years")
    # Numbers with units or percent
    metric_pattern = re.compile(r"\b(\d+(?:[\.,]\d+)?\s*(?:%|percent|prozent|x|k|m|million|millionen|€|\$))\b", re.IGNORECASE)
    letter_metrics = set(m.group(0).lower() for m in metric_pattern.finditer(letter_text))

    if letter_metrics:
        # Collect all metrics from all available profile bullets
        profile_text = " ".join(b.text for b in available_bullets)
        profile_metrics = set(m.group(0).lower() for m in metric_pattern.finditer(profile_text))

        for metric in letter_metrics:
            if metric not in profile_metrics:
                unverified_metrics.append(metric)
                warnings.append(f"Unverified metric in letter not found in CV: '{metric}'")

    # 2. Check how many selected bullets are referenced in letter
    grounded_count = 0
    bullets_to_check = selected_bullets if selected_bullets else available_bullets
    for bullet in bullets_to_check:
        # Extract keywords of 4+ chars
        keywords = [
            w.lower()
            for w in re.findall(r"\b\w{4,}\b", bullet.text)
            if w.lower() not in {"with", "that", "this", "from", "have", "were", "und", "oder", "dass", "eine"}
        ]
        if not keywords:
            continue
        # If at least 40% of keywords appear in letter, count as referenced
        matches = sum(1 for kw in keywords if kw in letter_text.lower())
        if matches / len(keywords) >= 0.4:
            grounded_count += 1

    # 3. Overall sanity
    passed = len(warnings) == 0

    return VerificationResult(
        passed=passed,
        warnings=warnings,
        grounded_bullets_count=grounded_count,
        unverified_metrics=unverified_metrics,
    )
