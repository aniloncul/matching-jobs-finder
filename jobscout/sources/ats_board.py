"""Applicant-tracking-system boards: Greenhouse, Lever, Ashby.

Employers publish their openings on these ATS providers with public, keyless
JSON endpoints.  Jobs that come straight from the employer's own board are
the highest-trust rows in the sheet, so each adapter records the canonical
posting URL and the requisition id (P1's identity model was built for this).

Config format (``sources.keyless.boards``):

    boards:
      - greenhouse:acme
      - lever:acme
      - ashby:acme

Tokens come from the public URLs: app.greenhouse.io/<token>,
jobs.lever.co/<token>, jobs.ashbyhq.com/<token>.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

from jobscout.models import Job, Source
from jobscout.sources.keyless import KeylessFetchError, KeylessSource, clean_text, html_to_text

GREENHOUSE_JOBS_URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
LEVER_POSTINGS_URL = "https://api.lever.co/v0/postings/{token}"
ASHBY_JOBS_URL = "https://api.ashbyhq.com/posting-api/job-board/{token}"

_TOKEN_RE = re.compile(r"^(greenhouse|lever|ashby)[:=](.+)$", re.IGNORECASE)

# Recognise an external posting URL so cross-source duplicates collapse onto
# the same canonical URL (dedupe uses canonical_url, P1).
GREENHOUSE_URL_RE = re.compile(
    r"(?:app\.greenhouse\.io|boards\.greenhouse\.io/jobs)/([^/?#]+)/jobs/(\d+)"
)
LEVER_URL_RE = re.compile(r"(?:api\.)?(?:jobs|hosted)\.lever\.co/([^/?#]+)/([0-9a-f-]{20,})")
ASHBY_URL_RE = re.compile(r"jobs\.ashbyhq\.com/([^/?#]+)/([0-9a-f-]{20,})")


def parse_board_ref(ref: str) -> tuple[str, str]:
    """``greenhouse:acme`` -> ("greenhouse", "acme"); bare tokens fail loudly."""
    match = _TOKEN_RE.match(ref.strip())
    if not match:
        raise KeylessFetchError(
            f"Board entry {ref!r} must look like greenhouse:token, lever:token "
            "or ashby:token"
        )
    return match.group(1).lower(), match.group(2).strip()


def ats_identifiers(url: str) -> tuple[str, str, str] | None:
    """Map a public ATS URL onto (kind, board token, requisition id)."""
    for pattern, kind in (
        (GREENHOUSE_URL_RE, "greenhouse"),
        (LEVER_URL_RE, "lever"),
        (ASHBY_URL_RE, "ashby"),
    ):
        match = pattern.search(url or "")
        if match:
            return kind, match.group(1), match.group(2)
    return None


def pretty_company(token: str) -> str:
    cleaned = re.sub(r"[-_]+", " ", token).strip()
    return cleaned.title() if cleaned else token


class BoardsSource(KeylessSource):
    source_name = "boards"
    agent_id = "boards"

    @property
    def label(self) -> str:
        return "ATS boards"

    def fetch_jobs(self, cfg: Any) -> list[Job]:
        refs = [str(ref).strip() for ref in getattr(cfg, "boards", []) if str(ref).strip()]
        if not refs:
            return []
        limit = max(0, int(getattr(cfg, "max_jobs", 25)))
        jobs: list[Job] = []
        failures: list[str] = []
        last_index = len(refs) - 1
        for index, ref in enumerate(refs):
            kind, token = parse_board_ref(ref)
            self.emit(
                "agent.progress",
                agent=self.agent_id,
                progress=min(1.0, len(jobs) / limit) if limit else 0.0,
                task=f"{kind}: {token}",
            )
            try:
                fetched = self._fetch_board(kind, token, cfg)
            except KeylessFetchError as exc:
                if not jobs and index == last_index:
                    raise
                failures.append(f"{ref}: {exc}")
                continue
            for job in fetched:
                if limit and len(jobs) >= limit:
                    break
                jobs.append(job)
        if failures:
            self.emit(
                "agent.log",
                agent=self.agent_id,
                level="warning",
                message="Some boards failed: " + "; ".join(failures[:4]),
            )
        return jobs

    def _fetch_board(self, kind: str, token: str, cfg: Any) -> list[Job]:
        if kind == "greenhouse":
            return self._greenhouse(token, cfg)
        if kind == "lever":
            return self._lever(token, cfg)
        return self._ashby(token, cfg)

    def _query_matches(self, job_title: str, query: str) -> bool:
        if not query:
            return True
        terms = [t for t in re.split(r"[,\s]+", query.lower()) if t]
        haystack = job_title.lower()
        return any(term in haystack for term in terms) if terms else True

    def _greenhouse(self, token: str, cfg: Any) -> list[Job]:
        # content=true returns the full ad; that is the point of the source.
        data = self.get_json(GREENHOUSE_JOBS_URL.format(token=token), params={"content": "true"})
        query = self.resolve_query(cfg)
        jobs: list[Job] = []
        for item in data.get("jobs") or []:
            title = clean_text(item.get("title"))
            if not title or not self._query_matches(title, query):
                continue
            job_id = str(item.get("id") or "")
            link = clean_text(item.get("absolute_url")) or (
                f"https://job-boards.greenhouse.io/{token}/jobs/{job_id}"
            )
            location = clean_text((item.get("location") or {}).get("name"))
            if not self._location_matches(location, cfg):
                continue
            jobs.append(
                Job(
                    company=pretty_company(token),
                    title=title,
                    link=link,
                    source=Source.boards,
                    job_id=f"gh-{job_id}",
                    location=location or None,
                    description=html_to_text(item.get("content")) or None,
                    canonical_url=link,
                    requisition_id=job_id or None,
                    search_query=query,
                )
            )
        return jobs

    def _lever(self, token: str, cfg: Any) -> list[Job]:
        data = self.get_json(LEVER_POSTINGS_URL.format(token=token), params={"mode": "json"})
        if not isinstance(data, list):
            raise KeylessFetchError(f"Lever board {token!r} returned an unexpected payload")
        query = self.resolve_query(cfg)
        jobs: list[Job] = []
        for item in data:
            title = clean_text(item.get("text"))
            if not title or not self._query_matches(title, query):
                continue
            job_id = str(item.get("id") or "")
            link = clean_text(item.get("hostedUrl")) or (
                f"https://jobs.lever.co/{token}/{job_id}"
            )
            location = clean_text((item.get("categories") or {}).get("location"))
            if not self._location_matches(location, cfg):
                continue
            jobs.append(
                Job(
                    company=pretty_company(token),
                    title=title,
                    link=link,
                    source=Source.boards,
                    job_id=f"lever-{job_id}",
                    location=location or None,
                    description=clean_text(item.get("descriptionPlain"))[: 14_000] or None,
                    posted_date=self._lever_posted(item),
                    canonical_url=link,
                    requisition_id=job_id or None,
                    search_query=query,
                )
            )
        return jobs

    @staticmethod
    def _lever_posted(item: dict) -> str | None:
        created = item.get("createdAt")
        if not isinstance(created, (int, float)):
            return None
        return dt.datetime.fromtimestamp(created / 1000, tz=dt.timezone.utc).date().isoformat()

    def _ashby(self, token: str, cfg: Any) -> list[Job]:
        data = self.get_json(ASHBY_JOBS_URL.format(token=token))
        query = self.resolve_query(cfg)
        jobs: list[Job] = []
        for item in data.get("jobs") or []:
            if not item.get("isListed", True):
                continue
            title = clean_text(item.get("title"))
            if not title or not self._query_matches(title, query):
                continue
            job_id = str(item.get("id") or "")
            link = clean_text(item.get("jobUrl")) or clean_text(item.get("applyUrl"))
            location = clean_text(item.get("location"))
            if not link or not self._location_matches(location, cfg):
                continue
            jobs.append(
                Job(
                    company=pretty_company(token),
                    title=title,
                    link=link,
                    source=Source.boards,
                    job_id=f"ashby-{job_id}",
                    location=location or None,
                    description=html_to_text(item.get("descriptionHtml")) or None,
                    posted_date=str(item.get("publishedAt") or "")[:10] or None,
                    canonical_url=link,
                    requisition_id=job_id or None,
                    search_query=query,
                )
            )
        return jobs

    def _location_matches(self, location: str, cfg: Any) -> bool:
        wanted = clean_text(getattr(cfg, "location", ""))
        if not wanted:
            return True
        return wanted.lower() in (location or "").lower()
