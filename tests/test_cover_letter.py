from __future__ import annotations

from datetime import date

import pytest

from jobscout.config import load_settings
from jobscout.cover_letter.description import (
    parse_job_description,
    parse_linkedin_main_text,
)
from jobscout.cover_letter.description_store import (
    load_saved_description,
    save_job_description,
)
from jobscout.cover_letter.generator import CoverLetterError, generate_cover_letter
from jobscout.cover_letter.latex import (
    availability_sentence,
    confidentiality_sentence,
    format_date,
    notice_period_sentence,
    tex_escape,
)
from jobscout.cover_letter.llm import _extract_json
from jobscout.cover_letter.profile import Experience, Profile, ProfileError, inspect_profile, load_profile
from jobscout.models import Job, Source


class FakeLLM:
    def __init__(self, result: dict | None = None) -> None:
        self.result = result or {
            "language": "de",
            "subject": "Bewerbung als AI Engineer",
            "salutation": "Sehr geehrte Damen und Herren,",
            "intro": "Mit großem Interesse bewerbe ich mich um die ausgeschriebene Position.",
            "main_paragraphs": [
                "Ich habe n8n-Workflows und Python-Automatisierungen entwickelt.",
                "Die Anforderungen an Workflow-Automatisierung entsprechen meinem Profil.",
            ],
            "conclusion": "Gerne erläutere ich meine Erfahrungen im Gespräch.",
            "closing": "Mit freundlichen Grüßen",
            "requirement_matches": [
                {"requirement": "n8n", "evidence": "n8n-Workflows entwickelt"}
            ],
        }
        self.calls = 0
        self.last_description = ""

    def generate(self, profile: Profile, job: Job, description: str) -> dict:
        self.calls += 1
        self.last_description = description
        return self.result


@pytest.fixture
def profile() -> Profile:
    return Profile(
        name="Anil Örnek",
        headline="AI Engineer",
        email="anil@example.com",
        phone="+49 123 456789",
        address="Musterstraße 1",
        postal_code="10115",
        city="Berlin",
        country="Deutschland",
        summary="AI engineer focused on workflow automation.",
        skills=["Python", "n8n"],
        soft_skills=["Teamwork", "Clear communication"],
        experience=[
            Experience(
                company="Beispiel GmbH",
                role="AI Engineer",
                start="2023",
                end="heute",
                achievements=["n8n-Workflows automatisiert"],
            )
        ],
        languages=["Deutsch — fließend"],
    )


@pytest.fixture
def job() -> Job:
    return Job(
        company="StackFuel & Co. GmbH",
        title="AI Engineer",
        link="https://example.com/job",
        source=Source.indeed,
        job_id="abc-123",
    )


def test_tex_escape() -> None:
    assert tex_escape("A&B_100% #1") == r"A\&B\_100\% \#1"


def test_german_date() -> None:
    assert format_date("de", date(2026, 10, 21)) == "21. Oktober 2026"
    assert format_date("en", date(2026, 10, 21)) == "21 October 2026"


def test_extract_json_handles_fences() -> None:
    assert _extract_json('```json\n{"language":"de"}\n```') == {"language": "de"}


def test_parse_linkedin_description() -> None:
    html = '<div class="show-more-less-html__markup">Build <b>n8n</b> workflows.</div>'
    assert "n8n" in parse_job_description("linkedin", html)


def test_parse_indeed_description() -> None:
    html = '<div id="jobDescriptionText">Python and automation.</div>'
    assert parse_job_description("indeed", html) == "Python and automation."


def test_parse_linkedin_main_text_fallback() -> None:
    text = """OrderYOYO
Details zum Jobangebot
Onboarding Associate
Deine Aufgaben
Begleitung neuer Partner
Das bringst du mit
Kommunikationsstärke
Benachrichtigung für ähnliche Jobangebote einrichten
Über dieses Unternehmen
"""
    parsed = parse_linkedin_main_text(text)
    assert "Deine Aufgaben" in parsed
    assert "Kommunikationsstärke" in parsed
    assert "Über dieses Unternehmen" not in parsed


