from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone


_RELATIVE = re.compile(
    r"(?P<count>\d+)\s*\+?\s*(?P<unit>minute|hour|day|week|month|year)s?\s+ago",
    re.IGNORECASE,
)
_RELATIVE_DE = re.compile(
    r"vor\s+(?P<count>\d+)\s*\+?\s*"
    r"(?P<unit>minute|stunde|tag|woche|monat|jahr)(?:n|en)?",
    re.IGNORECASE,
)


def parse_date_value(value: str | None) -> str | None:
    if not value:
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.fromisoformat(raw[:10])
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.date().isoformat()


def parse_relative_date(
    text: str | None,
    now: datetime | None = None,
) -> tuple[str | None, bool]:
    if not text:
        return None, False

    value = " ".join(text.lower().split())
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)

    if "just posted" in value or "today" in value or "heute" in value:
        return current.date().isoformat(), True
    if "yesterday" in value or "gestern" in value:
        return (current - timedelta(days=1)).date().isoformat(), True

    match = _RELATIVE.search(value) or _RELATIVE_DE.search(value)
    if not match:
        return None, False

    count = int(match.group("count"))
    unit = match.group("unit").lower()
    if unit in {"minute", "minuten"}:
        delta = timedelta(minutes=count)
    elif unit in {"hour", "stunde", "stunden"}:
        delta = timedelta(hours=count)
    elif unit in {"day", "tag", "tage", "tagen"}:
        delta = timedelta(days=count)
    elif unit in {"week", "woche", "wochen"}:
        delta = timedelta(weeks=count)
    elif unit in {"month", "monat", "monate", "monaten"}:
        delta = timedelta(days=count * 30)
    else:
        delta = timedelta(days=count * 365)

    return (current - delta).date().isoformat(), True


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(value.replace("\xa0", " ").split()).strip()
