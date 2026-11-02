"""Artifact archive + rebuildable index (roadmap item 19).

The guarantees that matter: the JD is verbatim, the index is disposable
(rebuild from folders), archiving can never crash a run, and the sheet/CSV
flow is untouched.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

from jobscout.artifacts import ArtifactStore, DisabledStore, store_for
from jobscout.config import ROOT, Settings
from jobscout.events import EventBus
from jobscout.models import Job, RunStatus, Source
from jobscout.run_manager import RunManager


def make_job(job_id: str = "gh-1", **overrides) -> Job:
    data = dict(
        company="Acme GmbH",
        title="Python Engineer",
        link="https://acme.example/j/1",
        source=Source.boards,
        job_id=job_id,
        location="Berlin",
        description="Wir bauen  Pipelines.\nAirflow, Kafka — verbatim.",
        rank_score=82,
    )
    data.update(overrides)
    return Job(**data)


# -- writing ---------------------------------------------------------------


def test_archive_job_writes_folder_and_index(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    directory = store.archive_job(make_job())
    assert directory is not None
    payload = json.loads((directory / "job.json").read_text(encoding="utf-8"))
    assert payload["job_id"] == "gh-1" and payload["source"] == "boards"
    assert (directory / "jd.md").exists()
    hits = store.search("Airflow")
    assert hits and hits[0]["source"] == "boards"
    assert hits[0]["rank_score"] == 82


def test_jd_is_stored_verbatim(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    text = "  Zeile eins  \nZweite Zeile mit  doppelten  Leerlaeufen\n"
    directory = store.archive_description("hn", "42", text)
    assert directory is not None
    assert (directory / "jd.md").read_text(encoding="utf-8") == text


def test_unsafe_ids_never_escape_the_archive(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    job = make_job(job_id="../../evil", company="Evil")
    directory = store.archive_job(job)
    assert directory is not None
    archive_root = (tmp_path / "artifacts").resolve()
    assert directory.resolve().is_relative_to(archive_root)


def test_link_documents_are_deduplicated(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    store.archive_job(make_job())
    store.link_document("boards", "gh-1", "cv-pdf", "/tmp/out.pdf")
    store.link_document("boards", "gh-1", "cv-pdf", "/tmp/out.pdf")
    store.link_document("boards", "gh-1", "letter-tex", "/tmp/letter.tex")
    entries = store.documents("boards", "gh-1")
    kinds = sorted(item["kind"] for item in entries)
    assert kinds == ["cv-pdf", "letter-tex"]


# -- index durability --------------------------------------------------------


def test_index_is_rebuildable_from_folders_only(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    store.archive_job(make_job())
    store.archive_job(make_job(job_id="gh-2", company="Bees AG", title="Docker Engineer"))
    store.link_document("boards", "gh-2", "cv-pdf", "/tmp/b.pdf")
    store.db_path.unlink()
    assert store.db_path.exists() is False

    rebuilt = ArtifactStore(tmp_path / "artifacts")
    assert rebuilt.rebuild_index() == 2
    hits = rebuilt.search("Bees")
    assert hits and hits[0]["job_id"] == "gh-2"
    # documents.json survived into the index too
    with sqlite3.connect(rebuilt.db_path) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM documents WHERE kind = 'cv-pdf'"
        ).fetchone()[0]
    assert count == 1


def test_search_falls_back_to_like_without_fts(tmp_path: Path, monkeypatch) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    store.archive_job(make_job())

    def boom(*_args, **_kwargs):
        raise sqlite3.OperationalError("no such module: fts5")

    monkeypatch.setattr(ArtifactStore, "_search_rows", boom)
    hits = store.search("Kafka")
    assert hits and hits[0]["job_id"] == "gh-1"


def test_search_source_filter_and_empty_query(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    store.archive_job(make_job())
    store.archive_job(make_job(job_id="9", source=Source.hn, company="HN Acme"))
    assert {hit["source"] for hit in store.search("Acme")} == {"boards", "hn"}
    assert {hit["source"] for hit in store.search("Acme", source="hn")} == {"hn"}
    assert store.search("") == []


def test_store_for_honours_enabled_flag(tmp_path: Path) -> None:
    on = Settings(root=tmp_path)
    assert isinstance(store_for(on), ArtifactStore)
    assert not isinstance(store_for(on), DisabledStore)
    off = Settings(root=tmp_path, artifacts={"enabled": False})
    disabled = store_for(off)
    assert isinstance(disabled, DisabledStore)
    assert disabled.archive_job(make_job()) is None
    assert not (tmp_path / "data").exists()


def test_archiving_failures_never_bubble_up(tmp_path: Path, monkeypatch) -> None:
    store = ArtifactStore(tmp_path / "artifacts")

    def explode(*_args, **_kwargs):
        raise OSError("disk gone")

    monkeypatch.setattr(ArtifactStore, "_index_job", explode)
    assert store.archive_job(make_job()) is None  # swallowed, logged


# -- integration: run + letter + cv hooks --------------------------------------


def test_cached_run_archives_new_jobs(tmp_path: Path) -> None:
    fixtures = tmp_path / "data" / "fixtures"
    fixtures.mkdir(parents=True)
    shutil.copy(ROOT / "data" / "fixtures" / "linkedin_jobs.json", fixtures)
    settings = Settings(
        root=tmp_path,
        sink="local",
        local_jobs_csv=tmp_path / "jobs.csv",
        local_runs_csv=tmp_path / "runs.csv",
    )
    manager = RunManager(settings, EventBus())
    manager.start(["linkedin"], cached=True)
    manager.wait(30)
    assert manager.state.status is RunStatus.completed

    archived = list((tmp_path / "data" / "artifacts" / "linkedin").iterdir())
    assert archived, "every new job must land in the archive"
    job_json = archived[0] / "job.json"
    assert job_json.exists()
    store = ArtifactStore(tmp_path / "data" / "artifacts")
    hits = store.search("linkedin") or store.search("Engineer")
    assert hits


def test_letter_generation_links_its_documents(tmp_path: Path) -> None:
    from datetime import date

    from test_cv_master import make_profile
    from jobscout.cover_letter.generator import generate_cover_letter
    from test_prompts_as_code import SpyLLM  # same fake letter contract

    settings = Settings(root=tmp_path)
    settings.cover_letter.output_dir = tmp_path / "letters"
    settings.cover_letter.compile_pdf = False
    settings.cover_letter.api_key = "test"
    job = Job(
        company="Acme",
        title="AI Engineer",
        link="https://acme.example/j/2",
        source=Source.indeed,
        job_id="letter-2",
    )
    result = generate_cover_letter(
        settings,
        job,
        description="Wir suchen Python und n8n.",
        profile=make_profile(),
        llm=SpyLLM(),
        today=date(2026, 11, 2),
    )
    assert result.reused is False
    store = store_for(settings)
    kinds = {entry["kind"] for entry in store.documents("indeed", "letter-2")}
    assert {"letter-json", "letter-tex"} <= kinds
    letter_json = store.job_dir("indeed", "letter-2") / "letter.json"
    assert json.loads(letter_json.read_text(encoding="utf-8"))["company"] == "Acme"


def test_cv_generation_links_its_documents(tmp_path: Path) -> None:
    from jobscout.config import CVConfig
    from jobscout.cv.master import MasterCV
    from jobscout.cv.pipeline import build_cv
    from test_cv_master import make_profile

    settings = Settings(root=tmp_path)
    settings.cv = CVConfig(output_dir=tmp_path / "cv", compile_pdf=False)
    master = MasterCV.from_profile(make_profile())
    result = build_cv(
        settings,
        job=make_job(job_id="42", source=Source.linkedin),
        description="Wir brauchen Python.",
        master=master,
        use_llm=False,
    )
    assert not result.reused
    store = store_for(settings)
    kinds = {entry["kind"] for entry in store.documents("linkedin", "42")}
    assert {"cv-tex", "cv-json"} <= kinds
    entry = next(e for e in store.documents("linkedin", "42") if e["kind"] == "cv-tex")
    assert Path(entry["path"]).exists()


def test_master_cv_without_job_does_not_touch_archive(tmp_path: Path) -> None:
    from jobscout.config import CVConfig
    from jobscout.cv.master import MasterCV
    from jobscout.cv.pipeline import build_cv
    from test_cv_master import make_profile

    settings = Settings(root=tmp_path)
    settings.cv = CVConfig(output_dir=tmp_path / "cv", compile_pdf=False)
    build_cv(settings, master=MasterCV.from_profile(make_profile()), use_llm=False)
    assert not (tmp_path / "data" / "artifacts").exists()
