from __future__ import annotations

from abc import ABC, abstractmethod

from jobscout.models import Job


class SinkError(Exception):
    """Raised when a sheet or file sink cannot be prepared or written."""


class Sink(ABC):
    kind: str = "base"
    sheet_url: str | None = None

    @abstractmethod
    def prepare(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def existing(self) -> tuple[set[tuple[str, str]], set[tuple[str, str, str]]]:
        raise NotImplementedError

    @abstractmethod
    def write(self, jobs: list[Job], cross_source: bool = False) -> int:
        raise NotImplementedError

    @abstractmethod
    def record_run(self, summary: dict) -> None:
        raise NotImplementedError

    def list_jobs(self, limit: int = 100) -> list[Job]:
        return []

    def save_description(self, source: str, job_id: str, description: str) -> bool:
        return False

    def delete_job(self, source: str, job_id: str) -> bool:
        return False
