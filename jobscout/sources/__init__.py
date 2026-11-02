from __future__ import annotations

from jobscout.config import KEYLESS_SOURCE_NAMES, Settings
from jobscout.control import RunControl
from jobscout.sources.arbeitnow import ArbeitnowSource
from jobscout.sources.ats_board import BoardsSource
from jobscout.sources.base import BaseSource, Emit, SourceError
from jobscout.sources.ba_jobsuche import BundesagenturSource
from jobscout.sources.cached import CachedSource
from jobscout.sources.hn_hiring import HackerNewsSource
from jobscout.sources.indeed import IndeedSource
from jobscout.sources.linkedin import LinkedInSource
from jobscout.sources.rss import RssSource

# Source name -> keyless adapter class.  All of them run under the single
# "boards" UI agent (they are one idea: pull jobs from public endpoints),
# but each keeps its own Source enum value so provenance survives.
KEYLESS_SOURCES = {
    "boards": BoardsSource,
    "arbeitnow": ArbeitnowSource,
    "hn": HackerNewsSource,
    "ba": BundesagenturSource,
    "rss": RssSource,
}

SOURCE_NAMES = ("linkedin", "indeed", *KEYLESS_SOURCE_NAMES)


def make_source(
    name: str,
    settings: Settings,
    control: RunControl,
    emit: Emit,
    cached: bool,
) -> BaseSource:
    if name not in SOURCE_NAMES:
        raise SourceError(f"Unknown source: {name}")
    if name in KEYLESS_SOURCES:
        # Keyless sources usually have no fixture: --cached then quietly
        # means "go live", which is safe because these endpoints are public.
        fixture = settings.root / "data" / "fixtures" / f"{name}_jobs.json"
        if cached and fixture.exists():
            return CachedSource(name, settings, control, emit)
        return KEYLESS_SOURCES[name](settings, control, emit)
    if cached:
        return CachedSource(name, settings, control, emit)
    if name == "linkedin":
        return LinkedInSource(settings, control, emit)
    return IndeedSource(settings, control, emit)


__all__ = [
    "KEYLESS_SOURCE_NAMES",
    "KEYLESS_SOURCES",
    "SOURCE_NAMES",
    "BaseSource",
    "SourceError",
    "make_source",
]
