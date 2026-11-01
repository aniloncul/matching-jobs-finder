"""Two-stage ATS analysis.

Stage 1 (this module) is purely structural: it reads the PDF text layer and
the text-object geometry and produces a 0-100 score with fixed weights.

Stage 2 lives in :mod:`jobscout.verification.keywords` and measures how much
of the job description vocabulary actually appears in the document.  The two
numbers are deliberately kept apart: a structurally perfect CV can still miss
every keyword, and a keyword-stuffed CV can still be unparseable.

The checker is read-only, deterministic, offline and advisory.  It never
blocks generation by itself; the caller decides what to do with the score.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from jobscout.verification.pdf import (
    PdfDocumentInfo,
    PdfIssue,
    PageInfo,
    check_ascii_dates,
    read_pdf,
)
from jobscout.verification.text import fold_for_match, text_lines

Severity = Literal["critical", "warning"]
Grade = Literal["A", "B", "C", "D", "F"]

# Weights sum to 100.  Mirrors the career-ops rubric so that scores are
# comparable across tools.
WEIGHTS = {
    "text": 15,
    "sections": 20,
    "contact": 15,
    "layout": 20,
    "images": 10,
    "fonts": 10,
    "charset": 5,
    "hidden": 5,
}
DEFAULT_MIN_SCORE = 70

# Headings are matched with a lossy fold, so umlauts and punctuation do not
# matter.  A localized heading must never be treated as "missing" when the CV
# language is German.
SECTION_HEADINGS: dict[str, tuple[str, ...]] = {
    "summary": ("zusammenfassung", "profil", "professional summary", "summary", "profile"),
    "experience": (
        "berufserfahrung",
        "beruflicher werdegang",
        "work experience",
        "professional experience",
        "experience",
    ),
    "education": ("ausbildung", "bildung", "education", "akademische ausbildung"),
    "skills": (
        "kenntnisse",
        "skills",
        "kompetenzen",
        "core competencies",
        "technical skills",
        "faehigkeiten",
    ),
    "certifications": ("zertifikate", "zertifizierungen", "certifications"),
    "projects": ("projekte", "projects"),
    "languages": ("sprachen", "languages"),
    "internships": ("praktika", "internships"),
    "awards": ("auszeichnungen", "awards", "honors", "ehrungen"),
}

_STANDARD_FONTS = {
    "helvetica",
    "arial",
    "times",
    "timesnewroman",
    "courier",
    "calibri",
    "cambria",
    "georgia",
    "verdana",
    "tahoma",
    "liberation",
    "dejavu",
    "noto",
    "lato",
    "roboto",
    "opensans",
    "sourcesans",
    "raleway",
}
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
_PHONE_RE = re.compile(r"(?:\+\d{1,3}[\s\-/]?)?(?:\(?\d{2,5}\)?[\s\-/]?)?\d[\d\s\-/]{5,}\d")
_MOJIBAKE_RE = re.compile("[\u00c2\u00c3][\u0080-\u00bf]|\u00e2\u20ac|\ufffd")
_GRADE_BANDS: tuple[tuple[int, Grade], ...] = (
    (90, "A"),
    (80, "B"),
    (70, "C"),
    (60, "D"),
    (0, "F"),
)


@dataclass
class CheckResult:
    id: str
    label: str
    weight: int
    points: int
    severity: Severity | None = None
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.severity != "critical" and self.points == self.weight

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "weight": self.weight,
            "points": self.points,
            "ok": self.ok,
            "severity": self.severity,
            "message": self.message,
        }


@dataclass
class AtsReport:
    path: str
    score: int
    grade: Grade
    passed: bool
    pages: int
    text_length: int
    columns: int
    checks: list[CheckResult]
    issues: list[PdfIssue] = field(default_factory=list)
    text_excerpt: str = ""

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "score": self.score,
            "grade": self.grade,
            "passed": self.passed,
            "pages": self.pages,
            "text_length": self.text_length,
            "columns": self.columns,
            "checks": [check.to_dict() for check in self.checks],
            "issues": [issue.to_dict() for issue in self.issues],
            "text_excerpt": self.text_excerpt,
        }


def _grade(score: int) -> Grade:
    for floor, grade in _GRADE_BANDS:
        if score >= floor:
            return grade
    return "F"


def _detect_sections(text: str) -> tuple[set[str], list[str]]:
    found: set[str] = set()
    headings: list[str] = []
    for line in text_lines(text):
        if len(line) > 80:
            continue
        folded = fold_for_match(line)
        for category, variants in SECTION_HEADINGS.items():
            if any(folded == variant or folded.startswith(f"{variant} ") for variant in variants):
                found.add(category)
                headings.append(line)
                break
    return found, headings


def _page_columns(page: PageInfo) -> int:
    spans = [span for span in page.spans if span.text.strip() and span.size >= 4]
    if len(spans) < 8:
        return 1
    split = page.width * 0.45
    left = [span for span in spans if span.center_x < split]
    right = [span for span in spans if span.center_x >= split]
    if len(left) < len(spans) * 0.15 or len(right) < len(spans) * 0.15:
        return 1

    bins = 20
    height = max(page.height, 1.0)
    has_left = [False] * bins
    has_right = [False] * bins
    for span in spans:
        index = min(bins - 1, max(0, int(span.center_y / height * bins)))
        if span.center_x < split:
            has_left[index] = True
        else:
            has_right[index] = True
    shared = sum(1 for left_bin, right_bin in zip(has_left, has_right) if left_bin and right_bin)
    occupied = sum(1 for left_bin, right_bin in zip(has_left, has_right) if left_bin or right_bin)
    if occupied and shared / occupied >= 0.5:
        return 2
    return 1


def detect_columns(document: PdfDocumentInfo) -> int:
    return max((_page_columns(page) for page in document.pages), default=1)


def _is_standard_font(name: str) -> bool:
    folded = re.sub(r"[^a-z]", "", (name or "").casefold())
    return any(folded.startswith(font) for font in _STANDARD_FONTS)


def _has_phone(text: str) -> bool:
    for match in _PHONE_RE.finditer(text):
        raw = match.group(0).strip()
        # A year range is not a phone number.
        if re.fullmatch(r"(?:19|20)\d{2}\s*[-\u2010-\u2015]\s*(?:19|20)\d{2}", raw):
            continue
        digits = re.sub(r"\D", "", raw)
        if len(digits) >= 8:
            return True
    return False


def score_document(
    document: PdfDocumentInfo,
    *,
    expected_pages: int | None = None,
    language: str = "de",
    min_score: int = DEFAULT_MIN_SCORE,
) -> AtsReport:
    """Score an already-read PDF document.  Pure function, easy to test."""
    text = document.text
    text_length = len(text.strip())
    checks: list[CheckResult] = []
    issues: list[PdfIssue] = []

    # --- text layer -----------------------------------------------------
    if text_length >= 300:
        points, severity, message = WEIGHTS["text"], None, ""
    elif text_length >= 100:
        points = WEIGHTS["text"] // 2
        severity, message = "warning", "Very little selectable text; the PDF may be partially image-based."
    else:
        points, severity = 0, "critical"
        message = "No usable text layer. An ATS sees an empty document."
    checks.append(CheckResult("text", "Selectable text", WEIGHTS["text"], points, severity, message))
    if severity == "critical":
        issues.append(PdfIssue("no-text-layer", "critical", message))

    # --- section headings ----------------------------------------------
    categories, headings = _detect_sections(text)
    if len(categories) >= 3:
        points, severity, message = WEIGHTS["sections"], None, ""
    elif len(categories) == 2:
        points = WEIGHTS["sections"] // 2
        severity, message = "warning", "Only two standard section headings found."
    else:
        points, severity = 0, "warning"
        message = "No standard section headings found (e.g. Berufserfahrung, Ausbildung, Kenntnisse)."
    checks.append(
        CheckResult("sections", "Standard section headings", WEIGHTS["sections"], points, severity, message)
    )

    # --- contact details -------------------------------------------------
    has_email = bool(_EMAIL_RE.search(text))
    has_phone = _has_phone(text)
    points = (11 if has_email else 0) + (4 if has_phone else 0)
    severity = None if has_email else "warning"
    message = ""
    if not has_email:
        message = "Email address is not literal text in the body."
    elif not has_phone:
        message = "No phone number detected in the body."
    checks.append(
        CheckResult("contact", "Contact details in body", WEIGHTS["contact"], points, severity, message)
    )

    # --- layout ----------------------------------------------------------
    columns = detect_columns(document)
    if columns == 1:
        points, severity, message = WEIGHTS["layout"], None, ""
    else:
        points, severity = 0, "warning"
        message = "Two-column layout detected. Many parsers read across columns."
    checks.append(CheckResult("layout", "Single column", WEIGHTS["layout"], points, severity, message))

    # --- images ----------------------------------------------------------
    max_image = max((page.image_area_ratio for page in document.pages), default=0.0)
    if max_image < 0.15:
        points, severity, message = WEIGHTS["images"], None, ""
    elif text_length < 200 and max_image >= 0.5:
        points, severity = 0, "critical"
        message = f"{max_image:.0%} of a page is an image and there is barely any text."
    else:
        points = WEIGHTS["images"] // 2
        severity, message = "warning", f"Large image covers {max_image:.0%} of a page; check that no CV text is baked into it."
    checks.append(CheckResult("images", "No text baked into images", WEIGHTS["images"], points, severity, message))

    # --- fonts -----------------------------------------------------------
    fonts = {span.font for span in document.spans if span.font}
    unsafe = sorted(
        font
        for font in fonts
        if not _is_standard_font(font)
        and not any(span.embedded for span in document.spans if span.font == font)
    )
    if not fonts:
        points, severity, message = 5, "warning", "Could not read font metadata."
    elif not unsafe:
        points, severity, message = WEIGHTS["fonts"], None, ""
    else:
        points = 5
        severity, message = "warning", f"Custom font(s) not embedded: {', '.join(unsafe[:5])}."
    checks.append(CheckResult("fonts", "Embeddable fonts", WEIGHTS["fonts"], points, severity, message))

    # --- charset ---------------------------------------------------------
    mojibake = bool(_MOJIBAKE_RE.search(text))
    if mojibake:
        points, severity = 0, "critical"
        message = "Mojibake or replacement characters found (e.g. 'Ã¤' instead of 'ä')."
    else:
        points, severity, message = WEIGHTS["charset"], None, ""
    checks.append(CheckResult("charset", "UTF-8 text intact", WEIGHTS["charset"], points, severity, message))
    if mojibake:
        issues.append(PdfIssue("charset", "critical", message))

    # --- hidden text / keyword stuffing ---------------------------------
    tiny = [span for span in document.spans if span.text.strip() and span.size and span.size < 4]
    if tiny:
        points, severity = 0, "critical"
        message = f"{len(tiny)} text object(s) smaller than 4pt. Hidden text is treated as keyword stuffing."
    else:
        points, severity, message = WEIGHTS["hidden"], None, ""
    checks.append(CheckResult("hidden", "No hidden text", WEIGHTS["hidden"], points, severity, message))
    if tiny:
        issues.append(PdfIssue("hidden-text", "critical", message))

    # --- structural issues not folded into the score ----------------------
    if expected_pages is not None and document.page_count != expected_pages:
        message = f"Expected {expected_pages} page(s), found {document.page_count}."
        issues.append(PdfIssue("page-count", "critical", message))
        checks.append(CheckResult("page-count", "Page count", 0, 0, "critical", message))

    date_ranges = check_ascii_dates(document.raw_text)
    for item in date_ranges:
        issues.append(PdfIssue("unicode-date-range", "critical", item.message))
    if date_ranges:
        checks.append(
            CheckResult(
                "date-ranges",
                "ASCII date ranges",
                0,
                0,
                "critical",
                f"{len(date_ranges)} date range(s) use a Unicode dash.",
            )
        )

    score = sum(check.points for check in checks)
    passed = score >= min_score and not any(issue.severity == "critical" for issue in issues)
    return AtsReport(
        path=document.path,
        score=score,
        grade=_grade(score),
        passed=passed,
        pages=document.page_count,
        text_length=text_length,
        columns=columns,
        checks=checks,
        issues=issues,
        text_excerpt=text.strip()[:700],
    )


def analyze_pdf(
    path: str | Path,
    *,
    expected_pages: int | None = None,
    language: str = "de",
    min_score: int = DEFAULT_MIN_SCORE,
) -> AtsReport:
    document = read_pdf(path)
    return score_document(
        document,
        expected_pages=expected_pages,
        language=language,
        min_score=min_score,
    )


def format_report(report: AtsReport) -> str:
    lines = [
        f"ATS score: {report.score}/100 (grade {report.grade}) "
        f"- {'PASS' if report.passed else 'FAIL'}",
        f"Pages: {report.pages} | text: {report.text_length} chars | columns: {report.columns}",
        "",
    ]
    for check in report.checks:
        marker = "OK  " if check.ok else ("FAIL" if check.severity == "critical" else "WARN")
        lines.append(f"[{marker}] {check.label}: {check.points}/{check.weight}")
        if check.message and not check.ok:
            lines.append(f"       {check.message}")
    if report.issues:
        lines.append("")
        lines.append("Blocking issues:")
        lines.extend(f"- {issue.message}" for issue in report.issues)
    return "\n".join(lines)
