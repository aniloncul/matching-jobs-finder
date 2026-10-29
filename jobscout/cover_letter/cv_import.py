"""Fill the applicant profile from an uploaded CV using a vision language model.

The applicant can upload a CV as a PDF or an image. PDFs are rasterised page by
page, every page is sent to a vision capable model together with a strict
extraction prompt, and the model answers with the profile fields as JSON.
"""

from __future__ import annotations

import base64
import io
import json
import re
from typing import Any

import httpx

from jobscout.cover_letter.profile import Education, Experience, Profile


class CVImportError(Exception):
    """Raised when a CV cannot be read or parsed."""


# Pages beyond this limit are not sent to the model. A CV longer than this is
# very unlikely and the token cost of the images grows quickly.
DEFAULT_MAX_PAGES = 4

# Accepted upload types. The extension is the fallback when the browser does
# not send a content type.
PDF_EXTENSIONS = {".pdf"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
IMAGE_MIME_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
}

SYSTEM_PROMPT = """You read a CV or resume image and extract the applicant's data as JSON.

Read every page carefully, including the header block, the contact details, the
experience section, the education section and the skills section.

Return one JSON object with exactly these keys:
- name: full name of the applicant
- headline: the current job title or professional headline, if the CV states one
- email: email address
- phone: phone number
- address: street and house number without the postal code
- postal_code: postal code as digits, if present
- city: city
- country: country
- linkedin: LinkedIn profile URL
- github: GitHub profile URL
- website: any other personal website
- summary: a short professional summary written in the language of the CV, based only on what the CV shows
- skills: array of technical skills, tools and technologies
- soft_skills: array of soft skills and personal strengths
- experience: array of objects with "company", "role", "start", "end", "location" and "achievements" (an array of bullet points, verbatim where possible)
- education: array of objects with "institution", "degree", "start", "end" and "location"
- languages: array of languages with the level, for example "English — C1"
- certifications: array of certification names
- cv_text: the full readable text of the CV, transcribed as plain text

Rules:
- Use only what is written on the CV. Never guess, never complete a missing value from general knowledge.
- If a field is not present, return an empty string, an empty array, or an empty object list.
- Keep the original order of the experience and education entries, most recent first.
- Keep the original language of the CV for cv_text and for the achievements.
- Do not read a photo, a date of birth, a marital status, a nationality, a salary expectation or a reference section.
"""

TEXT_SYSTEM_PROMPT = """You read the plain text of a CV or resume and extract the applicant's data as JSON.

Read every section carefully, including the header block, contact details, experience, education, skills, and languages.

Return one JSON object with exactly these keys:
- name: full name of the applicant
- headline: the current job title or professional headline, if the CV states one
- email: email address
- phone: phone number
- address: street and house number without the postal code
- postal_code: postal code as digits, if present
- city: city
- country: country
- linkedin: LinkedIn profile URL
- github: GitHub profile URL
- website: any other personal website
- summary: a short professional summary written in the language of the CV, based only on what the CV shows
- skills: array of technical skills, tools and technologies
- soft_skills: array of soft skills and personal strengths
- experience: array of objects with "company", "role", "start", "end", "location" and "achievements" (an array of bullet points, verbatim where possible)
- education: array of objects with "institution", "degree", "start", "end" and "location"
- languages: array of languages with the level, for example "English — C1"
- certifications: array of certification names
- cv_text: the full readable text of the CV, transcribed as plain text

Rules:
- Use only what is written in the text. Never guess, never complete a missing value from general knowledge.
- If a field is not present, return an empty string, an empty array, or an empty object list.
- Keep the original order of the experience and education entries, most recent first.
- Keep the original language of the CV for cv_text and for the achievements.
- Do not invent photos, date of birth, marital status, nationality, or salary expectations.
"""


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
            raise CVImportError("The model did not return JSON")
        try:
            value = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as exc:
            raise CVImportError(f"The model returned invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise CVImportError("The model response must be a JSON object")
    return value


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple)):
        return "\n".join(_as_text(item) for item in value if _as_text(item))
    return str(value).strip()


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        parts = re.split(r"[\n;]+", value)
        return [part.strip(" -•\t") for part in parts if part.strip(" -•\t")]
    if isinstance(value, (list, tuple)):
        return [_as_text(item) for item in value if _as_text(item)]
    text = _as_text(value)
    return [text] if text else []


