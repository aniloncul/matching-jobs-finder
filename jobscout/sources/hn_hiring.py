"""Hacker News "who is hiring" jobs via the Algolia API.

`hn.algolia.com` exposes every HN job post (the ``job`` tag, i.e. the monthly
Who-is-Hiring thread and standalone posts) as keyless JSON.  The posts are
plain text written by the hiring team, so they arrive already guarded and
short - good raw material for the fit scorer.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from jobscout.models import Job, Source
from jobscout.sources.keyless import KeylessSource, clean_text, html_to_text

SEARCH_URL = "https://hn.algolia.com/api/v1/search"
POST_URL = "https://news.ycombinator.com/item?id={post_id}"

HITS_PER_PAGE = 100
MAX_PAGES = 5


class HackerNewsSource(KeylessSource):
    source_name = "hn"
    agent_id = "boards"

    @property
    def label(self) -> str:
        return "Hacker News"

    def fetch_jobs(self, cfg: Any) -> list[Job]:
        query = self.resolve_query(cfg)
        limit = max(0, int(getattr(cfg, "max_jobs", 25)))
        jobs: list[Job] = []
        for page in range(MAX_PAGES):
            params = {
                "tags": "job",
                "hitsPerPage": min(HITS_PER_PAGE, max(1, limit or HITS_PER_PAGE)),
                "page": page,
            }
            if query:
                params["query"] = query
            data = self.get_json(f"{SEARCH_URL}?{urlencode(params)}")
            hits = data.get("hits") or []
            for hit in hits:
                job = self._to_job(hit, query)
                if job is not None:
                    jobs.append(job)
                    if limit and len(jobs) >= limit:
                        return jobs
            self.polite_pause()
            if len(hits) < params["hitsPerPage"]:
                break
        return jobs

    def _to_job(self, hit: dict, query: str) -> Job | None:
        post_id = str(hit.get("objectID") or "")
        title = clean_text(hit.get("title"))
        if not post_id or not title:
            return None
        body = html_to_text(hit.get("story_text"))
        created = clean_text(hit.get("created_at"))
        company, role = split_company(title)
        location = extract_location(body)
        return Job(
            company=company,
            title=role,
            link=POST_URL.format(post_id=post_id),
            source=Source.hn,
            job_id=post_id,
            location=location,
            posted_date=created[:10] or None,
            description=body or None,
            canonical_url=POST_URL.format(post_id=post_id),
            search_query=query,
        )


def split_company(title: str) -> tuple[str, str]:
    """``Acme — Senior Python Engineer (Remote)`` -> ("Acme", "...")."""
    for separator in ("\u2014", "\u2013", "|"):
        if separator in title:
            left, _, right = title.partition(separator)
            company, role = left.strip(), right.strip()
            if company and role:
                return company, role
    return "Hacker News", title


def extract_location(body: str) -> str | None:
    for line in body.splitlines():
        stripped = line.strip()
        lowered = stripped.lower().rstrip(":")
        if lowered in ("location", "locations", "where"):
            continue
        if lowered.startswith(("location", "locations")):
            _, _, rest = stripped.partition(":")
            rest = rest.strip()
            if rest:
                return rest[:120]
    return None


__all__ = ["HackerNewsSource", "split_company", "extract_location"]
