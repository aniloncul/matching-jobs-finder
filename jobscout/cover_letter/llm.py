from __future__ import annotations

import json
from typing import Any

import httpx

from jobscout.cover_letter.profile import Profile
from jobscout.models import Job


class LLMError(Exception):
    """Raised when the cloud LLM request fails."""


SYSTEM_PROMPT = """You write classic German-style cover letters (Anschreiben) for job applications adhering strictly to DIN 5008 standards.
Follow the structure of a traditional German cover letter: introduction, main part, conclusion.
Use only facts present in the applicant profile and in the job posting. Never invent employers, dates, degrees, skills, achievements, products, missions, or company details.
Write in the language of the job description. Use German for German ads and English for English ads; if unsure, use German.
Return plain text values, not LaTeX or Markdown. Do not include headings such as Introduction, Main Part, or Conclusion in the letter.
Keep the letter to one page. The template renders the sender block, recipient, date, subject line, salutation, the closing, and the optional sentences about the start date and confidentiality.

1. Introduction (Einstieg) - 2-3 sentences.
   - Name the exact position and why the applicant is enthusiastic about this specific role and company.
   - Weave in the applicant's motivation and unique value proposition. If the profile contains a motivation field, use it as the applicant's own argument.
   - Write it originally and specifically. Never start with an empty generic phrase such as "Mit großem Interesse bewerbe ich mich" or "hiermit bewerbe ich mich" without immediately explaining the connection to the role.

2. Main part (Hauptteil) - 2-3 focused paragraphs:
   a) Requirements Match & Experience Tailoring:
      - Thoroughly analyze the job description, core responsibilities, and key requirements.
      - Actively tailor and formulate the applicant's past work experience, milestones, and technical/soft skills to directly correspond to the role's daily responsibilities.
      - Explain specifically WHY and HOW their previous achievements and skills make them the ideal candidate for these tasks.
   b) Education & Background (Schulabschluss / Werdegang):
      - Naturally incorporate the applicant's educational background, graduation type, or current status if relevant to the position.
   c) Skill Gap Bridging (Crucial Rule):
      - If the job demands a crucial skill, tool, or qualification that is completely missing from the applicant's profile (and cannot be legitimately bridged via transferable skills), DO NOT invent fake experience.
      - Instead, proactively turn this into a strong positive: explicitly emphasize the applicant's fast comprehension ("schnelle Auffassungsgabe"), steep learning curve, high motivation, and eager readiness to learn ("hohe Lernbereitschaft"), stating their commitment to rapidly and independently master the required technology/domain on the job based on their solid foundational expertise.

3. Conclusion (Abschluss & Vorstellungsgespräch) - 1-2 sentences.
   - Reiterate motivation and explicitly express the applicant's eagerness for a personal interview (e.g., "Über eine Einladung zu einem persönlichen Gespräch freue ich mich sehr" or "Ich freue mich auf Ihre Rückmeldung und die Gelegenheit, mich Ihnen persönlich vorzustellen").
   - Do not mention the start date, the notice period, or confidentiality. The template appends those sentences automatically when provided in the profile.

4. Closing: formal closing line such as "Mit freundlichen Grüßen" in German or "Sincerely" in English.

Never mention salary expectations, references, nationality, marital status, date of birth, a photo, or a cover photo.
Do not write the sender address, the date, the subject line prefix, or the salutation into the paragraph text; the template renders them.

Return one JSON object with exactly these keys:
- language: "de" or "en"
- subject: subject line without the word "Betreff:" or "Subject:" (e.g. "Bewerbung als [Rolle]" or "Bewerbung für einen Ausbildungsplatz als [Rolle]")
- salutation: formal salutation (e.g., "Sehr geehrte Frau [Name]," or "Sehr geehrter Herr [Name]," if a contact person is named in the posting, otherwise "Sehr geehrte Damen und Herren,")
- recipient_person: optional contact person name (e.g., "Frau Meyer" or "Herr Schmidt") if found in the posting, otherwise ""
- recipient_department: optional department name (e.g., "Personalabteilung" or "Abteilung Marketing") if found in the posting, otherwise ""
- intro: one plain-text paragraph for the introduction
- main_paragraphs: array of 2 or 3 plain-text paragraphs for the main part
- conclusion: one plain-text paragraph for the conclusion
- closing: formal closing line such as "Mit freundlichen Grüßen" or "Sincerely"
- requirement_matches: array of objects with keys "requirement" and "evidence"; evidence must come from the profile (or note learning motivation for bridged gaps)
"""

SOURCE_LABELS = {
    "linkedin": "LinkedIn",
    "indeed": "Indeed",
}


