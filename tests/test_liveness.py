from pathlib import Path

import httpx
import pytest

from jobscout.config import LivenessConfig, Settings
from jobscout.liveness import (
    ACTIVE,
    EXPIRED,
    UNCERTAIN,
    LivenessStore,
    check_batch,
    check_liveness,
    detect_ats,
    probe_liveness,
    store_for,
)
from jobscout.models import Job


def make_job(job_id: str = "1", link: str = "https://acme.example/jobs/1") -> Job:
    return Job(
        company="Acme",
        title="AI Engineer",
        link=link,
        source="linkedin",
        job_id=job_id,
    )


def settings_for(tmp_path: Path, **overrides) -> Settings:
    liveness = LivenessConfig(enabled=True, **overrides)
    return Settings(root=tmp_path, liveness=liveness)


def client_for(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


# ---------------------------------------------------------------------------
# ATS detection
# ---------------------------------------------------------------------------


def test_detects_greenhouse() -> None:
    ref = detect_ats("https://boards.greenhouse.io/acme/jobs/123456?gh_src=xyz")
    assert ref is not None
    assert ref.provider == "greenhouse"
    assert ref.company == "acme"
    assert ref.job_id == "123456"
    assert ref.api_url.endswith("/acme/jobs/123456")


def test_detects_lever_ashby_workable_smartrecruiters() -> None:
    lever = detect_ats("https://jobs.lever.co/acme/9a2f3f0a-1111-2222-3333-444455556666")
    assert lever is not None and lever.provider == "lever"

    ashby = detect_ats("https://jobs.ashbyhq.com/acme/9a2f3f0a-1111-2222-3333-444455556666")
    assert ashby is not None and ashby.provider == "ashby"

    workable = detect_ats("https://apply.workable.com/acme/j/9A2F3F0A11/")
    assert workable is not None and workable.provider == "workable"

    smart = detect_ats(
        "https://jobs.smartrecruiters.com/acme/1234567890-senior-ai-engineer"
    )
    assert smart is not None and smart.provider == "smartrecruiters"
    assert smart.job_id == "1234567890"


def test_generic_url_is_not_an_ats() -> None:
    assert detect_ats("https://acme.example/jobs/1") is None
    assert detect_ats("") is None
    assert detect_ats(None) is None


# ---------------------------------------------------------------------------
# ATS API probing
# ---------------------------------------------------------------------------


def test_ats_api_404_and_410_are_expired() -> None:
    for code in (404, 410):
        def handler(request: httpx.Request, code=code) -> httpx.Response:
            return httpx.Response(code)

        job = make_job(link="https://boards.greenhouse.io/acme/jobs/123")
        with client_for(handler) as client:
            result = probe_liveness(job, client=client)
        assert result.status == EXPIRED
        assert result.source == "ats_api"
        assert result.reason == f"ats_api_{code}"


def test_ats_api_200_is_active() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": 123, "title": "AI Engineer"})

    job = make_job(link="https://boards.greenhouse.io/acme/jobs/123")
    with client_for(handler) as client:
        result = probe_liveness(job, client=client)
    assert result.status == ACTIVE
    assert result.reason == "ats_api_ok"


def test_ats_api_auth_and_5xx_are_uncertain_not_expired() -> None:
    for code in (401, 403, 500, 503):
        def handler(request: httpx.Request, code=code) -> httpx.Response:
            return httpx.Response(code)

        job = make_job(link="https://boards.greenhouse.io/acme/jobs/123")
        with client_for(handler) as client:
            result = probe_liveness(job, client=client)
        assert result.status == UNCERTAIN, code


def test_ats_network_error_is_uncertain() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nope")

    job = make_job(link="https://boards.greenhouse.io/acme/jobs/123")
    with client_for(handler) as client:
        result = probe_liveness(job, client=client)
    assert result.status == UNCERTAIN
    assert result.reason == "network_error"


def test_board_api_missing_job_is_expired() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"jobs": [{"id": "other-job", "title": "Backend Engineer"}]},
        )

    job = make_job(link="https://jobs.ashbyhq.com/acme/this-job")
    with client_for(handler) as client:
        result = probe_liveness(job, client=client)
    assert result.status == EXPIRED
    assert result.reason == "ats_board_missing_job"


