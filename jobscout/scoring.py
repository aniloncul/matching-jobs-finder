"""Deterministic job-fit scoring.

The model is allowed to *rate* four dimensions; it is not allowed to decide the
final number.  Python owns the weights, the rounding and the vetoes, so the
same ratings always produce the same score and a prompt change cannot silently
rebalance the ranking.

Weights and bands mirror the reference implementation used by the benchmark:

``technical .30 · experience .25 · behavioral .15 · career .30``

``≥75 Strong · ≥60 Good · ≥45 Moderate · ≥30 Weak · <30 Poor``

A failed language gate or location verdict is a hard veto, independent of the
score.  A deadline within seven days only acts as a tie-breaker.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date
from fractions import Fraction
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from jobscout.config import Settings
from jobscout.deadlines import CLOSING_SOON, deadline_status
from jobscout.models import Job, now_iso

Dimension = Literal["technical", "experience", "behavioral", "career"]
Gate = Literal["pass", "fail", "unknown"]

WEIGHTS: dict[Dimension, Fraction] = {
    "technical": Fraction(30, 100),
    "experience": Fraction(25, 100),
    "behavioral": Fraction(15, 100),
    "career": Fraction(30, 100),
}

BANDS: tuple[tuple[int, str], ...] = (
    (75, "Strong Fit"),
    (60, "Good Fit"),
    (45, "Moderate Fit"),
    (30, "Weak Fit"),
    (0, "Poor Fit"),
)


class FitScoreError(ValueError):
    """Raised when an LLM fit payload does not match the required schema."""


class FitScores(BaseModel):
    model_config = ConfigDict(extra="forbid")

    technical: int = Field(ge=0, le=100)
    experience: int = Field(ge=0, le=100)
    behavioral: int = Field(ge=0, le=100)
    career: int = Field(ge=0, le=100)
    strengths: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    language_gate: Gate = "unknown"
    location_verdict: Gate = "unknown"


@dataclass
class FitResult:
    score: int
    verdict: str
    veto: bool
    veto_reasons: list[str]
    closing_soon: bool
    deadline: str | None
    scores: FitScores

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "verdict": self.verdict,
            "veto": self.veto,
            "veto_reasons": self.veto_reasons,
            "closing_soon": self.closing_soon,
            "deadline": self.deadline,
            "dimensions": {
                "technical": self.scores.technical,
                "experience": self.scores.experience,
                "behavioral": self.scores.behavioral,
                "career": self.scores.career,
            },
            "strengths": self.scores.strengths,
            "gaps": self.scores.gaps,
            "language_gate": self.scores.language_gate,
            "location_verdict": self.scores.location_verdict,
        }


def band_for(score: int) -> str:
    for floor, label in BANDS:
        if score >= floor:
            return label
    return BANDS[-1][1]


def round_half_up(value: Fraction) -> int:
    """Round a non-negative fraction to the nearest integer, ties away from zero.

    ``int()`` truncates and ``round()`` uses banker's rounding, so 59.5 would
    become 59 in both cases.  The benchmark rounds half up: 59.5 -> 60.
    """
    if value < 0:
        return -round_half_up(-value)
    quotient, remainder = divmod(value.numerator, value.denominator)
    if remainder * 2 >= value.denominator:
        quotient += 1
    return quotient


def compute_fit(
    scores: FitScores,
    *,
    deadline: str | date | None = None,
    today: date | None = None,
    closing_days: int = 7,
) -> FitResult:
    """Weighted score with exact rational arithmetic and hard vetoes."""
    total = sum(
        Fraction(getattr(scores, dimension)) * weight
        for dimension, weight in WEIGHTS.items()
    )
    # int() truncates; the benchmark uses half-up rounding so 59.5 -> 60.
    score = round_half_up(total)

    veto_reasons: list[str] = []
    if scores.language_gate == "fail":
        veto_reasons.append("language_gate")
    if scores.location_verdict == "fail":
        veto_reasons.append("location")
    veto = bool(veto_reasons)

    status = deadline_status(deadline, today=today, closing_days=closing_days)
    return FitResult(
        score=score,
        verdict="Veto" if veto else band_for(score),
        veto=veto,
        veto_reasons=veto_reasons,
        closing_soon=status == CLOSING_SOON,
        deadline=deadline.isoformat() if isinstance(deadline, date) else (deadline or None),
        scores=scores,
    )


def parse_fit_payload(payload: dict[str, Any]) -> FitScores:
    """Validate the model payload strictly; unknown keys are rejected."""
    if not isinstance(payload, dict):
        raise FitScoreError("Fit payload must be a JSON object")
    try:
        return FitScores.model_validate(payload)
    except ValidationError as exc:
        raise FitScoreError(f"Invalid fit payload: {exc}") from exc


def rank_sort_key(job: Job, fit: FitResult) -> tuple:
    """Veto last, then score, then a closing deadline as tie-breaker."""
    deadline = job.deadline or "9999-12-31"
    return (
        1 if fit.veto else 0,
        -fit.score,
        0 if fit.closing_soon else 1,
        deadline,
        job.company.casefold(),
    )


def rank_jobs(pairs: list[tuple[Job, FitResult]]) -> list[tuple[Job, FitResult]]:
    ordered = sorted(pairs, key=lambda item: rank_sort_key(item[0], item[1]))
    for job, fit in ordered:
        job.rank_score = fit.score
        job.rank_verdict = fit.verdict
        job.ranked_at = now_iso()
    return ordered


def passing(pairs: list[tuple[Job, FitResult]], min_score: int = 60) -> list[Job]:
    """Jobs worth writing to the sheet: not vetoed and above the threshold."""
    return [
        job
        for job, fit in pairs
        if not fit.veto and fit.score >= max(0, min(100, min_score))
    ]


# ---------------------------------------------------------------------------
# Prompt + storage
# ---------------------------------------------------------------------------

FIT_SYSTEM_PROMPT = """You rate how well a candidate fits a job posting.
Return one JSON object with exactly these keys:
- technical: integer 0-100, how well the required tools and methods match the candidate's evidence
- experience: integer 0-100, seniority and years-of-experience match
- behavioral: integer 0-100, working style, language and soft-skill match
- career: integer 0-100, how well the role advances the candidate's stated direction
- strengths: array of 2-5 short strings, each backed by profile evidence
- gaps: array of 0-5 short strings, missing requirements; never invent experience
- language_gate: "pass" if the posting language is one the candidate speaks, "fail" if not, "unknown" if unclear
- location_verdict: "pass" if the location is workable (including remote), "fail" if it is not, "unknown" if unclear