def test_generate_renders_tex_and_metadata(tmp_path, profile, job) -> None:
    settings = load_settings()
    settings = settings.model_copy(deep=True)
    settings.cover_letter.output_dir = tmp_path
    settings.cover_letter.compile_pdf = False
    settings.cover_letter.api_key = "test"
    llm = FakeLLM()

    result = generate_cover_letter(
        settings,
        job,
        description="Wir suchen n8n und Python.",
        profile=profile,
        llm=llm,
        today=date(2026, 10, 21),
    )

    assert llm.calls == 1
    assert result.language == "de"
    assert result.reused is False
    assert result.tex_path.endswith("indeed-abc-123.tex")
    text = (tmp_path / "indeed-abc-123.tex").read_text(encoding="utf-8")
    assert "StackFuel \\& Co. GmbH" in text
    assert "21. Oktober 2026" in text
    assert "Bewerbung als AI Engineer" in text
    assert "n8n-Workflows" in text
    assert text.index("Mit großem Interesse") < text.index("n8n-Workflows") < text.index("Gerne erläutere")
    assert "% --- Introduction" in text
    assert "% --- Main part" in text
    assert "% --- Conclusion" in text
    metadata = (tmp_path / "indeed-abc-123.json").read_text(encoding="utf-8")
    assert '"requirement_matches"' in metadata


def test_contact_links_and_return_address_are_not_rendered(
    tmp_path, profile, job
) -> None:
    settings = load_settings()
    settings = settings.model_copy(deep=True)
    settings.cover_letter.output_dir = tmp_path
    settings.cover_letter.compile_pdf = False
    settings.cover_letter.api_key = "test"
    profile = profile.model_copy(
        update={
            "linkedin": "https://www.linkedin.com/in/anil-oncul/",
            "github": "https://github.com/aniloncul",
            "website": "https://aniloncul.github.io/",
        }
    )

    generate_cover_letter(
        settings,
        job,
        description="Wir suchen n8n und Python.",
        profile=profile,
        llm=FakeLLM(),
        today=date(2026, 10, 21),
    )

    text = (tmp_path / "indeed-abc-123.tex").read_text(encoding="utf-8")
    assert "linkedin.com/in/anil-oncul" not in text
    assert "github.com/aniloncul" not in text
    assert "aniloncul.github.io" not in text
    assert "Anil Örnek, Musterstraße 1" not in text
    assert "Rücksendeangabe" not in text


def test_existing_letter_is_not_overwritten(tmp_path, profile, job) -> None:
    settings = load_settings()
    settings = settings.model_copy(deep=True)
    settings.cover_letter.output_dir = tmp_path
    settings.cover_letter.compile_pdf = False
    settings.cover_letter.api_key = "test"
    (tmp_path / "indeed-abc-123.tex").write_text("edited by user", encoding="utf-8")
    llm = FakeLLM()

    result = generate_cover_letter(settings, job, description="x", profile=profile, llm=llm)

    assert result.reused is True
    assert llm.calls == 0
    assert (tmp_path / "indeed-abc-123.tex").read_text(encoding="utf-8") == "edited by user"


def test_force_regenerates(tmp_path, profile, job) -> None:
    settings = load_settings()
    settings = settings.model_copy(deep=True)
    settings.cover_letter.output_dir = tmp_path
    settings.cover_letter.compile_pdf = False
    settings.cover_letter.api_key = "test"
    (tmp_path / "indeed-abc-123.tex").write_text("old", encoding="utf-8")
    llm = FakeLLM()

    result = generate_cover_letter(
        settings, job, force=True, description="x", profile=profile, llm=llm
    )

    assert result.reused is False
    assert llm.calls == 1
    assert "old" not in (tmp_path / "indeed-abc-123.tex").read_text(encoding="utf-8")


def test_missing_profile_is_reported(tmp_path) -> None:
    with pytest.raises(ProfileError):
        load_profile(tmp_path / "missing.yaml")


def test_missing_api_key_is_reported(tmp_path, profile, job) -> None:
    settings = load_settings()
    settings = settings.model_copy(deep=True)
    settings.cover_letter.output_dir = tmp_path
    settings.cover_letter.compile_pdf = False
    settings.cover_letter.api_key = None

    with pytest.raises(CoverLetterError, match="COVER_LETTER_API_KEY"):
        generate_cover_letter(settings, job, description="x", profile=profile)


def test_cv_text_can_replace_structured_sections() -> None:
    profile = Profile(
        name="Anil Örnek",
        email="anil@example.com",
        cv_text="Senior AI engineer with eight years of experience building Python and n8n automation systems. " * 3,
    )
    assert profile.readiness_issues() == []


