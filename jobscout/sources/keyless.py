"""Shared plumbing for the keyless (no browser, no login) source adapters.

These fetchers talk to public JSON APIs or user-supplied feeds with plain
httpx: no Playwright, no cookies, no CAPTCHA races.  That makes them the
lowest-risk sources in the project (the benchmark's main argument for source
diversity), so they share one small base class:

* a fixed, honest User-Agent,
* one error taxonomy: auth/rate-limit problems become ``blocked``, network or
  server problems become ``error``, and the coordinator sees the difference,
* HTML->text conversion for posting bodies, capped so a giant board page does
  not end up in a prompt or a sheet cell.
"""

from __future__ import annotations

import html as html_lib
import re
from typing import Any

import httpx

from jobscout.config import Settings
from jobscout.control import RunControl
from jobscout.models import Job
from jobscout.sources.base import BaseSource, Emit, SourceResult

USER_AGENT = "JobScout/0.1 (personal job search tool; runs on the user's machine)"

DEFAULT_TIMEOUT_SECONDS = 20.0

# Long enough for a full job ad, short enough for prompts and sheet cells.
MAX_DESCRIPTION_CHARS = 14_000

_TAG_RE = re.compile(r"<[^>]+>")
_BLOCK_RE = re.compile(r"(?i)</(p|div|li|ul|ol|h[1-6]|tr|table)>|<br\s*/?>")
_SPACE_RE = re.compile(r"[ \t\f\v]+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")


class KeylessFetchError(Exception):
    """One fetch failed.  ``blocked`` distinguishes policy/auth failures."""

    def __init__(self, message: str, *, blocked: bool = False) -> None:
        super().__init__(message)
        self.blocked = blocked


def html_to_text(raw: str | None) -> str:
    """Plain text out of posting HTML, cheaply and offline-safely."""
    if not raw:
        return ""
    text = _BLOCK_RE.sub("\n", raw)
    text = _TAG_RE.sub(" ", text)
    text = html_lib.unescape(text)
    lines = [_SPACE_RE.sub(" ", line).strip() for line in text.splitlines()]
    text = "\n".join(line for line in lines if line)
    text = _BLANK_LINES_RE.sub("\n\n", text).strip()
    return text[:MAX_DESCRIPTION_CHARS]


def clean_text(raw: str | None) -> str:
    return _SPACE_RE.sub(" ", (raw or "")).strip()


class KeylessSource(BaseSource):
    """Base for every adapter that only needs httpx."""

    def __init__(
        self,
        settings: Settings,
        control: RunControl,
        emit: Emit,
        *,
        respect_robots: bool = False,
    ) -> None:
        super().__init__(settings, control, emit, cached=False)
        self.respect_robots = respect_robots
        self._client: httpx.Client | None = None
        self._robots = None

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                timeout=DEFAULT_TIMEOUT_SECONDS,
                follow_redirects=True,
            )
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    # -- HTTP with the shared error taxonomy --------------------------------

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        self.control.checkpoint(f"{self.source_name} request")
        if self.respect_robots and not self._robots_allowed(url):
            raise KeylessFetchError(
                f"{url} is disallowed by robots.txt", blocked=True
            )
        try:
            response = self.client.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            raise KeylessFetchError(f"{url} request failed: {exc}") from exc
        if response.status_code in (401, 403, 429):
            raise KeylessFetchError(
                f"{url} returned {response.status_code} (auth or rate limit)",
                blocked=True,
            )
        if response.status_code >= 400:
            raise KeylessFetchError(f"{url} returned {response.status_code}")
        return response

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", url, **kwargs)

    def get_json(self, url: str, **kwargs: Any) -> Any:
        response = self.get(url, **kwargs)
        try:
            return response.json()
        except ValueError as exc:
            raise KeylessFetchError(f"{url} returned invalid JSON") from exc

    def _robots_allowed(self, url: str) -> bool:
        if self._robots is None:
            from jobscout.sources.robots import RobotsGate

            self._robots = RobotsGate(self.client, USER_AGENT)
        return self._robots.allowed(url)

    # -- BaseSource contract -------------------------------------------------

    def search(self) -> SourceResult:
        cfg = self.settings.source_config(self.source_name)
        self.emit(
            "agent.status",
            agent=self.agent_id,
            status="working",
            task=f"Searching {self.label}",
        )
        try:
            jobs = self.fetch_jobs(cfg)
        except KeylessFetchError as exc:
            self.close()
            return self.finish(
                [],
                status="blocked" if exc.blocked else "error",
                message=str(exc),
            )
        except Exception as exc:  # adapter bug: report, never crash the run
            self.close()
            return self.finish([], status="error", message=str(exc))
        self.close()
        return self.finish(jobs)

    def fetch_jobs(self, cfg: Any) -> list[Job]:
        raise NotImplementedError

    @property
    def label(self) -> str:
        return self.source_name

    # -- helpers subclasses share ---------------------------------------------

    def resolve_query(self, cfg: Any) -> str:
        query = clean_text(getattr(cfg, "query", ""))
        if query:
            return query
        return clean_text(self.settings.sources.linkedin.query)

    def polite_pause(self) -> None:
        """Small delay between pages/feeds so we stay a good citizen."""
        self.control.sleep(0.2)


__all__ = [
    "MAX_DESCRIPTION_CHARS",
    "USER_AGENT",
    "KeylessFetchError",
    "KeylessSource",
    "clean_text",
    "html_to_text",
]
