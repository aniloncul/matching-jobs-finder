from test_cv_master import make_profile

from jobscout.cv.master import MasterCV
from jobscout.cv.payload import from_master, plain_text
from jobscout.cv.tailor import (
    apply_llm_tailoring,
    bold_label,
    build_summary,
    cut_bullets,
    derived_metric_claims,
    find_gaps,
    order_bullets,
    pick_competencies,
    relevance_score,
    tailor_payload,
    target_keywords,
    unsupported_terms,
)

JD = """Ihr Profil:
Erforderlich: Python, Docker und Kubernetes.
Von Vorteil: Erfahrung mit Kafka.
"""


def make_parts():
    master = MasterCV.from_profile(make_profile())
    payload = from_master(master)
    keywords = target_keywords(JD)
    return master, payload, keywords


def test_keywords_flag_priority_and_gaps_stay_honest() -> None:
    master, _payload, keywords = make_parts()
    required = {item.term for item in keywords if item.priority == "required"}
    assert {"Python", "Docker", "Kubernetes"} <= required

    gaps = find_gaps(keywords, master)
    terms = [gap.term for gap in gaps]
    assert "Kubernetes" in terms
    assert "Kafka" in " ".join(terms)
    # Skills the master CV does have are never gaps.
    assert "Python" not in terms and "Docker" not in terms
    # Ad boilerplate is not reported as a missing requirement.
    assert "erfahrung" not in [term.lower() for term in terms]


def test_a_gap_is_never_written_into_the_document() -> None:
    master, payload, keywords = make_parts()
    tailored, plan = tailor_payload(payload, master, keywords)
    assert plan.gaps
    document = plain_text(tailored)
    for gap in plan.gaps:
        assert gap.term not in document
    assert tailored.gaps == plan.gaps


def test_ordering_is_relevance_first_and_stable() -> None:
    master, payload, keywords = make_parts()
    tailored, moved = None, None
    reordered, moved = order_bullets(payload, keywords)
    first = reordered.experience[0].bullets[0]
    assert "Docker" in first.text  # the Docker bullet beats the other one
    assert moved == 1
    # Re-applying the same order changes nothing.
    again, moved_again = order_bullets(reordered, keywords)
    assert moved_again == 0
    assert [b.text for b in again.experience[0].bullets] == [
        b.text for b in reordered.experience[0].bullets
    ]


def test_cutting_protects_the_only_carrier_of_a_required_keyword() -> None:
    master = MasterCV.from_profile(
        make_profile(
            experience=[
                {
                    "company": "Acme GmbH",
                    "role": "AI Engineer",
                    "start": "2022",
                    "end": "present",
                    "achievements": [
                        "Unterstuetzte das Team bei allgemeinen Aufgaben.",
                        "Unterstuetzte das Team bei taeglichen Aufgaben.",
                        "Baute eine Docker Pipeline fuer das Reporting.",
                    ],
                }
            ]
        )
    )
    payload = from_master(master)
    keywords = target_keywords(JD)
    cut, dropped = cut_bullets(payload, keywords, max_bullets_per_role=2)
    kept = " ".join(b.text for b in cut.experience[0].bullets)
    assert "Docker" in kept
    assert len(dropped) == 1
    assert len(cut.experience[0].bullets) == 2


def test_bold_label_only_uses_terms_the_bullet_already_proves() -> None:
    _master, _payload, keywords = make_parts()
    assert bold_label("Baute eine Docker Pipeline.", keywords) == "Docker"
    assert bold_label("Verantwortete den Bereitschaftsdienst.", keywords) == ""


def test_competencies_come_only_from_the_master_skill_list() -> None:
    master, _payload, keywords = make_parts()
    grid = pick_competencies(master, keywords)
    assert 1 <= len(grid) <= 8
    supported = {item.casefold() for item in master.supported_skills()}
    assert all(item.casefold() in supported for item in grid)
    assert "kubernetes" not in {item.casefold() for item in grid}


