from __future__ import annotations

import json

from jobscout.config import Settings
from jobscout.control import RunControl
from jobscout.models import Job
from jobscout.sources.base import BaseSource, Emit, SourceError


class CachedSource(BaseSource):
    def __init__(
        self,
        source_name: str,
        settings: Settings,
        control: RunControl,
        emit: Emit,
    ) -> None:
        super().__init__(settings, control, emit, cached=True)
        self.source_name = source_name
        self.agent_id = source_name

    def search(self) -> list[Job]:
        fixture = self.settings.root / "data" / "fixtures" / f"{self.source_name}_jobs.json"
        if not fixture.exists():
            raise SourceError(f"Cached fixture missing: {fixture}")

        raw = json.loads(fixture.read_text(encoding="utf-8"))
        jobs = [Job.model_validate(item) for item in raw]
        limit = self.settings.source_config(self.source_name).max_jobs
        jobs = jobs[:limit]

        self.log(f"Reading cached fixture for {self.source_name} (found {len(jobs)} jobs)")
        self.status(f"Reading cached {self.source_name} results", 0.05)
        for index, job in enumerate(jobs, start=1):
            self.control.checkpoint("cached source")
            self.control.sleep(0.22)
            if index == 1 or index % 5 == 0 or index == len(jobs):
                self.log(f"Extracted job [{index}/{len(jobs)}]: {job.company} · {job.title}")
            self.status(
                f"Replaying cached result {index} of {len(jobs)}",
                index / max(1, len(jobs)),
            )
        self.status(f"Cache loaded · {len(jobs)} jobs", 1.0)
        self.log(f"Finished replay: {len(jobs)} jobs prepared for Registrar")
        return jobs