def _as_entries(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)):
        return []
    return [item for item in value if isinstance(item, dict)]


def _split_dates(entry: dict[str, Any]) -> tuple[str, str]:
    """Normalise a date range into a start and an end string."""
    start = _as_text(entry.get("start"))
    end = _as_text(entry.get("end"))
    period = _as_text(entry.get("period")) or _as_text(entry.get("dates"))
    if period and not (start and end):
        separator = re.split(r"\s*(?:-|–|—|to|until|bis|zu)\s*", period, maxsplit=1)
        start = start or _as_text(separator[0])
        if len(separator) > 1:
            end = end or _as_text(separator[1])
    return start, end


def merge_into_profile(profile: Profile, extracted: dict[str, Any]) -> Profile:
    """Merge the extracted data into a profile without losing manual edits.

    Only fields that are still empty are filled. Values the applicant typed by
    hand always win, so an import can never silently overwrite real data.
    """
    data = profile.model_dump(mode="python")

    def fill_text(key: str) -> None:
        value = _as_text(extracted.get(key))
        if value and not _as_text(data.get(key)):
            data[key] = value

    def fill_list(key: str) -> None:
        values = _as_list(extracted.get(key))
        if not values:
            return
        existing = data.get(key) or []
        if existing:
            known = {_as_text(item).casefold() for item in existing}
            for value in values:
                if value.casefold() not in known:
                    existing.append(value)
                    known.add(value.casefold())
            data[key] = existing
        else:
            data[key] = values

    for key in (
        "name",
        "headline",
        "email",
        "phone",
        "address",
        "postal_code",
        "city",
        "country",
        "linkedin",
        "github",
        "website",
        "summary",
        "cv_text",
    ):
        fill_text(key)

    for key in ("skills", "soft_skills", "languages", "certifications"):
        fill_list(key)

    has_real_experience = any(
        bool(_as_text(entry.get("company") if isinstance(entry, dict) else getattr(entry, "company", "")) or
             _as_text(entry.get("role") if isinstance(entry, dict) else getattr(entry, "role", "")))
        for entry in (data.get("experience") or [])
    )
    if not has_real_experience:
        experiences = []
        for entry in _as_entries(extracted.get("experience")):
            start, end = _split_dates(entry)
            experiences.append(
                Experience(
                    company=_as_text(entry.get("company")),
                    role=_as_text(entry.get("role")) or _as_text(entry.get("title")),
                    start=start,
                    end=end,
                    location=_as_text(entry.get("location")),
                    achievements=_as_list(entry.get("achievements")),
                )
            )
        if experiences:
            data["experience"] = experiences

    has_real_education = any(
        bool(_as_text(entry.get("institution") if isinstance(entry, dict) else getattr(entry, "institution", "")) or
             _as_text(entry.get("degree") if isinstance(entry, dict) else getattr(entry, "degree", "")))
        for entry in (data.get("education") or [])
    )
    if not has_real_education:
        education = []
        for entry in _as_entries(extracted.get("education")):
            start, end = _split_dates(entry)
            education.append(
                Education(
                    institution=_as_text(entry.get("institution")),
                    degree=_as_text(entry.get("degree")) or _as_text(entry.get("title")),
                    start=start,
                    end=end,
                    location=_as_text(entry.get("location")),
                )
            )
        if education:
            data["education"] = education

    return Profile.model_validate(data)


def _png_bytes(image: Any) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def extract_text_from_pdf(data: bytes, max_pages: int = DEFAULT_MAX_PAGES) -> str:
    """Extract readable text from a PDF document directly without rendering images."""
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:
        raise CVImportError("PDF support is not available on this installation") from exc

    try:
        document = pdfium.PdfDocument(data)
    except Exception as exc:
        raise CVImportError(f"The PDF could not be read: {exc}") from exc

    text_parts: list[str] = []
    try:
        total = len(document)
        for index in range(min(total, max_pages)):
            page = document[index]
            textpage = page.get_textpage()
            text = textpage.get_text_range()
            if text and text.strip():
                text_parts.append(text.strip())
            textpage.close()
            page.close()
    except Exception as exc:
        raise CVImportError(f"Failed to extract text from PDF: {exc}") from exc
    finally:
        try:
            document.close()
        except Exception:
            pass

    return "\n\n".join(text_parts)


