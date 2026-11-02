import pytest
from test_cv_master import make_profile

from jobscout.cv.master import MasterCV
from jobscout.cv.payload import from_master
from jobscout.cv.reviewer import (
    ReviewerError,
    apply_edits,
    parse_reviewer_payload,
)


@pytest.fixture()
def base():
    master = MasterCV.from_profile(make_profile())
    payload = from_master(master)
    payload.summary = "AI Engineer mit Fokus auf Automatisierung."
    return master, payload


def test_parser_is_strict() -> None:
    assert parse_reviewer_payload({"edits": []}) == []
    edits = parse_reviewer_payload(
        {"edits": [{"op": "remove_bullet", "reason": "structure", "role": 0, "index": 1}]}
    )
    assert edits[0].op == "remove_bullet"

    with pytest.raises(ReviewerError):
        parse_reviewer_payload({"edits": "nope"})
    with pytest.raises(ReviewerError):
        parse_reviewer_payload({})
    # Closed enums: an unknown op or reason must not parse.
    with pytest.raises(ReviewerError):
        parse_reviewer_payload({"edits": [{"op": "rewrite_life", "reason": "clarity"}]})
    with pytest.raises(ReviewerError):
        parse_reviewer_payload({"edits": [{"op": "set_label", "reason": "vibes"}]})
    # No extra keys.
    with pytest.raises(ReviewerError):
        parse_reviewer_payload(
            {"edits": [{"op": "set_label", "reason": "clarity", "confidence": 0.9}]}
        )


def test_grounding_is_reported_never_applied(base) -> None:
    master, payload = base
    edits = parse_reviewer_payload(
        {
            "edits": [
                {
                    "op": "remove_bullet",
                    "reason": "grounding",
                    "role": 0,
                    "index": 0,
                    "rationale": "Ich glaube das nicht",
                }
            ]
        }
    )
    fresh, outcome = apply_edits(payload, edits, master)
    assert len(fresh.experience[0].bullets) == len(payload.experience[0].bullets)
    assert outcome.applied == []
    assert outcome.skipped_grounding == edits
    assert any("grounding" in note for note in outcome.notes)


def test_reframing_is_shown_not_applied(base) -> None:
    master, payload = base
    edits = parse_reviewer_payload(
        {
            "edits": [
                {
                    "op": "replace_bullet",
                    "reason": "reframing",
                    "role": 0,
                    "index": 0,
                    "value": "Verantwortete die komplette Produktstrategie.",
                }
            ]
        }
    )
    fresh, outcome = apply_edits(payload, edits, master)
    assert outcome.needs_review == edits
    assert fresh.experience[0].bullets[0].text == payload.experience[0].bullets[0].text


def test_safe_edits_apply_when_grounded(base) -> None:
    master, payload = base
    original = payload.experience[0].bullets[0].text
    edits = parse_reviewer_payload(
        {
            "edits": [
                {
                    "op": "replace_bullet",
                    "reason": "grammar",
                    "role": 0,
                    "index": 0,
                    "value": "Baute ein Reportingsystem und senkte die Antwortzeit um 30%.",
                },
                {
                    "op": "set_label",
                    "reason": "keyword",
                    "role": 1,
                    "index": 0,
                    "value": "n8n",
                },
                {"op": "remove_bullet", "reason": "structure", "role": 1, "index": 0},
            ]
        }
    )
    fresh, outcome = apply_edits(payload, edits, master)
    assert len(outcome.applied) == 3
    assert fresh.experience[0].bullets[0].text != original
    assert "30%" in fresh.experience[0].bullets[0].text
    assert len(fresh.experience[1].bullets) == 0
    assert outcome.rejected == []
    # The original payload is untouched.
    assert payload.experience[1].bullets


def test_edits_that_add_facts_are_rejected(base) -> None:
    master, payload = base
    edits = parse_reviewer_payload(
        {
            "edits": [
                {
                    "op": "replace_bullet",
                    "reason": "clarity",
                    "role": 0,
                    "index": 0,
                    "value": "Baute ein Reportingsystem mit Kubernetes und sparte 90 Prozent.",
                },
                {
                    "op": "replace_summary",
                    "reason": "tone",
                    "value": "Kubernetes-Experte mit 20 Jahren Erfahrung.",
                },
                {
                    "op": "set_label",
                    "reason": "keyword",
                    "role": 0,
                    "index": 0,
                    "value": "Kubernetes",
                },
                {"op": "set_competencies", "reason": "keyword", "value": ["Kubernetes"]},
                {
                    "op": "replace_bullet",
                    "reason": "clarity",
                    "role": 9,
                    "index": 0,
                    "value": "Unsinn.",
                },
            ]
        }
    )
    fresh, outcome = apply_edits(payload, edits, master)
    assert outcome.applied == []
    assert len(outcome.rejected) == 5
    reasons = " | ".join(why for _edit, why in outcome.rejected)
    assert "Kubernetes" in reasons
    assert "does not appear in the bullet" in reasons
    assert "target bullet does not exist" in reasons
    assert fresh.experience[0].bullets[0].text == payload.experience[0].bullets[0].text


def test_set_competencies_drops_unsupported_entries(base) -> None:
    master, payload = base
    edits = parse_reviewer_payload(
        {
            "edits": [
                {
                    "op": "set_competencies",
                    "reason": "keyword",
                    "value": ["Python", "Kubernetes", "Docker"],
                }
            ]
        }
    )
    fresh, outcome = apply_edits(payload, edits, master)
    assert [item.casefold() for item in fresh.competencies] == ["python", "docker"]
    assert any("dropped" in item.description for item in outcome.applied)