def test_board_api_present_job_is_active() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"jobs": [{"shortcode": "this-job"}]})

    job = make_job(link="https://jobs.ashbyhq.com/acme/this-job")
    with client_for(handler) as client:
        result = probe_liveness(job, client=client)
    assert result.status == ACTIVE
    assert result.reason == "ats_board_contains_job"


def test_board_api_empty_board_is_uncertain() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"jobs": []})

    job = make_job(link="https://jobs.ashbyhq.com/acme/this-job")
    with client_for(handler) as client:
        result = probe_liveness(job, client=client)
    assert result.status == UNCERTAIN
    assert result.reason == "board_empty"


# ---------------------------------------------------------------------------
# Generic page probing
# ---------------------------------------------------------------------------


def test_page_closure_banner_is_expired() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="<html>This position has been filled.</html>",
        )

    with client_for(handler) as client:
        result = probe_liveness(make_job(), client=client)
    assert result.status == EXPIRED
    assert result.reason == "closure_banner"
    assert result.source == "page"


def test_page_german_closure_banner_is_expired() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>Diese Stelle wurde besetzt.</html>")

    with client_for(handler) as client:
        result = probe_liveness(make_job(), client=client)
    assert result.status == EXPIRED
    assert result.reason == "closure_banner"


def test_page_410_is_expired_but_404_and_403_are_uncertain() -> None:
    for code, expected in ((410, EXPIRED), (404, UNCERTAIN), (403, UNCERTAIN)):
        def handler(request: httpx.Request, code=code) -> httpx.Response:
            return httpx.Response(code)

        with client_for(handler) as client:
            result = probe_liveness(make_job(), client=client)
        assert result.status == expected, code


def test_page_login_wall_is_uncertain() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>Sign in to view this job</html>")

    with client_for(handler) as client:
        result = probe_liveness(make_job(), client=client)
    assert result.status == UNCERTAIN
    assert result.reason == "login_wall"


def test_page_captcha_is_uncertain() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>Verify you are human</html>")

    with client_for(handler) as client:
        result = probe_liveness(make_job(), client=client)
    assert result.status == UNCERTAIN


def test_reachable_page_without_closure_is_active() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>We are hiring an AI Engineer.</html>")

    with client_for(handler) as client:
        result = probe_liveness(make_job(), client=client)
    assert result.status == ACTIVE
    assert result.reason == "page_reachable"


def test_job_without_url_is_uncertain() -> None:
    job = make_job(link="")

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("should not be called")

    with client_for(handler) as client:
        result = probe_liveness(job, client=client)
    assert result.status == UNCERTAIN
    assert result.reason == "no_url"


# ---------------------------------------------------------------------------
# Retry policy (3 attempts / 48 hours)
# ---------------------------------------------------------------------------


def test_deadline_expired_short_circuits(tmp_path: Path) -> None:
    job = make_job()
    job.deadline_status = "expired"

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("should not be probed")

    settings = settings_for(tmp_path)
    with client_for(handler) as client:
        result = check_liveness(job, settings=settings, client=client)
    assert result.status == EXPIRED
    assert result.reason == "deadline_passed"
    assert job.expired_reason == "deadline_passed"


def test_persists_status_and_attempts(tmp_path: Path) -> None:
    job = make_job()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>hiring</html>")

    settings = settings_for(tmp_path)
    with client_for(handler) as client:
        result = check_liveness(job, settings=settings, client=client)
    assert result.status == ACTIVE
    assert job.status == ACTIVE
    assert job.liveness_checked_at
    assert job.liveness_attempts == 1

    store = store_for(settings)
    entry = store.load()["linkedin:1"]
    assert entry["last_status"] == ACTIVE


def test_three_uncertain_attempts_over_48h_promote_to_expired(
    tmp_path: Path, monkeypatch
) -> None:
    job = make_job()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403)

    settings = settings_for(tmp_path, max_attempts=3, retry_after_hours=48.0)
    clock = {"now": 1_000_000.0}
    monkeypatch.setattr("jobscout.liveness.time.time", lambda: clock["now"])

    store = store_for(settings)
    with client_for(handler) as client:
        first = check_liveness(job, settings=settings, client=client, store=store)
        assert first.status == UNCERTAIN

        # Second attempt one hour later: still uncertain.
        clock["now"] += 3600
        second = check_liveness(job, settings=settings, client=client, store=store)
        assert second.status == UNCERTAIN

        # Third attempt after the 48h window has elapsed: promoted.
        clock["now"] += 49 * 3600
        third = check_liveness(job, settings=settings, client=client, store=store)
        assert third.status == EXPIRED
        assert third.reason == "unreachable_after_attempts"

    assert job.status == EXPIRED
    assert job.expired_reason == "unreachable_after_attempts"
    assert job.liveness_attempts == 3


