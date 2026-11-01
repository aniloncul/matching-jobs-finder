"""PDF text-layer inspection.

An ATS reads the PDF's text layer, not the rendered page.  These helpers
answer the questions that matter for that text layer:

* how many pages, how much selectable text
* which fonts are embedded
* are German characters intact (no mojibake / ``(cid:)`` garbage)
* are date ranges written with ASCII hyphens instead of en/em dashes
* is the email address literal text

Geometry-based layout checks live in :mod:`jobscout.verification.ats`.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import pypdfium2 as pdfium

from jobscout.verification.text import normalize_text

Severity = Literal["critical", "warning"]

# Unicode dash code points that Word/LibreOffice insert in date ranges.
_UNICODE_DASHES = "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"
_DATE_RANGE_RE = re.compile(
    rf"(?<!\d)((?:19|20)\d{{2}})\s*([{_UNICODE_DASHES}])\s*((?:19|20)\d{{2}})(?!\d)"
)
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
# Context that makes a year-year range a quantity rather than a date range.
_QUANTITY_HINTS = (
    "€",
    "$",
    "eur",
    "usd",
    "%",
    "prozent",
    "percent",
    "req/s",
    "requests",
    "budget",
    "umsatz",
    "gehalt",
    "preis",
    "kosten",
    "revenue",
    "salary",
)
_MIN_TEXT_CHARS = 200


class PdfReadError(Exception):
    """Raised when a PDF cannot be opened or has no text layer."""


@dataclass(frozen=True)
class TextSpan:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    font: str = ""
    size: float = 0.0
    embedded: bool = False

    @property
    def center_x(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def center_y(self) -> float:
        return (self.y0 + self.y1) / 2


@dataclass
class PageInfo:
    width: float
    height: float
    text: str
    spans: list[TextSpan] = field(default_factory=list)
    image_area_ratio: float = 0.0
    raw_text: str = ""


@dataclass
class PdfDocumentInfo:
    path: str
    pages: list[PageInfo]

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def text(self) -> str:
        return "\n\f\n".join(page.text for page in self.pages)

    @property
    def raw_text(self) -> str:
        """Text with the original punctuation (dashes are not folded)."""
        return "\n\f\n".join(page.raw_text or page.text for page in self.pages)

    @property
    def spans(self) -> list[TextSpan]:
        return [span for page in self.pages for span in page.spans]


@dataclass
class PdfIssue:
    id: str
    severity: Severity
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"id": self.id, "severity": self.severity, "message": self.message}


@dataclass
class DateRangeIssue:
    raw: str
    line: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"raw": self.raw, "line": self.line, "message": self.message}


@dataclass
class PdfCheck:
    path: str
    pages: int
    text_length: int
    extractor: str
    issues: list[PdfIssue]
    date_ranges: list[DateRangeIssue]
    text: str

    @property
    def ok(self) -> bool:
        return not any(issue.severity == "critical" for issue in self.issues)

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "pages": self.pages,
            "text_length": self.text_length,
            "extractor": self.extractor,
            "ok": self.ok,
            "issues": [issue.to_dict() for issue in self.issues],
            "date_ranges": [item.to_dict() for item in self.date_ranges],
        }


def _clean_font_name(name: str | None) -> str:
    raw = (name or "").strip()
    # Embedded subsets are prefixed with "ABCDEF+".
    if "+" in raw:
        raw = raw.split("+", 1)[1]
    return raw


def read_pdf(path: str | Path) -> PdfDocumentInfo:
    """Read every page's text layer and text-object geometry."""
    source = Path(path)
    if not source.exists():
        raise PdfReadError(f"PDF not found: {source}")
    try:
        document = pdfium.PdfDocument(str(source))
    except Exception as exc:  # pragma: no cover - pdfium raises bare exceptions
        raise PdfReadError(f"Could not open PDF {source}: {exc}") from exc

    pages: list[PageInfo] = []
    try:
        for page in document:
            width, height = page.get_size()
            textpage = page.get_textpage()
            text = textpage.get_text_range() or ""
            spans: list[TextSpan] = []
            image_area = 0.0
            page_area = max(width * height, 1.0)
            for obj in page.get_objects(textpage=textpage):
                try:
                    bounds = obj.get_bounds()
                except Exception:
                    continue
                if isinstance(obj, pdfium.PdfTextObj):
                    try:
                        content = obj.extract() or ""
                    except Exception:
                        content = ""
                    if not content:
                        continue
                    try:
                        font = obj.get_font()
                        font_name = _clean_font_name(font.get_base_name())
                        embedded = bool(font.is_embedded)
                    except Exception:
                        font_name, embedded = "", False
                    try:
                        size = float(obj.get_font_size())
                    except Exception:
                        size = 0.0
                    spans.append(
                        TextSpan(
                            text=content,
                            x0=float(bounds[0]),
                            y0=float(bounds[1]),
                            x1=float(bounds[2]),
                            y1=float(bounds[3]),
                            font=font_name,
                            size=size,
                            embedded=embedded,
                        )
                    )
                elif isinstance(obj, pdfium.PdfImage):
                    x0, y0, x1, y1 = bounds
                    area = max(0.0, (x1 - x0)) * max(0.0, (y1 - y0))
                    image_area = max(image_area, area / page_area)
                else:  # pragma: no cover - other object types are irrelevant
                    continue
            pages.append(
                PageInfo(
                    width=float(width),
                    height=float(height),
                    text=normalize_text(text),
                    spans=spans,
                    image_area_ratio=min(image_area, 1.0),
                    raw_text=unicodedata.normalize("NFC", text),
                )
            )
    finally:
        try:
            document.close()
        except Exception:
            pass

    if not pages:
        raise PdfReadError(f"PDF has no pages: {source}")
    return PdfDocumentInfo(path=str(source), pages=pages)


