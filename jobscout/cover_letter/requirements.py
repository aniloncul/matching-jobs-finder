"""Extract discrete requirements and qualifications from a job posting description.

Splits a job description into target requirement units for semantic matching.
Uses a hybrid rule-based extractor with optional LLM refinement.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class Requirement:
    text: str
    category: str = "general"  # "must_have", "nice_to_have", "skill", "general"


# Section headers commonly found in DE and EN job ads
REQUIREMENT_HEADERS = [
    r"anforderungen",
    r"das\s+bringst\s+du\s+mit",
    r"dein\s+profil",
    r"qualifikationen",
    r"voraussetzungen",
    r"ihr\s+profil",
    r"requirements",
    r"what\s+you\s+(?:bring|need)",
    r"qualifications",
    r"who\s+you\s+are",
    r"skills\s+(?:&|and)\s+experience",
]

HEADER_PATTERN = re.compile(
    r"(?:^|\n)\s*(?:[#*-]+\s*)?(?:" + "|".join(REQUIREMENT_HEADERS) + r")\s*[:\n]",
    re.IGNORECASE,
)


TASK_HEADERS = [
    r"aufgaben",
    r"deine\s+aufgaben",
    r"ihre\s+aufgaben",
    r"responsibilities",
    r"what\s+you\s+will\s+do",
    r"tasks",
]

BENEFIT_HEADERS = [
    r"wir\s+bieten",
    r"was\s+wir\s+bieten",
    r"benefits",
    r"what\s+we\s+offer",
    r"perks",
    r"über\s+uns",
    r"about\s+us",
]


def extract_requirements(description: str) -> list[Requirement]:
    """Parse a job description into a list of Requirement objects.

    Extracts bullet points, numbered lists, or requirement sentences.
    Excludes perks/benefits and categorizes requirements and responsibilities.
    """
    if not description or not description.strip():
        return []

    lines = description.splitlines()
    requirements: list[Requirement] = []
    current_category: str | None = None

    bullet_pattern = re.compile(r"^\s*[-*•–—+]\s+(.+)$|^\s*\d+[\.)]\s+(.+)$")

    for line in lines:
        cleaned = line.strip()
        if not cleaned:
            continue

        # Check section headers
        if any(re.search(rf"\b{hdr}\b", cleaned, re.IGNORECASE) for hdr in REQUIREMENT_HEADERS):
            current_category = "must_have"
            continue
        if any(re.search(rf"\b{hdr}\b", cleaned, re.IGNORECASE) for hdr in TASK_HEADERS):
            current_category = "responsibility"
            continue
        if any(re.search(rf"\b{hdr}\b", cleaned, re.IGNORECASE) for hdr in BENEFIT_HEADERS):
            current_category = "benefit"  # ignore perks like free coffee or vacation days
            continue

        if current_category == "benefit":
            continue

        # Check for bullets
        match = bullet_pattern.match(line)
        if match:
            text = (match.group(1) or match.group(2) or "").strip()
            if len(text) >= 12:
                cat = current_category or "general"
                requirements.append(Requirement(text=text, category=cat))
            continue

        # If inside a requirements section and line is substantial
        if current_category == "must_have":
            if len(cleaned) >= 20 and not cleaned.endswith(":"):
                requirements.append(Requirement(text=cleaned, category="must_have"))

    # If no requirements were extracted via bullets or headers, extract sentences
    if not requirements:
        sentences = re.split(r"(?<=[.!?])\s+", description)
        for s in sentences:
            cleaned = s.strip()
            if 25 <= len(cleaned) <= 300:
                # Filter for requirement-like keywords
                if re.search(
                    r"\b(experience|erfahrung|kenntnisse|knowledge|proficient|skills|degree|abschluss|years|jahre)\b",
                    cleaned,
                    re.IGNORECASE,
                ):
                    requirements.append(Requirement(text=cleaned, category="general"))

    # Fallback to chunked paragraphs if still empty
    if not requirements:
        paragraphs = [p.strip() for p in description.split("\n\n") if len(p.strip()) >= 30]
        for p in paragraphs[:8]:
            requirements.append(Requirement(text=p, category="general"))

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[Requirement] = []
    for req in requirements:
        key = req.text.casefold()
        if key not in seen:
            seen.add(key)
            unique.append(req)

    return unique
