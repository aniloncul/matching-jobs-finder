"""JSON-only LLM calls for the CV pipeline.

Two calls exist and neither of them writes the document:

* ``tailor`` picks and orders what is already in the master CV,
* ``review`` returns a typed edit list for a CV that was rendered without this
  call having written it.

Both reuse the OpenAI-compatible client of the cover letter
(:class:`jobscout.cover_letter.llm.CoverLetterLLM`), so there is one endpoint,
one key and one model to configure.
"""

from __future__ import annotations

from typing import Any, Sequence

from jobscout.cover_letter.llm import CoverLetterLLM, LLMError, _extract_json
from jobscout.cv.master import MasterCV
from jobscout.cv.payload import CVRenderPayload, plain_text
from jobscout.cv.reviewer import OPS, REASONS
from jobscout.llm import is_loopback
from jobscout.verification.keywords import Keyword

CV_TAILOR_SYSTEM_PROMPT = """You adapt one applicant's CV for one job advert.
You do not write the CV and you do not own any facts about the person.
The only allowed source of content is the FACT LIST you are given.

Hard rules:
- Never invent an employer, a job title, a date, a tool, a certification or a number.
- A requirement the fact list cannot support is a gap: leave it out entirely. Never phrase it as "fast learner", "exposed to" or "familiar with".
- Do not repeat the fact list; you only choose, order and label what is already there.
- Write in the language you are told. Plain text only: no LaTeX, no Markdown, no bullet characters.
- A "label" is a bold prefix shown in front of a bullet. You may only use a term that literally occurs inside that bullet.

Return one JSON object with exactly these keys:
- summary: at most 550 characters, only facts from the fact list, or "" to keep the current summary
- competencies: array of 6 to 8 strings, each taken verbatim from the SKILL LIST
- labels: array of {"role": <role index>, "index": <bullet index>, "label": "<term already in that bullet>"}
- bullet_order: array of {"role": <role index>, "order": [permutation of that role's bullet indices]}
- notes: array of short strings about what you changed and which requirements the CV cannot evidence
"""

CV_REVIEW_SYSTEM_PROMPT = """You are a fresh-context reviewer of one rendered CV. You did not write it and you cannot see the model that produced it.
Judge it against the FACT LIST and the job advert.

Return one JSON object: {"edits": [ ... ]} with at most 8 edits.
Each edit is an object with exactly these keys:
- op: one of {ops}
- reason: one of {reasons}
- role: index of the experience block (0 based)
- index: index of the bullet inside that block (0 based)
- value: the new text, or an array of strings for set_competencies
- rationale: one short sentence

Rules:
- If you think something is not supported by the fact list, use reason "grounding" and do NOT propose a reworded version: the system never applies grounding edits automatically.
- If you disagree with the story the candidate is telling, use reason "reframing": that edit is shown to the human, not applied.
- "value" must never contain a tool, employer, date or number that is not in the fact list.
- Prefer few precise edits over many cosmetic ones. Return {"edits": []} if the CV is fine.
"""


def _keyword_lines(keywords: Sequence[Keyword]) -> str:
    if not keywords:
        return "(none)"
    return ", ".join(f"{item.term} [{item.priority}]" for item in keywords)


def _fact_lines(master: MasterCV, limit: int = 120) -> str:
    lines: list[str] = []
    if master.headline:
        lines.append(f"- headline: {master.headline}")
    if master.years_experience:
        lines.append(f"- total experience: {int(master.years_experience)} years")
    for role in master.roles:
        head = f"- {role.role} at {role.company} ({role.period})"
        lines.append(head)
        for fact_id in role.fact_ids[:12]:
            fact = master.fact(fact_id)
            if fact:
                lines.append(f"    - [{fact.id}] {fact.text}")
    for item in master.education:
        lines.append(f"- education: {item.degree} {item.institution}".strip())
    lines.extend(f"- certification: {item}" for item in master.certifications)
    lines.extend(f"- language: {item}" for item in master.languages)
    return "\n".join(lines[:limit])


