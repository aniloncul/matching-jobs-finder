from __future__ import annotations

from urllib.parse import urlencode

from jobscout.browser import BrowserError, browser_context
from jobscout.config import Settings
from jobscout.control import RunControl
from jobscout.models import Job
from jobscout.parsers.indeed import parse_indeed_html
from jobscout.queries import per_query_limit, split_queries
from jobscout.sources.base import BaseSource, Emit, SourceError


class IndeedSource(BaseSource):
    source_name = "indeed"
    agent_id = "indeed"

    def __init__(
        self,
        settings: Settings,
        control: RunControl,
        emit: Emit,
    ) -> None:
        super().__init__(settings, control, emit)

    def search(self) -> list[Job]:
        cfg = self.settings.sources.indeed
        queries = split_queries(cfg.query) or [cfg.query]
        limit = per_query_limit(cfg.max_jobs, len(queries))
        jobs: list[Job] = []
        seen: set[str] = set()

        self.status("Opening visible Indeed browser", 0.05)
        try:
            with browser_context(self.settings, "indeed") as (context, page):
                for query_index, query in enumerate(queries, start=1):
                    if len(jobs) >= cfg.max_jobs:
                        break
                    self.control.checkpoint(f"indeed query {query_index}")
                    target = min(limit, cfg.max_jobs - len(jobs))
                    term_jobs: list[Job] = []
                    pages = list(range(0, max(target, 1), 10))[:3]

                    for page_index, start in enumerate(pages, start=1):
                        if len(term_jobs) >= target or len(jobs) >= cfg.max_jobs:
                            break
                        self.control.checkpoint("indeed page")
                        url = _indeed_url(cfg, start, query)
                        self.status(
                            f"Indeed · {query!r} · page {page_index}",
                            0.08 + 0.84 * (query_index - 1) / len(queries),
                        )
                        page.goto(url, wait_until="domcontentloaded")
                        page.wait_for_timeout(1800)

                        if _indeed_signin_wall(page):
                            if page_index == 1:
                                raise SourceError(
                                    "Indeed is asking for a signed-in session. Run "
                                    "`make warm-indeed`, sign in once, then retry."
                                )
                            self.status(
                                "Indeed stopped this query after page 1 "
                                "(sign-in required for more)",
                                0.92,
                            )
                            break

                        if _indeed_blocked(page):
                            if not self._handle_challenge_fallback(context, page, url):
                                if page_index > 1 and (term_jobs or jobs):
                                    self.status(
                                        "Indeed stopped this query after page 1 "
                                        "(verification challenge)",
                                        0.92,
                                    )
                                    break
                                raise SourceError(
                                    "Indeed served a verification challenge. Run "
                                    "`make warm-indeed` or `make reset-indeed`, "
                                    "solve it once, then retry."
                                )

                        try:
                            page.wait_for_selector(
                                "div.job_seen_beacon, "
                                "div[data-testid='slider_item'], "
                                "a.jcs-JobTitle[data-jk]",
                                timeout=12_000,
                            )
                        except Exception as exc:
                            if _indeed_blocked(page):
                                if not self._handle_challenge_fallback(context, page, url):
                                    if page_index > 1 and (term_jobs or jobs):
                                        self.status(
                                            "Indeed stopped this query after page 1 "
                                            "(verification challenge)",
                                            0.92,
                                        )
                                        break
                                    raise SourceError(
                                        "Indeed verification challenge detected. "
                                        "Run `make warm-indeed` or `make reset-indeed` "
                                        "once, then retry."
                                    ) from exc
                                try:
                                    page.wait_for_selector(
                                        "div.job_seen_beacon, "
                                        "div[data-testid='slider_item'], "
                                        "a.jcs-JobTitle[data-jk]",
                                        timeout=10_000,
                                    )
                                except Exception:
                                    pass
                            if _indeed_no_results(page) or jobs:
                                self.status(
                                    f"Indeed · no jobs for {query!r}",
                                    0.92,
                                )
                                break
                            if page_index > 1 and term_jobs:
                                self.status(
                                    "Indeed stopped this query after page 1 "
                                    "(next page unavailable)",
                                    0.92,
                                )
                                break
                            if not _indeed_blocked(page) and not _indeed_no_results(page):
                                raise SourceError(
                                    "No Indeed job cards appeared. The page may be "
                                    "blocked or the markup changed."
                                ) from exc

                        html = page.content()
                        page_jobs = parse_indeed_html(
                            html,
                            query,
                            base_url=cfg.base_url,
                            skip_sponsored=cfg.skip_sponsored,
                        )
                        new_count = 0
                        for job in page_jobs:
                            if job.job_id in seen:
                                continue
                            if len(term_jobs) >= target or len(jobs) >= cfg.max_jobs:
                                break
                            seen.add(job.job_id)
                            jobs.append(job)
                            term_jobs.append(job)
                            new_count += 1

                        if len(term_jobs) >= target or len(jobs) >= cfg.max_jobs:
                            break
                        if new_count == 0:
                            break

                jobs = jobs[: cfg.max_jobs]
                if not jobs:
                    self.status("Indeed returned no jobs", 1.0)
                    return []
                self.status(f"Indeed · {len(jobs)} jobs extracted", 1.0)
                return jobs
        except SourceError:
            raise
        except BrowserError as exc:
            raise SourceError(str(exc)) from exc
        except Exception as exc:
            raise SourceError(f"Indeed run failed: {exc}") from exc

    def _handle_challenge_fallback(self, context, page, url: str) -> bool:
        """Fallback when Cloudflare/Indeed verification challenge is detected.

        1. Brings the browser to front and waits up to 45 seconds for interactive
           verification or auto-resolution.
        2. If still blocked, clears session cookies and retries the URL once with
           a clean session.
        3. Returns True if unblocked, False if still blocked.
        """
        import time

        self.status("Indeed · Verification challenge detected — waiting...", 0.5)
        self.emit(
            "log",
            "Cloudflare / Indeed verification challenge detected. "
            "Please solve the captcha in the open browser window if visible (waiting up to 45s)...",
        )

        try:
            page.bring_to_front()
        except Exception:
            pass

        start_time = time.time()
        while time.time() - start_time < 45:
            self.control.checkpoint("indeed challenge wait")
            page.wait_for_timeout(1000)
            if not _indeed_blocked(page):
                self.status("Indeed · Challenge resolved! Continuing...", 0.5)
                self.emit("log", "Indeed verification challenge resolved.")
                return True

        # Second fallback: clear session cookies and retry with a clean context
        self.status("Indeed · Clearing cookies & retrying clean session...", 0.5)
        self.emit(
            "log",
            "Challenge still active after 45s. Clearing cookies and retrying once...",
        )
        try:
            context.clear_cookies()
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(2500)
            if not _indeed_blocked(page):
                self.status("Indeed · Clean session bypass succeeded! Continuing...", 0.5)
                self.emit("log", "Clean session successfully bypassed the challenge.")
                return True
        except Exception as exc:
            self.emit("log", f"Cookie clear retry encountered error: {exc}")

        return False


