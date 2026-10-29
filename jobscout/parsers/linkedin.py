from __future__ import annotations

import re

from bs4 import BeautifulSoup

from jobscout.models import Job, Source
from jobscout.parsers.dates import clean_text, parse_date_value, parse_relative_date
from jobscout.parsers.urls import extract_linkedin_job_id, linkedin_link


LEGACY_CARD_SELECTOR = (
    "li[data-occludable-job-id], "
    "div.job-card-container[data-occludable-job-id], "
    "li.jobs-search-results__list-item"
)
NEW_CARD_SELECTOR = 'div[role="button"][componentkey^="job-card-component-ref-"]'
CARD_SELECTOR = f"{LEGACY_CARD_SELECTOR}, {NEW_CARD_SELECTOR}"


def parse_linkedin_html(html: str, query: str) -> list[Job]:
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select(LEGACY_CARD_SELECTOR)

    jobs: list[Job] = []
    seen: set[str] = set()

    for card in cards:
        link_el = card.select_one(
            "a.base-card__full-link[href], "
            "a.job-card-container__link[href], "
            "a[href*='/jobs/view/']"
        )
        href = link_el.get("href") if link_el else None
        job_id = (
            card.get("data-occludable-job-id")
            or card.get("data-job-id")
            or extract_linkedin_job_id(href)
        )
        if not job_id or job_id in seen:
            continue

        visible_el = (
            link_el.select_one("span[aria-hidden='true']") if link_el else None
        )
        title = (
            clean_text(visible_el.get_text(" ", strip=True)) if visible_el else ""
        )
        if not title:
            title = clean_text(link_el.get("aria-label") or "") if link_el else ""
        if not title:
            title_el = card.select_one(
                "h3.base-search-card__title, "
                "h3.job-card-list__title, "
                "a.job-card-container__link, "
                "h3"
            )
            visible_el = (
                title_el.select_one("span[aria-hidden='true']") if title_el else None
            )
            title = clean_text(
                (visible_el or title_el).get_text(" ", strip=True) if title_el else ""
            )
        if not title:
            continue

        company_el = card.select_one(
            ".artdeco-entity-lockup__subtitle, "
            ".job-card-container__company-name, "
            "h4.base-search-card__subtitle"
        )
        company = clean_text(company_el.get_text(" ", strip=True) if company_el else "")

        location_el = card.select_one(
            ".job-card-container__metadata-item, "
            ".artdeco-entity-lockup__caption, "
            ".job-search-card__location, "
            ".job-card-container__metadata-wrapper"
        )
        location = clean_text(location_el.get_text(" ", strip=True) if location_el else "")

        time_el = card.select_one("time[datetime], time")
        posted_text = _visible_text(time_el)
        posted_date = parse_date_value(time_el.get("datetime") if time_el else None)
        estimated = False
        if not posted_date:
            posted_date, estimated = parse_relative_date(posted_text)

        if not posted_text:
            listed = card.select_one(
                ".job-card-container__listed-time, .job-search-card__listdate, "
                ".job-search-card__listdate--new"
            )
            posted_text = _visible_text(listed)
            if not posted_date:
                posted_date, estimated = parse_relative_date(posted_text)

        seen.add(job_id)
        jobs.append(
            Job(
                company=company or "Unknown company",
                title=title,
                link=linkedin_link(href, job_id),
                posted_date=posted_date,
                source=Source.linkedin,
                posted_text=posted_text or None,
                estimated=estimated,
                job_id=job_id,
                location=location or None,
                search_query=query,
            )
        )

    if not jobs:
        jobs = _parse_new_cards(soup, query)
    return jobs


def _parse_new_cards(soup: BeautifulSoup, query: str) -> list[Job]:
    jobs: list[Job] = []
    seen: set[str] = set()
    for card in soup.select(NEW_CARD_SELECTOR):
        key = card.get("componentkey", "")
        match = re.search(r"job-card-component-ref-(\d+)", key)
        if not match:
            continue
        job_id = match.group(1)
        if job_id in seen:
            continue

        paragraphs = card.select("p")
        if not paragraphs:
            continue
        title = _new_card_title(paragraphs[0])
        if not title:
            continue
        company = (
            clean_text(paragraphs[1].get_text(" ", strip=True))
            if len(paragraphs) > 1
            else ""
        )
        location = (
            clean_text(paragraphs[2].get_text(" ", strip=True))
            if len(paragraphs) > 2
            else ""
        )
        posted_text = ""
        posted_date = None
        estimated = False
        for paragraph in paragraphs[3:]:
            candidate = clean_text(
                paragraph.get_text("\n", strip=True).splitlines()[0]
            )
            parsed, is_estimated = parse_relative_date(candidate)
            if parsed:
                posted_text = candidate
                posted_date = parsed
                estimated = is_estimated
                break

        seen.add(job_id)
        jobs.append(
            Job(
                company=company or "Unknown company",
                title=title,
                link=linkedin_link(None, job_id),
                posted_date=posted_date,
                source=Source.linkedin,
                posted_text=posted_text or None,
                estimated=estimated,
                job_id=job_id,
                location=location or None,
                search_query=query,
            )
        )
    return jobs


def _new_card_title(paragraph) -> str:
    lines = [
        clean_text(line)
        for line in paragraph.get_text("\n", strip=True).splitlines()
        if clean_text(line)
    ]
    cleaned: list[str] = []
    for line in lines:
        line = re.sub(r"^Ausgewählt,\s*", "", line, flags=re.IGNORECASE)
        line = re.sub(
            r"\s*\(Verifizierte Stellenanzeige\)\s*$",
            "",
            line,
            flags=re.IGNORECASE,
        )
        if line and (not cleaned or cleaned[-1] != line):
            cleaned.append(line)
    return cleaned[0] if cleaned else ""


def _visible_text(el) -> str:
    if el is None:
        return ""
    direct = clean_text(
        " ".join(str(node) for node in el.find_all(string=True, recursive=False))
    )
    if direct:
        return direct
    clone = BeautifulSoup(str(el), "html.parser")
    for hidden in clone.select(".visually-hidden, .sr-only, [aria-hidden='true']"):
        hidden.decompose()
    return clean_text(clone.get_text(" ", strip=True))