Do not compute a weighted total. Do not return prose outside the JSON object.
Never lower a rating because of age, gender, nationality, marital status or a
photo; those are not job requirements and must not be considered."""


def build_fit_prompt(
    profile_text: str,
    job: Job,
    description: str,
    *,
    candidate_location: str | None = None,
) -> str:
    return (
        "Candidate profile:\n"
        f"{profile_text.strip()[:12000]}\n\n"
        "Job posting:\n"
        f"Company: {job.company}\n"
        f"Role: {job.title}\n"
        f"Location: {job.location or candidate_location or 'not specified'}\n"
        f"Candidate location: {candidate_location or 'not specified'}\n"
        f"Deadline: {job.deadline or 'not specified'}\n\n"
        f"Description:\n{description.strip()[:12000]}\n\n"
        "Rate the fit as the requested JSON object."
    )


def ranking_path(settings: Settings) -> Path:
    return Path(settings.root) / "data" / "rankings.json"


class RankingStore:
    """Append-only-ish JSON index of fit results, keyed by source:job_id."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> dict[str, dict]:
        if not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(payload, dict):
            return {}
        rankings = payload.get("rankings")
        return rankings if isinstance(rankings, dict) else {}

    def save(self, rankings: dict[str, dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "updated_at": now_iso(),
            "rankings": rankings,
        }
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temporary, self.path)

    @staticmethod
    def key(job: Job) -> str:
        return f"{job.source.value}:{job.job_id}"

    def upsert(self, job: Job, fit: FitResult) -> dict:
        rankings = self.load()
        record = {
            "source": job.source.value,
            "job_id": job.job_id,
            "company": job.company,
            "title": job.title,
            "link": job.link,
            "ranked_at": now_iso(),
            **fit.to_dict(),
        }
        rankings[self.key(job)] = record
        self.save(rankings)
        return record

    def get(self, job: Job) -> dict | None:
        return self.load().get(self.key(job))


def store_for(settings: Settings) -> RankingStore:
    return RankingStore(ranking_path(settings))


def format_ranking(job: Job, fit: FitResult) -> str:
    marker = "VETO" if fit.veto else f"{fit.score:3d}"
    deadline = f" · deadline {job.deadline}" if job.deadline else ""
    closing = " · closing soon" if fit.closing_soon else ""
    return f"[{marker}] {job.company} · {job.title}{deadline}{closing} · {fit.verdict}"
