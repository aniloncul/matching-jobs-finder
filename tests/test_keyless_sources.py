"""Keyless source adapters: parsing, error taxonomy, registry and wiring.

Every adapter's HTTP layer is replaced with a fake client, so the suite is
offline and deterministic; only the public `KeylessSource.client` property is
patched, which is exactly what the adapters touch.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from jobscout.cli import _sources
from jobscout.config import KEYLESS_SOURCE_NAMES, Settings, load_settings
from jobscout.control import RunControl
from jobscout.events import EventBus
from jobscout.models import Job, RunStatus, Source
from jobscout.run_manager import RunManager
from jobscout.sources import KEYLESS_SOURCES, SourceError, make_source
from jobscout.sources.arbeitnow import ArbeitnowSource
from jobscout.sources.ats_board import BoardsSource, ats_identifiers, parse_board_ref
from jobscout.sources.ba_jobsuche import BundesagenturSource
from jobscout.sources.base import SourceResult
from jobscout.sources.cached import CachedSource
from jobscout.sources.hn_hiring import HackerNewsSource, split_company
from jobscout.sources.keyless import KeylessSource, html_to_text
from jobscout.sources.rss import RssSource

LEVER_ID = "12345678-90ab-cdef-1234-567890abcdef"


class FakeResponse:
    def __init__(self, status_code: int = 200, json_data=None, text: str = "") -> None:
        self.status_code = status_code
        self._json = json_data
        self.text = text if json_data is None else json.dumps(json_data)

    @property
    def content(self) -> bytes:
        return self.text.encode("utf-8")

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


class FakeClient:
    """Routes requests by (method, URL substring); records everything."""

    def __init__(self, routes: list[tuple[str, str, FakeResponse]]) -> None:
        self.routes = routes
        self.calls: list[tuple[str, str]] = []
        self.closed = False

    def _respond(self, method: str, url: str) -> FakeResponse:
        self.calls.append((method, url))
        for route_method, pattern, response in self.routes:
            if route_method == method and pattern in url:
                return response
        raise AssertionError(f"unexpected request: {method} {url}")

    def request(self, method: str, url: str, **_kwargs):
        return self._respond(method.upper(), url)

    def get(self, url: str, **_kwargs):
        return self._respond("GET", url)

    def post(self, url: str, **_kwargs):
        return self._respond("POST", url)

    def close(self) -> None:
        self.closed = True


def make_emit():
    events: list[tuple[str, dict]] = []
    return events, (lambda kind, **kwargs: events.append((kind, kwargs)))


def make_source_instance(cls, tmp_path: Path, monkeypatch, routes, keyless=None):
    settings = Settings(root=tmp_path, sources={"keyless": keyless or {}})
    events, emit = make_emit()
    fake = FakeClient(routes)
    monkeypatch.setattr(KeylessSource, "client", property(lambda self: fake))
    source = cls(settings, RunControl(), emit)
    source._client = fake  # so close() reaches the fake, as it does live
    return source, events, fake


# -- shared helpers ------------------------------------------------------------


def test_parse_board_ref_and_url_identifiers():
    assert parse_board_ref("greenhouse:acme") == ("greenhouse", "acme")
    with pytest.raises(Exception):
        parse_board_ref("acme")
    assert ats_identifiers("https://app.greenhouse.io/acme/jobs/123") == (
        "greenhouse",
        "acme",
        "123",
    )
    assert ats_identifiers(f"https://jobs.lever.co/bees/{LEVER_ID}")[0] == "lever"
    assert ats_identifiers(f"https://jobs.ashbyhq.com/x/{LEVER_ID}")[0] == "ashby"
    assert ats_identifiers("https://example.com/jobs/1") is None


def test_html_to_text_keeps_structure_short():
    text = html_to_text("<p>Build <b>pipelines</b></p><ul><li>Airflow</li></ul>")
    assert text == "Build pipelines\nAirflow"


# -- ATS boards ------------------------------------------------------------------

GREENHOUSE_PAYLOAD = {
    "jobs": [
        {
            "id": 123,
            "title": "Senior Python Engineer",
            "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/123",
            "location": {"name": "Berlin, Germany"},
            "content": "<p>Build <b>pipelines</b></p><ul><li>Airflow</li></ul>",
        },
        {
            "id": 124,
            "title": "Sales Lead",
            "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/124",
            "location": {"name": "Berlin, Germany"},
            "content": "<p>Quotas</p>",
        },
    ]
}


def test_greenhouse_board_maps_identity_and_description(tmp_path, monkeypatch):
    source, _events, _fake = make_source_instance(
        BoardsSource,
        tmp_path,
        monkeypatch,
        [("GET", "boards-api.greenhouse.io", FakeResponse(json_data=GREENHOUSE_PAYLOAD))],
        keyless={"boards": ["greenhouse:acme"], "query": "python", "max_jobs": 10},
    )
    result = source.search()
    assert isinstance(result, SourceResult) and result.ok
    assert len(result.jobs) == 1  # query filter drops "Sales Lead"
    job = result.jobs[0]
    assert job.job_id == "gh-123"
    assert job.source is Source.boards
    assert job.company == "Acme"
    assert job.requisition_id == "123"
    assert job.canonical_url == job.link
    assert "Airflow" in (job.description or "")


def test_lever_and_ashby_boards(tmp_path, monkeypatch):
    lever = [
        {
            "id": LEVER_ID,
            "text": "Backend Engineer",
            "hostedUrl": f"https://jobs.lever.co/bees/{LEVER_ID}",
            "categories": {"location": "Remote"},
            "descriptionPlain": "plain body",
            "createdAt": 1730000000000,
        }
    ]
    ashby = {
        "jobs": [
            {
                "id": LEVER_ID,
                "title": "Data Engineer",
                "location": "Remote (EU)",
                "jobUrl": f"https://jobs.ashbyhq.com/start/{LEVER_ID}",
                "descriptionHtml": "<p>Kafka</p>",
                "isListed": True,
                "publishedAt": "2026-10-30T08:00:00.000Z",
            }
        ]
    }
    source, _events, _fake = make_source_instance(
        BoardsSource,
        tmp_path,
        monkeypatch,
        [
            ("GET", "api.lever.co", FakeResponse(json_data=lever)),
            ("GET", "api.ashbyhq.com", FakeResponse(json_data=ashby)),
        ],
        keyless={"boards": ["lever:bees", "ashby:start"], "query": "engineer"},
    )
    result = source.search()
    assert result.ok and len(result.jobs) == 2
    lever_job, ashby_job = result.jobs
    assert lever_job.job_id == f"lever-{LEVER_ID}"
    assert lever_job.posted_date == "2024-10-27"
    assert ashby_job.job_id == f"ashby-{LEVER_ID}"
    assert ashby_job.posted_date == "2026-10-30"


def test_board_partial_failure_still_returns_jobs(tmp_path, monkeypatch):
    source, events, _fake = make_source_instance(
        BoardsSource,
        tmp_path,
        monkeypatch,
        [
            ("GET", "greenhouse", FakeResponse(status_code=500)),
            ("GET", "lever", FakeResponse(json_data=[])),
        ],
        keyless={"boards": ["greenhouse:broken", "lever:ok"], "query": ""},
    )
    result = source.search()
    assert result.ok and result.jobs == []
    warnings = [kw for kind, kw in events if kind == "agent.log" and kw.get("level") == "warning"]
    assert warnings and "greenhouse:broken" in warnings[0]["message"]


def test_board_auth_failure_is_blocked(tmp_path, monkeypatch):
    source, _events, _fake = make_source_instance(
        BoardsSource,
        tmp_path,
        monkeypatch,
        [("GET", "greenhouse", FakeResponse(status_code=403))],
        keyless={"boards": ["greenhouse:locked"]},
    )
    result = source.search()
    assert result.status == "blocked"


# -- Arbeitnow / HN / BA / RSS -----------------------------------------------------

ARBEITNOW_PAYLOAD = {
    "data": [
        {
            "slug": "python-dev-1",
            "title": "Python Developer",
            "company_name": "NanoTech",
            "location": "Munich",
            "url": "https://www.arbeitnow.com/job/python-dev-1",
            "description": "<p>Django and FastAPI</p>",
            "created_at": 1730000000,
            "tags": ["python", "django"],
        }
    ]
}


def test_arbeitnow_maps_jobs(tmp_path, monkeypatch):
    source, _events, fake = make_source_instance(
        ArbeitnowSource,
        tmp_path,
        monkeypatch,
        [("GET", "arbeitnow.com/api", FakeResponse(json_data=ARBEITNOW_PAYLOAD))],
        keyless={"query": "python", "max_jobs": 10},
    )
    result = source.search()
    assert result.ok and len(result.jobs) == 1
    job = result.jobs[0]
    assert job.job_id == "python-dev-1" and job.source is Source.arbeitnow
    assert job.company == "NanoTech" and job.posted_date == "2024-10-27"
    assert "Tags: python, django" in (job.description or "")
    assert fake.closed, "adapter must close its http client"


def test_hackernews_maps_and_splits(tmp_path, monkeypatch):
    payload = {
        "hits": [
            {
                "objectID": "41000000",
                "title": "Acme \u2014 Staff ML Engineer (Remote)",
                "story_text": "<p>Location: Berlin<br>We use Python.</p>",
                "created_at": "2026-11-01T10:00:00.000Z",
            }
        ]
    }
    source, _events, _fake = make_source_instance(
        HackerNewsSource,
        tmp_path,
        monkeypatch,
        [("GET", "hn.algolia.com", FakeResponse(json_data=payload))],
        keyless={"query": "ml", "max_jobs": 10},
    )
    result = source.search()
    assert result.ok and len(result.jobs) == 1
    job = result.jobs[0]
    assert job.job_id == "41000000" and job.source is Source.hn
    assert job.company == "Acme" and "ML Engineer" in job.title
    assert job.location == "Berlin" and job.posted_date == "2026-11-01"
    assert job.link.endswith("/item?id=41000000")
    assert split_company("Plain title") == ("Hacker News", "Plain title")


def test_ba_without_key_is_blocked_with_guidance(tmp_path, monkeypatch):
    source, _events, _fake = make_source_instance(
        BundesagenturSource, tmp_path, monkeypatch, []
    )
    result = source.search()
    assert result.status == "blocked"
    assert "JOBSCOUT_BA_API_KEY" in (result.message or "")


BA_PAYLOAD = {
    ".jobsuche": {
        "treffer": [
            {
                "id": "10000-123",
                "headlines": [{"titel": "Python Entwickler"}],
                "dienstort": {"ort": "Hamburg"},
                "kontakt": {"name": "Mueller GmbH"},
                "stellenangebotText": "<p>Django</p>",
                "veroeffentlichungsdatum": "2026-10-31",
                "url": "https://www.arbeitsagentur.de/stelle/123",
            }
        ],
        "totalTreffer": 1,
    }
}


def test_ba_with_key_maps_hits(tmp_path, monkeypatch):
    source, _events, _fake = make_source_instance(
        BundesagenturSource,
        tmp_path,
        monkeypatch,
        [("POST", "/arkon/jobsuche", FakeResponse(json_data=BA_PAYLOAD))],
        keyless={"ba_api_key": "test-key", "query": "python"},
    )
    result = source.search()
    assert result.ok and len(result.jobs) == 1
    job = result.jobs[0]
    assert job.job_id == "ba-10000-123" and job.source is Source.ba
    assert job.company == "Mueller GmbH" and job.location == "Hamburg"


RSS_XML = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <title>Acme Careers</title>
  <item>
    <title>Data Engineer @ Acme</title>
    <link>https://acme.example/job/9</link>
    <guid>https://acme.example/job/9</guid>
    <pubDate>Thu, 30 Oct 2026 09:00:00 GMT</pubDate>
    <description>&lt;p&gt;Kafka and Airflow&lt;/p&gt;</description>
  </item>
</channel></rss>
"""


