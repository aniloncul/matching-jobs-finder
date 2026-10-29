from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

from jobscout.config import Settings
from jobscout.control import RunControl
from jobscout.models import Job


Emit = Callable[..., None]


class SourceError(Exception):
    """A source failed in a way the coordinator can report and survive."""


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

    @abstractmethod
    def search(self) -> list[Job]:
        raise NotImplementedError
