from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Source(StrEnum):
    linkedin = "linkedin"
    indeed = "indeed"
    custom = "custom"


class AgentId(StrEnum):
    coordinator = "coordinator"
    linkedin = "linkedin"
    indeed = "indeed"
    registrar = "registrar"
    writer = "writer"


class AgentStatus(StrEnum):
    idle = "idle"
    working = "working"
    waiting = "waiting"
    paused = "paused"
    done = "done"
    error = "error"
    blocked = "blocked"


class RunStatus(StrEnum):
    idle = "idle"
    running = "running"
    paused = "paused"
    cancelling = "cancelling"
    cancelled = "cancelled"
    completed = "completed"
    failed = "failed"


class Job(BaseModel):
    model_config = ConfigDict(extra="ignore")

    company: str
    title: str
    link: str
    posted_date: str | None = None
    source: Source
    posted_text: str | None = None
    description: str | None = None
    estimated: bool = False
    job_id: str
    location: str | None = None
    search_query: str = ""
    scraped_at: str = Field(default_factory=now_iso)
    applied: bool = False

    def key(self) -> tuple[str, str]:
        return (self.source.value, self.job_id)

    def row(self) -> dict[str, str]:
        return {
            "Company Name": self.company,
            "Job Title": self.title,
            "Job Link": self.link,
            "Post Created Date": self.posted_date or "",
            "Source": self.source.value,
            "Posted Text": self.posted_text or "",
            "Description": self.description or "",
            "Estimated": "yes" if self.estimated else "no",
            "Job ID": self.job_id,
            "Location": self.location or "",
            "Search Query": self.search_query,
            "Scraped At": self.scraped_at,
            "Applied": "Applied" if self.applied else "Will Apply",
        }


class AgentState(BaseModel):
    id: str
    name: str
    role: str
    status: AgentStatus = AgentStatus.idle
    task: str = "Standing by"
    progress: float = 0.0
    updated_at: float = Field(default_factory=lambda: datetime.now(timezone.utc).timestamp())


class Stats(BaseModel):
    found: int = 0
    new: int = 0
    skipped: int = 0
    errors: int = 0


class AgentLogEntry(BaseModel):
    seq: int
    ts: float
    agent: str
    level: str
    text: str


class RunState(BaseModel):
    run_id: str | None = None
    status: RunStatus = RunStatus.idle
    mode: Literal["live", "cached"] = "live"
    sources: list[str] = Field(default_factory=list)
    started_at: float | None = None
    finished_at: float | None = None
    agents: dict[str, AgentState] = Field(default_factory=dict)
    stats: Stats = Field(default_factory=Stats)
    messages: list[dict] = Field(default_factory=list)
    logs: list[dict] = Field(default_factory=list)
    agent_logs: dict[str, list[dict]] = Field(default_factory=dict)
    agent_status_history: dict[str, list[dict]] = Field(default_factory=dict)
    sheet_url: str | None = None
    sink_kind: str | None = None
    error: str | None = None
    last_seq: int = 0


class MessageKind(StrEnum):
    command = "command"
    result = "result"
    status = "status"
    error = "error"