def test_rss_parses_feed(tmp_path, monkeypatch):
    source, _events, _fake = make_source_instance(
        RssSource,
        tmp_path,
        monkeypatch,
        [
            ("GET", "robots.txt", FakeResponse(status_code=404)),
            ("GET", "acme.example/feed", FakeResponse(text=RSS_XML)),
        ],
        keyless={"feeds": ["https://acme.example/feed"], "query": "kafka"},
    )
    result = source.search()
    assert result.ok and len(result.jobs) == 1
    job = result.jobs[0]
    assert job.source is Source.rss
    assert job.company == "Acme" and job.title == "Data Engineer"
    assert job.posted_date == "2026-10-30"
    assert "Kafka" in (job.description or "")


def test_rss_honours_robots(tmp_path, monkeypatch):
    source, _events, _fake = make_source_instance(
        RssSource,
        tmp_path,
        monkeypatch,
        [
            ("GET", "robots.txt", FakeResponse(text="User-agent: *\nDisallow: /")),
            ("GET", "acme.example/feed", FakeResponse(text=RSS_XML)),
        ],
        keyless={"feeds": ["https://acme.example/feed"]},
    )
    result = source.search()
    assert result.status == "blocked"
    assert "robots.txt" in (result.message or "")


def test_rss_empty_config_is_clean_empty(tmp_path, monkeypatch):
    source, _events, _fake = make_source_instance(RssSource, tmp_path, monkeypatch, [])
    result = source.search()
    assert result.ok and result.jobs == []


