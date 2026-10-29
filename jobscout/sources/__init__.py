from __future__ import annotations

from jobscout.config import Settings
from jobscout.control import RunControl
from jobscout.sources.base import BaseSource, Emit, SourceError
from jobscout.sources.cached import CachedSource
from jobscout.sources.indeed import IndeedSource
from jobscout.sources.linkedin import LinkedInSource


def make_source(
    name: str,
    settings: Settings,
    control: RunControl,
    emit: Emit,
    cached: bool,
) -> BaseSource:
    if name not in {"linkedin", "indeed"}:
        raise SourceError(f"Unknown source: {name}")
    if cached:
        return CachedSource(name, settings, control, emit)
    if name == "linkedin":
        return LinkedInSource(settings, control, emit)
    return IndeedSource(settings, control, emit)


__all__ = ["BaseSource", "SourceError", "make_source"]