def extract_text_with_ocr(pages: list[bytes]) -> str:
    """Extract text from rasterised page images using Tesseract OCR if available."""
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        return ""

    ocr_parts: list[str] = []
    for page in pages:
        try:
            with Image.open(io.BytesIO(page)) as img:
                text = pytesseract.image_to_string(img, lang="eng+deu")
                if text.strip():
                    ocr_parts.append(text.strip())
        except Exception:
            continue

    return "\n\n".join(ocr_parts)


def pdf_pages_as_png(data: bytes, max_pages: int = DEFAULT_MAX_PAGES) -> list[bytes]:
    """Rasterise the pages of a PDF into PNG images."""
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise CVImportError("PDF support is not available on this installation") from exc

    try:
        document = pdfium.PdfDocument(data)
    except Exception as exc:
        raise CVImportError(f"The PDF could not be read: {exc}") from exc

    pages: list[bytes] = []
    try:
        total = len(document)
        for index in range(min(total, max_pages)):
            page = document[index]
            bitmap = page.render(scale=2)
            image = bitmap.to_pil()
            pages.append(_png_bytes(image))
            bitmap.close()
            page.close()
    except CVImportError:
        raise
    except Exception as exc:
        raise CVImportError(f"The PDF pages could not be rendered: {exc}") from exc
    finally:
        try:
            document.close()
        except Exception:
            pass
    if not pages:
        raise CVImportError("The PDF has no readable pages")
    return pages


def _image_to_png(data: bytes) -> bytes:
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise CVImportError("Image support is not available on this installation") from exc
    try:
        with Image.open(io.BytesIO(data)) as image:
            return _png_bytes(image.convert("RGB"))
    except Exception as exc:
        raise CVImportError(f"The image could not be read: {exc}") from exc