def _skill_lines(master: MasterCV) -> str:
    return "\n".join(f"- {item}" for item in master.supported_skills()) or "(none)"


def _payload_view(payload: CVRenderPayload) -> str:
    """The CV as the reviewer sees it: numbered, no internal ids."""
    lines: list[str] = [payload.headline, payload.summary]
    if payload.competencies:
        lines.append("Competencies: " + ", ".join(payload.competencies))
    for role_index, role in enumerate(payload.experience):
        lines.append(f"[{role_index}] {role.role} - {role.company} {role.period()} {role.location}".rstrip())
        for bullet_index, bullet in enumerate(role.bullets):
            lines.append(f"    [{role_index}.{bullet_index}] {bullet.display()}")
    return "\n".join(line for line in lines if line)


class CVLLM(CoverLetterLLM):
    """CV tailoring and review on the same endpoint as the cover letter."""

    def _chat(self, system: str, user: str, *, max_tokens: int = 1600) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": min(self.temperature, 0.2),
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }
        if "nemotron" in self.model.lower():
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        if self.num_ctx > 0 and is_loopback(self.base_url):
            payload["options"] = {"num_ctx": self.num_ctx}
        data = self._post(payload)
        try:
            content = data["choices"][0]["message"].get("content") or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("Unexpected response from the CV model") from exc
        if not isinstance(content, str) or not content.strip():
            raise LLMError("The CV model returned an empty response")
        return _extract_json(content)

    def tailor(
        self,
        master: MasterCV,
        payload: CVRenderPayload,
        keywords: Sequence[Keyword],
        *,
        description: str = "",
        language: str = "de",
    ) -> dict[str, Any]:
        if not self.api_key:
            raise LLMError("COVER_LETTER_API_KEY is not set. CV tailoring needs an LLM.")
        user = (
            f"Language: {'German' if language == 'de' else 'English'}\n\n"
            f"FACT LIST (the only allowed content):\n{_fact_lines(master)}\n\n"
            f"SKILL LIST:\n{_skill_lines(master)}\n\n"
            f"TARGET KEYWORDS:\n{_keyword_lines(keywords)}\n\n"
            f"CURRENT CV:\n{_payload_view(payload)}\n\n"
        )
        if description:
            user += f"JOB ADVERT:\n{description[:6000]}\n\n"
        user += "Return the tailoring JSON object now."
        return self._chat(CV_TAILOR_SYSTEM_PROMPT, user, max_tokens=2000)

    def review(
        self,
        master: MasterCV,
        payload: CVRenderPayload,
        keywords: Sequence[Keyword] = (),
        *,
        description: str = "",
        language: str = "de",
    ) -> dict[str, Any]:
        if not self.api_key:
            raise LLMError("COVER_LETTER_API_KEY is not set. CV review needs an LLM.")
        system = CV_REVIEW_SYSTEM_PROMPT.format(
            ops=", ".join(f'"{item}"' for item in OPS),
            reasons=", ".join(f'"{item}"' for item in REASONS),
        )
        user = (
            f"Language: {'German' if language == 'de' else 'English'}\n\n"
            f"FACT LIST:\n{_fact_lines(master)}\n\n"
            f"TARGET KEYWORDS:\n{_keyword_lines(keywords)}\n\n"
            f"CV UNDER REVIEW:\n{_payload_view(payload)}\n\n"
        )
        if description:
            user += f"JOB ADVERT:\n{description[:4000]}\n\n"
        user += "Return the review JSON object now."
        return self._chat(system, user, max_tokens=1400)


def document_text(payload: CVRenderPayload) -> str:
    """Convenience alias used by callers that log the document."""
    return plain_text(payload)


__all__ = ["CVLLM", "CV_REVIEW_SYSTEM_PROMPT", "CV_TAILOR_SYSTEM_PROMPT", "document_text"]
