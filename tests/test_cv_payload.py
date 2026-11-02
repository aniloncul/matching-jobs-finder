import pytest

from jobscout.cv.master import MasterCV
from jobscout.cv.payload import (
    MAX_BULLETS_PER_ROLE,
    CVRenderPayload,
    CVSchemaError,
    all_fact_ids,
    bullet_count,
    from_master,
    merge_notes,
    parse_payload,
    plain_text,
    to_dict,
)
from test_cv_master import make_profile


def test_from_master_keeps_cv_order_and_fact_ids() -> None:
    master = MasterCV.from_profile(make_profile())
    payload = from_master(master, target_role="AI Engineer", company="Acme GmbH")
    assert [role.company for role in payload.experience] == [
        "Acme GmbH",
        "Nordwind UG",
    ]
    assert all_fact_ids(payload) == [
        *master.roles[0].fact_ids,
        *master.roles[1].fact_ids,
        master.education[0].fact_id,
    ]
    assert payload.meta.target_role == "AI Engineer"


def test_payload_rejects_unknown_keys() -> None:
    with pytest.raises(CVSchemaError):
        parse_payload({"headline": "AI Engineer", "hired": True})
    with pytest.raises(CVSchemaError):
        parse_payload({"experience": [{"company": "Acme", "salary_expectation": 1}]})


def test_round_trip_through_dict() -> None:
    master = MasterCV.from_profile(make_profile())
    payload = from_master(master)
    again = parse_payload(to_dict(payload))
    assert again == payload


def test_bullet_label_renders_as_prefix_in_plain_text() -> None:
    payload = parse_payload(
        {
            "experience": [
                {
                    "company": "Acme GmbH",
                    "role": "AI Engineer",
                    "bullets": [
                        {"text": "Baute Docker Pipelines.", "label": "Docker", "fact_ids": ["ach-1"]}
                    ],
                }
            ]
        }
    )
    text = plain_text(payload)
    assert "Docker: Baute Docker Pipelines." in text


def test_gaps_never_reach_the_rendered_text() -> None:
    payload = parse_payload(
        {
            "headline": "AI Engineer",
            "gaps": [{"term": "Kubernetes", "priority": "required"}],
        }
    )
    assert payload.gaps[0].term == "Kubernetes"
    assert "Kubernetes" not in plain_text(payload)
    assert "Kubernetes" in str(to_dict(payload))


def test_limits_are_enforced() -> None:
    with pytest.raises(CVSchemaError):
        parse_payload({"summary": "x" * 901})
    with pytest.raises(CVSchemaError):
        parse_payload({"headline": "x" * 121})
    with pytest.raises(CVSchemaError):
        parse_payload({"competencies": [f"skill {i}" for i in range(13)]})
    with pytest.raises(CVSchemaError):
        parse_payload(
            {
                "experience": [
                    {
                        "company": "Acme",
                        "bullets": [
                            {"text": f"Bullet number {i} with enough length."}
                            for i in range(MAX_BULLETS_PER_ROLE + 1)
                        ],
                    }
                ]
            }
        )


def test_bullet_count_and_merge_notes_do_not_mutate() -> None:
    master = MasterCV.from_profile(make_profile())
    payload = from_master(master)
    before = bullet_count(payload)
    merged = merge_notes(payload, ["note a", "note a", "  "])
    assert bullet_count(payload) == before
    assert merged.meta.notes.count("note a") == 1
    assert "note a" not in payload.meta.notes
    assert isinstance(merged, CVRenderPayload)
