"""RSS/Atom feeds as a job source.

Companies, ATS boards and aggregators publish feeds nobody has to scrape.
The user lists the feed URLs in config; this adapter reads them with the
standard library parser (no new dependency), converts items to jobs and is
the one keyless source that honours robots.txt, because feeds are ordinary
web pages and the user may point it at any host.
"""

from __future__ import annotations

import email.utils
import hashlib
import xml.etree.ElementTree as ET
from typing import Any, Iterable
from urllib.parse import urljoin

from jobscout.models import Job, Source
from jobscout.sources.keyless import KeylessFetchError, KeylessSource, clean_text, html_to_text

ATOM_NS = "{http://www.w3.org/2005/Atom}"


class RssSource(KeylessSource):
    source_name = "rss"
    agent_id = "boards"

    def __init__(self, settings, control, emit) -> None:  # noqa: ANN001
        super().__init__(settings, control, emit, respect_robots=True)

    @property
    def label(self) -> str:
        return "RSS feeds"

    def fetch_jobs(self, cfg: Any) -> list[Job]:
        feeds = [str(f).strip() for f in getattr(cfg, "feeds", []) if str(f).strip()]
        if not feeds:
            return []
        limit = max(0, int(getattr(cfg, "max_jobs", 25)))
        query = self.resolve_query(cfg)
        jobs: list[Job] = []
        failures: list[str] = []
        for index, feed_url in enumerate(feeds):
            self.emit(
                "agent.progress",
                agent=self.agent_id,
                progress=index / len(feeds),
                task=feed_url[:60],
            )
            try:
                fetched = self._fetch_feed(feed_url, query)
            except KeylessFetchError as exc:
                if not jobs and index == len(feeds) - 1:
                    raise
                failures.append(f"{feed_url}: {exc}")
                continue
            for job in fetched:
                jobs.append(job)
                if limit and len(jobs) >= limit:
                    return jobs
            self.polite_pause()
        if failures:
            self.emit(
                "agent.log",
                agent=self.agent_id,
                level="warning",
                message="Some feeds failed: " + "; ".join(failures[:4]),
            )
        return jobs

    def _fetch_feed(self, feed_url: str, query: str) -> list[Job]:
        response = self.get(feed_url, headers={"Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml"})
        try:
            root = ET.fromstring(response.content)
        except ET.ParseError as exc:
            raise KeylessFetchError(f"{feed_url} is not well-formed XML: {exc}") from exc
        channel_title = _first_text(root, "channel/title", f"{ATOM_NS}title") or feed_url
        items: Iterable[ET.Element] = list(root.iter("item")) or list(root.iter(f"{ATOM_NS}entry"))
        jobs: list[Job] = []
        for entry in items:
            job = self._entry_to_job(entry, feed_url, channel_title, query)
            if job is not None:
                jobs.append(job)
        return jobs

    def _entry_to_job(
        self, entry: ET.Element, feed_url: str, feed_title: str, query: str
    ) -> Job | None:
        title = _first_text(entry, "title", f"{ATOM_NS}title")
        link = (
            _first_text(entry, "link")
            or _atom_link(entry)
            or _first_text(entry, "guid", f"{ATOM_NS}id")
        )
        if not title or not link:
            return None
        link = urljoin(feed_url, link)
        description = (
            _first_text(entry, "description", "content:encoded")
            or _first_text(entry, f"{ATOM_NS}summary", f"{ATOM_NS}content")
        )
        body = html_to_text(description)
        guid = _first_text(entry, "guid", f"{ATOM_NS}id") or link
        job_id = hashlib.sha1(guid.encode("utf-8")).hexdigest()[:16]
        posted = _first_text(entry, "pubDate", f"{ATOM_NS}published", f"{ATOM_NS}updated")
        posted_date = _to_iso_date(posted) if posted else None
        company, role = split_company(title, feed_title)
        if query and not _matches(f"{title} {body}", query):
            return None
        return Job(
            company=company,
            title=role,
            link=link,
            source=Source.rss,
            job_id=job_id,
            description=body or None,
            posted_date=posted_date,
            canonical_url=link,
            search_query=query,
        )


def _atom_link(entry: ET.Element) -> str | None:
    best: str | None = None
    for link in entry.findall(f"{ATOM_NS}link"):
        rel = (link.get("rel") or "alternate").lower()
        href = link.get("href")
        if href and rel == "alternate":
            return href
        if href and best is None:
            best = href
    return best


def _first_text(element: ET.Element, *paths: str) -> str | None:
    for path in paths:
        found = element.find(path)
        if found is None:
            continue
        text = clean_text("".join(found.itertext()))
        if text:
            return text
    return None


def _to_iso_date(raw: str) -> str | None:
    raw = clean_text(raw)
    if not raw:
        return None
    if len(raw) >= 10 and raw[4] == "-" and raw[7] == "-":  # already ISO
        return raw[:10]
    try:
        return email.utils.parsedate_to_datetime(raw).date().isoformat()
    except (TypeError, ValueError, IndexError):
        return None

def split_company(title: str, feed_title: str) -> tuple[str, str]:
    """Pull a company name out of common feed-title conventions."""
    for separator in (" @ ", " - ", " | ", " \u2013 ", " \u2014 "):
        if separator in title:
            left, _, right = title.partition(separator)
            left, right = left.strip(), right.strip()
            if separator in (" - ", " | ") and not left:
                continue
            if separator in (" @ ",):
                return right or feed_title, left
            if left and right:
                return left, right
    return feed_title, title


def _matches(haystack: str, query: str) -> bool:
    terms = [term.lower() for term in query.replace(",", " ").split() if term]
    lowered = haystack.lower()
    return any(term in lowered for term in terms) if terms else True


__all__ = ["RssSource"]
