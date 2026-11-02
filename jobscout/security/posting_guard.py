"""Sanitise untrusted job-posting HTML/text before it reaches a model.

A scraped posting is untrusted input.  Attackers hide text with CSS, encode
instructions in Unicode tag characters, or mix scripts so that a model reads
something a human never sees.  This module strips the invisible payload and
flags the instruction-shaped lines; it does not silently delete visible text.

The result is intentionally conservative: visible job-description text is
preserved, invisible carriers are removed, and suspicious lines are reported
so the UI can ask the user to review the posting.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Literal

from bs4 import BeautifulSoup, Comment

Verdict = Literal["clean", "review"]

_HIDDEN_STYLE_RE = re.compile(
    r"(?:"
    r"display\s*:\s*none"
    r"|visibility\s*:\s*hidden"
    r"|opacity\s*:\s*0(?:[;\s!]|$)"
    r"|font-size\s*:\s*0(?:[;\s!]|$)"
    r"|color\s*:\s*transparent"
    r"|clip\s*:\s*rect\(\s*0"
    r"|clip-path\s*:\s*inset\(\s*100%"
    r"|(?:left|top|text-indent)\s*:\s*-\d{3,}px"
    r"|height\s*:\s*0(?:[;\s!]|$)"
    r"|width\s*:\s*0(?:[;\s!]|$)"
    r")",
    re.IGNORECASE,
)

_INVISIBLE_RE = re.compile(
    "[\u00ad\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff"
    "\U000E0000-\U000E007F]"
)
_WHITESPACE_RE = re.compile(r"[ \t\f\v]+")
_MULTI_NEWLINE_RE = re.compile(r"\n{3,}")
_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)

# Letter-spaced text from CSS (`letter-spacing` on skill tags) survives text
# extraction as "P y t h o n".  Four or more single letters in a row are not
# real words; they are one word with the spacing burned in, and the joined
# form is what every keyword matcher downstream expects.
_SPACED_WORD_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:[A-Za-z][ \u00a0]){3,}[A-Za-z](?![A-Za-z0-9])"
)


def _join_spaced_words(text: str) -> str:
    return _SPACED_WORD_RE.sub(lambda m: "".join(m.group(0).split()), text)

_INSTRUCTION_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE | re.MULTILINE)
    for pattern in (
        r"\bignore\s+(?:all\s+)?(?:previous|prior|above|the)\s+instructions?\b",
        r"\bdisregard\s+(?:all\s+)?(?:previous|prior|above)\b",
        r"\bsystem\s+prompt\b",
        r"\byou\s+are\s+(?:now\s+)?(?:an?\s+)?(?:ai|assistant|chatgpt|claude|model)\b",
        r"^\s*(?:system|assistant|user)\s*:",
        r"</?\s*(?:system|instruction|prompt)\s*>",
        r"\bdo\s+not\s+tell\s+the\s+(?:user|candidate|applicant)\b",
        r"\bignoriere\s+(?:alle\s+)?(?:vorherigen|bisherigen|obigen)\s+anweisungen\b",
        r"\bvergiss\s+(?:alle\s+)?(?:vorherigen|bisherigen)\s+anweisungen\b",
        r"\bsystemanweisung(?:en)?\b",
        r"\bdu\s+bist\s+jetzt\b",
        r"\bantworte\s+nur\s+mit\b",
    )
)

_SCRIPT_PREFIXES = ("LATIN", "CYRILLIC", "GREEK", "HEBREW", "ARABIC", "CJK")


@dataclass
class PostingGuardResult:
    clean_text: str
    verdict: Verdict = "clean"
    hidden_blocks: int = 0
    removed_comments: int = 0
    invisible_chars: int = 0
    instruction_lines: list[str] = field(default_factory=list)
    mixed_script_words: list[str] = field(default_factory=list)

    @property
    def flagged(self) -> bool:
        return self.verdict == "review"

    def summary(self) -> str:
        if not self.flagged:
            return "posting_guard: clean"
        parts: list[str] = []
        if self.hidden_blocks:
            parts.append(f"{self.hidden_blocks} hidden block(s) removed")
        if self.invisible_chars:
            parts.append(f"{self.invisible_chars} invisible character(s) removed")
        if self.instruction_lines:
            parts.append(f"{len(self.instruction_lines)} instruction-like line(s)")
        if self.mixed_script_words:
            parts.append(f"{len(self.mixed_script_words)} mixed-script word(s)")
        return "posting_guard: review before continuing (" + ", ".join(parts) + ")"

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "clean_text": self.clean_text,
            "hidden_blocks": self.hidden_blocks,
            "removed_comments": self.removed_comments,
            "invisible_chars": self.invisible_chars,
            "instruction_lines": self.instruction_lines,
            "mixed_script_words": self.mixed_script_words,
            "summary": self.summary(),
        }


def _script_of(char: str) -> str:
    name = unicodedata.name(char, "")
    for prefix in _SCRIPT_PREFIXES:
        if name.startswith(prefix):
            return prefix
    return ""


def _mixed_script_words(text: str) -> list[str]:
    suspicious: list[str] = []
    for match in _WORD_RE.finditer(text):
        word = match.group(0)
        if len(word) < 2:
            continue
        scripts = {script for script in (_script_of(char) for char in word) if script}
        if "LATIN" in scripts and ({"CYRILLIC", "GREEK"} & scripts):
            suspicious.append(word)
        if len(suspicious) >= 10:
            break
    return suspicious


def _normalise_block(text: str) -> str:
    text = _WHITESPACE_RE.sub(" ", text)
    text = _join_spaced_words(text)
    text = _MULTI_NEWLINE_RE.sub("\n\n", text)
    return text.strip()


def guard_text(text: str) -> PostingGuardResult:
    """Sanitise plain text and flag instruction-shaped content."""
    source = text or ""
    stripped = _INVISIBLE_RE.sub("", source)
    invisible_count = len(source) - len(stripped)
    cleaned = _normalise_block(stripped)

    instruction_lines: list[str] = []
    for line in cleaned.splitlines():
        if any(pattern.search(line) for pattern in _INSTRUCTION_PATTERNS):
            instruction_lines.append(line.strip()[:200])
            if len(instruction_lines) >= 5:
                break

    mixed = _mixed_script_words(cleaned)
    verdict: Verdict = "review" if (instruction_lines or mixed or invisible_count) else "clean"

    return PostingGuardResult(
        clean_text=cleaned,
        verdict=verdict,
        invisible_chars=invisible_count,
        instruction_lines=instruction_lines,
        mixed_script_words=mixed,
    )


def guard_html(html: str) -> PostingGuardResult:
    """Strip hidden DOM nodes, comments and scripts, then guard the text."""
    soup = BeautifulSoup(html or "", "html.parser")

    removed_comments = 0
    for comment in soup.find_all(string=lambda node: isinstance(node, Comment)):
        comment.extract()
        removed_comments += 1

    for tag in soup.find_all(["script", "style", "template", "noscript", "iframe", "object", "embed"]):
        tag.decompose()

    hidden_blocks = 0
    for element in soup.find_all(True):
        style = element.get("style") or ""
        hidden = (
            element.has_attr("hidden")
            or str(element.get("aria-hidden", "")).lower() == "true"
            or bool(_HIDDEN_STYLE_RE.search(str(style)))
        )
        if hidden:
            element.decompose()
            hidden_blocks += 1

    text = soup.get_text("\n")
    result = guard_text(text)
    result.hidden_blocks = hidden_blocks
    result.removed_comments = removed_comments
    if hidden_blocks or removed_comments:
        result.verdict = "review"
    return result


def guard_posting(value: str) -> PostingGuardResult:
    """Auto-detect whether the payload is HTML or plain text."""
    raw = value or ""
    if re.search(r"<[a-zA-Z][^>]*>", raw):
        return guard_html(raw)
    return guard_text(raw)