def test_placeholder_company_blocks_generation(profile: Profile) -> None:
    profile.experience[0].company = "Example GmbH"
    assert "Replace the example company with a real employer." in profile.readiness_issues()


def test_save_profile_roundtrip(tmp_path, profile: Profile) -> None:
    from jobscout.cover_letter.profile import save_profile

    path = tmp_path / "profile.yaml"
    save_profile(path, profile)
    loaded, issues = inspect_profile(path)
    assert loaded is not None
    assert issues == []
    assert loaded.name == "Anil Örnek"
    assert loaded.skills == ["Python", "n8n"]


def test_profile_api_updates_local_yaml(tmp_path, profile: Profile, monkeypatch) -> None:
    from fastapi.testclient import TestClient
    from jobscout.ui import server

    path = tmp_path / "profile.yaml"
    monkeypatch.setattr(server.settings.cover_letter, "profile_path", path)
    client = TestClient(server.app)

    response = client.put("/api/profile", json=profile.model_dump(mode="json"))
    assert response.status_code == 200
    payload = response.json()
    assert payload["ready"] is True
    assert payload["profile"]["name"] == "Anil Örnek"
    assert path.exists()

    status = client.get("/api/profile")
    assert status.status_code == 200
    assert status.json()["profile"]["skills"] == ["Python", "n8n"]


def test_legacy_paragraphs_are_supported(tmp_path, profile, job) -> None:
    settings = load_settings()
    settings = settings.model_copy(deep=True)
    settings.cover_letter.output_dir = tmp_path
    settings.cover_letter.compile_pdf = False
    settings.cover_letter.api_key = "test"
    legacy = {
        "language": "de",
        "subject": "Bewerbung",
        "salutation": "Sehr geehrte Damen und Herren,",
        "paragraphs": ["Einleitung.", "Hauptteil.", "Schluss."],
        "closing": "Mit freundlichen Grüßen",
        "requirement_matches": [],
    }
    result = generate_cover_letter(
        settings,
        job,
        description="x",
        profile=profile,
        llm=FakeLLM(legacy),
    )
    text = (tmp_path / "indeed-abc-123.tex").read_text(encoding="utf-8")
    assert result.reused is False
    assert text.index("Einleitung.") < text.index("Hauptteil.") < text.index("Schluss.")


def test_city_country_and_location_date_suffix_are_not_rendered(
    tmp_path, profile, job
) -> None:
    settings = load_settings()
    settings = settings.model_copy(deep=True)
    settings.cover_letter.output_dir = tmp_path
    settings.cover_letter.compile_pdf = False
    settings.cover_letter.api_key = "test"

    generate_cover_letter(
        settings,
        job,
        description="x",
        profile=profile,
        llm=FakeLLM(),
        today=date(2026, 10, 21),
    )

    text = (tmp_path / "indeed-abc-123.tex").read_text(encoding="utf-8")
    assert "Musterstraße 1" in text
    assert "10115 Berlin" not in text
    assert "Berlin" not in text
    assert "Deutschland" not in text
    assert "21. Oktober 2026" in text


def test_confidential_application_marks_the_letter(tmp_path, profile, job) -> None:
    settings = load_settings()
    settings = settings.model_copy(deep=True)
    settings.cover_letter.output_dir = tmp_path
    settings.cover_letter.compile_pdf = False
    settings.cover_letter.api_key = "test"
    profile.confidential_application = True

    generate_cover_letter(
        settings,
        job,
        description="x",
        profile=profile,
        llm=FakeLLM(),
    )

    text = (tmp_path / "indeed-abc-123.tex").read_text(encoding="utf-8")
    assert "Bitte behandeln Sie meine Bewerbung vertraulich." in text


def test_letter_is_not_marked_confidential_by_default(tmp_path, profile, job) -> None:
    settings = load_settings()
    settings = settings.model_copy(deep=True)
    settings.cover_letter.output_dir = tmp_path
    settings.cover_letter.compile_pdf = False
    settings.cover_letter.api_key = "test"

    generate_cover_letter(
        settings,
        job,
        description="x",
        profile=profile,
        llm=FakeLLM(),
    )

    text = (tmp_path / "indeed-abc-123.tex").read_text(encoding="utf-8")
    assert "vertraulich" not in text.lower()