def check_ascii_dates(text: str) -> list[DateRangeIssue]:
    """Find year ranges joined by a Unicode dash.

    ``2019–2023`` is the default output of every word processor and is the
    exact shape that makes ATS date importers drop an education entry.  The
    fix is an ASCII hyphen.  Genuine quantity ranges (``600-1.000 €``,
    ``2000-5000 req/s``) are ignored.
    """
    issues: list[DateRangeIssue] = []
    for line in unicodedata.normalize("NFC", text).splitlines():
        lowered = line.casefold()
        if any(hint in lowered for hint in _QUANTITY_HINTS):
            continue
        for match in _DATE_RANGE_RE.finditer(line):
            raw = match.group(0)
            issues.append(
                DateRangeIssue(
                    raw=raw,
                    line=line.strip()[:160],
                    message=(
                        f"Date range '{raw}' uses a Unicode dash. "
                        "Write it with a single ASCII hyphen, e.g. "
                        f"'{match.group(1)}-{match.group(3)}'."
                    ),
                )
            )
    return issues


def check_pdf(
    path: str | Path,
    *,
    expected_pages: int | None = None,
    require_email: bool = False,
) -> PdfCheck:
    """Mechanical checks on the PDF text layer."""
    document = read_pdf(path)
    text = document.text
    issues: list[PdfIssue] = []

    if document.page_count == 0:
        issues.append(PdfIssue("no-pages", "critical", "The PDF has no pages."))
    if expected_pages is not None and document.page_count != expected_pages:
        issues.append(
            PdfIssue(
                "page-count",
                "critical",
                f"Expected {expected_pages} page(s), found {document.page_count}.",
            )
        )
    if len(text.strip()) < _MIN_TEXT_CHARS:
        issues.append(
            PdfIssue(
                "no-text-layer",
                "critical",
                f"Only {len(text.strip())} characters of selectable text. "
                "The PDF is probably a scan or the text is baked into an image.",
            )
        )
    if "\ufffd" in text:
        issues.append(
            PdfIssue(
                "replacement-char",
                "critical",
                "The text layer contains U+FFFD replacement characters. "
                "A font is not embedded or the encoding is broken.",
            )
        )
    if "(cid:" in text:
        issues.append(
            PdfIssue(
                "cid-garbage",
                "critical",
                "The text layer contains '(cid:...)' glyph references instead of "
                "readable characters. Use an embedded Unicode font.",
            )
        )
    if require_email and not _EMAIL_RE.search(text):
        issues.append(
            PdfIssue(
                "email-not-literal",
                "critical",
                "No literal email address found in the text layer. "
                "Icon-only or hyperlink-only contact details are invisible to an ATS.",
            )
        )

    date_ranges = check_ascii_dates(document.raw_text)
    for item in date_ranges:
        issues.append(PdfIssue("unicode-date-range", "critical", item.message))

    return PdfCheck(
        path=str(path),
        pages=document.page_count,
        text_length=len(text.strip()),
        extractor="pypdfium2",
        issues=issues,
        date_ranges=date_ranges,
        text=text,
    )
