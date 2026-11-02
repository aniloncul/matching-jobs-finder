"""robots.txt gate for the keyless fetchers.

JSON APIs with a documented public contract (Greenhouse, Lever, Ashby,
Arbeitnow, Hacker News, Bundesagentur) are not HTML crawls and are used the
way their providers publish them, so they do not go through here.  Anything
that pulls arbitrary pages or user-supplied RSS feeds asks the site first.

Fail-open policy, deliberately: a missing robots.txt (404) means "no rules",
and a network failure must not take the whole source down.  A *found* and
*parsed* robots.txt is always honoured.
"""

from __future__ import annotations

import threading
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx

ROBOTS_TIMEOUT_SECONDS = 5.0


class RobotsGate:
    def __init__(self, client: httpx.Client, user_agent: str) -> None:
        self._client = client
        self._user_agent = user_agent
        self._lock = threading.Lock()
        self._rules: dict[tuple[str, str], RobotFileParser | None] = {}

    def allowed(self, url: str) -> bool:
        key = _site(url)
        if key is None:
            return True
        with self._lock:
            if key not in self._rules:
                self._rules[key] = self._fetch_rules(key)
            parser = self._rules[key]
        if parser is None:
            return True
        try:
            return parser.can_fetch(self._user_agent, url)
        except Exception:
            return True

    def _fetch_rules(self, site: tuple[str, str]) -> RobotFileParser | None:
        scheme, netloc = site
        robots_url = urljoin(f"{scheme}://{netloc}/", "/robots.txt")
        try:
            response = self._client.get(robots_url, timeout=ROBOTS_TIMEOUT_SECONDS)
        except httpx.HTTPError:
            return None
        if response.status_code in (401, 403, 404, 410):
            return None
        if response.status_code >= 400:
            return None
        parser = RobotFileParser()
        try:
            parser.parse(response.text.splitlines())
        except Exception:
            return None
        return parser


# Keep scheme+host+port as the cache key: robots.txt is per site origin, and
# two boards on one host with different ports must not share one file.
def _site(url: str) -> tuple[str, str] | None:
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if not parsed.netloc or parsed.scheme not in ("http", "https"):
        return None
    return (parsed.scheme, parsed.netloc)


__all__ = ["RobotsGate"]
