"""Unicode normalisation shared by the ATS, keyword and fact checks.

Two different folds are needed:

* :func:`normalize_text` keeps German letters intact and only replaces
  typographic punctuation (curly quotes, en/em dashes, non-breaking spaces)
  with their ASCII equivalents.  This is the text shown to the user and the
  text that must survive a PDF round-trip.
* :func:`fold_for_match` is a lossy fold used for *comparisons* only.  It
  casefolds, transliterates ``ä ö ü ß`` to ``ae oe ue ss`` and strips every
  remaining diacritic so that ``Müller`` matches ``Mueller`` and
  ``Datenanalyse`` matches ``daten analyse``.
"""

from __future__ import annotations

import re
import unicodedata

# Typographic punctuation -> ASCII.  German umlauts are deliberately untouched.
_TYPOGRAPHIC = {
    0x2018: "'",
    0x2019: "'",
    0x201A: "'",
    0x201B: "'",
    0x2032: "'",
    0x201C: '"',
    0x201D: '"',
    0x201E: '"',
    0x201F: '"',
    0x2033: '"',
    0x00AB: '"',
    0x00BB: '"',
    0x2039: "'",
    0x203A: "'",
    0x2010: "-",
    0x2011: "-",
    0x2012: "-",
    0x2013: "-",
    0x2014: "-",
    0x2015: "-",
    0x2212: "-",
    0x00A0: " ",
    0x2007: " ",
    0x2009: " ",
    0x202F: " ",
    0x00AD: "",  # soft hyphen
}

_TYPOGRAPHIC_TABLE = str.maketrans(_TYPOGRAPHIC)
_SPACES = re.compile(r"[ \t\f\v]+")
_MULTI_NEWLINE = re.compile(r"\n{3,}")


def normalize_text(value: str | None) -> str:
    """Normalise typography without destroying German characters."""
    if not value:
        return ""
    text = unicodedata.normalize("NFC", str(value))
    text = text.translate(_TYPOGRAPHIC_TABLE)
    text = _SPACES.sub(" ", text)
    text = _MULTI_NEWLINE.sub("\n\n", text)
    return text.strip()


def fold_for_match(value: str | None) -> str:
    """Lossy fold used for keyword/claim comparisons.

    ``Müller`` -> ``mueller``, ``Straße`` -> ``strasse``,
    ``Daten-Analyse`` -> ``daten analyse``.
    """
    text = normalize_text(value).casefold()
    text = text.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
    text = text.replace("ß", "ss")
    text = "".join(
        char
        for char in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(char)
    )
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def contains_keyword(text: str | None, keyword: str | None) -> bool:
    """Whole-phrase match on the folded representation."""
    haystack = fold_for_match(text)
    needle = fold_for_match(keyword)
    if not needle:
        return False
    return f" {needle} " in f" {haystack} "


def count_keyword(text: str | None, keyword: str | None) -> int:
    """Count whole-phrase occurrences on the folded representation."""
    haystack = f" {fold_for_match(text)} "
    needle = fold_for_match(keyword)
    if not needle:
        return 0
    return haystack.count(f" {needle} ")


def text_lines(value: str | None) -> list[str]:
    return [line.strip() for line in normalize_text(value).splitlines() if line.strip()]