def test_tailor_payload_fills_meta_and_notes() -> None:
    master, payload, keywords = make_parts()
    payload.summary = "AI Engineer mit Fokus auf Automatisierung."
    tailored, plan = tailor_payload(payload, master, keywords)
    assert tailored.meta.keywords == [item.term for item in keywords]
    assert tailored.meta.coverage_pct == plan.coverage_pct
    assert any("target keywords" in note for note in plan.notes)
    assert relevance_score("Baute Docker.", keywords) > relevance_score(
        "Allgemeine Aufgaben.", keywords
    )


# ---------------------------------------------------------------------------
# Summary building (the only place a derived number may appear)
# ---------------------------------------------------------------------------


def test_build_summary_without_years_uses_no_numbers() -> None:
    master = MasterCV.from_profile(make_profile())
    summary = build_summary(master, (), include_years=False)
    assert summary
    # No claims with numbers; the only digits allowed are inside skill names.
    assert not any(char.isdigit() for char in summary.replace("n8n", ""))
    assert master.headline in summary


def test_derived_years_are_the_only_allowed_metric() -> None:
    master = MasterCV.from_profile(make_profile())
    years = int(master.years_experience or 0)
    claims = derived_metric_claims(master)
    assert f"{years} Jahren" in claims
    summary = build_summary(master, (), include_years=True)
    assert f"{years} Jahren" in summary

    from jobscout.cv.verify import verify_cv
    from jobscout.cv.payload import from_master as build

    payload = build(master)
    payload.summary = summary
    verification = verify_cv(payload, master)
    assert verification.verdict != "block"
    assert not any("30" in blocker for blocker in verification.blockers)

    payload.summary = "AI Engineer mit 9 Jahren Berufserfahrung."
    strict = verify_cv(payload, master)
    assert strict.verdict == "block"


# ---------------------------------------------------------------------------
# LLM tailoring JSON (only choice and order belong to the model)
# ---------------------------------------------------------------------------


def test_llm_tailoring_applies_the_safe_subset() -> None:
    master, payload, _keywords = make_parts()
    first = payload.experience[0]
    data = {
        "summary": "AI Engineer mit Fokus auf Automatisierung und Docker.",
        "competencies": ["Python", "Docker", "Stakeholder Kommunikation"],
        "labels": [{"role": 0, "index": 0, "label": "Reportingsystem"}],
        "bullet_order": [{"role": 0, "order": [1, 0]}],
        "notes": ["Umgestellt."],
    }
    tailored, notes = apply_llm_tailoring(payload, master, data)
    assert tailored.summary == data["summary"]
    assert tailored.competencies == data["competencies"]
    # Order moves the bullets; the label was set on the bullet itself and travels.
    bullets = tailored.experience[0].bullets
    assert bullets[1].text == first.bullets[0].text
    assert bullets[1].label == "Reportingsystem"
    assert any("Umgestellt." in note for note in notes)


def test_llm_tailoring_refuses_invention() -> None:
    master, payload, _keywords = make_parts()
    data = {
        "summary": "Experte fuer Kubernetes mit 10 Jahren Erfahrung.",
        "competencies": ["Kubernetes", "Python"],
        "labels": [{"role": 0, "index": 0, "label": "Kubernetes"}],
        "bullet_order": [{"role": 0, "order": [0, 5]}],
        "experience": [{"company": "Fiktion AG", "bullets": []}],
    }
    tailored, notes = apply_llm_tailoring(payload, master, data)
    assert tailored.summary == payload.summary
    assert "Kubernetes" not in tailored.competencies
    assert "Python" in tailored.competencies
    assert all(not b.label for b in tailored.experience[0].bullets)
    assert [b.text for b in tailored.experience[0].bullets] == [
        b.text for b in payload.experience[0].bullets
    ]
    joined = " ".join(notes)
    assert "Ignored unsupported tailoring keys: experience" in joined
    assert "Kubernetes" in joined


def test_unsupported_terms_detects_tools_and_numbers() -> None:
    evidence = MasterCV.from_profile(make_profile()).evidence_text()
    found = [term.casefold() for term in unsupported_terms("Erfahrung mit Kubernetes", evidence)]
    assert "kubernetes" in found
    assert any("50" in term for term in unsupported_terms("Kosten um 50% gesenkt", evidence))
    assert unsupported_terms("Python und Docker genutzt", evidence) == []
