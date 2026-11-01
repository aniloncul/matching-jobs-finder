"""Deterministic verification helpers (no LLM, no network)."""

from jobscout.verification.ats import AtsReport, analyze_pdf, score_document
from jobscout.verification.facts import FactCheckResult, check_facts
from jobscout.verification.keywords import (
    Keyword,
    KeywordCoverage,
    coverage,
    extract_keywords,
)
from jobscout.verification.pdf import (
    PdfCheck,
    PdfDocumentInfo,
    PdfReadError,
    check_ascii_dates,
    check_pdf,
    read_pdf,
)
from jobscout.verification.text import (
    contains_keyword,
    fold_for_match,
    normalize_text,
)

__all__ = [
    "AtsReport",
    "FactCheckResult",
    "Keyword",
    "KeywordCoverage",
    "PdfCheck",
    "PdfDocumentInfo",
    "PdfReadError",
    "analyze_pdf",
    "check_ascii_dates",
    "check_facts",
    "check_pdf",
    "contains_keyword",
    "coverage",
    "extract_keywords",
    "fold_for_match",
    "normalize_text",
    "read_pdf",
    "score_document",
]