def test_english_confidential_marker_is_used(tmp_path, profile, job) -> None:
    settings = load_settings()
    settings = settings.model_copy(deep=True)
    settings.cover_letter.output_dir = tmp_path
    settings.cover_letter.compile_pdf = False
    settings.cover_letter.api_key = "test"
    profile.confidential_application = True
    english = dict(FakeLLM().result)
    english["language"] = "en"

    generate_cover_letter(
        settings,
        job,
        description="We are looking for n8n and Python.",
        profile=profile,
        llm=FakeLLM(english),
    )

    text = (tmp_path / "indeed-abc-123.tex").read_text(encoding="utf-8")
    assert "Please treat my application confidentially." in text
    assert "vertraulich" not in text.lower()


def test_start_date_and_notice_period_are_rendered_in_the_conclusion(
    tmp_path, profile, job
) -> None:
    settings = load_settings()
    settings = settings.model_copy(deep=True)
    settings.cover_letter.output_dir = tmp_path
    settings.cover_letter.compile_pdf = False
    settings.cover_letter.api_key = "test"
    profile.earliest_start = "2026-03-01"
    profile.notice_period_months = 1

    generate_cover_letter(
        settings,
        job,
        description="x",
        profile=profile,
        llm=FakeLLM(),
    )

    text = (tmp_path / "indeed-abc-123.tex").read_text(encoding="utf-8")
    assert "Meine Kündigungsfrist beträgt einen Monat." in text
    assert "Ab dem 1. März 2026 bin ich verfügbar." in text
    # The sentences belong to the conclusion paragraph.
    conclusion = text.split("Gerne erläutere ich meine Erfahrungen im Gespräch.")[1]
    assert "Kündigungsfrist" in conclusion


def test_english_start_date_and_notice_period(tmp_path, profile, job) -> None:
    settings = load_settings()
    settings = settings.model_copy(deep=True)
    settings.cover_letter.output_dir = tmp_path
    settings.cover_letter.compile_pdf = False
    settings.cover_letter.api_key = "test"
    profile.earliest_start = "2026-03-01"
    profile.notice_period_months = 2
    english = dict(FakeLLM().result)
    english["language"] = "en"

    generate_cover_letter(
        settings,
        job,
        description="We are looking for n8n and Python.",
        profile=profile,
        llm=FakeLLM(english),
    )

    text = (tmp_path / "indeed-abc-123.tex").read_text(encoding="utf-8")
    assert "My notice period is two months." in text
    assert "I am available to start on 1 March 2026." in text


def test_prompt_text_omits_empty_and_template_rendered_fields(profile: Profile) -> None:
    text = profile.prompt_text()
    assert "postal_code: '10115'" in text
    assert "soft_skills:" in text
    # Rendered by the template, so the model must not see them.
    assert "confidential_application" not in text
    assert "earliest_start" not in text
    assert "notice_period_months" not in text


def test_prompt_text_includes_the_motivation(profile: Profile) -> None:
    profile.motivation = "Ich baue Automatisierungen, dieteams wirklich entlasten."
    text = profile.prompt_text()
    assert "motivation: Ich baue Automatisierungen" in text


@pytest.mark.parametrize(
    ("value", "german", "english"),
    [
        ("2026-03-01", "Ab dem 1. März 2026 bin ich verfügbar.", "I am available to start on 1 March 2026."),
        ("2026-03", "Ab März 2026 bin ich verfügbar.", "I am available to start in March 2026."),
        ("01.03.2026", "Ab dem 1. März 2026 bin ich verfügbar.", "I am available to start on 1 March 2026."),
        ("sofort", "Ich bin sofort verfügbar.", "I am available to start immediately."),
        ("nach Verhandlung", "Ich bin nach Verhandlung verfügbar.", "I am available to start nach Verhandlung."),
    ],
)
def test_availability_sentence_formats(value: str, german: str, english: str) -> None:
    assert availability_sentence(value, "de") == german
    assert availability_sentence(value, "en") == english


def test_availability_sentence_ignores_empty_values() -> None:
    assert availability_sentence("   ", "de") == ""


