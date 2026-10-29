from __future__ import annotations

import io
import json

import pytest

from jobscout.cover_letter.cv_import import (
    CVImportError,
    CVImportLLM,
    images_from_upload,
    merge_into_profile,
)
from jobscout.cover_letter.profile import Profile


def make_pdf(page_texts: list[str]) -> bytes:
    """Build a tiny multi page PDF with real text on every page."""
    objects: list[bytes] = []
    page_ids: list[int] = []
    content_ids: list[int] = []
    for index in range(len(page_texts)):
        page_ids.append(3 + index * 2)
        content_ids.append(4 + index * 2)
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode())
    for text, cid in zip(page_texts, content_ids):
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 120] "
            f"/Contents {cid} 0 R /Resources << /Font << /F1 9 0 R >> >> >>".encode()
        )
        content = f"BT /F1 18 Tf 20 40 Td ({text}) Tj ET".encode()
        objects.append(
            b"<< /Length "
            + str(len(content)).encode()
            + b" >>\nstream\n"
            + content
            + b"\nendstream"
        )
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets: list[int] = []
    for index, obj in enumerate(objects, 1):
        offsets.append(out.tell())
        out.write(str(index).encode() + b" 0 obj\n" + obj + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 " + str(len(objects) + 1).encode() + b"\n0000000000 65535 f \n")
    for offset in offsets:
        out.write(f"{offset:010d} 00000 n \n".encode())
    out.write(
        b"trailer\n<< /Size "
        + str(len(objects) + 1).encode()
        + b" /Root 1 0 R >>\nstartxref\n"
        + str(xref).encode()
        + b"\n%%EOF\n"
    )
    return out.getvalue()


def png_bytes(color: str = "white", size: tuple[int, int] = (60, 20)) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def test_pdf_is_rasterised_into_pages() -> None:
    pages = images_from_upload(make_pdf(["Page one", "Page two"]), "cv.pdf")
    assert len(pages) == 2
    assert all(page.startswith(b"\x89PNG") for page in pages)


def test_pdf_page_limit_is_respected() -> None:
    pages = images_from_upload(
        make_pdf(["A", "B", "C", "D", "E"]), "cv.pdf", max_pages=2
    )
    assert len(pages) == 2


def test_image_upload_is_converted_to_png() -> None:
    pages = images_from_upload(png_bytes(), "cv.png", "image/png")
    assert len(pages) == 1
    assert pages[0].startswith(b"\x89PNG")


def test_jpeg_upload_is_accepted() -> None:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (40, 20), "red").save(buffer, format="JPEG")
    pages = images_from_upload(buffer.getvalue(), "cv.jpg", "image/jpeg")
    assert len(pages) == 1
    assert pages[0].startswith(b"\x89PNG")


def test_unsupported_file_type_is_rejected() -> None:
    with pytest.raises(CVImportError, match="Unsupported file type"):
        images_from_upload(b"whatever", "cv.docx", "application/msword")


def test_empty_upload_is_rejected() -> None:
    with pytest.raises(CVImportError, match="empty"):
        images_from_upload(b"", "cv.pdf")


def test_broken_pdf_is_reported() -> None:
    with pytest.raises(CVImportError, match="could not be read"):
        images_from_upload(b"not a pdf at all", "cv.pdf")


def test_merge_fills_empty_fields() -> None:
    profile = Profile(name="Anil Öncül", email="anil@example.com")
    merged = merge_into_profile(
        profile,
        {
            "name": "Someone Else",
            "email": "other@example.com",
            "phone": "+49 30 123456",
            "city": "Berlin",
            "skills": ["Python", "n8n"],
        },
    )
    # Manual values survive the import.
    assert merged.name == "Anil Öncül"
    assert merged.email == "anil@example.com"
    # Empty fields are filled in.
    assert merged.phone == "+49 30 123456"
    assert merged.city == "Berlin"
    assert merged.skills == ["Python", "n8n"]


def test_merge_appends_new_skills_without_duplicates() -> None:
    profile = Profile(skills=["Python"])
    merged = merge_into_profile(profile, {"skills": ["python", "n8n", "Docker"]})
    assert merged.skills == ["Python", "n8n", "Docker"]


def test_merge_builds_experience_entries() -> None:
    merged = merge_into_profile(
        Profile(),
        {
            "experience": [
                {
                    "company": "Helix Systems",
                    "role": "AI Engineer",
                    "start": "2024",
                    "end": "Present",
                    "location": "Berlin",
                    "achievements": ["Shipped an agent workflow"],
                }
            ]
        },
    )
    assert len(merged.experience) == 1
    entry = merged.experience[0]
    assert entry.company == "Helix Systems"
    assert entry.role == "AI Engineer"
    assert entry.achievements == ["Shipped an agent workflow"]


def test_merge_splits_a_date_range_period() -> None:
    merged = merge_into_profile(
        Profile(),
        {"experience": [{"company": "Acme", "role": "Dev", "period": "2021 - 2024"}]},
    )
    assert merged.experience[0].start == "2021"
    assert merged.experience[0].end == "2024"


