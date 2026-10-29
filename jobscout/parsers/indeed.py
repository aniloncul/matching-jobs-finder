from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from bs4 import BeautifulSoup

from jobscout.models import Job, Source
from jobscout.parsers.dates import clean_text, parse_date_value, parse_relative_date
from jobscout.parsers.urls import extract_indeed_job_id, indeed_link


def parse_indeed_html(
    html: str,
    query: str,
    base_url: str = "https://www.indeed.com",
    skip_sponsored: bool = True,
) -> list[Job]:
    soup = BeautifulSoup(html, "html.parser")
    metadata = _job_metadata(html)
    cards = soup.select(
        "div.job_seen_beacon[data-jk], "
        "div[data-testid='slider_item'][data-jk], "
        "div.job_seen_beacon"
    )

    jobs: list[Job] = []
    seen: set[str] = set()

    for card in cards:
        if skip_sponsored:
            card_text = card.get_text(" ", strip=True).lower()
            if (
                "sponsored" in card_text
                or "gesponsert" in card_text
                or card.select_one("[data-testid='sponsoredJob'], .sponsoredJob")
            ):
                continue

        link_el = card.select_one(
            "h2.jobTitle a[href], "
            "h3.jobTitle a[href], "
            "a.jcs-JobTitle[href], "
            "a[href*='viewjob'], "
            "a[href*='/rc/clk'], "
            "a[id^='job_']"
        )
        href = link_el.get("href") if link_el else None
        job_id = (
            card.get("data-jk")
            or (link_el.get("data-jk") if link_el else None)
            or extract_indeed_job_id(href)
        )
        if not job_id or job_id in seen:
            continue

        title_el = card.select_one(
            "h2.jobTitle span[title], "
            "h2.jobTitle a span, "
            "h2.jobTitle, "
            "h3.jobTitle span[title], "
            "h3.jobTitle a span, "
            "h3.jobTitle, "
            "h2 a span, "
            "h2, "
            "h3"
        )
        title = clean_text(title_el.get_text(" ", strip=True) if title_el else "")
        if not title:
            continue

        company_el = card.select_one(
            "[data-testid='company-name'], .companyName, span.companyName"
        )
        company = clean_text(company_el.get_text(" ", strip=True) if company_el else "")

        location_el = card.select_one(
            "[data-testid='text-location'], "
            ".companyLocation, "
            "div.companyLocation, "
            "[data-testid='text-jobLocation']"
        )
        location = clean_text(location_el.get_text(" ", strip=True) if location_el else "")

        meta = metadata.get(job_id)
        if meta:
            posted_date, estimated, posted_text = meta
        else:
            date_el = card.select_one(
                "[data-testid='myJobsStateDate'], "
                "span.date, "
                ".date, "
                "time[datetime]"
            )
            posted_text = clean_text(
                date_el.get_text(" ", strip=True) if date_el else ""
            )
            posted_date = parse_date_value(
                date_el.get("datetime") if date_el else None
            )
            estimated = False
            if not posted_date:
                posted_date, estimated = parse_relative_date(posted_text)

        seen.add(job_id)
        jobs.append(
            Job(
                company=company or "Unknown company",
                title=title,
                link=indeed_link(href, job_id, base_url),
                posted_date=posted_date,
                source=Source.indeed,
                posted_text=posted_text or None,
                estimated=estimated,
                job_id=job_id,
                location=location or None,
                search_query=query,
            )
        )

    return jobs


_JOB_CARDS_DATA = re.compile(
    r'window\.mosaic\.providerData\["mosaic-provider-jobcards"\]\s*=\s*'
)


def _job_metadata(html: str) -> dict[str, tuple[str | None, bool, str]]:
    match = _JOB_CARDS_DATA.search(html)
    if not match:
        return {}
    brace = html.find("{", match.end())
    if brace < 0:
        return {}
    try:
        data, _ = json.JSONDecoder().raw_decode(html[brace:])
    except (TypeError, ValueError):
        return {}

    results = (
        data.get("metaData", {})
        .get("mosaicProviderJobCardsModel", {})
        .get("results", [])
    )
    metadata: dict[str, tuple[str | None, bool, str]] = {}
    for item in results:
        if not isinstance(item, dict):
            continue
        job_id = str(item.get("jobkey") or item.get("jobKey") or "")
        if not job_id:
            continue
        posted_text = clean_text(item.get("formattedRelativeTime") or "")
        created = item.get("createDate")
        posted_date = None
        estimated = False
        if isinstance(created, (int, float)) and created > 0:
            seconds = created / 1000 if created > 10_000_000_000 else created
            posted_date = datetime.fromtimestamp(
                seconds, tz=timezone.utc
            ).date().isoformat()
        else:
            posted_date, estimated = parse_relative_date(posted_text)
        metadata[job_id] = (posted_date, estimated, posted_text)
    return metadata
