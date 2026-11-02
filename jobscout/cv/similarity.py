"""Deterministic CV-reuse recommendation for similar job descriptions.

A port of career-ops ``jd-similarity.mjs``.  This is a *recommendation* layer:
it never regenerates a CV, never blocks a build and never deletes anything.  It
answers one question - "before I tailor a fresh CV for this posting, is there an
already-tailored CV I could reuse instead?" - with ``reuse``,
``reuse-with-edits`` or ``regenerate`` plus the Jaccard score that led there.

Two signals: token-set overlap (Jaccard) on the folded text, and a seniority
gate that forces ``regenerate`` when the two documents name two different,
unambiguous levels (a Junior CV is not reusable for a Lead posting no matter how
much their boilerplate overlaps).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from jobscout.config import Settings
from jobscout.cover_letter.description_store import load_saved_description
from jobscout.cv.pipeline import cv_key, cv_paths
from jobscout.models import Job
from jobscout.verification.keywords import STOPWORDS
from jobscout.verification.text import fold_for_match

# Job-level words.  A document "names a level" only when exactly one of these
# rows is present; naming two is ambiguous and the seniority gate stands down.
LEVELS: tuple[tuple[str, ...], ...] = (
    # 0 intern / student
    ("praktikant", "praktikantin", "praktikanten", "praktikum", "werkstudent",
     "werkstudentin", "werkstudenten", "intern", "internship", "trainee",
     "absolvent", "absolventin", "absolventen", "berufseinsteiger", "berufseinsteigerin", "entry"),
    # 1 junior / new grad
    ("junior", "juniorin", "juniors", "jun"),
    # 2 mid / experienced individual contributor
    ("midlevel", "mid-level", "berufserfahren"),
    # 3 senior
    ("senior", "seniorin", "seniors", "sen"),
    # 4 staff / lead / principal / management
    ("lead", "teamlead", "teamleitung", "techlead", "principal", "staff",
     "architect", "architekt", "architektin", "distinguished", "chief",
     "direktor", "direktorin", "direktion", "head"),
)

# Words in LEVELS that are also ordinary English/German.  When one of these is
# followed by a word in its map, it is boilerplate ("principal
# responsibilities", "lead to", "head of marketing" is a title but "header" is
# not a token after folding, "mid-market").  Matching those as job levels makes
# the seniority gate fire on two postings of identical seniority.
_NON_LEVEL_FOLLOWERS: dict[str, set[str]] = {
    "principal": {
        "responsibilities", "responsibility", "duties", "tasks", "activities",
        "accountabilities", "objectives", "purpose", "aufgaben", "verantwortlich",
    },
    "lead": {
        "to", "the", "a", "an", "our", "and", "or", "by", "on", "in", "for",
        "with", "from", "you", "your", "projects", "mentoring",
    },
    "staff": {"meetings", "department", "planung", "software", "number"},
    "head": {"office", "quarters", "quarter", "line", "sets", "hunter"},
    "mid": {"market", "markets", "sized", "size", "tier", "term", "fall", "range"},
}

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9.\-/+#]*")


def tokenize(text: str | None) -> set[str]:
    """Normalised, stop-word-filtered term set shared by both documents."""
    folded = fold_for_match(text)
    terms: set[str] = set()
    for raw in _TOKEN_RE.findall(folded):
        token = raw.strip(".-/#+")
        if not token or token in STOPWORDS:
            continue
        if len(token) <= 1 and not token.isdigit():
            continue
        terms.add(token)
    return terms


def jaccard_similarity(left: Any, right: Any) -> float:
    a = left if isinstance(left, set) else tokenize(left)
    b = right if isinstance(right, set) else tokenize(right)
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    intersection = a & b
    return len(intersection) / len(a | b)


def _reads_as_level(word: str, folded: str, position: int) -> bool:
    followers = _NON_LEVEL_FOLLOWERS.get(word)
    if not followers:
        return True
    rest = folded[position + len(word):]
    nxt = re.match(r"[^a-z0-9]*([a-z0-9]+)", rest)
    return not nxt or nxt.group(1) not in followers


def levels_in(text: str | None) -> set[int]:
    """Every distinct seniority level named in the text, as LEVELS indices."""
    folded = f" {fold_for_match(text)} "
    found: set[int] = set()
    for level, words in enumerate(LEVELS):
        for word in words:
            pattern = re.compile(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])")
            match = pattern.search(folded)
            if match and _reads_as_level(word, folded, match.start()):
                found.add(level)
                break
    return found


def level_of(text: str | None) -> int:
    """The single unambiguous level of a document, or -1 if ambiguous/none."""
    levels = levels_in(text)
    return next(iter(levels)) if len(levels) == 1 else -1


def hard_mismatch(new_jd: str | None, previous_text: str | None) -> bool:
    new_level = level_of(new_jd)
    previous_level = level_of(previous_text)
    return new_level >= 0 and previous_level >= 0 and new_level != previous_level


def recommend_jd_reuse(
    new_jd: str | None,
    previous_text: str | None,
    *,
    high_threshold: float = 0.72,
    medium_threshold: float = 0.45,
) -> dict[str, Any]:
    """reuse / reuse-with-edits / regenerate + the score and reason behind it."""
    score = jaccard_similarity(new_jd, previous_text)
    if hard_mismatch(new_jd, previous_text):
        return {"decision": "regenerate", "score": round(score, 3), "reason": "level-mismatch"}
    if score >= high_threshold:
        return {"decision": "reuse", "score": round(score, 3), "reason": "high-similarity"}
    if score >= medium_threshold:
        return {"decision": "reuse-with-edits", "score": round(score, 3), "reason": "medium-similarity"}
    return {"decision": "regenerate", "score": round(score, 3), "reason": "low-similarity"}


# ── Cross-CV candidate discovery ──────────────────────────────────────

@dataclass
class ReuseCandidate:
    key: str
    source: str
    job_id: str
    company: str
    title: str
    decision: str
    score: float
    reason: str
    has_pdf: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "source": self.source,
            "job_id": self.job_id,
            "company": self.company,
            "title": self.title,
            "decision": self.decision,
            "score": self.score,
            "reason": self.reason,
            "has_pdf": self.has_pdf,
        }


@dataclass
class ReuseCheck:
    new_key: str
    jd_available: bool
    candidates: list[ReuseCandidate] = field(default_factory=list)

    @property
    def best(self) -> ReuseCandidate | None:
        if not self.candidates:
            return None
        rank = {"reuse": 2, "reuse-with-edits": 1, "regenerate": 0}
        return max(self.candidates, key=lambda c: (rank[c.decision], c.score))

    def to_dict(self) -> dict[str, Any]:
        best = self.best
        return {
            "new_key": self.new_key,
            "jd_available": self.jd_available,
            "best": best.to_dict() if best else None,
            "candidates": [c.to_dict() for c in self.candidates],
        }


def _iter_cv_metadata(output_dir: Path) -> Iterable[tuple[str, dict[str, Any]]]:
    if not output_dir.is_dir():
        return
    for path in sorted(output_dir.glob("*.json")):
        if path.name.endswith(".payload.json"):
            continue
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(meta, dict):
            yield path.stem, meta


def _saved_text_for_meta(settings: Settings, meta: dict[str, Any]) -> str:
    """The previous CV's JD text: saved description, else its target terms."""
    job = meta.get("job") or {}
    source = job.get("source")
    job_id = job.get("job_id")
    if source and job_id:
        saved = load_saved_description(settings, str(source), str(job_id))
        if saved:
            return saved
    keywords = meta.get("keywords") or []
    terms = [str(k.get("term")) for k in keywords if isinstance(k, dict) and k.get("term")]
    title = job.get("title") or ""
    company = job.get("company") or ""
    return "\n".join([title, company, *terms]).strip()


