from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape


TEX_ESCAPES = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}

GERMAN_MONTHS = (
    "Januar",
    "Februar",
    "März",
    "April",
    "Mai",
    "Juni",
    "Juli",
    "August",
    "September",
    "Oktober",
    "November",
    "Dezember",
)

ENGLISH_MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


GERMAN_NUMBER_WORDS = {
    1: "einen",
    2: "zwei",
    3: "drei",
    4: "vier",
    5: "fünf",
    6: "sechs",
    7: "sieben",
    8: "acht",
    9: "neun",
    10: "zehn",
    11: "elf",
    12: "zwölf",
}

ENGLISH_NUMBER_WORDS = {
    1: "one",
    2: "two",
    3: "three",
    4: "four",
    5: "five",
    6: "six",
    7: "seven",
    8: "eight",
    9: "nine",
    10: "ten",
    11: "eleven",
    12: "twelve",
}

IMMEDIATE_WORDS = {"sofort", "immediately", "asap", "now", "umgehend", "kurzfristig"}

# Free-text start values that already carry a preposition, for example
# "nach Verhandlung" or "ab sofort". They cannot be prefixed with "Ab".
_GERMAN_PREPOSITIONS = ("nach ", "ab ", "seit ", "per ", "zum ", "vom ")

_ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_ISO_MONTH = re.compile(r"^(\d{4})-(\d{2})$")
_DOTTED_DATE = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$")


def tex_escape(value: object) -> str:
    text = str(value)
    return "".join(TEX_ESCAPES.get(char, char) for char in text)


def format_date(language: str, today: date | None = None) -> str:
    current = today or date.today()
    if language == "en":
        return f"{current.day} {ENGLISH_MONTHS[current.month - 1]} {current.year}"
    return f"{current.day}. {GERMAN_MONTHS[current.month - 1]} {current.year}"


def _parse_start_date(value: str) -> tuple[int, int, int | None] | None:
    """Parse the supported start date formats into (year, month, day)."""
    text = value.strip()
    match = _ISO_DATE.match(text)
    if match:
        year, month, day = (int(part) for part in match.groups())
        if 1 <= month <= 12:
            return year, month, day
        return None
    match = _ISO_MONTH.match(text)
    if match:
        year, month = (int(part) for part in match.groups())
        if 1 <= month <= 12:
            return year, month, None
        return None
    match = _DOTTED_DATE.match(text)
    if match:
        day, month, year = (int(part) for part in match.groups())
        if 1 <= month <= 12 and 1 <= day <= 31:
            return year, month, day
    return None


def format_start_date(value: str, language: str) -> str:
    """Render a start date as a readable date, or return the raw text."""
    parsed = _parse_start_date(value)
    if parsed is None:
        return value.strip()
    year, month, day = parsed
    if language == "en":
        if day:
            return f"{day} {ENGLISH_MONTHS[month - 1]} {year}"
        return f"{ENGLISH_MONTHS[month - 1]} {year}"
    if day:
        return f"{day}. {GERMAN_MONTHS[month - 1]} {year}"
    return f"{GERMAN_MONTHS[month - 1]} {year}"


def notice_period_sentence(months: int, language: str) -> str:
    """Build the notice period sentence in the language of the letter."""
    count = max(1, int(months))
    if language == "en":
        word = ENGLISH_NUMBER_WORDS.get(count, str(count))
        plural = "month" if count == 1 else "months"
        return f"My notice period is {word} {plural}."
    word = GERMAN_NUMBER_WORDS.get(count, str(count))
    plural = "Monat" if count == 1 else "Monate"
    return f"Meine Kündigungsfrist beträgt {word} {plural}."


def availability_sentence(value: str, language: str) -> str:
    """Build the sentence that states when the applicant can start."""
    text = value.strip()
    if not text:
        return ""
    if text.lower() in IMMEDIATE_WORDS:
        if language == "en":
            return "I am available to start immediately."
        return "Ich bin sofort verfügbar."
    parsed = _parse_start_date(text)
    if parsed is None:
        if language == "en":
            return f"I am available to start {text}."
        if text.lower().startswith(_GERMAN_PREPOSITIONS):
            return f"Ich bin {text} verfügbar."
        return f"Ab {text} bin ich verfügbar."
    year, month, day = parsed
    formatted = format_start_date(text, language)
    if language == "en":
        if day:
            return f"I am available to start on {formatted}."
        return f"I am available to start in {formatted}."
    if day:
        return f"Ab dem {formatted} bin ich verfügbar."
    return f"Ab {formatted} bin ich verfügbar."


def confidentiality_sentence(language: str) -> str:
    """Build the sentence that asks for the application to be handled confidentially."""
    if language == "en":
        return "Please treat my application confidentially."
    return "Bitte behandeln Sie meine Bewerbung vertraulich."


def render_letter(template_dir: str | Path, context: dict) -> str:
    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=select_autoescape(enabled_extensions=()),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["tex"] = tex_escape
    template = env.get_template("cover_letter.tex.j2")
    return template.render(**context)
