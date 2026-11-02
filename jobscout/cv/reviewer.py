"""Fresh-context reviewer with a typed, groundable edit list.

The reviewer is a second, separate LLM call over the *rendered* CV.  It never
returns prose that goes straight into the document - it returns edits, and each
edit carries a reason from a closed enum:

* ``grounding``    the reviewer believes something is not supported.  Never
  auto-applied: only the candidate or the master CV can settle a factual claim,
  so these edits are dropped and reported instead.
* ``reframing``    the reviewer wants a different story.  Kept for the user, not
  applied automatically, because re-framing is a career decision.
* anything else    language, structure, keyword placement: applied, but only if
  the new text stays inside the master CV's evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from jobscout.cv.master import MasterCV
from jobscout.cv.payload import CVBullet, CVRenderPayload
from jobscout.cv.tailor import unsupported_terms
from jobscout.verification.text import contains_keyword, fold_for_match, normalize_text

EditReason = Literal[
    "grammar",
    "clarity",
    "keyword",
    "structure",
    "tone",
    "reframing",
    "grounding",
]
EditOp = Literal[
    "replace_summary",
    "replace_bullet",
    "remove_bullet",
    "set_label",
    "set_competencies",
]

AUTO_APPLY: frozenset[str] = frozenset({"grammar", "clarity", "keyword", "structure", "tone"})
NEVER_AUTO_APPLY: frozenset[str] = frozenset({"grounding"})
SHOW_TO_USER: frozenset[str] = frozenset({"reframing"})

REASONS: tuple[str, ...] = (
    "grammar",
    "clarity",
    "keyword",
    "structure",
    "tone",
    "reframing",
    "grounding",
)
OPS: tuple[str, ...] = (
    "replace_summary",
    "replace_bullet",
    "remove_bullet",
    "set_label",
    "set_competencies",
)


class ReviewerError(Exception):
    """Raised when the reviewer response cannot be understood at all."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReviewerEdit(_Strict):
    op: EditOp
    reason: EditReason
    rationale: str = ""
    value: str | list[str] = ""
    role: int | None = None
    index: int | None = None

    @property
    def text(self) -> str:
        return self.value if isinstance(self.value, str) else ""

    @property
    def items(self) -> list[str]:
        if isinstance(self.value, list):
            return [normalize_text(str(item)) for item in self.value if str(item).strip()]
        return []


@dataclass
class AppliedEdit:
    edit: ReviewerEdit
    description: str