def _indeed_url(cfg, start: int, query: str | None = None) -> str:
    params = {
        "q": query or cfg.query,
        "l": cfg.location,
        "fromage": cfg.fromage,
        "sort": "date",
        "radius": cfg.radius,
        "start": start,
    }
    if cfg.remote:
        params["sc"] = "0kf:attr(DSQF7);"
    return f"{cfg.base_url.rstrip('/')}/jobs?{urlencode(params)}"


def _indeed_signin_wall(page) -> bool:
    try:
        url = (page.url or "").lower()
        title = (page.title() or "").lower()
    except Exception:
        return False
    return (
        "secure.indeed.com/auth" in url
        or "page-two-signin" in url
        or title.startswith("anmelden")
    )


def _indeed_blocked(page) -> bool:
    try:
        title = (page.title() or "").lower()
        url = (page.url or "").lower()
        body = page.locator("body").inner_text().lower()
    except Exception:
        return False

    if any(
        marker in title
        for marker in (
            "just a moment",
            "attention required",
            "security check",
            "verify you are human",
        )
    ):
        return True
    if "captcha" in url or "challenge" in url:
        return True

    markers = (
        "additional verification required",
        "just a moment",
        "unusual traffic",
        "verify you are human",
    )
    return any(marker in body for marker in markers)


def _indeed_no_results(page) -> bool:
    try:
        body = page.locator("body").inner_text().lower()
    except Exception:
        return False
    return any(
        marker in body
        for marker in (
            "didn't find any results",
            "no results",
            "keine ergebnisse",
        )
    )