def recommend_cv_reuse(
    settings: Settings,
    job: Job,
    *,
    high_threshold: float = 0.72,
    medium_threshold: float = 0.45,
    limit: int = 5,
) -> ReuseCheck:
    """Compare ``job``'s JD against every already-built CV and recommend."""
    output_dir = Path(settings.cv.output_dir)
    target_key = cv_key(job)

    new_jd = load_saved_description(settings, job.source.value, job.job_id) or ""
    if not new_jd and job.description and len(job.description.strip()) >= 20:
        new_jd = job.description.strip()
    if not new_jd and job.posted_text and len(job.posted_text.strip()) >= 40:
        new_jd = job.posted_text.strip()

    check = ReuseCheck(new_key=target_key, jd_available=bool(new_jd))
    if not new_jd:
        return check

    for key, meta in _iter_cv_metadata(output_dir):
        if key == target_key or key == "master":
            continue
        meta_job = meta.get("job")
        if not isinstance(meta_job, dict):
            continue
        previous_text = _saved_text_for_meta(settings, meta)
        if len(tokenize(previous_text)) < 5:
            continue
        rec = recommend_jd_reuse(
            new_jd, previous_text,
            high_threshold=high_threshold, medium_threshold=medium_threshold,
        )
        check.candidates.append(
            ReuseCandidate(
                key=key,
                source=str(meta_job.get("source") or ""),
                job_id=str(meta_job.get("job_id") or ""),
                company=str(meta_job.get("company") or ""),
                title=str(meta_job.get("title") or ""),
                decision=rec["decision"],
                score=float(rec["score"]),
                reason=rec["reason"],
                has_pdf=cv_paths(settings, key)["pdf"].exists(),
            )
        )
    check.candidates.sort(key=lambda c: c.score, reverse=True)
    return ReuseCheck(
        new_key=check.new_key,
        jd_available=check.jd_available,
        candidates=check.candidates[:limit],
    )