@dataclass
class ReviewOutcome:
    applied: list[AppliedEdit] = field(default_factory=list)
    skipped_grounding: list[ReviewerEdit] = field(default_factory=list)
    needs_review: list[ReviewerEdit] = field(default_factory=list)
    rejected: list[tuple[ReviewerEdit, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        def dump(edit: ReviewerEdit) -> dict[str, Any]:
            return edit.model_dump(mode="json")

        return {
            "applied": [
                {"description": item.description, **dump(item.edit)} for item in self.applied
            ],
            "skipped_grounding": [dump(edit) for edit in self.skipped_grounding],
            "needs_review": [dump(edit) for edit in self.needs_review],
            "rejected": [
                {"edit": dump(edit), "why": why} for edit, why in self.rejected
            ],
        }

    @property
    def notes(self) -> list[str]:
        notes: list[str] = []
        if self.skipped_grounding:
            notes.append(
                f"{len(self.skipped_grounding)} reviewer edit(s) claimed a grounding "
                "problem and were not applied automatically."
            )
        if self.needs_review:
            notes.append(
                f"{len(self.needs_review)} reframing suggestion(s) need your decision."
            )
        if self.rejected:
            notes.append(
                f"{len(self.rejected)} edit(s) were rejected because they added "
                "unsupported content."
            )
        return notes


def parse_reviewer_payload(data: Any) -> list[ReviewerEdit]:
    """Strict parse; unknown keys or reasons are a hard error (caller retries)."""
    if isinstance(data, list):
        data = {"edits": data}
    if not isinstance(data, dict):
        raise ReviewerError("The reviewer must return a JSON object with an 'edits' list")
    payload = data.get("edits", data.get("Edits"))
    if payload is None:
        raise ReviewerError("The reviewer response has no 'edits' list")
    if not isinstance(payload, list):
        raise ReviewerError("'edits' must be a list")
    try:
        return [ReviewerEdit.model_validate(item) for item in payload]
    except ValidationError as exc:
        raise ReviewerError(f"Invalid reviewer edit: {exc}") from exc


def _new_terms(text: str, evidence: str) -> list[str]:
    """Product/tool names or numbers the master CV cannot support."""
    return unsupported_terms(text, evidence)


def _resolve(payload: CVRenderPayload, edit: ReviewerEdit) -> tuple[int, int] | None:
    if edit.role is None or edit.role < 0 or edit.role >= len(payload.experience):
        return None
    if edit.op in {"replace_summary", "set_competencies"}:
        return edit.role, -1
    if edit.index is None or edit.index < 0:
        return None
    role = payload.experience[edit.role]
    if edit.index >= len(role.bullets):
        return None
    return edit.role, edit.index


def apply_edits(
    payload: CVRenderPayload,
    edits: Sequence[ReviewerEdit],
    master: MasterCV,
) -> tuple[CVRenderPayload, ReviewOutcome]:
    """Apply the safe subset of a reviewer's edits."""
    fresh = payload.model_copy(deep=True)
    outcome = ReviewOutcome()
    evidence = master.evidence_text()
    supported = {fold_for_match(item) for item in master.supported_skills()}

    for edit in edits:
        if edit.reason in NEVER_AUTO_APPLY:
            outcome.skipped_grounding.append(edit)
            continue
        if edit.reason in SHOW_TO_USER:
            outcome.needs_review.append(edit)
            continue
        if edit.reason not in AUTO_APPLY:
            outcome.rejected.append((edit, f"unknown reason '{edit.reason}'"))
            continue

        location = _resolve(fresh, edit)
        if edit.op not in {"replace_summary", "set_competencies"} and location is None:
            outcome.rejected.append((edit, "target bullet does not exist"))
            continue

        if edit.op == "replace_summary":
            candidate = normalize_text(edit.text)
            if not candidate:
                outcome.rejected.append((edit, "empty summary"))
                continue
            blockers = _new_terms(candidate, evidence)
            if blockers:
                outcome.rejected.append(
                    (edit, "unsupported in summary: " + ", ".join(blockers[:5]))
                )
                continue
            fresh.summary = candidate
            outcome.applied.append(AppliedEdit(edit, "summary rewritten"))
            continue

        if edit.op == "set_competencies":
            items = [item for item in edit.items if fold_for_match(item) in supported]
            dropped = len(edit.items) - len(items)
            if not items:
                outcome.rejected.append((edit, "no competency is in the master CV"))
                continue
            fresh.competencies = items
            text = "competency grid set"
            if dropped:
                text += f" ({dropped} unsupported dropped)"
            outcome.applied.append(AppliedEdit(edit, text))
            continue

        role_index, bullet_index = location or (0, 0)
        role = fresh.experience[role_index]
        bullet = role.bullets[bullet_index]

        if edit.op == "remove_bullet":
            role.bullets.pop(bullet_index)
            outcome.applied.append(AppliedEdit(edit, f"bullet removed from {role.company}"))
            continue

        candidate = normalize_text(edit.text)
        if edit.op == "set_label":
            if not candidate:
                outcome.rejected.append((edit, "empty label"))
                continue
            if not contains_keyword(bullet.text, candidate):
                outcome.rejected.append(
                    (edit, f"'{candidate}' does not appear in the bullet")
                )
                continue
            bullet.label = candidate
            outcome.applied.append(AppliedEdit(edit, f"label set to '{candidate}'"))
            continue

        # replace_bullet
        if not candidate:
            outcome.rejected.append((edit, "empty bullet"))
            continue
        blockers = _new_terms(candidate, evidence)
        if blockers:
            outcome.rejected.append(
                (edit, "unsupported in bullet: " + ", ".join(blockers[:5]))
            )
            continue
        role.bullets[bullet_index] = CVBullet(
            text=candidate,
            label=bullet.label if contains_keyword(candidate, bullet.label) else "",
            fact_ids=list(bullet.fact_ids),
            relevance=bullet.relevance,
        )
        outcome.applied.append(AppliedEdit(edit, "bullet rewritten"))

    return fresh, outcome
