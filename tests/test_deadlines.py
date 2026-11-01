from datetime import date

from jobscout.deadlines import (
    ACTIVE,
    CLOSING_SOON,
    EXPIRED,
    UNPARSEABLE,
    UNKNOWN,
    apply_deadlines,
    deadline_status,
    extract_deadline,
    parse_deadline_value,
    sweep_saved_jobs,
)
from jobscout.models import Job


TODAY = date(2026, 11, 1)


def make_job(job_id: str = "1", description: str = "") -> Job:
    return Job(
        company="Acme",
        title="AI Engineer",
        link=f"https://example.com/{job_id}",
        source="linkedin",
        job_id=job_id,
        description=description or None,
    )


def test_parse_iso_and_german_dates() -> None:
    assert parse_deadline_value("2026-12-31") == date(2026, 12, 31)
    assert parse_deadline_value("31.12.2026") == date(2026, 12, 31)
    assert parse_deadline_value("31. Dezember 2026") == date(2026, 12, 31)
    assert parse_deadline_value("December 31, 2026") == date(2026, 12, 31)
    assert parse_deadline_value("") is None
    assert parse_deadline_value("irgendwas") is None


def test_deadline_status_buckets() -> None:
    assert deadline_status(date(2026, 10, 1), today=TODAY) == EXPIRED
    assert deadline_status(date(2026, 11, 3), today=TODAY) == CLOSING_SOON
    assert deadline_status(date(2026, 11, 8), today=TODAY) == CLOSING_SOON
    assert deadline_status(date(2026, 12, 31), today=TODAY) == ACTIVE
    assert deadline_status(None, today=TODAY) == UNKNOWN
    assert deadline_status("nicht parsebar", today=TODAY) == UNPARSEABLE


def test_extract_deadline_finds_marker_date() -> None:
    info = extract_deadline(
        "Wir suchen ab sofort. Bewerbungsfrist: 20.11.2026. Start sofort.",
        today=TODAY,
    )
    assert info.deadline == "2026-11-20"
    assert info.status == ACTIVE


def test_extract_deadline_ignores_unmarked_dates() -> None:
    info = extract_deadline(
        "Eintritt ab 01.01.2027. Befristet bis 31.03.2027.",
        today=TODAY,
    )
    assert info.deadline is None
    assert info.status == UNKNOWN


def test_extract_deadline_reports_unparseable_date() -> None:
    info = extract_deadline("Application deadline: 32.13.9999", today=TODAY)
    assert info.deadline is None
    assert info.status == UNPARSEABLE


def test_extract_deadline_english_marker() -> None:
    info = extract_deadline(
        "Great team. Apply by December 31, 2026.",
        today=TODAY,
    )
    assert info.deadline == "2026-12-31"
    assert info.status == ACTIVE


def test_extract_deadline_before_marker() -> None:
    info = extract_deadline("31.12.2026 ist die Bewerbungsfrist.", today=TODAY)
    assert info.deadline == "2026-12-31"


def test_apply_deadlines_sets_fields_and_flags_expired() -> None:
    jobs = [
        make_job("1", "Bewerbungsfrist: 20.11.2026"),
        make_job("2", "Bewerbungsfrist: 01.10.2026"),
        make_job("3", "Kein Datum hier."),
    ]
    counts = apply_deadlines(jobs, today=TODAY)

    assert jobs[0].deadline == "2026-11-20"
    assert jobs[0].deadline_status == ACTIVE
    assert jobs[1].deadline == "2026-10-01"
    assert jobs[1].deadline_status == EXPIRED
    assert jobs[1].status == "expired"
    assert jobs[1].expired_reason == "deadline_passed"
    assert jobs[2].deadline_status == UNKNOWN
    assert counts[ACTIVE] == 1
    assert counts[EXPIRED] == 1
    assert counts[UNKNOWN] == 1


def test_sweep_saved_jobs_returns_report() -> None:
    jobs = [make_job("1", "Deadline: 2026-11-03")]
    report = sweep_saved_jobs(jobs, today=TODAY)
    assert report[0]["deadline"] == "2026-11-03"
    assert report[0]["deadline_status"] == CLOSING_SOON
    assert report[0]["checked_at"]


def test_closing_days_parameter_is_respected() -> None:
    jobs = [make_job("1", "Deadline: 2026-11-25")]
    apply_deadlines(jobs, today=TODAY, closing_days=7)
    assert jobs[0].deadline_status == ACTIVE
    apply_deadlines(jobs, today=TODAY, closing_days=30)
    assert jobs[0].deadline_status == CLOSING_SOON