# -- registry, config and CLI wiring -------------------------------------------------


def test_registry_covers_every_keyless_name():
    assert set(KEYLESS_SOURCES) == set(KEYLESS_SOURCE_NAMES)


def test_make_source_registry(tmp_path):
    _events, emit = make_emit()
    settings = Settings(root=tmp_path)
    for name, cls in KEYLESS_SOURCES.items():
        assert isinstance(make_source(name, settings, RunControl(), emit, False), cls)
    with pytest.raises(SourceError):
        make_source("monster", settings, RunControl(), emit, False)


def test_keyless_cached_falls_back_to_live_without_fixture(tmp_path):
    _events, emit = make_emit()
    settings = Settings(root=tmp_path)
    source = make_source("arbeitnow", settings, RunControl(), emit, cached=True)
    assert isinstance(source, ArbeitnowSource)


def test_keyless_cached_uses_fixture_when_present(tmp_path):
    _events, emit = make_emit()
    settings = Settings(root=tmp_path)
    fixtures = tmp_path / "data" / "fixtures"
    fixtures.mkdir(parents=True)
    job = Job(
        company="Acme",
        title="Python Engineer",
        link="https://acme.example/j/1",
        source=Source.boards,
        job_id="gh-1",
    )
    (fixtures / "boards_jobs.json").write_text(
        json.dumps([job.model_dump(mode="json")]), encoding="utf-8"
    )
    source = make_source("boards", settings, RunControl(), emit, cached=True)
    assert isinstance(source, CachedSource)
    result = source.search()
    assert result.ok and result.jobs[0].job_id == "gh-1"


