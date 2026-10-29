import time
from pathlib import Path

from jobscout.config import ROOT, Settings
from jobscout.events import EventBus
from jobscout.models import RunStatus
from jobscout.run_manager import RunManager


def make_manager(tmp_path: Path) -> RunManager:
    settings = Settings(
        root=ROOT,
        sink="local",
        local_jobs_csv=tmp_path / "jobs.csv",
        local_runs_csv=tmp_path / "runs.csv",
    )
    return RunManager(settings, EventBus())


def test_cached_run_completes_and_second_run_dedupes(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)

    manager.start(["linkedin", "indeed"], cached=True)
    manager.wait(30)
    state = manager.state
    assert state.status == RunStatus.completed
    assert state.sink_kind == "local"
    assert state.stats.found == 12
    assert state.stats.new == 12
    assert state.stats.skipped == 0
    assert state.stats.errors == 0
    assert len(state.messages) == 8
    assert {
        agent.status.value for agent_id, agent in state.agents.items() if agent_id != "writer"
    } == {"done"}
    assert state.agents["writer"].status.value == "idle"

    manager.start(["linkedin", "indeed"], cached=True)
    manager.wait(30)
    state = manager.state
    assert state.status == RunStatus.completed
    assert state.stats.found == 12
    assert state.stats.new == 0
    assert state.stats.skipped == 12


def test_cancel_ends_run_cleanly(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)

    manager.start(["linkedin"], cached=True)
    time.sleep(0.05)
    manager.cancel()
    manager.wait(10)

    assert manager.state.status == RunStatus.cancelled
    assert manager.state.finished_at is not None


def test_query_override_is_scoped_to_the_run(tmp_path: Path, monkeypatch) -> None:
    captured: list[Settings] = []

    class FakeOrchestrator:
        def __init__(self, **kwargs) -> None:
            captured.append(kwargs["settings"])

        def run(self) -> None:
            return None

    monkeypatch.setattr("jobscout.run_manager.Orchestrator", FakeOrchestrator)
    manager = make_manager(tmp_path)
    manager.start(["linkedin"], cached=True, query="n8n OR low-code")
    manager.wait(5)

    assert manager.state.status == RunStatus.completed
    assert captured[0].sources.linkedin.query == "n8n OR low-code"
    assert captured[0].sources.indeed.query == "n8n OR low-code"
    assert manager.settings.sources.linkedin.query == "AI Engineer"
    assert manager.settings.sources.indeed.query == "AI Engineer"