def _extract_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            raise LLMError("The model did not return JSON")
        try:
            value = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as exc:
            raise LLMError(f"The model returned invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise LLMError("The model response must be a JSON object")
    return value


class CoverLetterLLM:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        temperature: float = 0.2,
        max_tokens: int = 2500,
        timeout_seconds: float = 120.0,
        num_ctx: int = 4096,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout_seconds = timeout_seconds
        self.num_ctx = num_ctx

    def generate(
        self,
        profile: Profile,
        job: Job,
        description: str,
        matched_bullets: list[Any] | None = None,
    ) -> dict[str, Any]:
        if not self.api_key:
            raise LLMError(
                "COVER_LETTER_API_KEY is not set. Add it to .env before generating a letter."
            )
        job_text = (
            f"Company: {job.company}\n"
            f"Role: {job.title}\n"
            f"Location: {job.location or 'not specified'}\n"
            f"Job board where the applicant found this posting: "
            f"{SOURCE_LABELS.get(job.source.value, job.source.value)}\n"
            f"Job link: {job.link}\n\n"
            f"Job description:\n{description[:8000]}"
        )

        evidence_section = ""
        if matched_bullets:
            evidence_lines = ["Matched Target Requirements & Verified Profile Evidence:"]
            for idx, item in enumerate(matched_bullets, 1):
                req_text = getattr(item.requirement, "text", str(item.requirement))
                b_text = getattr(item.bullet, "text", str(item.bullet))
                b_meta = getattr(item.bullet, "metadata", {})
                source_info = []
                if b_meta.get("company"):
                    source_info.append(f"Company: {b_meta['company']}")
                if b_meta.get("role"):
                    source_info.append(f"Role: {b_meta['role']}")
                meta_str = f" ({', '.join(source_info)})" if source_info else ""
                evidence_lines.append(
                    f"{idx}. Target: {req_text}\n"
                    f"   Verified Evidence{meta_str}: {b_text}"
                )
            evidence_section = "\n".join(evidence_lines) + "\n\n"

        user_prompt = (
            "Applicant profile (YAML):\n"
            f"{profile.prompt_text()}\n\n"
            f"{evidence_section}"
            "Job posting:\n"
            f"{job_text}\n\n"
            "Write the cover letter content as the requested JSON object. "
            "Base the main paragraphs strictly on the verified profile evidence without inventing new claims."
        )
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "response_format": {"type": "json_object"},
        }
        if "nemotron" in self.model.lower():
            # Nemotron 3.5 otherwise spends the entire completion budget on
            # reasoning_content and returns an empty content field.
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        # `options.num_ctx` is Ollama-specific; OpenAI/NVIDIA NIM rejects it.
        _is_local = any(
            kw in self.base_url
            for kw in ("localhost", "127.0.0.1", "ollama", "::1")
        )
        if self.num_ctx > 0 and _is_local:
            payload["options"] = {"num_ctx": self.num_ctx}
        # Disable chain-of-thought for NVIDIA NIM reasoning models (e.g. Nemotron).
        # Without this, the model spends all its max_tokens on internal thinking
        # and has nothing left to write the actual JSON response.
        _is_nvidia_nim = "integrate.api.nvidia.com" in self.base_url
        if _is_nvidia_nim:
            kwargs = payload.setdefault("chat_template_kwargs", {})
            kwargs.setdefault("enable_thinking", False)
            kwargs.setdefault("thinking", False)

        data = self._post(payload)
        try:
            message = data["choices"][0]["message"]
            content = message.get("content") or ""
        except (KeyError, IndexError, TypeError) as exc:
            import json as _json
            print(f"[LLM] Unexpected response shape: {_json.dumps(data)[:600]}")
            raise LLMError("Unexpected response from the cover letter model") from exc
        if not isinstance(content, str) or not content.strip():
            import json as _json
            finish = ""
            try:
                finish = data["choices"][0].get("finish_reason", "")
            except Exception:
                pass
            print(f"[LLM] Empty content (finish_reason={finish}). Data: {_json.dumps(data)[:800]}")
            raise LLMError(
                f"The cover letter model returned an empty response (finish_reason={finish}). "
                "Try increasing COVER_LETTER_MAX_TOKENS or switching to a non-reasoning model."
            )
        return _extract_json(content)

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                )
                if response.status_code >= 400 and (
                    "response_format" in payload
                    or "options" in payload
                    or "chat_template_kwargs" in payload
                ):
                    fallback = dict(payload)
                    fallback.pop("response_format", None)
                    fallback.pop("options", None)
                    fallback.pop("chat_template_kwargs", None)
                    response = client.post(
                        f"{self.base_url}/chat/completions",
                        headers=headers,
                        json=fallback,
                    )
        except httpx.TimeoutException as exc:
            raise LLMError(
                "Cover letter API request timed out after "
                f"{int(self.timeout_seconds)}s. Increase COVER_LETTER_TIMEOUT "
                "and retry."
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"Cover letter API request failed: {exc}") from exc
        if response.status_code >= 400:
            detail = response.text[:600]
            raise LLMError(f"Cover letter API returned {response.status_code}: {detail}")
        try:
            data = response.json()
        except ValueError as exc:
            raise LLMError("Cover letter API returned invalid JSON") from exc
        if not isinstance(data, dict):
            raise LLMError("Cover letter API returned an unexpected payload")
        return data
