from pathlib import Path

import pytest

from jobscout.cover_letter.profile import Profile, save_profile
from jobscout.cv.master import (
    CVFact,
    MasterCV,
    MasterCVError,
    fact_id,
    infer_metrics,
    infer_scope,
    infer_skills,
    inspect_master,
    load_master,
)


def make_profile(**overrides) -> Profile:
    data = {
        "name": "Anna Beispiel",
        "headline": "AI Engineer",
        "email": "anna@beispiel.de",
        "phone": "+49 30 55501",
        "city": "Berlin",
        "postal_code": "10115",
        "country": "Germany",
        "summary": "AI Engineer mit Fokus auf Automatisierung.",
        "skills": ["Python", "n8n", "Docker"],
        "soft_skills": ["Stakeholder Kommunikation"],
        "experience": [
            {
                "company": "Acme GmbH",
                "role": "AI Engineer",
                "start": "2022",
                "end": "present",
                "location": "Berlin",
                "achievements": [
                    "Baute ein Reportingsystem, das die Antwortzeit um 30% senkte.",
                    "Unterstuetzte die Migration auf Docker.",
                ],
            },
            {
                "company": "Nordwind UG",
                "role": "Werkstudent Automation",
                "start": "2020",
                "end": "2022",
                "location": "Hamburg",
                "achievements": ["Automatisierte n8n Workflows fuer 3 Teams."],
            },
        ],
        "education": [
            {
                "institution": "TU Berlin",
                "degree": "M.Sc. Informatik",
                "start": "2018",
                "end": "2020",
            }
        ],
        "languages": ["Deutsch - Muttersprache"],
    }
    data.update(overrides)
    return Profile.model_validate(data)


def test_fact_ids_are_stable_and_scoped() -> None:
    assert fact_id("achievement", "Acme", "AI Engineer", "Built X") == fact_id(
        "achievement", "Acme", "AI Engineer", "Built X"
    )
    assert fact_id("achievement", "Acme", "AI Engineer", "Built X") != fact_id(
        "achievement", "Acme", "AI Engineer", "Built Y"
    )
    assert fact_id("achievement", "a", "b", "c").startswith("ach-")


def test_scope_is_inferred_from_the_verb() -> None:
    assert infer_scope("Leitete ein Team von 5 Personen") == "led"
    assert infer_scope("Unterstützte das Team bei der Migration") == "contributed"
    assert infer_scope("Verwendete Python für die Auswertung") == "used"
    assert infer_scope("Reportingsystem im Team genutzt und gepflegt") != "led"


def test_skill_and_metric_inference() -> None:
    skills = infer_skills("Baute eine Docker Pipeline mit Python.", ["Python", "Docker", "Java"])
    assert set(skills) == {"Python", "Docker"}
    assert infer_metrics("Senkte die Antwortzeit um 30% und sparte 12 Stunden.") == (
        "30%",
        "12 Stunden",
    )


def test_master_is_derived_from_the_profile() -> None:
    master = MasterCV.from_profile(make_profile())
    assert master.name == "Anna Beispiel"
    assert [block.company for block in master.roles] == ["Acme GmbH", "Nordwind UG"]
    assert len(master.roles[0].fact_ids) == 2
    assert len(master.facts) == 3 + 1 + 1  # achievements + education + language
    fact = master.fact(master.roles[0].fact_ids[0])
    assert fact is not None
    assert fact.company == "Acme GmbH"
    assert fact.metrics == ("30%",)
    assert fact.scope == "led"
    assert fact.sources[0].startswith("profile:experience[0]")


def test_years_experience_comes_from_the_dates() -> None:
    from datetime import date

    master = MasterCV.from_profile(make_profile())
    assert master.years_experience == float(date.today().year - 2020)


def test_known_years_and_evidence_text() -> None:
    master = MasterCV.from_profile(make_profile())
    assert {2022, 2020, 2018, 2020} <= master.known_years()
    evidence = master.evidence_text()
    assert "Reportingsystem" in evidence
    assert "TU Berlin" in evidence
    assert "n8n" in evidence


def test_duplicate_fact_text_is_not_added_twice() -> None:
    master = MasterCV.from_profile(make_profile())
    before = len(master.facts)
    again = master.add_fact(
        CVFact(
            id=fact_id("achievement", "Acme GmbH", "AI Engineer", "other"),
            kind="achievement",
            text="Baute ein Reportingsystem, das die Antwortzeit um 30% senkte.",
        )
    )
    assert len(master.facts) == before
    assert again.text.startswith("Baute ein Reportingsystem")