def test_uncertain_stays_uncertain_before_48h(tmp_path: Path, monkeypatch) -> None:
    job = make_job()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403)

    settings = settings_for(tmp_path, max_attempts=3, retry_after_hours=48.0)
    clock = {"now": 1_000_000.0}
    monkeypatch.setattr("jobscout.liveness.time.time", lambda: clock["now"])

    store = store_for(settings)
    with client_for(handler) as client:
        for _ in range(3):
            result = check_liveness(job, settings=settings, client=client, store=store)
            clock["now"] += 60  # three checks inside one minute
    assert result.status == UNCERTAIN


def test_force_rechecks_a_promoted_job(tmp_path: Path, monkeypatch) -> None:
    job = make_job()

    def blocked(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403)

    def alive(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>hiring</html>")

    settings = settings_for(tmp_path, max_attempts=3, retry_after_hours=48.0)
    clock = {"now": 1_000_000.0}
    monkeypatch.setattr("jobscout.liveness.time.time", lambda: clock["now"])

    store = store_for(settings)
    with client_for(blocked) as client:
        for _ in range(3):
            check_liveness(job, settings=settings, client=client, store=store)
            clock["now"] += 50 * 3600
    assert job.status == EXPIRED

    # Without --all the promoted verdict is served from the store.
    with client_for(alive) as client:
        result = check_liveness(job, settings=settings, client=client, store=store)
    assert result.status == EXPIRED
    assert result.reason == "unreachable_after_attempts"

    # With --all the job is probed again and recovers.
    with client_for(alive) as client:
        result = check_liveness(
            job, settings=settings, client=client, store=store, force=True
        )
    assert result.status == ACTIVE
    assert job.status == ACTIVE


def test_real_closure_banner_keeps_its_reason(tmp_path: Path) -> None:
    """A genuine closure must never be relabeled as unreachable."""
    job = make_job()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>Job posting has expired</html>")

    settings = settings_for(tmp_path)
    store = store_for(settings)
    with client_for(handler) as client:
        result = check_liveness(job, settings=settings, client=client, store=store)
    assert result.status == EXPIRED
    assert result.reason == "closure_banner"
    assert job.expired_reason == "closure_banner"


def test_successful_check_resets_the_retry_budget(tmp_path: Path) -> None:
    job = make_job()
    settings = settings_for(tmp_path, max_attempts=3, retry_after_hours=48.0)
    store = store_for(settings)

    def blocked(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403)

    def alive(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>hiring</html>")

    with client_for(blocked) as client:
        for _ in range(2):
            check_liveness(job, settings=settings, client=client, store=store)
    with client_for(alive) as client:
        check_liveness(job, settings=settings, client=client, store=store)

    entry = store.load()["linkedin:1"]
    assert entry["attempts"] == 3  # every probe is counted
    assert entry["uncertain_attempts"] == 0  # the retry budget was reset


def test_check_batch_counts(tmp_path: Path) -> None:
    jobs = [make_job("1"), make_job("2", "https://acme.example/jobs/2")]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/2"):
            return httpx.Response(410)
        return httpx.Response(200, text="<html>hiring</html>")

    settings = settings_for(tmp_path)
    with client_for(handler) as client:
        counts = check_batch(jobs, settings=settings, client=client)
    assert counts == {ACTIVE: 1, EXPIRED: 1, UNCERTAIN: 0}


def test_check_batch_respects_limit(tmp_path: Path) -> None:
    jobs = [make_job(str(index)) for index in range(5)]
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, text="<html>hiring</html>")

    settings = settings_for(tmp_path, max_checks=2)
    with client_for(handler) as client:
        counts = check_batch(jobs, settings=settings, client=client)
    assert len(calls) == 2
    assert counts[ACTIVE] == 2


def test_store_reset_removes_entry(tmp_path: Path) -> None:
    job = make_job()
    store = LivenessStore(tmp_path / "liveness.json")
    store.record(job, probe_result_uncertain(), max_attempts=3)
    assert store.load()
    store.reset(job)
    assert store.load() == {}


def probe_result_uncertain():
    from jobscout.liveness import LivenessResult

    return LivenessResult(status=UNCERTAIN, reason="network_error", source="page")