def test_cli_expands_keyless_group():
    assert _sources("keyless") == list(KEYLESS_SOURCE_NAMES)
    assert _sources("both") == ["linkedin", "indeed"]
    assert _sources("hn") == ["hn"]


def test_source_config_shared_block(tmp_path):
    settings = Settings(root=tmp_path, sources={"keyless": {"max_jobs": 7}})
    for name in KEYLESS_SOURCE_NAMES:
        assert settings.source_config(name).max_jobs == 7
    with pytest.raises(KeyError):
        settings.source_config("monster")


def test_run_request_accepts_keyless_sources():
    from jobscout.ui.server import RunRequest

    assert RunRequest(sources=["boards", "arbeitnow", "hn", "ba", "rss"]).sources
    with pytest.raises(ValidationError):
        RunRequest(sources=["monster"])


def test_run_manager_reports_boards_agent(tmp_path, monkeypatch):
    payload = {
        "hits": [
            {
                "objectID": "1",
                "title": "Acme \u2014 ML Engineer",
                "story_text": "<p>Python</p>",
                "created_at": "2026-11-01T10:00:00.000Z",
            }
        ]
    }
    fake = FakeClient([("GET", "hn.algolia.com", FakeResponse(json_data=payload))])
    monkeypatch.setattr(KeylessSource, "client", property(lambda self: fake))
    settings = Settings(
        root=tmp_path,
        sink="local",
        local_jobs_csv=tmp_path / "jobs.csv",
        local_runs_csv=tmp_path / "runs.csv",
        sources={"keyless": {"query": "ml", "max_jobs": 5}},
    )
    manager = RunManager(settings, EventBus())
    manager.start(["hn"], cached=True)
    manager.wait(30)
    state = manager.state
    assert state.status is RunStatus.completed
    assert state.source_results["hn"]["status"] == "ok"
    assert state.source_results["hn"]["jobs"] == 1
    assert state.agents["boards"].status.value == "done"
    text = (tmp_path / "jobs.csv").read_text(encoding="utf-8")
    assert "Acme" in text and "hn" in text


# keep load_settings import used (guards env wiring for the new sources)
def test_env_wiring_smoke(monkeypatch):
    monkeypatch.setenv("JOBSCOUT_BA_API_KEY", "abc")
    monkeypatch.setenv("JOBSCOUT_BOARDS", "greenhouse:acme, lever:bees")
    monkeypatch.setenv("JOBSCOUT_RSS_FEEDS", "https://a.example/feed")
    settings = load_settings()
    assert settings.sources.keyless.ba_api_key == "abc"
    assert settings.sources.keyless.boards == ["greenhouse:acme", "lever:bees"]
    assert settings.sources.keyless.feeds == ["https://a.example/feed"]
