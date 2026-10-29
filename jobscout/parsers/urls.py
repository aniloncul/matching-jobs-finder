from __future__ import annotations

import re
from urllib.parse import parse_qs, urljoin, urlparse


_LINKEDIN_ID = re.compile(r"/jobs/view/(?:[^/?#]*-)?(\d+)")
_INDEED_ID = re.compile(r"(?:[?&]jk=|/viewjob\?jk=)([a-zA-Z0-9]+)")


def extract_linkedin_job_id(href: str | None) -> str | None:
    if not href:
        return None
    match = _LINKEDIN_ID.search(href)
    return match.group(1) if match else None


def extract_indeed_job_id(href: str | None) -> str | None:
    if not href:
        return None
    parsed = urlparse(href)
    values = parse_qs(parsed.query).get("jk")
    if values:
        return values[0]
    match = _INDEED_ID.search(href)
    return match.group(1) if match else None


def linkedin_link(href: str | None, job_id: str | None) -> str:
    if job_id:
        return f"https://www.linkedin.com/jobs/view/{job_id}"
    return urljoin("https://www.linkedin.com", href or "")


def indeed_link(href: str | None, job_id: str | None, base_url: str) -> str:
    if job_id:
        return f"{base_url.rstrip('/')}/viewjob?jk={job_id}"
    return urljoin(base_url, href or "")
