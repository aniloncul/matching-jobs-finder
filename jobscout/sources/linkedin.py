from __future__ import annotations

from urllib.parse import urlencode

from jobscout.browser import BrowserError, browser_context
from jobscout.config import Settings
from jobscout.control import RunControl
from jobscout.models import Job
from jobscout.parsers.linkedin import CARD_SELECTOR, parse_linkedin_html
from jobscout.queries import per_query_limit, split_queries
from jobscout.sources.base import BaseSource, Emit, SourceError


class LinkedInSource(BaseSource):
    source_name = "linkedin"
    agent_id = "linkedin"

    def __init__(
        self,
        settings: Settings,
        control: RunControl,
        emit: Emit,
    ) -> None:
        super().__init__(settings, control, emit)

    def search(self) -> list[Job]:
        cfg = self.settings.sources.linkedin
        queries = split_queries(cfg.query) or [cfg.query]
        limit = per_query_limit(cfg.max_jobs, len(queries))
        jobs: list[Job] = []
        seen: set[str] = set()

        self.status("Opening visible LinkedIn browser", 0.05)
        try:
            with browser_context(self.settings, "linkedin") as (_context, page):
                for index, query in enumerate(queries, start=1):
                    if len(jobs) >= cfg.max_jobs:
                        break
                    self.control.checkpoint(f"linkedin query {index}")
                    self.status(
                        f"LinkedIn · searching {query!r}",
                        0.05 + 0.7 * (index - 1) / len(queries),
                    )
                    page.goto(_search_url(cfg, query), wait_until="domcontentloaded")
                    page.wait_for_timeout(2200)

                    if _linkedin_blocked(page):
                        raise SourceError(
                            "LinkedIn is asking for a signed-in session. "
                            "Run `make warm-linkedin`, sign in once, then retry."
                        )

                    try:
                        page.wait_for_selector(CARD_SELECTOR, timeout=12_000)
                    except Exception as exc:
                        if _linkedin_blocked(page):
                            raise SourceError(
                                "LinkedIn auth wall detected. Run `make warm-linkedin`."
                            ) from exc
                        if _linkedin_no_results(page) or jobs:
                            self.status(
                                f"LinkedIn · no jobs for {query!r}",
                                0.05 + 0.7 * index / len(queries),
                            )
                            continue
                        raise SourceError(
                            "No LinkedIn job cards appeared. The page may be blocked "
                            "or the markup changed."
                        ) from exc

                    target = min(limit, cfg.max_jobs - len(jobs))
                    for _ in range(4):
                        self.control.checkpoint("linkedin scroll")
                        count = page.locator(CARD_SELECTOR).count()
                        if count >= target:
                            break
                        page.mouse.wheel(0, 3200)
                        page.wait_for_timeout(650)

                    html = page.content()
                    term_jobs = parse_linkedin_html(html, query)
                    if not term_jobs:
                        raise SourceError(
                            "LinkedIn returned no parseable jobs. Check the visible "
                            "browser for a challenge or sign-in prompt."
                        )

                    added = 0
                    for job in term_jobs:
                        if job.job_id in seen:
                            continue
                        seen.add(job.job_id)
                        jobs.append(job)
                        added += 1
                        if added >= limit or len(jobs) >= cfg.max_jobs:
                            break
                    self.status(
                        f"LinkedIn · {len(jobs)} jobs extracted",
                        0.08 + 0.82 * index / len(queries),
                    )

                self.control.checkpoint("linkedin parse")
                jobs = jobs[: cfg.max_jobs]
                if not jobs:
                    self.status("LinkedIn returned no jobs", 1.0)
                    return []
                self.status(f"LinkedIn · {len(jobs)} jobs extracted", 1.0)
                return jobs
        except SourceError:
            raise
        except BrowserError as exc:
            raise SourceError(str(exc)) from exc
        except Exception as exc:
            raise SourceError(f"LinkedIn run failed: {exc}") from exc


def _search_url(cfg, query: str | None = None) -> str:
    params = {
        "keywords": query or cfg.query,
        "location": cfg.location,
        "f_TPR": f"r{cfg.date_posted_hours * 3600}",
        "sortBy": "DD",
    }
    if cfg.workplace == "remote":
        params["f_WT"] = "2"
    if cfg.experience == "mid":
        params["f_E"] = "4"
    return f"https://www.linkedin.com/jobs/search/?{urlencode(params)}"


def _linkedin_no_results(page) -> bool:
    try:
        body = page.locator("body").inner_text().lower()
    except Exception:
        return False
    return any(
        marker in body
        for marker in (
            "no matching jobs",
            "no results found",
            "keine ergebnisse",
            "0 ergebnisse",
        )
    )


def _linkedin_blocked(page) -> bool:
    url = (page.url or "").lower()
    if "authwall" in url or "/login" in url or "checkpoint" in url:
        return True
    try:
        body = page.locator("body").inner_text().lower()
    except Exception:
        return False
    markers = (
        "sign in to view",
        "sign in to see",
        "join linkedin to see",
        "authwall",
        "security verification",
    )
    return any(marker in body for marker in markers)
