"""Arbeitnow (arbeitnow.com) — keyless public job-board API.

``GET https://www.arbeitnow.com/api/job-board-api`` answers with plain JSON,
no key and no login: it is the source that gives a Germany-focused run broad
coverage without any scraping risk.  Descriptions arrive as HTML and are
flattened with the shared converter.
"""

from __future__ import annotations

import datetime as dt
from typing import Any
from urllib.parse import urlencode

from jobscout.models import Job, Source
from jobscout.sources.keyless import KeylessSource, clean_text, html_to_text

API_URL = "https://www.arbeitnow.com/api/job-board-api"

PAGE_SIZE = 50
MAX_PAGES = 10


class ArbeitnowSource(KeylessSource):
    source_name = "arbeitnow"
    agent_id = "boards"

    @property
    def label(self) -> str:
        return "Arbeitnow"

    def fetch_jobs(self, cfg: Any) -> list[Job]:
        query = self.resolve_query(cfg)
        location = clean_text(getattr(cfg, "location", ""))
        limit = max(0, int(getattr(cfg, "max_jobs", 25)))
        jobs: list[Job] = []
        for page in range(1, MAX_PAGES + 1):
            params: dict[str, Any] = {"page": page}
            if query:
                params["search"] = query
            if location:
                params["location"] = location
            data = self.get_json(f"{API_URL}?{urlencode(params)}")
            items = data.get("data") or []
            for item in items:
                job = self._to_job(item, query)
                if job is not None:
                    jobs.append(job)
                    if limit and len(jobs) >= limit:
                        return jobs
            self.polite_pause()
            if len(items) < PAGE_SIZE:
                break
        return jobs

    def _to_job(self, item: dict, query: str) -> Job | None:
        slug = clean_text(item.get("slug"))
        title = clean_text(item.get("title"))
        link = clean_text(item.get("url"))
        if not slug or not title or not link:
            return None
        created = item.get("created_at")
        posted_date = None
        if isinstance(created, (int, float)):
            posted_date = (
                dt.datetime.fromtimestamp(created, tz=dt.timezone.utc).date().isoformat()
            )
        tags = [clean_text(tag) for tag in (item.get("tags") or []) if clean_text(tag)]
        description = html_to_text(item.get("description"))
        if tags:
            tag_line = "Tags: " + ", ".join(tags[:12])
            description = f"{tag_line}\n\n{description}" if description else tag_line
        return Job(
            company=clean_text(item.get("company_name")) or title,
            title=title,
            link=link,
            source=Source.arbeitnow,
            job_id=slug,
            location=clean_text(item.get("location")) or None,
            posted_date=posted_date,
            description=description or None,
            canonical_url=link,
            search_query=query,
        )