def test_notice_period_sentence_uses_correct_grammar() -> None:
    assert notice_period_sentence(1, "de") == "Meine Kündigungsfrist beträgt einen Monat."
    assert notice_period_sentence(3, "de") == "Meine Kündigungsfrist beträgt drei Monate."
    assert notice_period_sentence(1, "en") == "My notice period is one month."
    assert notice_period_sentence(2, "en") == "My notice period is two months."


def test_confidentiality_sentence_is_localised() -> None:
    assert confidentiality_sentence("de") == "Bitte behandeln Sie meine Bewerbung vertraulich."
    assert confidentiality_sentence("en") == "Please treat my application confidentially."


def test_example_postal_code_blocks_generation(profile: Profile) -> None:
    profile.postal_code = "12345"
    assert "Replace the example postal code." in profile.readiness_issues()


def test_example_soft_skill_blocks_generation(profile: Profile) -> None:
    profile.soft_skills = ["Example teamwork"]
    assert "Replace or remove the example soft skill." in profile.readiness_issues()


def test_profile_api_roundtrips_new_fields(tmp_path, profile: Profile, monkeypatch) -> None:
    from fastapi.testclient import TestClient
    from jobscout.ui import server

    path = tmp_path / "profile.yaml"
    monkeypatch.setattr(server.settings.cover_letter, "profile_path", path)
    client = TestClient(server.app)

    profile.postal_code = "10115"
    profile.soft_skills = ["Teamwork"]
    profile.motivation = "Ich baue Automatisierungen für Teams."
    profile.earliest_start = "2026-03-01"
    profile.notice_period_months = 1
    profile.confidential_application = True

    response = client.put("/api/profile", json=profile.model_dump(mode="json"))
    assert response.status_code == 200
    assert response.json()["profile"]["postal_code"] == "10115"

    payload = client.get("/api/profile").json()["profile"]
    assert payload["soft_skills"] == ["Teamwork"]
    assert payload["motivation"] == "Ich baue Automatisierungen für Teams."
    assert payload["earliest_start"] == "2026-03-01"
    assert payload["notice_period_months"] == 1
    assert payload["confidential_application"] is True


def test_custom_cover_letter_generation(tmp_path, profile: Profile, monkeypatch) -> None:
    from fastapi.testclient import TestClient
    from jobscout.ui import server

    path = tmp_path / "profile.yaml"
    monkeypatch.setattr(server.settings.cover_letter, "profile_path", path)
    monkeypatch.setattr(server.settings.cover_letter, "output_dir", tmp_path)
    monkeypatch.setattr(server.settings.cover_letter, "compile_pdf", False)
    monkeypatch.setattr(server.settings.cover_letter, "api_key", "test")
    monkeypatch.setattr(
        server,
        "generate_cover_letter",
        lambda *args, **kwargs: FakeLLM().generate(profile, None, kwargs.get("description", "")) and kwargs.get("on_progress", lambda *_: None)("done", 1.0) or type(
            "Result",
            (),
            {
                "source": "custom",
                "job_id": "custom-123",
                "company": "Creative Pool",
                "title": "Kauffrau für Dialogmarketing",
                "language": "de",
                "tex_path": str(tmp_path / "custom-custom-123.tex"),
                "pdf_path": None,
                "metadata_path": str(tmp_path / "custom-custom-123.json"),
                "reused": False,
                "warning": None,
                "pdf_error": None,
                "model_dump": lambda self, mode="json": {
                    "source": "custom",
                    "job_id": "custom-123",
                    "company": "Creative Pool",
                    "title": "Kauffrau für Dialogmarketing",
                    "language": "de",
                    "tex_path": str(tmp_path / "custom-custom-123.tex"),
                    "pdf_path": None,
                },
            },
        )(),
    )
    client = TestClient(server.app)

    response = client.post(
        "/api/cover-letters",
        json={
            "source": "custom",
            "job_id": "custom-123",
            "company": "Creative Pool",
            "title": "Kauffrau für Dialogmarketing",
            "description": "Wir suchen Kauffrau für Dialogmarketing...",
            "force": True,
        },
    )
    assert response.status_code == 200, response.json()
    payload = response.json()
    assert payload["letter"]["source"] == "custom"
    assert payload["letter"]["company"] == "Creative Pool"



def test_description_cache_roundtrip(tmp_path, job) -> None:
    settings = load_settings().model_copy(deep=True)
    settings.cover_letter.description_dir = tmp_path / "descriptions"
    text = "Full job description with Python, n8n and stakeholder communication."
    path = save_job_description(settings, job, text)
    assert path.exists()
    assert load_saved_description(settings, "indeed", "abc-123") == text


