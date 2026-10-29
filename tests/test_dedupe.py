from jobscout.dedupe import dedupe_jobs
from jobscout.models import Job


def job(
    source: str,
    job_id: str,
    *,
    company: str = "Acme",
    title: str = "AI Engineer",
    location: str = "Remote",
) -> Job:
    return Job(
        company=company,
        title=title,
        link=f"https://example.com/{source}/{job_id}",
        source=source,
        job_id=job_id,
        location=location,
    )


def test_dedupes_same_source_job_id() -> None:
    jobs = [job("linkedin", "1"), job("linkedin", "1"), job("linkedin", "2")]
    unique, skipped = dedupe_jobs(jobs)
    assert [item.job_id for item in unique] == ["1", "2"]
    assert skipped == 1


def test_cross_source_duplicates_kept_by_default() -> None:
    jobs = [job("linkedin", "1"), job("indeed", "abc")]
    unique, skipped = dedupe_jobs(jobs)
    assert len(unique) == 2
    assert skipped == 0


def test_cross_source_can_be_collapsed() -> None:
    jobs = [job("linkedin", "1"), job("indeed", "abc")]
    unique, skipped = dedupe_jobs(jobs, cross_source=True)
    assert [item.source.value for item in unique] == ["linkedin"]
    assert skipped == 1


def test_existing_rows_are_skipped() -> None:
    jobs = [job("linkedin", "1"), job("linkedin", "2")]
    unique, skipped = dedupe_jobs(jobs, existing_keys={("linkedin", "1")})
    assert [item.job_id for item in unique] == ["2"]
    assert skipped == 1
