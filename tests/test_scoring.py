from datetime import date

import pytest

from jobscout.models import Job
from jobscout.scoring import (
    BANDS,
    FitScoreError,
    FitScores,
    WEIGHTS,
    compute_fit,
    format_ranking,
    parse_fit_payload,
    passing,
    rank_jobs,
)


TODAY = date(2026, 11, 1)


def scores(
    technical: int = 70,
    experience: int = 70,
    behavioral: int = 70,
    career: int = 70,
    **kwargs,
) -> FitScores:
    return FitScores(
        technical=technical,
        experience=experience,
        behavioral=behavioral,
        career=career,
        **kwargs,
    )


def make_job(job_id: str = "1", deadline: str | None = None) -> Job:
    return Job(
        company="Acme",
        title="AI Engineer",
        link=f"https://example.com/{job_id}",
        source="linkedin",
        job_id=job_id,
        deadline=deadline,
    )


def test_weights_sum_to_one() -> None:
    assert sum(WEIGHTS.values()) == 1


def test_compute_fit_uses_exact_weights() -> None:
    # 80*.30 + 60*.25 + 40*.15 + 50*.30 = 24 + 15 + 6 + 15 = 60
    fit = compute_fit(scores(80, 60, 40, 50), today=TODAY)
    assert fit.score == 60
    assert fit.verdict == "Good Fit"
    assert not fit.veto


def test_compute_fit_rounds_half_up() -> None:
    # 50*.30 + 100*.25 + 30*.15 + 50*.30 = 15 + 25 + 4.5 + 15 = 59.5 -> 60
    fit = compute_fit(scores(50, 100, 30, 50), today=TODAY)
    assert fit.score == 60

    # 65*.30 + 65*.25 + 60*.15 + 55*.30 = 19.5 + 16.25 + 9 + 16.5 = 61.25 -> 61
    fit = compute_fit(scores(65, 65, 60, 55), today=TODAY)
    assert fit.score == 61

    # 50*.30 + 50*.25 + 50*.15 + 50*.30 = 50 exactly; boundary must not drift
    fit = compute_fit(scores(50, 50, 50, 50), today=TODAY)
    assert fit.score == 50

    # 75*.30 + 75*.25 + 75*.15 + 75*.30 = 75
    fit = compute_fit(scores(75, 75, 75, 75), today=TODAY)
    assert fit.score == 75

    # 100*.30 + 100*.25 + 100*.15 + 100*.30 = 100
    fit = compute_fit(scores(100, 100, 100, 100), today=TODAY)
    assert fit.score == 100


def test_band_thresholds() -> None:
    assert [floor for floor, _ in BANDS] == [75, 60, 45, 30, 0]
    for value, label in ((75, "Strong Fit"), (60, "Good Fit"), (45, "Moderate Fit"), (30, "Weak Fit")):
        fit = compute_fit(scores(value, value, value, value), today=TODAY)
        assert fit.verdict == label
    fit = compute_fit(scores(10, 10, 10, 10), today=TODAY)
    assert fit.verdict == "Poor Fit"


def test_language_gate_fail_is_veto() -> None:
    fit = compute_fit(scores(100, 100, 100, 100, language_gate="fail"), today=TODAY)
    assert fit.veto
    assert fit.verdict == "Veto"
    assert "language_gate" in fit.veto_reasons
    assert fit.score == 100  # the number is still computed deterministically


def test_location_fail_is_veto() -> None:
    fit = compute_fit(scores(90, 90, 90, 90, location_verdict="fail"), today=TODAY)
    assert fit.veto
    assert "location" in fit.veto_reasons


def test_unknown_gate_is_not_veto() -> None:
    fit = compute_fit(
        scores(50, 50, 50, 50, language_gate="unknown", location_verdict="unknown"),
        today=TODAY,
    )
    assert not fit.veto


def test_closing_deadline_is_flagged_and_used_as_tiebreaker() -> None:
    closing = make_job("1", deadline="2026-11-05")
    later = make_job("2", deadline="2026-12-31")
    fit_a = compute_fit(scores(70, 70, 70, 70), deadline=closing.deadline, today=TODAY)
    fit_b = compute_fit(scores(70, 70, 70, 70), deadline=later.deadline, today=TODAY)
    assert fit_a.closing_soon
    assert not fit_b.closing_soon

    # Same score: the closing one must rank first.
    ordered = rank_jobs([(later, fit_b), (closing, fit_a)])
    assert [job.job_id for job, _ in ordered] == ["1", "2"]


def test_vetoed_jobs_rank_last() -> None:
    vetoed = make_job("1")
    clean = make_job("2")
    fit_veto = compute_fit(scores(100, 100, 100, 100, location_verdict="fail"), today=TODAY)
    fit_clean = compute_fit(scores(30, 30, 30, 30), today=TODAY)
    ordered = rank_jobs([(vetoed, fit_veto), (clean, fit_clean)])
    assert [job.job_id for job, _ in ordered] == ["2", "1"]


def test_rank_jobs_sets_rank_fields() -> None:
    job = make_job("9")
    fit = compute_fit(scores(80, 80, 80, 80), today=TODAY)
    rank_jobs([(job, fit)])
    assert job.rank_score == 80
    assert job.rank_verdict == "Strong Fit"
    assert job.ranked_at


def test_passing_filters_by_score_and_veto() -> None:
    good = make_job("1")
    weak = make_job("2")
    vetoed = make_job("3")
    pairs = [
        (good, compute_fit(scores(80, 80, 80, 80), today=TODAY)),
        (weak, compute_fit(scores(30, 30, 30, 30), today=TODAY)),
        (vetoed, compute_fit(scores(90, 90, 90, 90, language_gate="fail"), today=TODAY)),
    ]
    kept = passing(pairs, min_score=60)
    assert [job.job_id for job in kept] == ["1"]


def test_parse_fit_payload_accepts_full_schema() -> None:
    payload = {
        "technical": 70,
        "experience": 65,
        "behavioral": 60,
        "career": 55,
        "strengths": ["Python"],
        "gaps": ["Kubernetes"],
        "language_gate": "pass",
        "location_verdict": "unknown",
    }
    parsed = parse_fit_payload(payload)
    assert parsed.technical == 70
    assert parsed.strengths == ["Python"]


def test_parse_fit_payload_rejects_unknown_keys() -> None:
    payload = {
        "technical": 70,
        "experience": 65,
        "behavioral": 60,
        "career": 55,
        "score": 99,  # the model must not decide the total
    }
    with pytest.raises(FitScoreError):
        parse_fit_payload(payload)


def test_parse_fit_payload_rejects_out_of_range() -> None:
    with pytest.raises(FitScoreError):
        parse_fit_payload(
            {"technical": 101, "experience": 5, "behavioral": 5, "career": 5}
        )
    with pytest.raises(FitScoreError):
        parse_fit_payload(
            {"technical": -1, "experience": 5, "behavioral": 5, "career": 5}
        )


def test_parse_fit_payload_rejects_non_object() -> None:
    with pytest.raises(FitScoreError):
        parse_fit_payload(["technical", 70])  # type: ignore[arg-type]


def test_format_ranking_marks_veto() -> None:
    job = make_job("1", deadline="2026-11-05")
    fit = compute_fit(scores(80, 80, 80, 80), deadline=job.deadline, today=TODAY)
    text = format_ranking(job, fit)
    assert "Acme" in text
    assert "80" in text
    assert "closing soon" in text

    veto_fit = compute_fit(scores(90, 90, 90, 90, location_verdict="fail"), today=TODAY)
    assert "VETO" in format_ranking(job, veto_fit)