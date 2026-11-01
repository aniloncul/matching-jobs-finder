from jobscout.config import Settings
from jobscout.control import RunControl
from jobscout.dedupe import (
    ExistingJobs,
    canonical_url,
    dedupe_jobs,
    hamming_distance,
    job_role_key,
    role_key_values,
    simhash,
)
from jobscout.models import Job
from jobscout.sources.base import SourceResult
from jobscout.sources.cached import CachedSource


def make_job(
    job_id: str = "1",
    source: str = "linkedin",
    company: str = "Acme",
    title: str = "AI Engineer",
    location: str = "Remote",
    link: str | None = None,
    description: str | None = None,
) -> Job:
    return Job(
        company=company,
        title=title,
        link=link or f"https://example.com/{source}/{job_id}",
        source=source,
        job_id=job_id,
        location=location,
        description=description,
    )


# ---------------------------------------------------------------------------
# SourceResult contract
# ---------------------------------------------------------------------------


def test_source_result_distinguishes_outcomes() -> None:
    ok = SourceResult(jobs=[make_job()], status="ok", pages_fetched=2)
    empty = SourceResult(jobs=[], status="empty", pages_fetched=1)
    blocked = SourceResult(jobs=[], status="blocked", message="captcha")

    assert ok.ok and ok.count == 1
    assert empty.ok and empty.count == 0
    assert not blocked.ok
    assert blocked.message == "captcha"


def test_source_result_to_dict_is_json_safe() -> None:
    result = SourceResult(jobs=[make_job(), make_job("2")], status="ok")
    payload = result.to_dict()
    assert payload == {
        "status": "ok",
        "message": None,
        "pages_fetched": 0,
        "jobs": 2,
    }


def test_cached_source_finishes_with_source_result(tmp_path) -> None:
    settings = Settings(root=tmp_path)
    events: list[tuple[str, dict]] = []
    fixture_dir = tmp_path / "data" / "fixtures"
    fixture_dir.mkdir(parents=True)
    (fixture_dir / "linkedin_jobs.json").write_text("[]", encoding="utf-8")

    source = CachedSource(
        "linkedin",
        settings,
        RunControl(),
        lambda kind, **payload: events.append((kind, payload)),
    )
    result = source.search()

    assert isinstance(result, SourceResult)
    assert result.status == "empty"
    assert result.ok
    kinds = [kind for kind, _ in events]
    assert "source.result" in kinds
    emitted = dict(events[-1][1])
    assert emitted["status"] == "empty"


# ---------------------------------------------------------------------------
# Canonical URLs
# ---------------------------------------------------------------------------


def test_canonical_url_strips_tracking_and_normalizes() -> None:
    left = canonical_url(
        "https://www.linkedin.com/jobs/view/12345/?refId=abc&trackingId=xyz"
    )
    right = canonical_url("https://linkedin.com/jobs/view/12345")
    assert left == right

    bare = canonical_url("HTTP://Acme.Example/Jobs/1/")
    assert bare == "https://acme.example/jobs/1"


def test_canonical_url_keeps_meaningful_query_params() -> None:
    a = canonical_url("https://acme.example/jobs?team=ai&loc=berlin")
    b = canonical_url("https://acme.example/jobs?loc=berlin&team=ai")
    c = canonical_url("https://acme.example/jobs?team=platform&loc=berlin")
    assert a == b
    assert a != c


def test_canonical_url_of_garbage_is_empty() -> None:
    assert canonical_url("") == ""
    assert canonical_url("not a url") == ""


# ---------------------------------------------------------------------------
# Role keys (company + role cascade)
# ---------------------------------------------------------------------------


def test_role_key_folds_company_suffixes_and_gender_markers() -> None:
    assert role_key_values("Acme GmbH", "AI Engineer (m/w/d)") == ("acme", "ai engineer")
    assert role_key_values("The Acme, Inc.", "AI Engineer / AI Engineer (f/m/x)") == (
        "acme",
        "ai engineer",
    )


def test_role_key_keeps_distinct_roles_apart() -> None:
    assert role_key_values("Acme", "AI Engineer") != role_key_values("Acme", "ML Engineer")
    assert role_key_values("Acme", "AI Engineer") != role_key_values("Beta", "AI Engineer")


def test_job_role_key_matches_helper() -> None:
    job = make_job(company="Acme GmbH", title="Data Scientist (m/f/d)")
    assert job_role_key(job) == role_key_values("Acme GmbH", "Data Scientist (m/f/d)")


# ---------------------------------------------------------------------------
# Dedupe cascade
# ---------------------------------------------------------------------------


