from __future__ import annotations

from urllib.parse import urlencode

from jobscout.browser import BrowserError, browser_context
from jobscout.config import Settings
from jobscout.control import RunCancelled, RunControl
from jobscout.models import Job
from jobscout.parsers.linkedin import CARD_SELECTOR, parse_linkedin_html
from jobscout.queries import per_query_limit, split_queries
from jobscout.sources.base import BaseSource, Emit, SourceError, SourceResult


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

    def search(self) -> SourceResult:
        cfg = self.settings.sources.linkedin
        queries = split_queries(cfg.query) or [cfg.query]
        limit = per_query_limit(cfg.max_jobs, len(queries))
        jobs: list[Job] = []
        seen: set[str] = set()
        pages_fetched = 0
        status: str = "ok"
        message: str | None = None

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
                    pages_fetched += 1
                    page.wait_for_timeout(2200)

                    if _linkedin_blocked(page):
                        status = "login_required"
                        message = (
                            "LinkedIn is asking for a signed-in session. "
                            "Run `make warm-linkedin`, sign in once, then retry."
                        )
                        break

                    try:
                        page.wait_for_selector(CARD_SELECTOR, timeout=12_000)
                    except Exception:
                        if _linkedin_blocked(page):
                            status = "login_required"
                            message = "LinkedIn auth wall detected. Run `make warm-linkedin`."
                            break
                        if _linkedin_no_results(page):
                            self.status(
                                f"LinkedIn · no jobs for {query!r}",
                                0.05 + 0.7 * index / len(queries),
                            )
                            continue
                        status = "layout_changed"
                        message = (
                            "No LinkedIn job cards appeared. The page may be blocked "
                            "or the markup changed."
                        )
                        break

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
                        if _linkedin_no_results(page):
                            continue
                        status = "layout_changed"
                        message = (
                            "LinkedIn returned no parseable jobs. Check the visible "
                            "browser for a challenge or a markup change."
                        )
                        break

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
        except RunCancelled:
            raise
        except SourceError as exc:
            status, message = "error", str(exc)
        except BrowserError as exc:
            status, message = "error", str(exc)
        except Exception as exc:
            status, message = "error", f"LinkedIn run failed: {exc}"

        jobs = jobs[: cfg.max_jobs]
        if status == "ok":
            status = "ok" if jobs else "empty"
        if status in {"ok", "empty"}:
            self.status(
                f"LinkedIn · {len(jobs)} jobs extracted" if jobs else "LinkedIn returned no jobs",
                1.0,
            )
        else:
            self.log(message or status, level="error")
            self.status(message or f"LinkedIn · {status}", None, status="error")
        return self.finish(
            jobs,
            status=status,  # type: ignore[arg-type]
            message=message,
            pages_fetched=pages_fetched,
        )


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
