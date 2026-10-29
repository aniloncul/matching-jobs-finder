from __future__ import annotations

from urllib.parse import urlencode

from jobscout.browser import BrowserError, browser_context
from jobscout.config import Settings
from jobscout.control import RunCancelled
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
        _terminal_error: str | None = None  # set if we must raise after browser closes

        self.status("Opening visible Indeed browser", 0.05)
        try:
            with browser_context(self.settings, "indeed") as (context, page):
                for query_index, query in enumerate(queries, start=1):
                    if len(jobs) >= cfg.max_jobs or _terminal_error:
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
                                _terminal_error = (
                                    "Indeed is asking for a signed-in session. Run "
                                    "`make warm-indeed`, sign in once, then retry."
                                )
                            else:
                                self.status(
                                    "Indeed stopped this query after page 1 "
                                    "(sign-in required for more)",
                                    0.92,
                                )
                            break

                        if _indeed_blocked(page):
                            resolved = self._handle_challenge_fallback(context, page, url)
                            if not resolved:
                                if page_index > 1 and (term_jobs or jobs):
                                    self.status(
                                        "Indeed stopped after challenge — keeping results so far",
                                        0.92,
                                    )
                                else:
                                    _terminal_error = (
                                        "Indeed verification challenge could not be resolved. "
                                        "Run `make warm-indeed` or `make reset-indeed`, "
                                        "solve it once, then retry."
                                    )
                                break

                        try:
                            page.wait_for_selector(
                                "div.job_seen_beacon, "
                                "div[data-testid='slider_item'], "
                                "a.jcs-JobTitle[data-jk]",
                                timeout=12_000,
                            )
                        except Exception as exc:
                            if _indeed_blocked(page):
                                resolved = self._handle_challenge_fallback(context, page, url)
                                if not resolved:
                                    if page_index > 1 and (term_jobs or jobs):
                                        self.status(
                                            "Indeed stopped after challenge — keeping results so far",
                                            0.92,
                                        )
                                    else:
                                        _terminal_error = (
                                            "Indeed verification challenge could not be resolved. "
                                            "Run `make warm-indeed` or `make reset-indeed`, "
                                            "solve it once, then retry."
                                        )
                                    break
                                # challenge resolved — retry selector
                                try:
                                    page.wait_for_selector(
                                        "div.job_seen_beacon, "
                                        "div[data-testid='slider_item'], "
                                        "a.jcs-JobTitle[data-jk]",
                                        timeout=10_000,
                                    )
                                except Exception:
                                    break
                            elif _indeed_no_results(page) or jobs:
                                self.status(
                                    f"Indeed · no jobs for {query!r}",
                                    0.92,
                                )
                                break
                            elif page_index > 1 and term_jobs:
                                self.status(
                                    "Indeed stopped this query after page 1 "
                                    "(next page unavailable)",
                                    0.92,
                                )
                                break
                            else:
                                # No cards, not detected as blocked, not "no results" —
                                # Indeed may be serving a soft-block page. Try the fallback
                                # (cookie clear + manual wait) before giving up.
                                self.status(
                                    "Indeed · No job cards — trying cookie reset...",
                                    0.5,
                                )
                                resolved = self._handle_challenge_fallback(context, page, url)
                                if resolved:
                                    try:
                                        page.wait_for_selector(
                                            "div.job_seen_beacon, "
                                            "div[data-testid='slider_item'], "
                                            "a.jcs-JobTitle[data-jk]",
                                            timeout=10_000,
                                        )
                                    except Exception:
                                        pass
                                else:
                                    _terminal_error = (
                                        "No Indeed job cards appeared after cookie reset. "
                                        "The page may be blocked or the markup changed."
                                    )
                                    break

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

            # browser is now closed — safe to raise if we hit a hard error
            if _terminal_error and not jobs:
                raise SourceError(_terminal_error)

            jobs = jobs[: cfg.max_jobs]
            if not jobs:
                self.status("Indeed returned no jobs", 1.0)
                return []
            self.status(f"Indeed · {len(jobs)} jobs extracted", 1.0)
            return jobs
        except (SourceError, RunCancelled):
            raise
        except BrowserError as exc:
            raise SourceError(str(exc)) from exc
        except Exception as exc:
            raise SourceError(f"Indeed run failed: {exc}") from exc

    def _handle_challenge_fallback(self, context, page, url: str) -> bool:
        """Fallback when Cloudflare/Indeed verification challenge is detected.

        1. Focuses Chrome and updates status with countdown while waiting up to 45s
           for interactive verification or auto-resolution.
        2. If still blocked, clears session cookies and retries the URL once with
           a clean session.
        3. Returns True if unblocked, False if still blocked.
        """
        import subprocess
        import time

        self.emit(
            "log",
            text="Cloudflare / Indeed verification challenge detected. "
            "Please solve the captcha in the open Chrome browser window...",
        )

        try:
            page.bring_to_front()
            subprocess.run(
                ["osascript", "-e", 'tell application "Google Chrome" to activate'],
                check=False,
                capture_output=True,
            )
        except Exception:
            pass

        start_time = time.time()
        timeout_seconds = 45
        while time.time() - start_time < timeout_seconds:
            remaining = int(timeout_seconds - (time.time() - start_time))
            self.status(
                f"Indeed · Cloudflare challenge! Solve in Chrome ({remaining}s)",
                0.5,
            )
            # NOTE: do NOT call self.control.checkpoint() here — RunCancelled would
            # break the wait loop and skip the cookie-clear fallback entirely.
            page.wait_for_timeout(1000)
            if not _indeed_blocked(page):
                # Confirm resolution: wait 2s and re-check in case it's mid-redirect
                page.wait_for_timeout(2000)
                if not _indeed_blocked(page):
                    self.status("Indeed · Challenge resolved! Continuing...", 0.5)
                    self.emit("log", text="Indeed verification challenge resolved.")
                    return True

        # Second fallback: clear session cookies and retry with a clean context
        self.status("Indeed · Clearing cookies & retrying clean session...", 0.5)
        self.emit(
            "log",
            text="Challenge still active after 45s. Clearing cookies and retrying with a fresh session...",
        )
        try:
            context.clear_cookies()
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(4000)
            if not _indeed_blocked(page):
                self.status("Indeed · Clean session bypass succeeded! Continuing...", 0.5)
                self.emit("log", text="Clean session successfully bypassed the challenge.")
                return True
            # Cookie clear didn't help — bring Chrome to front for one more manual attempt
            self.status("Indeed · Cookie clear failed — waiting 20s for manual solve...", 0.5)
            self.emit("log", text="Cookie clear didn't resolve challenge. Giving 20s for manual solve.")
            try:
                page.bring_to_front()
                subprocess.run(
                    ["osascript", "-e", 'tell application "Google Chrome" to activate'],
                    check=False,
                    capture_output=True,
                )
            except Exception:
                pass
            for _ in range(20):
                page.wait_for_timeout(1000)
                if not _indeed_blocked(page):
                    page.wait_for_timeout(2000)
                    if not _indeed_blocked(page):
                        self.status("Indeed · Challenge resolved after cookie clear! Continuing...", 0.5)
                        self.emit("log", text="Challenge resolved after cookie clear.")
                        return True
        except Exception as exc:
            self.emit("log", text=f"Cookie clear retry encountered error: {exc}")

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

    title_markers = (
        # English
        "just a moment",
        "attention required",
        "security check",
        "verify you are human",
        # German (de-DE locale)
        "nur einen moment",
        "sicherheitsüberprüfung",
        "bitte bestätigen",
    )
    if any(marker in title for marker in title_markers):
        return True

    if "captcha" in url or "challenge" in url:
        return True

    body_markers = (
        # English
        "additional verification required",
        "just a moment",
        "unusual traffic",
        "verify you are human",
        # German
        "zusätzliche verifizierung",
        "nur einen moment",
        "ungewöhnlicher datenverkehr",
        "sie sind kein roboter",
        "bitte bestätigen sie",
        # Universal Cloudflare markers (language-independent)
        "ray-id",
        "cloudflare ray id",
        "fehlerbehebung bei cloudflare",
    )
    return any(marker in body for marker in body_markers)



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
