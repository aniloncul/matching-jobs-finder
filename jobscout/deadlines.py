"""Deadline extraction and the daily closing-date sweep.

A deadline is only reported when the posting itself names one next to a
deadline marker ("Bewerbungsfrist", "apply by", ...).  Random dates in the body
(start dates, contract dates, graduation years) are ignored.  When a date is
visible but cannot be parsed the status is ``unparseable`` - never a guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

from jobscout.models import Job

ACTIVE = "active"
CLOSING_SOON = "closing_soon"
EXPIRED = "expired"
UNPARSEABLE = "unparseable"
UNKNOWN = "unknown"

DEFAULT_CLOSING_DAYS = 7

_MARKERS = (
    "bewerbungsfrist",
    "bewerbungsschluss",
    "einsendeschluss",
    "stellenschluss",
    "bewerben sie sich bis",
    "bewerben bis",
    "bewerbung bis",
    "application deadline",
    "applications close",
    "application closes",
    "apply by",
    "apply before",
    "closing date",
    "deadline",
    "last date to apply",
)

_MONTHS = {
    "januar": 1,
    "jänner": 1,
    "january": 1,
    "jan": 1,
    "februar": 2,
    "february": 2,
    "feb": 2,
    "märz": 3,
    "maerz": 3,
    "marz": 3,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "mai": 5,
    "may": 5,
    "juni": 6,
    "june": 6,
    "jun": 6,
    "juli": 7,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sept": 9,
    "sep": 9,
    "oktober": 10,
    "october": 10,
    "okt": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "dezember": 12,
    "december": 12,
    "dez": 12,
    "dec": 12,
}

_ISO_RE = re.compile(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b")
_GERMAN_NUMERIC_RE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{2,4})\b")
_MONTH_NAMES = "|".join(sorted(_MONTHS, key=len, reverse=True))
_DMY_WORD_RE = re.compile(
    rf"\b(\d{{1,2}})\.?\s+({_MONTH_NAMES})\.?\s+(20\d{{2}})\b",
    re.IGNORECASE,
)
_MDY_WORD_RE = re.compile(
    rf"\b({_MONTH_NAMES})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(20\d{{2}})\b",
    re.IGNORECASE,
)


@dataclass
class DeadlineInfo:
    deadline: str | None = None
    status: str = UNKNOWN
    raw: str | None = None

    def to_dict(self) -> dict:
        return {"deadline": self.deadline, "status": self.status, "raw": self.raw}


def _safe_date(year: int, month: int, day: int) -> date | None:
    if year < 2000 or year > 2100:
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _expand_year(raw: str) -> int:
    value = int(raw)
    if value < 100:
        return 2000 + value
    return value


def parse_deadline_value(value: str | None) -> date | None:
    """Parse ISO, German numeric and month-name dates. Returns None if unsure."""
    raw = (value or "").strip()
    if not raw:
        return None

    match = _ISO_RE.search(raw)
    if match:
        return _safe_date(int(match.group(1)), int(match.group(2)), int(match.group(3)))

    match = _GERMAN_NUMERIC_RE.search(raw)
    if match:
        return _safe_date(
            _expand_year(match.group(3)),
            int(match.group(2)),
            int(match.group(1)),
        )

    match = _DMY_WORD_RE.search(raw)
    if match:
        month = _MONTHS.get(match.group(2).casefold().rstrip("."))
        if month:
            return _safe_date(int(match.group(3)), month, int(match.group(1)))

    match = _MDY_WORD_RE.search(raw)
    if match:
        month = _MONTHS.get(match.group(1).casefold().rstrip("."))
        if month:
            return _safe_date(int(match.group(3)), month, int(match.group(2)))

    return None


def deadline_status(
    deadline: str | date | None,
    *,
    today: date | None = None,
    closing_days: int = DEFAULT_CLOSING_DAYS,
) -> str:
    if deadline is None or (isinstance(deadline, str) and not deadline.strip()):
        return UNKNOWN
    parsed = deadline if isinstance(deadline, date) else parse_deadline_value(deadline)
    if parsed is None:
        return UNPARSEABLE
    current = today or date.today()
    if parsed < current:
        return EXPIRED
    if (parsed - current).days <= max(0, closing_days):
        return CLOSING_SOON
    return ACTIVE


def extract_deadline(
    text: str | None,
    *,
    today: date | None = None,
    closing_days: int = DEFAULT_CLOSING_DAYS,
    window: int = 90,
) -> DeadlineInfo:
    """Find a deadline marker and the date that belongs to it."""
    body = (text or "").strip()
    if not body:
        return DeadlineInfo()

    lowered = body.casefold()
    for marker in _MARKERS:
        start = lowered.find(marker)
        while start >= 0:
            snippet = body[start + len(marker) : start + len(marker) + window]
            # Date before the marker ("31.12.2026 Bewerbungsfrist") is also common.
            before = body[max(0, start - 40) : start]
            for candidate in (snippet, before):
                raw = _first_date(candidate)
                if raw:
                    parsed = parse_deadline_value(raw)
                    if parsed is None:
                        return DeadlineInfo(deadline=None, status=UNPARSEABLE, raw=raw)
                    return DeadlineInfo(
                        deadline=parsed.isoformat(),
                        status=deadline_status(
                            parsed, today=today, closing_days=closing_days
                        ),
                        raw=raw,
                    )
            start = lowered.find(marker, start + len(marker))
    return DeadlineInfo()


def _first_date(text: str) -> str | None:
    """Return the earliest date-like substring in the text."""
    candidates: list[tuple[int, str]] = []
    for pattern in (_ISO_RE, _GERMAN_NUMERIC_RE, _DMY_WORD_RE, _MDY_WORD_RE):
        match = pattern.search(text)
        if match:
            candidates.append((match.start(), match.group(0)))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def apply_deadlines(
    jobs: list[Job],
    *,
    descriptions: dict[tuple[str, str], str] | None = None,
    today: date | None = None,
    closing_days: int = DEFAULT_CLOSING_DAYS,
) -> dict[str, int]:
    """Set ``deadline``/``deadline_status`` on every job, in place."""
    counts = {
        ACTIVE: 0,
        CLOSING_SOON: 0,
        EXPIRED: 0,
        UNPARSEABLE: 0,
        UNKNOWN: 0,
    }
    descriptions = descriptions or {}
    for job in jobs:
        text = (
            descriptions.get(job.key())
            or job.description
            or job.posted_text
            or ""
        )
        info = extract_deadline(text, today=today, closing_days=closing_days)
        if info.deadline:
            job.deadline = info.deadline
        job.deadline_status = info.status
        counts[info.status] = counts.get(info.status, 0) + 1
        if info.status == EXPIRED:
            job.status = "expired"
            job.expired_reason = "deadline_passed"
    return counts


def sweep_saved_jobs(
    jobs: list[Job],
    *,
    descriptions: dict[tuple[str, str], str] | None = None,
    today: date | None = None,
    closing_days: int = DEFAULT_CLOSING_DAYS,
) -> list[dict]:
    """Return a serializable sweep report for the CLI/API."""
    apply_deadlines(
        jobs, descriptions=descriptions, today=today, closing_days=closing_days
    )
    return [
        {
            "source": job.source.value,
            "job_id": job.job_id,
            "company": job.company,
            "title": job.title,
            "deadline": job.deadline,
            "deadline_status": job.deadline_status,
            "checked_at": datetime.now().isoformat(timespec="seconds"),
        }
        for job in jobs
    ]
