"""Fact gate: verify generated prose against the candidate's evidence.

This is the deterministic half of the anti-hallucination design used by both
reference repos.  The model drafts; this module decides whether the draft is
allowed to become a PDF.

Verdicts:

* ``pass``  - every claim is supported by the evidence text
* ``warn``  - advisory issues only (unit mismatch, scope inflation, coverage gap)
* ``block`` - an invented metric, an unsupported tool, or a forbidden phrase

A ``block`` must stop PDF generation.  Do not silently rewrite the text with
the model: either remove the claim or ask the candidate for evidence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal, Sequence

from jobscout.verification.text import (
    contains_keyword,
    fold_for_match,
    normalize_text,
)

Verdict = Literal["pass", "warn", "block"]

_NUMBER = r"\d+(?:[.,]\d+)?"
_METRIC_RE = re.compile(
    rf"(?P<prefix>[€$])\s?(?P<pnum>{_NUMBER})"
    rf"|(?P<num>{_NUMBER})\s?(?P<unit>%|Prozent|percent|€|\$|EUR|USD|Tsd\.?|k(?![A-Za-z])|Mio\.?|Millionen?|million|x(?![A-Za-z])|mal(?![A-Za-z]))"
    rf"|(?P<cnum>\d+)\s+(?P<cunit>Teams?|Kunden|Kundinnen|Mitarbeiter(?:innen)?|Stunden|Tagen?|Wochen?|Monaten?|Jahren?|Jahre|Projekte?|Standorte|Filialen|Nutzer(?:innen)?|Kandidaten|hours?|days?|weeks?|months?|years?|projects?|customers?|clients?|users?)",
    re.IGNORECASE,
)
_WORD_RE = re.compile(r"[A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß0-9+#.\-]{4,}")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+|\n+")

DEFAULT_FORBIDDEN_PHRASES = (
    "patentiert",
    "patented",
    "fortune 500",
    "weltmarktführer",
    "weltmarktfuehrer",
    "100% sicher",
)

# Only unambiguous product/tool names.  Short or overloaded tokens ("go",
# "r", "bi", "qa", "ci", "excel", "spring") are deliberately excluded.
TECH_FACT_TERMS = {
    "python", "java", "javascript", "typescript", "django", "flask", "fastapi",
    "docker", "kubernetes", "k8s", "terraform", "ansible", "postgresql",
    "postgres", "mysql", "mongodb", "redis", "elasticsearch", "kafka",
    "rabbitmq", "jenkins", "n8n", "zapier", "airflow", "langchain", "pytorch",
    "tensorflow", "sklearn", "pandas", "numpy", "tableau", "powerbi",
    "salesforce", "hubspot", "figma", "jira", "confluence", "graphql", "grpc",
    "microservices", "golang", "rust", "kotlin", "swift", "matlab", "opencv",
    "huggingface", "ollama", "snowflake", "databricks", "selenium",
    "playwright", "devops", "mlops", "grafana", "prometheus", "dbt", "looker",
    "qlik", "vba", "sap",
}

_STRONG_VERBS = (
    "leitete", "fuehrte", "verantwortete", "baute", "entwickelte", "konzipierte",
    "implementierte", "entwarf", "gruendete", "skalierte", "optimierte",
    "managed", "led", "built", "developed", "designed", "implemented",
    "scaled", "optimized", "owned",
)
_WEAK_VERBS = (
    "beteiligte", "unterstuetzte", "wirkte", "nutzte", "verwendete",
    "arbeitete", "assisted", "supported", "used", "contributed", "helped",
    "participated",
)


@dataclass(frozen=True)
class MetricClaim:
    raw: str
    value: float | None
    unit: str

    @property
    def key(self) -> tuple[float | None, str]:
        return (self.value, self.unit)


@dataclass
class FactCheckResult:
    verdict: Verdict
    invented_metrics: list[str] = field(default_factory=list)
    unsupported_facts: list[str] = field(default_factory=list)
    advisory_facts: list[str] = field(default_factory=list)
    forbidden_phrases: list[str] = field(default_factory=list)
    scope_issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def blockers(self) -> list[str]:
        messages: list[str] = []
        messages.extend(f"Unverified metric: {item}" for item in self.invented_metrics)
        messages.extend(f"Unsupported fact: {item}" for item in self.unsupported_facts)
        messages.extend(f"Forbidden phrase: {item}" for item in self.forbidden_phrases)
        return messages

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "invented_metrics": self.invented_metrics,
            "unsupported_facts": self.unsupported_facts,
            "advisory_facts": self.advisory_facts,
            "forbidden_phrases": self.forbidden_phrases,
            "scope_issues": self.scope_issues,
            "warnings": self.warnings,
            "blockers": self.blockers,
        }


def _parse_number(raw: str) -> float | None:
    value = raw.replace(" ", "")
    if "," in value and "." in value:
        # German: 1.234,56 -> 1234.56
        value = value.replace(".", "").replace(",", ".")
    elif "," in value:
        value = value.replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(?:\.\d{3})+", value):
        value = value.replace(".", "")
    try:
        return float(value)
    except ValueError:
        return None


_COUNT_UNITS = {
    "team": "team",
    "teams": "team",
    "kunde": "kunde",
    "kunden": "kunde",
    "kundin": "kunde",
    "kundinnen": "kunde",
    "mitarbeiter": "mitarbeiter",
    "mitarbeiterin": "mitarbeiter",
    "mitarbeiterinnen": "mitarbeiter",
    "stunde": "stunde",
    "stunden": "stunde",
    "tag": "tag",
    "tage": "tag",
    "tagen": "tag",
    "woche": "woche",
    "wochen": "woche",
    "monat": "monat",
    "monate": "monat",
    "monaten": "monat",
    "jahr": "jahr",
    "jahre": "jahr",
    "jahren": "jahr",
    "projekt": "projekt",
    "projekte": "projekt",
    "standort": "standort",
    "standorte": "standort",
    "filiale": "filiale",
    "filialen": "filiale",
    "nutzer": "nutzer",
    "nutzerin": "nutzer",
    "nutzerinnen": "nutzer",
    "kandidat": "kandidat",
    "kandidaten": "kandidat",
    "hour": "stunde",
    "hours": "stunde",
    "day": "tag",
    "days": "tag",
    "week": "woche",
    "weeks": "woche",
    "month": "monat",
    "months": "monat",
    "year": "jahr",
    "years": "jahr",
    "project": "projekt",
    "projects": "projekt",
    "customer": "kunde",
    "customers": "kunde",
    "client": "kunde",
    "clients": "kunde",
    "user": "nutzer",
    "users": "nutzer",
}


def _canonical_unit(raw: str) -> str:
    folded = fold_for_match(raw)
    if folded in {"prozent", "percent"}:
        return "%"
    if folded in {"eur"}:
        return "eur"
    if folded in {"usd"}:
        return "usd"
    if folded in {"tsd", "tsd."}:
        return "k"
    if folded in {"mio", "mio.", "million", "millionen"}:
        return "m"
    if folded in {"x", "mal"}:
        return "x"
    return _COUNT_UNITS.get(folded, folded)


def extract_metrics(text: str) -> list[MetricClaim]:
    claims: list[MetricClaim] = []
    for match in _METRIC_RE.finditer(normalize_text(text)):
        if match.group("pnum"):
            raw = match.group(0)
            claims.append(
                MetricClaim(raw=raw, value=_parse_number(match.group("pnum")), unit="eur" if match.group("prefix") == "€" else "usd")
            )
        elif match.group("num"):
            raw = match.group(0)
            claims.append(
                MetricClaim(
                    raw=raw,
                    value=_parse_number(match.group("num")),
                    unit=_canonical_unit(match.group("unit")),
                )
            )
        else:
            raw = match.group(0)
            claims.append(
                MetricClaim(
                    raw=raw,
                    value=_parse_number(match.group("cnum")),
                    unit=_canonical_unit(match.group("cunit")),
                )
            )
    return claims


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in _SENTENCE_RE.split(normalize_text(text)) if part.strip()]


def _content_words(text: str) -> set[str]:
    words = set()
    for match in _WORD_RE.finditer(normalize_text(text)):
        folded = fold_for_match(match.group(0))
        if len(folded) >= 5:
            words.add(folded)
    return words


def _scope_issues(letter: str, evidence: str) -> list[str]:
    """Flag strong claims when the evidence only supports a weak role."""
    evidence_sentences = _sentences(evidence)
    issues: list[str] = []
    seen: set[tuple[str, ...]] = set()
    for sentence in _sentences(letter):
        folded = fold_for_match(sentence)
        strong = [verb for verb in _STRONG_VERBS if verb in folded]
        if not strong:
            continue
        words = _content_words(sentence)
        if not words:
            continue
        best: tuple[int, str] | None = None
        for candidate in evidence_sentences:
            candidate_folded = fold_for_match(candidate)
            overlap = len(words & _content_words(candidate))
            if overlap >= 2 and (best is None or overlap > best[0]):
                best = (overlap, candidate_folded)
        if best is None:
            continue
        if any(verb in best[1] for verb in _STRONG_VERBS):
            continue
        key = tuple(sorted(words)[:4])
        if key in seen:
            continue
        seen.add(key)
        issues.append(
            f"Possible scope inflation: '{sentence.strip()[:120]}' is stronger than the supporting evidence."
        )
        if len(issues) >= 5:
            break
    return issues


def check_facts(
    text: str,
    evidence: str | Sequence[str],
    *,
    forbidden_phrases: Sequence[str] | None = None,
    supported_terms: Sequence[str] | None = None,
    allow_metrics: Sequence[str] | None = None,
) -> FactCheckResult:
    """Compare generated prose with the candidate's factual evidence."""
    letter = normalize_text(text)
    evidence_text = evidence if isinstance(evidence, str) else "\n".join(evidence)
    evidence_text = normalize_text(evidence_text)

    if not letter:
        return FactCheckResult(verdict="block", warnings=["Generated text is empty."])

    warnings: list[str] = []
    if not evidence_text.strip():
        warnings.append(
            "No evidence text supplied; the fact check could not verify any claim."
        )

    allowed_raw = {normalize_text(item).casefold() for item in (allow_metrics or ())}
    allowed_values = {
        claim.value for item in (allow_metrics or ()) for claim in extract_metrics(str(item))
    }

    evidence_metrics = extract_metrics(evidence_text)
    evidence_keys = {claim.key for claim in evidence_metrics}
    evidence_values = {claim.value for claim in evidence_metrics if claim.value is not None}
    evidence_raw = {claim.raw.casefold() for claim in evidence_metrics}

    invented: list[str] = []
    advisory: list[str] = []
    for claim in extract_metrics(letter):
        if claim.raw.casefold() in allowed_raw:
            continue
        if claim.value is not None and claim.value in allowed_values:
            continue
        if claim.raw.casefold() in evidence_raw or claim.key in evidence_keys:
            continue
        if claim.value is not None and claim.value in evidence_values:
            advisory.append(
                f"'{claim.raw}' matches a number in the evidence but with a different unit."
            )
            continue
        invented.append(claim.raw)

    forbidden_found: list[str] = []
    phrases = tuple(forbidden_phrases) if forbidden_phrases else DEFAULT_FORBIDDEN_PHRASES
    letter_folded = fold_for_match(letter)
    for phrase in phrases:
        folded = fold_for_match(phrase)
        if folded and f" {folded} " in f" {letter_folded} ":
            forbidden_found.append(phrase)

    supported = " ".join(str(item) for item in (supported_terms or ()))
    supported_folded = f" {fold_for_match(evidence_text)} {fold_for_match(supported)} "
    unsupported: list[str] = []
    for term in sorted(TECH_FACT_TERMS):
        if not contains_keyword(letter, term):
            continue
        if f" {fold_for_match(term)} " in supported_folded:
            continue
        unsupported.append(term)

    scope = _scope_issues(letter, evidence_text) if evidence_text.strip() else []

    warnings.extend(advisory)
    warnings.extend(scope)

    verdict: Verdict = "pass"
    if invented or unsupported or forbidden_found:
        verdict = "block"
    elif warnings:
        verdict = "warn"

    return FactCheckResult(
        verdict=verdict,
        invented_metrics=invented,
        unsupported_facts=unsupported,
        advisory_facts=advisory,
        forbidden_phrases=forbidden_found,
        scope_issues=scope,
        warnings=warnings,
    )
