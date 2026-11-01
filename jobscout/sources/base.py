from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from jobscout.config import Settings
from jobscout.control import RunControl
from jobscout.models import Job


Emit = Callable[..., None]

SourceStatus = Literal[
    "ok",
    "empty",
    "blocked",
    "login_required",
    "layout_changed",
    "error",
]

# Statuses that mean "the source answered, but not with jobs".
NON_ERROR_STATUSES = {"ok", "empty"}


class SourceError(Exception):
    """A source failed in a way the coordinator can report and survive."""


@dataclass
class SourceResult:
    """Explicit outcome of one source run.

    ``[]`` used to mean four different things: no matching jobs, a CAPTCHA, an
    expired session and a broken selector.  The UI and the run summary need
    that distinction, so every adapter returns this object instead of a bare
    list.
    """

    jobs: list[Job] = field(default_factory=list)
    status: SourceStatus = "ok"
    message: str | None = None
    pages_fetched: int = 0

    @property
    def ok(self) -> bool:
        """True when the source answered normally (even with zero jobs)."""
        return self.status in NON_ERROR_STATUSES

    @property
    def count(self) -> int:
        return len(self.jobs)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "message": self.message,
            "pages_fetched": self.pages_fetched,
            "jobs": len(self.jobs),
        }


class BaseSource(ABC):
    source_name: str
    agent_id: str

    def __init__(
        self,
        settings: Settings,
        control: RunControl,
        emit: Emit,
        cached: bool = False,
    ) -> None:
        self.settings = settings
        self.control = control
        self.emit = emit
        self.cached = cached

    def status(
        self,
        task: str,
        progress: float | None = None,
        status: str = "working",
    ) -> None:
        payload: dict[str, Any] = {
            "agent": self.agent_id,
            "status": status,
            "task": task,
        }
        if progress is not None:
            payload["progress"] = max(0.0, min(1.0, progress))
        self.emit("agent.status", **payload)

    def log(self, text: str, level: str = "info") -> None:
        self.emit("log", agent=self.agent_id, level=level, text=text)

    def finish(
        self,
        jobs: list[Job],
        *,
        status: SourceStatus = "ok",
        message: str | None = None,
        pages_fetched: int = 0,
    ) -> SourceResult:
        """Build the result and publish it for the UI/run summary."""
        if status == "ok" and not jobs:
            status = "empty"
        result = SourceResult(
            jobs=jobs,
            status=status,
            message=message,
            pages_fetched=pages_fetched,
        )
        self.emit("source.result", agent=self.agent_id, **result.to_dict())
        return result

    @abstractmethod
    def search(self) -> SourceResult:
        raise NotImplementedError
