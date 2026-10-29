"""Bullet bank: extracts individual achievement bullets from a Profile.

Each bullet is an embeddable unit:
  - source: "experience" | "education" | "skills" | "summary"
  - text: the actual text content
  - content_hash: SHA-256 hex of the text (used as cache key)
  - metadata: optional context (company, role, dates…)
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Bullet:
    source: str  # "experience", "education", "skills", "summary"
    text: str
    content_hash: str
    metadata: dict[str, Any] = field(default_factory=dict, compare=False, hash=False)

    @classmethod
    def from_text(cls, source: str, text: str, **metadata: Any) -> "Bullet":
        text = text.strip()
        h = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return cls(source=source, text=text, content_hash=h, metadata=metadata)


def _clean(text: Any) -> str:
    return str(text).strip() if text else ""


def extract_bullets(profile_dict: dict[str, Any]) -> list[Bullet]:
    """Extract all embeddable bullets from a profile dictionary.

    Returns bullets ordered by source priority:
    1. Experience achievements (most valuable for matching)
    2. Summary
    3. Skills
    4. Education

    Bullets with empty text or text shorter than 8 characters are skipped.
    """
    bullets: list[Bullet] = []

    # 1. Experience achievements — most granular and most valuable
    for exp in profile_dict.get("experience") or []:
        company = _clean(exp.get("company"))
        role = _clean(exp.get("role"))
        start = _clean(exp.get("start"))
        end = _clean(exp.get("end"))
        period = f"{start}–{end}".strip("–")
        for achievement in exp.get("achievements") or []:
            text = _clean(achievement)
            if len(text) < 8:
                continue
            bullets.append(
                Bullet.from_text(
                    "experience",
                    text,
                    company=company,
                    role=role,
                    period=period,
                )
            )

    # 2. Summary (split into sentences for finer granularity)
    summary = _clean(profile_dict.get("summary"))
    if summary:
        # Try to split on sentence boundaries; fall back to whole summary
        import re
        sentences = re.split(r"(?<=[.!?])\s+", summary)
        for sentence in sentences:
            text = sentence.strip()
            if len(text) >= 20:
                bullets.append(Bullet.from_text("summary", text))
        if not bullets or all(b.source != "summary" for b in bullets):
            if len(summary) >= 20:
                bullets.append(Bullet.from_text("summary", summary))

    # 3. Skills (each skill as a bullet, grouped for context)
    skills = [_clean(s) for s in (profile_dict.get("skills") or []) if _clean(s)]
    if skills:
        # Whole skills list as one bullet (for semantic matching of skill groups)
        grouped = ", ".join(skills)
        if grouped:
            bullets.append(Bullet.from_text("skills", grouped))
        # Individual skills
        for skill in skills:
            if len(skill) >= 3:
                bullets.append(Bullet.from_text("skills", skill))

    # 4. Education
    for edu in profile_dict.get("education") or []:
        institution = _clean(edu.get("institution"))
        degree = _clean(edu.get("degree"))
        text = " — ".join(filter(None, [degree, institution]))
        if len(text) >= 8:
            bullets.append(
                Bullet.from_text(
                    "education",
                    text,
                    institution=institution,
                    degree=degree,
                )
            )

    # Deduplicate by content_hash while preserving insertion order
    seen: set[str] = set()
    unique: list[Bullet] = []
    for b in bullets:
        if b.content_hash not in seen:
            seen.add(b.content_hash)
            unique.append(b)

    return unique
