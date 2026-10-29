from __future__ import annotations

import re

from jobscout.models import Job


_NON_WORD = re.compile(r"[^a-z0-9]+")


def _normalize(value: str | None) -> str:
    return _NON_WORD.sub(" ", (value or "").lower()).strip()


def fingerprint_values(
    company: str | None,
    title: str | None,
    location: str | None,
) -> tuple[str, str, str]:
    return (_normalize(company), _normalize(title), _normalize(location))


def job_fingerprint(job: Job) -> tuple[str, str, str]:
    return fingerprint_values(job.company, job.title, job.location)


def dedupe_jobs(
    jobs: list[Job],
    existing_keys: set[tuple[str, str]] | None = None,
    existing_fingerprints: set[tuple[str, str, str]] | None = None,
    cross_source: bool = False,
    excluded_keys: set[tuple[str, str]] | None = None,
    excluded_fingerprints: set[tuple[str, str, str]] | None = None,
) -> tuple[list[Job], int]:
    seen_keys = set(existing_keys or set())
    seen_fingerprints = set(existing_fingerprints or set())
    ex_keys = set(excluded_keys or set())
    ex_fingerprints = {fp for fp in (excluded_fingerprints or set()) if fp != ("", "", "")}
    unique: list[Job] = []
    skipped = 0

    for job in jobs:
        key = job.key()
        fingerprint = job_fingerprint(job)

        # Excluded jobs (deleted by user) are NEVER re-added
        if key in ex_keys or (fingerprint != ("", "", "") and fingerprint in ex_fingerprints):
            skipped += 1
            continue

        duplicate = key in seen_keys
        if cross_source and not duplicate:
            duplicate = fingerprint in seen_fingerprints

        if duplicate:
            skipped += 1
            continue

        seen_keys.add(key)
        seen_fingerprints.add(fingerprint)
        unique.append(job)

    return unique, skipped