def test_find_facts_only_returns_grounded_rows() -> None:
    master = MasterCV.from_profile(make_profile())
    hits = master.find_facts("Docker")
    assert [fact.text for fact in hits] == ["Unterstuetzte die Migration auf Docker."]
    assert master.find_facts("Kubernetes") == []


# ---------------------------------------------------------------------------
# Overlay
# ---------------------------------------------------------------------------


def write_profile(tmp_path: Path, profile: Profile) -> Path:
    path = tmp_path / "profile.yaml"
    save_profile(path, profile)
    return path


def test_overlay_annotates_by_id(tmp_path: Path) -> None:
    profile = make_profile()
    master = MasterCV.from_profile(profile)
    target = master.roles[0].fact_ids[0]
    overlay = tmp_path / "cv.master.yaml"
    overlay.write_text(
        f"""
facts:
  - id: {target}
    skills: ["Python"]
    metrics: ["30%"]
    scope: contributed
    sources:
      - "https://internal.example/metrics"
""",
        encoding="utf-8",
    )
    loaded, issues = inspect_master(write_profile(tmp_path, profile), overlay)
    assert loaded is not None
    assert issues == []
    fact = loaded.fact(target)
    assert fact is not None
    assert fact.scope == "contributed"
    assert fact.skills == ("Python",)
    assert "https://internal.example/metrics" in fact.sources
    # The text itself can never be rewritten by the overlay.
    assert fact.text.startswith("Baute ein Reportingsystem")


def test_overlay_matches_by_text_and_reports_unknown_ids(tmp_path: Path) -> None:
    profile = make_profile()
    overlay = tmp_path / "cv.master.yaml"
    overlay.write_text(
        """
facts:
  - match: "Automatisierte n8n Workflows fuer 3 Teams."
    skills: ["n8n"]
  - id: ach-doesnotexist
    skills: ["Rust"]
""",
        encoding="utf-8",
    )
    master, issues = inspect_master(write_profile(tmp_path, profile), overlay)
    assert master is not None
    assert any("no fact matches id" in issue for issue in issues)
    matches = master.find_facts("n8n")
    assert matches and matches[0].skills == ("n8n",)
    # The unknown annotation must not have created a fact.
    assert master.find_facts("Rust") == []


def test_overlay_can_add_a_fact_into_the_right_role(tmp_path: Path) -> None:
    profile = make_profile()
    overlay = tmp_path / "cv.master.yaml"
    overlay.write_text(
        """
extra_facts:
  - text: "Verantwortete den 24/7 Bereitschaftsdienst fuer zwei Produkte."
    company: "Acme GmbH"
    role: "AI Engineer"
    kind: responsibility
    scope: led
    source: "vorgesetzter"
""",
        encoding="utf-8",
    )
    master, _issues = inspect_master(write_profile(tmp_path, profile), overlay)
    assert master is not None
    role = master.roles[0]
    assert len(role.fact_ids) == 3
    added = master.fact(role.fact_ids[-1])
    assert added is not None
    assert added.kind == "responsibility"
    assert added.scope == "led"


def test_overlay_rejects_an_unknown_scope_quietly_but_keeps_the_fact(tmp_path: Path) -> None:
    profile = make_profile()
    master = MasterCV.from_profile(profile)
    target = master.roles[0].fact_ids[0]
    overlay = tmp_path / "cv.master.yaml"
    overlay.write_text(
        f"""
facts:
  - id: {target}
    scope: "world_record"
""",
        encoding="utf-8",
    )
    loaded, issues = inspect_master(write_profile(tmp_path, profile), overlay)
    assert loaded is not None
    assert issues == []
    assert loaded.fact(target).scope == "led"


def test_load_master_refuses_an_empty_profile(tmp_path: Path) -> None:
    profile = make_profile(experience=[], skills=[], summary="", education=[])
    path = write_profile(tmp_path, profile)
    with pytest.raises(MasterCVError):
        load_master(path)


def test_load_master_reports_missing_profile(tmp_path: Path) -> None:
    with pytest.raises(MasterCVError):
        load_master(tmp_path / "nope.yaml")