def test_fetched_description_is_saved_and_reused(
    tmp_path, profile, job, monkeypatch
) -> None:
    settings = load_settings().model_copy(deep=True)
    settings.cover_letter.output_dir = tmp_path / "letters"
    settings.cover_letter.description_dir = tmp_path / "descriptions"
    settings.cover_letter.compile_pdf = False
    settings.cover_letter.api_key = "test"

    fetched = "Full job description with Python, n8n and stakeholder communication."
    calls: list[str] = []

    def fake_fetch(_settings, _job):
        calls.append(_job.job_id)
        return fetched

    monkeypatch.setattr(
        "jobscout.cover_letter.generator.fetch_job_description",
        fake_fetch,
    )
    llm = FakeLLM()
    generate_cover_letter(settings, job, profile=profile, llm=llm)
    assert calls == ["abc-123"]
    assert load_saved_description(settings, "indeed", "abc-123") == fetched
    assert llm.last_description == fetched

    generate_cover_letter(settings, job, force=True, profile=profile, llm=llm)
    assert calls == ["abc-123"]
    assert llm.calls == 2


def test_description_api_saves_and_serves(tmp_path, job, monkeypatch) -> None:
    from fastapi.testclient import TestClient
    from jobscout.ui import server

    monkeypatch.setattr(
        server.settings.cover_letter,
        "description_dir",
        tmp_path / "descriptions",
    )

    class FakeSink:
        def list_jobs(self, limit=100):
            return [job]

    monkeypatch.setattr(server, "build_sink", lambda *_args, **_kwargs: FakeSink())
    monkeypatch.setattr(
        server,
        "fetch_job_description",
        lambda _settings, _job: "Full job description with Python and n8n.",
    )
    client = TestClient(server.app)

    response = client.post("/api/jobs/indeed/abc-123/description")
    assert response.status_code == 200, response.json()
    assert response.json()["saved"] is True
    assert response.json()["cached"] is False

    text = client.get("/api/jobs/indeed/abc-123/description")
    assert text.status_code == 200
    assert text.text == "Full job description with Python and n8n."

    cached = client.post("/api/jobs/indeed/abc-123/description")
    assert cached.status_code == 200
    assert cached.json()["cached"] is True

    jobs = client.get("/api/jobs").json()["jobs"]
    assert jobs[0]["has_description"] is True
    assert jobs[0]["description_url"] == "/api/jobs/indeed/abc-123/description"



def test_auto_fetch_descriptions_limits_and_saves(monkeypatch, tmp_path) -> None:
    from jobscout import orchestrator as orchestrator_module
    from jobscout.models import Job

    settings = load_settings()
    settings.cover_letter.description_dir = tmp_path
    settings.cover_letter.auto_fetch_limit = 1

    fetched: list[str] = []

    def fake_fetch(_settings, job):
        fetched.append(job.job_id)
        return "A complete job description for testing."

    monkeypatch.setattr(orchestrator_module, "fetch_job_description", fake_fetch)

    saved: list[tuple[str, str, str]] = []

    class FakeSink:
        def save_description(self, source, job_id, description):
            saved.append((source, job_id, description))

    class FakeRegistrar:
        def status(self, *_args, **_kwargs):
            pass

        def send(self, *_args, **_kwargs):
            pass

        def log(self, *_args, **_kwargs):
            pass

    class FakeControl:
        def checkpoint(self, *_args, **_kwargs):
            pass

    orchestrator = orchestrator_module.Orchestrator(
        settings,
        FakeControl(),
        lambda *_args, **_kwargs: None,
        ["linkedin"],
        False,
        "run-test",
        "search",
        0,
    )
    jobs = [
        Job(
            company="One",
            title="Role One",
            link="https://example.com/1",
            source="linkedin",
            job_id="1",
        ),
        Job(
            company="Two",
            title="Role Two",
            link="https://example.com/2",
            source="linkedin",
            job_id="2",
        ),
    ]
    orchestrator._fetch_descriptions(FakeSink(), jobs, FakeRegistrar())

    assert fetched == ["1"]
    assert len(saved) == 1
    assert saved[0][1] == "1"
    assert (tmp_path / "linkedin-1.json").exists()