def test_canonical_url_duplicates_are_collapsed_even_across_sources() -> None:
    jobs = [
        make_job("1", "linkedin", link="https://acme.example/jobs/1?utm_source=li"),
        make_job("abc", "indeed", link="https://acme.example/jobs/1"),
    ]
    unique, skipped = dedupe_jobs(jobs)
    assert len(unique) == 1
    assert skipped == 1


def test_role_cascade_only_applies_when_cross_source() -> None:
    jobs = [
        make_job("1", "linkedin", company="Acme", title="AI Engineer", location="Berlin"),
        make_job("2", "indeed", company="Acme", title="AI Engineer", location="Berlin"),
    ]
    kept, skipped = dedupe_jobs(jobs, cross_source=False)
    assert len(kept) == 2 and skipped == 0

    kept, skipped = dedupe_jobs(jobs, cross_source=True)
    assert len(kept) == 1 and skipped == 1


def test_existing_urls_and_roles_are_skipped() -> None:
    jobs = [
        make_job("1", link="https://acme.example/jobs/1"),
        make_job("2", company="Acme", title="ML Engineer", link="https://acme.example/jobs/2"),
    ]
    existing = ExistingJobs(
        urls={canonical_url("https://acme.example/jobs/1")},
        roles={role_key_values("Acme", "ML Engineer")},
    )
    unique, skipped = dedupe_jobs(
        jobs,
        existing_urls=existing.urls,
        existing_roles=existing.roles,
        cross_source=True,
    )
    assert unique == []
    assert skipped == 2


def test_excluded_urls_and_roles_never_come_back() -> None:
    jobs = [
        make_job("1", link="https://acme.example/jobs/1"),
        make_job("2", company="Acme", title="ML Engineer", link="https://other.example/x"),
    ]
    unique, skipped = dedupe_jobs(
        jobs,
        excluded_urls={canonical_url("https://acme.example/jobs/1")},
        excluded_roles={role_key_values("Acme", "ML Engineer")},
        cross_source=True,
    )
    assert unique == []
    assert skipped == 2


def test_first_seen_and_last_seen_are_populated() -> None:
    job = make_job("1")
    unique, _ = dedupe_jobs([job])
    assert unique[0].first_seen == unique[0].scraped_at
    assert unique[0].last_seen == unique[0].scraped_at


def test_canonical_url_is_written_back_to_the_job() -> None:
    job = make_job("1", link="https://acme.example/Jobs/1?utm_source=x")
    unique, _ = dedupe_jobs([job])
    assert unique[0].canonical_url == "https://acme.example/jobs/1"


# ---------------------------------------------------------------------------
# SimHash (warning only, never a silent skip)
# ---------------------------------------------------------------------------


def test_simhash_is_deterministic() -> None:
    text = "We are looking for an AI Engineer with strong Python skills."
    assert simhash(text) == simhash(text)
    assert simhash("") == 0


def test_simhash_detects_near_duplicates() -> None:
    base = (
        "We are looking for an AI Engineer with strong Python skills "
        "and experience with LLMs in a remote team."
    )
    near = base.replace("remote team", "distributed team")
    far = (
        "Warehouse worker wanted. Forklift license required. "
        "Night shifts in Hamburg. No prior experience needed."
    )
    assert hamming_distance(simhash(base), simhash(near)) <= 8
    assert hamming_distance(simhash(base), simhash(far)) > 20


def test_simhash_warns_but_keeps_the_job() -> None:
    description = (
        "We are looking for an AI Engineer with strong Python skills "
        "and experience with LLMs in a remote team."
    )
    jobs = [
        make_job(
            "1",
            "linkedin",
            company="Acme",
            description=description,
            link="https://acme.example/jobs/1",
        ),
        make_job(
            "2",
            "linkedin",
            company="Beta",
            description=description.replace("remote team", "distributed team"),
            link="https://acme.example/jobs/2",
        ),
    ]
    warnings: list[str] = []
    unique, skipped = dedupe_jobs(jobs, warnings=warnings)

    assert len(unique) == 2, "SimHash must never drop a job on its own"
    assert skipped == 0
    assert len(warnings) == 1
    assert "Possible duplicate" in warnings[0]


def test_simhash_ignores_same_company_rows() -> None:
    # The same company posting twice is handled by the strong cascade, not by
    # the advisory hash.
    description = "We are looking for an AI Engineer with strong Python skills."
    jobs = [
        make_job("1", "linkedin", company="Acme", description=description),
        make_job("2", "linkedin", company="Acme", description=description),
    ]
    warnings: list[str] = []
    unique, _ = dedupe_jobs(jobs, warnings=warnings)
    assert len(unique) == 2
    assert warnings == []


def test_simhash_fingerprint_is_stored_on_the_job() -> None:
    job = make_job("1", description="We are looking for an AI Engineer.")
    unique, _ = dedupe_jobs([job])
    assert unique[0].fingerprint
    assert len(unique[0].fingerprint) == 16