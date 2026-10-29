from __future__ import annotations

import re

from bs4 import BeautifulSoup

from jobscout.browser import BrowserError, browser_context
from jobscout.config import Settings
from jobscout.models import Job


class DescriptionError(Exception):
    """Raised when a job description cannot be fetched or parsed."""


LINKEDIN_SELECTORS = (
    ".show-more-less-html__markup",
    ".jobs-description-content__text",
    ".jobs-description__content",
    "[class*='jobs-description']",
)

INDEED_SELECTORS = (
    "#jobDescriptionText",
    "[class*='jobsearch-JobComponent-description']",
    "[class*='jobDescription']",
)


def _clean(text: str) -> str:
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


LINKEDIN_MAIN_START = (
    "Details zum Jobangebot",
    "About the job",
    "Job details",
)
LINKEDIN_MAIN_END = (
    "Benachrichtigung für ähnliche Jobangebote",
    "Set alert for similar jobs",
    "Über dieses Unternehmen",
    "About the company",
)


def parse_linkedin_main_text(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    start = None
    for index, line in enumerate(lines):
        if any(marker.lower() in line.lower() for marker in LINKEDIN_MAIN_START):
            start = index + 1
            break
    if start is None:
        return ""
    end = len(lines)
    for index in range(start, len(lines)):
        if any(marker.lower() in lines[index].lower() for marker in LINKEDIN_MAIN_END):
            end = index
            break
    body = [
        line
        for line in lines[start:end]
        if line not in {"… mehr", "... more", "Show more", "See more", "Mehr anzeigen"}
    ]
    return _clean("\n".join(body))


def parse_job_description(source: str, html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    selectors = LINKEDIN_SELECTORS if source == "linkedin" else INDEED_SELECTORS
    for selector in selectors:
        node = soup.select_one(selector)
        if node is not None:
            text = _clean(node.get_text("\n", strip=True))
            if len(text) >= 10:
                return text
    if source == "linkedin":
        main = soup.select_one("main") or soup.select_one("body")
        if main is not None:
            text = parse_linkedin_main_text(main.get_text("\n", strip=True))
            if len(text) >= 10:
                return text
    return ""


def fetch_job_description(
    settings: Settings,
    job: Job,
    headless: bool = True,
) -> str:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    try:
        with browser_context(settings, job.source.value, headless=headless) as (_context, page):
            page.goto(job.link, wait_until="domcontentloaded")
            page.wait_for_timeout(1800)
            if job.source.value == "linkedin" and "authwall" in page.url.lower():
                raise DescriptionError("LinkedIn requires a fresh sign-in before the description can be read.")
            if job.source.value == "linkedin":
                for label in ("… mehr", "See more", "mehr anzeigen", "Show more"):
                    button = page.get_by_text(label, exact=False)
                    if button.count() > 0:
                        try:
                            button.first.click(timeout=1200)
                        except Exception:
                            pass
                        break
            try:
                page.wait_for_selector(
                    ", ".join(LINKEDIN_SELECTORS if job.source.value == "linkedin" else INDEED_SELECTORS),
                    timeout=settings.cover_letter.detail_timeout_ms,
                )
            except PlaywrightTimeoutError:
                pass
            text = parse_job_description(job.source.value, page.content())
    except DescriptionError:
        raise
    except BrowserError as exc:
        raise DescriptionError(str(exc)) from exc
    except Exception as exc:
        raise DescriptionError(f"Could not open the job description: {exc}") from exc

    if not text:
        raise DescriptionError("No job description was found on the posting page.")
    return text
