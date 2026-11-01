"""Job-description keyword extraction and coverage.

Deterministic, no LLM.  Coverage is a *diagnostic*: it is never folded into
the structural ATS score.  ``thin`` means a keyword appears exactly once, which
is usually not enough for a skim-reading recruiter or a keyword matcher.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from jobscout.verification.text import count_keyword, fold_for_match, normalize_text

Priority = Literal["required", "preferred", "normal"]

_TOKEN_RE = re.compile(r"\.?[A-Za-zÄÖÜäöüß0-9][A-Za-zÄÖÜäöüß0-9+#.\-/]*")
_TRAILING = ".,-/"

_REQUIRED_MARKERS = (
    "erforderlich",
    "vorausgesetzt",
    "zwingend",
    "muss",
    "required",
    "must have",
    "essential",
    "minimum",
)
_PREFERRED_MARKERS = (
    "von vorteil",
    "wünschenswert",
    "wuenschenswert",
    "nice to have",
    "preferred",
    "plus",
    "idealerweise",
)

STOPWORDS = {
    # German
    "aber", "alle", "allem", "allen", "aller", "alles", "als", "also", "am", "an",
    "andere", "anderen", "auch", "auf", "aus", "bei", "beim", "bin", "bis", "bist",
    "da", "damit", "dann", "das", "dass", "dein", "deine", "dem", "den", "der",
    "des", "die", "dies", "diese", "diesem", "diesen", "dieser", "dieses", "doch",
    "dort", "du", "durch", "ein", "eine", "einem", "einen", "einer", "eines",
    "er", "es", "euer", "eure", "für", "fuer", "gegen", "gewesen", "hab", "habe",
    "haben", "hat", "hatte", "hatten", "hier", "hin", "hinter", "ich", "ihm",
    "ihn", "ihnen", "ihr", "ihre", "im", "in", "indem", "ins", "ist", "jede",
    "jedem", "jeden", "jeder", "jedes", "jene", "jenem", "jenen", "jener", "jenes",
    "jetzt", "kann", "kannst", "kein", "keine", "keinem", "keinen", "keiner",
    "keines", "können", "koennen", "könnte", "koennte", "machen", "man", "manche",
    "mein", "meine", "mich", "mir", "mit", "muss", "musst", "müssen", "muessen",
    "nach", "nicht", "nichts", "noch", "nun", "nur", "ob", "oder", "ohne", "sehr",
    "sein", "seine", "seinem", "seinen", "seiner", "seines", "selbst", "sich",
    "sie", "sind", "so", "solche", "soll", "sollen", "sollte", "sondern", "sonst",
    "über", "ueber", "um", "und", "uns", "unsere", "unter", "viel", "vom", "von",
    "vor", "während", "waehrend", "war", "waren", "warst", "was", "weg", "weil",
    "weiter", "welche", "welchem", "welchen", "welcher", "welches", "wenn", "wer",
    "werden", "wie", "wieder", "will", "wir", "wird", "wo", "wollen", "wollte",
    "würde", "wuerde", "zu", "zum", "zur", "zusammen", "zwischen",
    # English
    "a", "about", "above", "after", "again", "against", "all", "also", "am", "an",
    "and", "any", "are", "as", "at", "be", "because", "been", "before", "being",
    "below", "between", "both", "but", "by", "can", "cannot", "could", "did", "do",
    "does", "doing", "down", "during", "each", "few", "for", "from", "further",
    "had", "has", "have", "having", "he", "her", "here", "hers", "herself", "him",
    "himself", "his", "how", "i", "if", "in", "into", "is", "it", "its", "itself",
    "just", "me", "more", "most", "my", "myself", "no", "nor", "not", "now", "of",
    "off", "on", "once", "only", "or", "other", "our", "ours", "ourselves", "out",
    "over", "own", "same", "she", "should", "so", "some", "such", "than", "that",
    "the", "their", "theirs", "them", "themselves", "then", "there", "these",
    "they", "this", "those", "through", "to", "too", "under", "until", "up",
    "very", "was", "we", "were", "what", "when", "where", "which", "while", "who",
    "whom", "why", "will", "with", "would", "you", "your", "yours", "yourself",
    "yourselves", "role", "position", "stellen", "stelle", "aufgaben", "team",
    "unternehmen", "company", "job", "arbeit", "stellenangebot",
}

# Terms that should survive the length filter and get a small boost.
TECH_TERMS = {
    "python", "java", "javascript", "typescript", "react", "vue", "angular",
    "node", "nodejs", "django", "flask", "fastapi", "spring", "docker",
    "kubernetes", "k8s", "terraform", "ansible", "aws", "azure", "gcp", "sql",
    "postgresql", "postgres", "mysql", "mongodb", "redis", "elasticsearch",
    "kafka", "rabbitmq", "git", "github", "gitlab", "jenkins", "n8n", "zapier",
    "airflow", "langchain", "llama", "openai", "pytorch", "tensorflow", "sklearn",
    "pandas", "numpy", "tableau", "powerbi", "excel", "sap", "salesforce",
    "hubspot", "figma", "jira", "confluence", "scrum", "agile", "kanban", "rest",
    "graphql", "grpc", "microservices", "linux", "bash", "powershell", "c++",
    "c#", ".net", "golang", "rust", "php", "ruby", "swift", "kotlin", "scala",
    "matlab", "opencv", "nlp", "llm", "rag", "huggingface", "ollama", "snowflake",
    "databricks", "spark", "hadoop", "etl", "dwh", "selenium", "playwright",
    "html", "css", "api", "apis", "ci/cd", "devops", "mlops", "ki",
}

# Folded keyword -> accepted variants.  Folded on both sides at use time, so
# "Kubernetes" matches "k8s" and "Datenanalyse" matches "data analysis".
DEFAULT_SYNONYMS: dict[str, tuple[str, ...]] = {
    "kubernetes": ("k8s",),
    "javascript": ("js",),
    "typescript": ("ts",),
    "postgresql": ("postgres",),
    "machine learning": ("ml",),
    "artificial intelligence": ("ai", "ki"),
    "kuenstliche intelligenz": ("ki", "ai"),
    "datenanalyse": ("data analysis",),
    "datenbank": ("database",),
    "projektmanagement": ("project management",),
    "kundenbetreuung": ("customer support", "customer service"),
    "vertrieb": ("sales",),
    "buchhaltung": ("accounting",),
    "personalwesen": ("hr", "human resources"),
    "qualitaetssicherung": ("quality assurance", "qa"),
    "softwareentwicklung": ("software development",),
    "continous integration": ("ci",),
    "kontinuierliche integration": ("ci",),
}


@dataclass
class Keyword:
    term: str
    priority: Priority = "normal"
    count: int = 0
    score: float = 0.0

    def to_dict(self) -> dict:
        return {
            "term": self.term,
            "priority": self.priority,
            "count": self.count,
            "score": round(self.score, 2),
        }


@dataclass
class KeywordCoverage:
    total: int
    present: list[str] = field(default_factory=list)
    thin: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    @property
    def coverage_pct(self) -> float:
        if not self.total:
            return 0.0
        return round(100.0 * (len(self.present) + len(self.thin)) / self.total, 1)

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "coverage_pct": self.coverage_pct,
            "present": self.present,
            "thin": self.thin,
            "missing": self.missing,
        }


def _priority_for_line(line: str) -> Priority:
    folded = fold_for_match(line)
    if any(marker in folded for marker in _REQUIRED_MARKERS):
        return "required"
    if any(marker in folded for marker in _PREFERRED_MARKERS):
        return "preferred"
    return "normal"


def _tokens(line: str) -> list[str]:
    tokens: list[str] = []
    for match in _TOKEN_RE.finditer(line):
        token = match.group(0).strip(_TRAILING)
        if not token:
            continue
        tokens.append(token)
    return tokens


def _usable(token: str) -> bool:
    folded = fold_for_match(token)
    if not folded:
        return False
    if folded.isdigit():
        return False
    if folded in STOPWORDS:
        return False
    if len(folded) < 3 and folded not in TECH_TERMS:
        return False
    return True


def extract_keywords(text: str, limit: int = 20) -> list[Keyword]:
    """Extract the most important unigrams and bigrams from a posting.

    Frequency is the base signal; tech terms get a boost, and terms that sit on
    a line with "erforderlich"/"required" outrank optional ones.
    """
    normalized = normalize_text(text)
    if not normalized:
        return []

    priority_rank = {"required": 2, "preferred": 1, "normal": 0}
    accumulator: dict[str, Keyword] = {}

    def add(term: str, priority: Priority, weight: float) -> None:
        key = fold_for_match(term)
        if not key:
            return
        current = accumulator.get(key)
        if current is None:
            accumulator[key] = Keyword(term=term, priority=priority, count=1, score=weight)
            return
        current.count += 1
        current.score += weight
        if priority_rank[priority] > priority_rank[current.priority]:
            current.priority = priority

    for line in normalized.splitlines():
        priority = _priority_for_line(line)
        tokens = _tokens(line)
        usable = [token for token in tokens if _usable(token)]
        for token in usable:
            folded = fold_for_match(token)
            weight = 1.0
            if folded in TECH_TERMS:
                weight += 2.0
            if priority == "required":
                weight += 1.5
            elif priority == "preferred":
                weight += 0.5
            add(token, priority, weight)

        for first, second in zip(tokens, tokens[1:]):
            if not (_usable(first) and _usable(second)):
                continue
            phrase = f"{first} {second}"
            folded = fold_for_match(phrase)
            weight = 2.0
            if any(part in TECH_TERMS for part in (fold_for_match(first), fold_for_match(second))):
                weight += 1.0
            add(phrase, priority, weight)

    keywords = list(accumulator.values())
    keywords.sort(key=lambda item: (-priority_rank[item.priority], -item.score, item.term))
    return keywords[:limit]


def coverage(
    text: str,
    keywords: list[Keyword] | list[str],
    *,
    synonyms: dict[str, tuple[str, ...]] | None = None,
) -> KeywordCoverage:
    """Measure how much of the keyword list appears in ``text``."""
    mapping = {key: tuple(value) for key, value in (synonyms or DEFAULT_SYNONYMS).items()}
    result = KeywordCoverage(total=len(keywords))
    for item in keywords:
        term = item.term if isinstance(item, Keyword) else str(item)
        folded = fold_for_match(term)
        variants = (term, *mapping.get(folded, ()))
        count = sum(count_keyword(text, variant) for variant in variants)
        if count == 0:
            result.missing.append(term)
        elif count == 1:
            result.thin.append(term)
        else:
            result.present.append(term)
    return result