def test_merge_builds_education_entries() -> None:
    merged = merge_into_profile(
        Profile(),
        {
            "education": [
                {
                    "institution": "TU Berlin",
                    "degree": "BSc Computer Science",
                    "start": "2018",
                    "end": "2022",
                }
            ]
        },
    )
    assert merged.education[0].institution == "TU Berlin"
    assert merged.education[0].degree == "BSc Computer Science"


def test_merge_accepts_a_string_instead_of_a_list() -> None:
    merged = merge_into_profile(Profile(), {"skills": "Python\nn8n\nDocker"})
    assert merged.skills == ["Python", "n8n", "Docker"]


def test_merge_keeps_existing_experience() -> None:
    from jobscout.cover_letter.profile import Experience

    profile = Profile(
        experience=[
            Experience(
                company="Existing GmbH",
                role="Engineer",
                start="2020",
                end="2023",
                achievements=["Did things"],
            )
        ]
    )
    merged = merge_into_profile(
        profile,
        {"experience": [{"company": "New GmbH", "role": "Engineer"}]},
    )
    assert len(merged.experience) == 1
    assert merged.experience[0].company == "Existing GmbH"


def test_llm_requires_an_api_key() -> None:
    client = CVImportLLM(base_url="https://example.test/v1", api_key="", model="m")
    with pytest.raises(CVImportError, match="COVER_LETTER_API_KEY"):
        client.extract([png_bytes()])


def test_llm_sends_the_pages_as_images(monkeypatch) -> None:
    captured: dict = {}

    def fake_post(self, payload):
        captured["payload"] = payload
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {"name": "Anil Öncül", "city": "Berlin", "skills": ["Python"]}
                        )
                    }
                }
            ]
        }

    monkeypatch.setattr(CVImportLLM, "_post", fake_post)
    client = CVImportLLM(base_url="https://example.test/v1", api_key="k", model="m")
    result = client.extract([png_bytes(), png_bytes()])

    content = captured["payload"]["messages"][1]["content"]
    assert content[0]["type"] == "text"
    images = [part for part in content if part["type"] == "image_url"]
    assert len(images) == 2
    assert images[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert result["name"] == "Anil Öncül"


def test_llm_handles_a_fenced_json_answer(monkeypatch) -> None:
    def fake_post(self, payload):
        return {
            "choices": [
                {"message": {"content": '```json\n{"city": "Berlin"}\n```'}}
            ]
        }

    monkeypatch.setattr(CVImportLLM, "_post", fake_post)
    client = CVImportLLM(base_url="https://example.test/v1", api_key="k", model="m")
    assert client.extract([png_bytes()])["city"] == "Berlin"


def test_llm_reports_an_empty_answer(monkeypatch) -> None:
    def fake_post(self, payload):
        return {"choices": [{"message": {"content": "  "}}]}

    monkeypatch.setattr(CVImportLLM, "_post", fake_post)
    client = CVImportLLM(base_url="https://example.test/v1", api_key="k", model="m")
    with pytest.raises(CVImportError, match="empty response"):
        client.extract([png_bytes()])


def test_import_endpoint_fills_the_profile(tmp_path, monkeypatch) -> None:
    from fastapi.testclient import TestClient
    from jobscout.ui import server

    path = tmp_path / "profile.yaml"
    monkeypatch.setattr(server.settings.cover_letter, "profile_path", path)
    monkeypatch.setattr(server.settings.cover_letter, "api_key", "test")

    def fake_import(profile, data, filename, content_type=None, **kwargs):
        from jobscout.cover_letter.cv_import import merge_into_profile

        return merge_into_profile(
            profile, {"name": "Anil Öncül", "city": "Berlin", "skills": ["Python"]}
        )

    monkeypatch.setattr(server, "import_cv_into_profile", fake_import)
    client = TestClient(server.app)

    response = client.post(
        "/api/profile/import",
        files={"file": ("cv.pdf", make_pdf(["Anil Öncül"]), "application/pdf")},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["profile"]["name"] == "Anil Öncül"
    assert payload["profile"]["city"] == "Berlin"
    assert "name" in payload["filled"]
    assert "skills" in payload["filled"]


def test_import_endpoint_rejects_an_unsupported_type(tmp_path, monkeypatch) -> None:
    from fastapi.testclient import TestClient
    from jobscout.ui import server

    monkeypatch.setattr(
        server.settings.cover_letter, "profile_path", tmp_path / "profile.yaml"
    )
    client = TestClient(server.app)

    response = client.post(
        "/api/profile/import",
        files={"file": ("cv.docx", b"nope", "application/msword")},
    )
    assert response.status_code == 400
    assert "Unsupported file type" in response.json()["detail"]


def test_import_endpoint_rejects_an_oversized_file(tmp_path, monkeypatch) -> None:
    from fastapi.testclient import TestClient
    from jobscout.ui import server

    monkeypatch.setattr(
        server.settings.cover_letter, "profile_path", tmp_path / "profile.yaml"
    )
    monkeypatch.setattr(server.settings.cover_letter, "cv_import_max_upload_mb", 1)
    client = TestClient(server.app)

    response = client.post(
        "/api/profile/import",
        files={"file": ("cv.pdf", b"x" * (1024 * 1024 + 10), "application/pdf")},
    )
    assert response.status_code == 413



