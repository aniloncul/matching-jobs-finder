import pytest
from test_cv_master import make_profile

from jobscout.cv.master import MasterCV
from jobscout.cv.payload import from_master
from jobscout.cv.verify import (
    check_title_drift,
    cross_document_checks,
    format_verification,
    title_rank,
    verify_cv,
)


@pytest.fixture()
def base():
    master = MasterCV.from_profile(make_profile())
    return master, from_master(master)


def test_clean_master_cv_passes(base) -> None:
    master, payload = base
    result = verify_cv(payload, master)
    assert result.verdict == "pass"
    assert result.passed
    assert "PASS" in format_verification(result)


def test_invented_employer_blocks(base) -> None:
    master, payload = base
    role = payload.experience[0].model_copy()
    role.company = "Geheimnis AG"
    payload.experience.append(role)
    result = verify_cv(payload, master)
    assert result.verdict == "block"
    assert any("Geheimnis AG" in blocker for blocker in result.blockers)


def test_invented_role_blocks(base) -> None:
    master, payload = base
    payload.experience[0].role = "Principal ML Architect"
    result = verify_cv(payload, master)
    assert result.verdict == "block"
    assert any("Principal ML Architect" in blocker for blocker in result.blockers)


def test_fabricated_metrics_and_tools_block(base) -> None:
    master, payload = base
    payload.experience[0].bullets[0].text = (
        "Baute ein Kubernetes Cluster, das die Kosten um 55% senkte."
    )
    result = verify_cv(payload, master)
    assert result.verdict == "block"
    joined = " ".join(result.blockers)
    assert "55%" in joined or "Kubernetes" in joined


def test_forbidden_phrase_blocks(base) -> None:
    master, payload = base
    payload.experience[0].bullets[0].text = (
        "Das neue Reportingsystem fuer die Antwortzeit wurde patentiert."
    )
    result = verify_cv(payload, master, forbidden_phrases=["patentiert"])
    assert result.verdict == "block"
    assert any("patentiert" in blocker for blocker in result.blockers)


def test_cut_bullets_only_warn(base) -> None:
    master, payload = base
    payload.experience[0].bullets.pop()
    result = verify_cv(payload, master)
    assert result.verdict == "warn"
    assert any("cut" in warning for warning in result.warnings)


def test_bullet_without_fact_id_warns(base) -> None:
    master, payload = base
    payload.experience[0].bullets[0].fact_ids = []
    result = verify_cv(payload, master)
    assert result.verdict == "warn"
    assert any("no master CV fact id" in warning for warning in result.warnings)


# ---------------------------------------------------------------------------
# Title drift
# ---------------------------------------------------------------------------


def test_title_rank_ordering() -> None:
    assert title_rank("Praktikant Automation") < title_rank("Senior AI Engineer")
    assert title_rank("AI Engineer") < title_rank("Tech Lead")
    assert title_rank("Head of AI") > title_rank("Lead AI Engineer")
    assert title_rank("CTO") == 6


def test_promotion_is_flagged(base) -> None:
    master, payload = base
    payload.headline = "Head of AI Engineering"
    issues = check_title_drift(master, payload)
    assert any("Title drift" in issue for issue in issues)

    payload.headline = "AI Engineer"
    payload.experience[1].role = "Engineering Manager"  # was Werkstudent
    assert any("Nordwind" in issue for issue in check_title_drift(master, payload))


def test_lateral_or_honest_titles_pass(base) -> None:
    master, payload = base
    payload.headline = "AI Engineer"
    assert check_title_drift(master, payload) == []


# ---------------------------------------------------------------------------
# CV <-> letter consistency
# ---------------------------------------------------------------------------


def test_letter_must_not_disagree_with_the_cv(base) -> None:
    master, payload = base
    clean = (
        "Bewerbung als AI Engineer. Von 2022 bis heute senkte ich die "
        "Antwortzeit um 30% bei Acme GmbH."
    )
    assert cross_document_checks(payload, master, clean, today_year=2026) == []

    bad_year = "Bewerbung. Ich arbeitete 2001 bei Acme GmbH."
    issues = cross_document_checks(payload, master, bad_year, today_year=2026)
    assert any("2001" in issue for issue in issues)

    bad_metric = "Bewerbung. Ich senkte die Kosten um 90% in 2023."
    issues = cross_document_checks(payload, master, bad_metric, today_year=2026)
    assert any("90%" in issue for issue in issues)


def test_cross_document_is_opt_in(base) -> None:
    master, payload = base
    result = verify_cv(payload, master, letter_text="Unfug aus 1999 ueber 99%.")
    assert result.verdict == "block" or any(
        "1999" in warning for warning in result.cross_document_issues
    )
    result_off = verify_cv(
        payload, master, letter_text="Unfug aus 1999 ueber 99%.", check_cross_document=False
    )
    assert result_off.cross_document_issues == []