def images_from_upload(
    data: bytes,
    filename: str,
    content_type: str | None = None,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> list[bytes]:
    """Turn an uploaded PDF or image into a list of PNG page images."""
    if not data:
        raise CVImportError("The uploaded file is empty")

    suffix = ""
    if filename and "." in filename:
        suffix = "." + filename.rsplit(".", 1)[-1].lower()

    mime = (content_type or "").split(";")[0].strip().lower()
    if mime == "application/pdf" or suffix in PDF_EXTENSIONS:
        return pdf_pages_as_png(data, max_pages=max_pages)
    if mime in IMAGE_MIME_TYPES or suffix in IMAGE_EXTENSIONS:
        return [_image_to_png(data)]
    raise CVImportError(
        "Unsupported file type. Upload a PDF, PNG, JPEG or WebP file."
    )


def _data_url(png: bytes) -> str:
    encoded = base64.b64encode(png).decode("ascii")
    return f"data:image/png;base64,{encoded}"


class CVImportLLM:
    """Sends the CV pages to a vision language model."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        temperature: float = 0.1,
        max_tokens: int = 4000,
        timeout_seconds: float = 180.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.max_tokens = max(max_tokens, 8192)
        self.timeout_seconds = timeout_seconds

    def extract(self, pages: list[bytes]) -> dict[str, Any]:
        if not self.api_key:
            raise CVImportError(
                "COVER_LETTER_API_KEY is not set. Add it to .env before importing a CV."
            )
        if not pages:
            raise CVImportError("There are no pages to read")

        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": (
                    "Read this CV and return the applicant data as JSON. "
                    "The pages are in reading order."
                ),
            }
        ]
        for page in pages:
            content.append(
                {"type": "image_url", "image_url": {"url": _data_url(page)}}
            )

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "response_format": {"type": "json_object"},
        }
        data = self._post(payload)
        try:
            msg = data["choices"][0]["message"]
            text = msg.get("content") or ""
            if not text.strip() and msg.get("reasoning_content"):
                text = msg["reasoning_content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise CVImportError("Unexpected response from the vision model") from exc
        if not isinstance(text, str) or not text.strip():
            raise CVImportError("The vision model returned an empty response")
        return _extract_json(text)

    def extract_from_text(self, text: str) -> dict[str, Any]:
        """Extract profile information directly from plain text using a text LLM."""
        if not self.api_key:
            raise CVImportError(
                "COVER_LETTER_API_KEY is not set. Add it to .env before importing a CV."
            )
        if not text or len(text.strip()) < 40:
            raise CVImportError("The extracted CV text is too short or empty")

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": TEXT_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        "Extract the applicant profile into JSON directly from this CV text:\n\n"
                        f"{text}\n\n"
                        "Return only the JSON object."
                    ),
                },
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "response_format": {"type": "json_object"},
        }
        data = self._post(payload)
        try:
            msg = data["choices"][0]["message"]
            content = msg.get("content") or ""
            if not content.strip() and msg.get("reasoning_content"):
                content = msg["reasoning_content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise CVImportError("Unexpected response from the language model") from exc
        if not isinstance(content, str) or not content.strip():
            raise CVImportError("The language model returned an empty response")
        extracted = _extract_json(content)
        if not extracted.get("cv_text"):
            extracted["cv_text"] = text
        return extracted

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
                if response.status_code >= 400 and "response_format" in payload:
                    fallback = dict(payload)
                    fallback.pop("response_format", None)
                    response = client.post(
                        f"{self.base_url}/chat/completions",
                        headers=headers,
                        json=fallback,
                    )
        except httpx.HTTPError as exc:
            raise CVImportError(f"The model request failed: {exc}") from exc
        if response.status_code >= 400:
            detail = response.text[:600]
            raise CVImportError(
                f"The model API returned {response.status_code}: {detail}"
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise CVImportError("The model API returned invalid JSON") from exc
        if not isinstance(data, dict):
            raise CVImportError("The model API returned an unexpected payload")
        return data


def import_cv_into_profile(
    profile: Profile,
    data: bytes,
    filename: str,
    content_type: str | None = None,
    base_url: str = "https://integrate.api.nvidia.com/v1",
    api_key: str = "",
    model: str = "nvidia/nemotron-3.5-super-vl-120b-a12b",
    max_pages: int = DEFAULT_MAX_PAGES,
    timeout_seconds: float = 180.0,
    pages: list[bytes] | None = None,
    vision_model: str | None = None,
) -> Profile:
    """Read an uploaded CV and return the profile with the extracted data.

    Tries text extraction first (PDF text), then OCR fallback, and finally
    vision model fallback.
    """
    client = CVImportLLM(
        base_url=base_url,
        api_key=api_key or "ollama",
        model=model,
        timeout_seconds=timeout_seconds,
    )

    suffix = ("." + filename.rsplit(".", 1)[-1].lower()) if (filename and "." in filename) else ""
    mime = (content_type or "").split(";")[0].strip().lower()
    is_pdf = mime == "application/pdf" or suffix in PDF_EXTENSIONS

    # 1. Direct PDF text extraction (fastest, uses zero vision tokens)
    if is_pdf and data:
        try:
            pdf_text = extract_text_from_pdf(data, max_pages=max_pages)
            if len(pdf_text.strip()) >= 80:
                extracted = client.extract_from_text(pdf_text)
                return merge_into_profile(profile, extracted)
        except Exception as exc:
            print(f"[cv_import] Text extraction notice: {exc}")

    # 2. Rasterise pages if not provided
    if pages is None:
        pages = images_from_upload(data, filename, content_type, max_pages=max_pages)

    # 3. Local OCR fallback for scanned documents or image uploads
    ocr_text = extract_text_with_ocr(pages)
    if len(ocr_text.strip()) >= 80:
        try:
            extracted = client.extract_from_text(ocr_text)
            return merge_into_profile(profile, extracted)
        except Exception as exc:
            print(f"[cv_import] OCR text extraction notice: {exc}")

    # 4. Fallback to vision model
    if vision_model and not ("nvidia.com" in base_url and "/" not in vision_model):
        client.model = vision_model
    elif "nvidia.com" in base_url:
        client.model = "nvidia/nemotron-3.5-super-vl-120b-a12b"

    extracted = client.extract(pages)
    return merge_into_profile(profile, extracted)

