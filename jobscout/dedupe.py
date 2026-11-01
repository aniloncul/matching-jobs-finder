"""Job identity: canonical URLs, normalized roles and near-duplicate detection.

The dedupe cascade is deliberately ordered from strongest to weakest signal:

1. ``(source, job_id)`` - the board's own primary key.
2. canonical URL - the same posting reached through a different tracking link.
3. normalized ``company + role`` - the same opening reposted by an agency.
4. SimHash - *warning only*, never a silent skip.

Only steps 1-3 remove a job.  SimHash reports "this description looks like a
posting you already have" because agency reposts are often genuinely different
applications (different location or contract), and hiding a real opening is
worse than showing a duplicate.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from jobscout.models import Job

_NON_WORD = re.compile(r"[^a-z0-9]+")
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_GENDER_RE = re.compile(
    r"\((?:m|w|d|f|x)(?:\s*[/|]\s*(?:m|w|d|f|x))+\)"
    r"|\b(?:m|w|d|f|x)\s*[/|]\s*(?:m|w|d|f|x)\s*[/|]\s*(?:m|w|d|f|x)\b",
    re.IGNORECASE,
)
_LEGAL_SUFFIXES = {
    "ag",
    "ab",
    "as",
    "bv",
    "co",
    "corp",
    "gmbh",
    "inc",
    "kg",
    "kgaa",
    "llc",
    "ltd",
    "mbh",
    "nv",
    "oy",
    "plc",
    "sarl",
    "sas",
    "se",
    "spa",
    "ug",
}
_TRACKING_PARAMS = {
    "campaign",
    "fbclid",
    "gclid",
    "igshid",
    "mc_cid",
    "mc_eid",
    "msclkid",
    "originalSubdomain".lower(),
    "ref",
    "refid",
    "referrer",
    "si",
    "source",
    "spm",
    "src",
    "tk",
    "trackingid",
    "trk",
    "vjs",
}
_LINKEDIN_JOB_PATH = re.compile(r"^/jobs/view/(\d+)")


@dataclass
class ExistingJobs:
    """Everything a sink knows about rows that are already stored."""

    keys: set[tuple[str, str]] = field(default_factory=set)
    fingerprints: set[tuple[str, str, str]] = field(default_factory=set)
    urls: set[str] = field(default_factory=set)
    roles: set[tuple[str, str]] = field(default_factory=set)


def _plain(value: str | None) -> str:
    return _NON_WORD.sub(" ", (value or "").lower()).strip()


def fingerprint_values(
    company: str | None,
    title: str | None,
    location: str | None,
) -> tuple[str, str, str]:
    return (_plain(company), _plain(title), _plain(location))


def canonical_url(value: str | None) -> str:
    """Return a stable URL for duplicate detection.

    Removes the fragment, campaign parameters and tracking parameters, folds
    the host and path to lower case, sorts the remaining query string and
    collapses the board-specific noise (LinkedIn job slugs, Indeed's
    ``viewjob`` wrapper).
    """
    raw = (value or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return ""
    if not parsed.scheme or not parsed.netloc:
        return ""

    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if host.endswith(":443"):
        host = host[: -len(":443")]
    elif host.endswith(":80"):
        host = host[: -len(":80")]

    path = re.sub(r"/{2,}", "/", parsed.path or "/").lower()
    match = _LINKEDIN_JOB_PATH.match(path)
    if match:
        path = f"/jobs/view/{match.group(1)}"
    else:
        path = path.rstrip("/") or "/"

    query = [
        (key.lower(), item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=False)
        if key.lower() not in _TRACKING_PARAMS
        and not key.lower().startswith("utm_")
    ]
    query.sort()
    return urlunsplit(("https", host, path, urlencode(query, doseq=True), ""))


def _fold_company(value: str | None) -> str:
    text = (value or "").casefold()
    text = text.replace("&", " and ")
    text = _GENDER_RE.sub(" ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    tokens = [token for token in text.split() if token]
    while tokens and tokens[0] == "the":
        tokens.pop(0)
    while tokens and tokens[-1] in _LEGAL_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def _fold_role_segment(value: str) -> str:
    text = _GENDER_RE.sub(" ", value.casefold())
    text = re.sub(r"[^a-z0-9+#.]+", " ", text)
    tokens = [token.strip(".") for token in text.split() if token.strip(".")]
    noise = {"gn", "all", "genders", "any", "gender"}
    tokens = [token for token in tokens if token not in noise]
    return " ".join(tokens)


def _fold_role(value: str | None) -> str:
    # Drop gender markers first: "m/w/d" itself contains slashes and must not
    # survive as role tokens.  Boards sometimes repeat the same title around a
    # slash ("AI Engineer / AI Engineer (f/m/x)"); collapse identical segments
    # but keep genuinely distinct ones ("Data Scientist / Data Analyst").
    text = _GENDER_RE.sub(" ", (value or "").casefold())
    segments = [part for part in text.split("/") if part.strip()]
    folded: list[str] = []
    for segment in segments or [text]:
        part = _fold_role_segment(segment)
        if part and part not in folded:
            folded.append(part)
    return " ".join(folded)


def role_key_values(company: str | None, title: str | None) -> tuple[str, str]:
    return (_fold_company(company), _fold_role(title))


def job_role_key(job: Job) -> tuple[str, str]:
    return role_key_values(job.company, job.title)


def job_fingerprint(job: Job) -> tuple[str, str, str]:
    """Legacy company/title/location fingerprint used by excluded jobs."""
    return fingerprint_values(job.company, job.title, job.location)


# ---------------------------------------------------------------------------
# SimHash (64 bit)
# ---------------------------------------------------------------------------


def simhash(text: str | None, bits: int = 64) -> int:
    """Deterministic 64-bit SimHash over word unigrams and bigrams."""
    folded = (text or "").casefold()
    tokens = _TOKEN_RE.findall(folded)
    if not tokens:
        return 0
    features = list(tokens)
    features.extend(f"{first} {second}" for first, second in zip(tokens, tokens[1:]))
    vector = [0] * bits
    for feature in features:
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
        value = int.from_bytes(digest, "big")
        for index in range(bits):
            vector[index] += 1 if (value >> index) & 1 else -1
    result = 0
    for index, weight in enumerate(vector):
        if weight > 0:
            result |= 1 << index
    return result


def hamming_distance(left: int, right: int) -> int:
    return (left ^ right).bit_count()


def job_simhash(job: Job, bits: int = 64) -> int:
    text = job.description or job.posted_text or f"{job.company} {job.title}"
    return simhash(text, bits=bits)


def _describe_duplicate(job: Job, other: Job) -> str:
    return (
        f"Possible duplicate: '{job.title}' at {job.company} has a near-identical "
        f"description to '{other.title}' at {other.company}."
    )


def dedupe_jobs(
    jobs: list[Job],
    existing_keys: set[tuple[str, str]] | None = None,
    existing_fingerprints: set[tuple[str, str, str]] | None = None,
    cross_source: bool = False,
    excluded_keys: set[tuple[str, str]] | None = None,
    excluded_fingerprints: set[tuple[str, str, str]] | None = None,
    *,
    existing_urls: set[str] | None = None,
    existing_roles: set[tuple[str, str]] | None = None,
    excluded_urls: set[str] | None = None,
    excluded_roles: set[tuple[str, str]] | None = None,
    warnings: list[str] | None = None,
    simhash_threshold: int = 8,
) -> tuple[list[Job], int]:
    """Remove exact duplicates and flag near-duplicates.

    ``existing_roles`` and ``excluded_roles`` are only consulted when
    ``cross_source`` is true: collapsing the same role across boards is an
    opt-in trade-off, while an exact URL is always a duplicate.
    """
    seen_keys = set(existing_keys or set())
    seen_fingerprints = set(existing_fingerprints or set())
    seen_urls = {url for url in (existing_urls or set()) if url}
    seen_roles = set(existing_roles or set()) if cross_source else set()
    ex_keys = set(excluded_keys or set())
    ex_fingerprints = {fp for fp in (excluded_fingerprints or set()) if fp != ("", "", "")}
    ex_urls = {url for url in (excluded_urls or set()) if url}
    ex_roles = set(excluded_roles or set()) if cross_source else set()

    unique: list[Job] = []
    accepted: list[tuple[Job, int]] = []
    skipped = 0

    for job in jobs:
        key = job.key()
        fingerprint = job_fingerprint(job)
        url = canonical_url(job.canonical_url or job.link)
        role = job_role_key(job)

        if url:
            job.canonical_url = url
        if not job.first_seen:
            job.first_seen = job.scraped_at
        job.last_seen = job.scraped_at

        # Excluded jobs (deleted by the user) are NEVER re-added.
        if (
            key in ex_keys
            or (fingerprint != ("", "", "") and fingerprint in ex_fingerprints)
            or (url and url in ex_urls)
            or (role in ex_roles and role != ("", ""))
        ):
            skipped += 1
            continue

        duplicate = key in seen_keys
        if not duplicate and url:
            duplicate = url in seen_urls
        if not duplicate and cross_source:
            duplicate = fingerprint in seen_fingerprints or (
                role != ("", "") and role in seen_roles
            )

        if duplicate:
            skipped += 1
            continue

        seen_keys.add(key)
        if url:
            seen_urls.add(url)
        seen_fingerprints.add(fingerprint)
        if role != ("", ""):
            seen_roles.add(role)
        unique.append(job)

        # SimHash is advisory: compare against accepted jobs from this batch.
        value = job_simhash(job)
        job.fingerprint = f"{value:016x}"
        for previous, previous_hash in accepted:
            if (
                previous.company.strip().casefold() == job.company.strip().casefold()
                or value == 0
            ):
                continue
            if hamming_distance(value, previous_hash) <= simhash_threshold:
                message = _describe_duplicate(job, previous)
                if warnings is not None:
                    warnings.append(message)
                break
        accepted.append((job, value))

    return unique, skipped
