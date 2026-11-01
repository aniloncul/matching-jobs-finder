"""Job liveness: is the posting still open?

Two rules decide the design:

* Only positive evidence may mark a job ``expired``: an authoritative ATS API
  saying the requisition is gone, an HTTP 410, or a visible "no longer
  accepting applications" banner.
* Everything else is ``uncertain``.  A 403, a login wall, a 404 from a generic
  page, a CAPTCHA or a timeout is *not* proof that the role is closed.  A wrong
  ``expired`` is more expensive than a stale row.

The 3-attempt / 48-hour rule upgrades a persistently unreachable posting to
``expired`` with reason ``unreachable_after_attempts``.  ``--all`` (force)
reopens and re-checks it.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx

from jobscout.config import Settings
from jobscout.models import Job, now_iso

LivenessStatus = Literal["active", "expired", "uncertain"]

ACTIVE = "active"
EXPIRED = "expired"
UNCERTAIN = "uncertain"

USER_AGENT = "JobScout/0.1 (personal job search; +https://github.com/)"

_CLOSURE_MARKERS = (
    "no longer accepting applications",
    "applications are closed",
    "this job is no longer available",
    "this position has been filled",
    "job has expired",
    "job posting has expired",
    "diese stelle ist nicht mehr",
    "diese stelle wurde besetzt",
    "nicht mehr verfügbar",
    "bewerbungsfrist ist abgelaufen",
    "das stellenangebot ist abgelaufen",
    "stellenangebot nicht mehr aktiv",
)

_UNCERTAIN_MARKERS = (
    "sign in to view",
    "join linkedin to see",
    "authwall",
    "verify you are human",
    "captcha",
)


@dataclass(frozen=True)
class AtsRef:
    provider: str
    company: str
    job_id: str
    api_url: str


@dataclass
class LivenessResult:
    status: LivenessStatus
    reason: str
    source: str = "none"
    http_status: int | None = None
    checked_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "source": self.source,
            "http_status": self.http_status,
            "checked_at": self.checked_at,
        }


def _host(url: str) -> tuple[str, list[str]]:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return ("", [])
    segments = [part for part in parsed.path.split("/") if part]
    return (parsed.netloc.lower(), segments)


def detect_ats(url: str | None) -> AtsRef | None:
    """Recognize the public ATS board behind a job URL."""
    host, segments = _host(url or "")
    if host.startswith("www."):
        host = host[4:]

    if host in {"boards.greenhouse.io", "job-boards.greenhouse.io"}:
        if len(segments) >= 3 and segments[1] == "jobs":
            company, job_id = segments[0], segments[2]
            return AtsRef(
                "greenhouse",
                company,
                job_id,
                f"https://boards-api.greenhouse.io/v1/boards/{company}/jobs/{job_id}",
            )

    if host == "jobs.lever.co" and len(segments) >= 2:
        company, job_id = segments[0], segments[1]
        return AtsRef(
            "lever",
            company,
            job_id,
            f"https://api.lever.co/v0/postings/{company}/{job_id}?mode=json",
        )

    if host == "jobs.ashbyhq.com" and len(segments) >= 2:
        company, job_id = segments[0], segments[1]
        return AtsRef(
            "ashby",
            company,
            job_id,
            f"https://api.ashbyhq.com/posting-api/job-board/{company}",
        )

    if host in {"apply.workable.com", "jobs.workable.com"}:
        if len(segments) >= 3 and segments[1] == "j":
            company, job_id = segments[0], segments[2]
            return AtsRef(
                "workable",
                company,
                job_id,
                f"https://apply.workable.com/api/v1/widget/accounts/{company}?details=true",
            )

    if host in {"jobs.smartrecruiters.com", "careers.smartrecruiters.com"}:
        if len(segments) >= 2:
            company = segments[0]
            match = re.match(r"(\d+)", segments[1])
            if match:
                job_id = match.group(1)
                return AtsRef(
                    "smartrecruiters",
                    company,
                    job_id,
                    "https://api.smartrecruiters.com/v1/companies/"
                    f"{company}/postings/{job_id}",
                )
    return None


def _result(
    status: LivenessStatus,
    reason: str,
    *,
    source: str,
    http_status: int | None = None,
) -> LivenessResult:
    return LivenessResult(
        status=status,
        reason=reason,
        source=source,
        http_status=http_status,
    )


def _fetch(client: httpx.Client, url: str) -> httpx.Response | None:
    try:
        return client.get(url, follow_redirects=True)
    except (httpx.HTTPError, ValueError):
        return None


def _probe_single_job_api(
    client: httpx.Client, ref: AtsRef
) -> LivenessResult:
    response = _fetch(client, ref.api_url)
    if response is None:
        return _result(UNCERTAIN, "network_error", source="ats_api")
    status = response.status_code
    if status in {404, 410}:
        return _result(EXPIRED, f"ats_api_{status}", source="ats_api", http_status=status)
    if status in {401, 403}:
        return _result(UNCERTAIN, "ats_api_auth", source="ats_api", http_status=status)
    if status >= 500:
        return _result(UNCERTAIN, "ats_api_5xx", source="ats_api", http_status=status)
    if 200 <= status < 300:
        return _result(ACTIVE, "ats_api_ok", source="ats_api", http_status=status)
    return _result(UNCERTAIN, "ats_api_unexpected", source="ats_api", http_status=status)


def _probe_board_api(client: httpx.Client, ref: AtsRef) -> LivenessResult:
    """Ashby and Workable expose one board document, not one job endpoint."""
    response = _fetch(client, ref.api_url)
    if response is None:
        return _result(UNCERTAIN, "network_error", source="ats_api")
    status = response.status_code
    if status in {404, 410}:
        return _result(UNCERTAIN, "board_not_found", source="ats_api", http_status=status)
    if status in {401, 403}:
        return _result(UNCERTAIN, "ats_api_auth", source="ats_api", http_status=status)
    if status >= 500:
        return _result(UNCERTAIN, "ats_api_5xx", source="ats_api", http_status=status)
    if not 200 <= status < 300:
        return _result(UNCERTAIN, "ats_api_unexpected", source="ats_api", http_status=status)
    try:
        payload = response.json()
    except ValueError:
        return _result(UNCERTAIN, "board_invalid_json", source="ats_api", http_status=status)
    ids = _board_job_ids(payload)
    if not ids:
        return _result(UNCERTAIN, "board_empty", source="ats_api", http_status=status)
    if ref.job_id.casefold() in ids:
        return _result(ACTIVE, "ats_board_contains_job", source="ats_api", http_status=status)
    return _result(EXPIRED, "ats_board_missing_job", source="ats_api", http_status=status)


def _board_job_ids(payload: Any) -> set[str]:
    ids: set[str] = set()

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            for key in ("id", "shortcode", "shortCode", "externalPostingId"):
                value = node.get(key)
                if isinstance(value, (str, int)) and str(value).strip():
                    ids.add(str(value).strip().casefold())
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for item in node:
                visit(item)

    visit(payload)
    return ids


def probe_liveness(
    job: Job,
    *,
    client: httpx.Client,
) -> LivenessResult:
    """Check one job without touching persistent state."""
    url = job.canonical_url or job.link
    if not url:
        return _result(UNCERTAIN, "no_url", source="none")

    ref = detect_ats(url)
    if ref is not None:
        if ref.provider in {"ashby", "workable"}:
            return _probe_board_api(client, ref)
        return _probe_single_job_api(client, ref)

    response = _fetch(client, url)
    if response is None:
        return _result(UNCERTAIN, "network_error", source="page")
    status = response.status_code
    if status == 410:
        return _result(EXPIRED, "http_410", source="page", http_status=status)
    if status in {401, 403, 404}:
        return _result(UNCERTAIN, f"http_{status}", source="page", http_status=status)
    if status >= 500:
        return _result(UNCERTAIN, "http_5xx", source="page", http_status=status)
    if not 200 <= status < 300:
        return _result(UNCERTAIN, "http_unexpected", source="page", http_status=status)

    body = (response.text or "").casefold()
    if any(marker in body for marker in _CLOSURE_MARKERS):
        return _result(EXPIRED, "closure_banner", source="page", http_status=status)
    if any(marker in body for marker in _UNCERTAIN_MARKERS):
        return _result(UNCERTAIN, "login_wall", source="page", http_status=status)
    # The page loads and shows no closure banner: that is the only positive
    # signal a generic posting can give.  A soft-404 that answers 200 keeps the
    # row alive (a stale row is cheaper than a wrongly expired one).
    return _result(ACTIVE, "page_reachable", source="page", http_status=status)


# ---------------------------------------------------------------------------
# Persistent attempt tracking
# ---------------------------------------------------------------------------


class LivenessStore:
    """Small JSON ledger so the 3-attempt / 48-hour rule survives reruns."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> dict[str, dict]:
        if not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def save(self, data: dict[str, dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temporary, self.path)

    @staticmethod
    def key(job: Job) -> str:
        return f"{job.source.value}:{job.job_id}"

    def record(
        self,
        job: Job,
        result: LivenessResult,
        *,
        max_attempts: int = 3,
        retry_after_hours: float = 48.0,
    ) -> dict:
        data = self.load()
        key = self.key(job)
        entry = data.get(key, {})
        now = time.time()
        # ``attempts`` counts every probe; ``uncertain_attempts`` counts the
        # consecutive inconclusive ones and drives the promotion rule.  A
        # definitive answer resets the retry budget so one later hiccup cannot
        # expire a job that has been verified before.
        attempts = int(entry.get("attempts", 0)) + 1
        if result.status == UNCERTAIN:
            uncertain_attempts = int(entry.get("uncertain_attempts", 0)) + 1
            entry.setdefault("first_uncertain_ts", now)
        else:
            uncertain_attempts = 0
            entry.pop("first_uncertain_ts", None)
        entry.update(
            {
                "source": job.source.value,
                "job_id": job.job_id,
                "company": job.company,
                "title": job.title,
                "url": job.canonical_url or job.link,
                "attempts": attempts,
                "uncertain_attempts": uncertain_attempts,
                "last_status": result.status,
                "last_reason": result.reason,
                "last_source": result.source,
                "last_checked": result.checked_at,
            }
        )
        if result.status == UNCERTAIN and uncertain_attempts >= max_attempts:
            first = float(entry.get("first_uncertain_ts", now))
            if now - first >= retry_after_hours * 3600:
                entry["last_status"] = EXPIRED
                entry["last_reason"] = "unreachable_after_attempts"
                entry["promoted_at"] = now_iso()
        data[key] = entry
        self.save(data)
        return entry

    def reset(self, job: Job) -> None:
        data = self.load()
        if data.pop(self.key(job), None) is not None:
            self.save(data)


def store_for(settings: Settings) -> LivenessStore:
    return LivenessStore(Path(settings.root) / "data" / "liveness.json")


def make_client(settings: Settings) -> httpx.Client:
    return httpx.Client(
        timeout=settings.liveness.timeout_seconds,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    )


def check_liveness(
    job: Job,
    *,
    settings: Settings,
    client: httpx.Client | None = None,
    store: LivenessStore | None = None,
    force: bool = False,
) -> LivenessResult:
    """Probe one job and apply the configured retry policy."""
    config = settings.liveness
    if job.deadline_status == "expired":
        job.status = EXPIRED
        job.expired_reason = "deadline_passed"
        return _result(EXPIRED, "deadline_passed", source="deadline")

    ledger = store or store_for(settings)
    if job.status == EXPIRED and job.expired_reason == "unreachable_after_attempts":
        if not force:
            return _result(
                EXPIRED, "unreachable_after_attempts", source="store"
            )
        ledger.reset(job)

    own_client = client is None
    active_client = client or make_client(settings)
    try:
        result = probe_liveness(job, client=active_client)
    finally:
        if own_client:
            active_client.close()

    entry = ledger.record(
        job,
        result,
        max_attempts=max(1, config.max_attempts),
        retry_after_hours=config.retry_after_hours,
    )
    # Only the retry policy may overwrite the probe's verdict.  A real closure
    # banner keeps its own reason; it is never rewritten to "unreachable".
    if (
        entry.get("last_reason") == "unreachable_after_attempts"
        and entry.get("last_status") == EXPIRED
    ):
        result = _result(
            EXPIRED,
            "unreachable_after_attempts",
            source="store",
            http_status=result.http_status,
        )

    job.status = result.status
    job.liveness_checked_at = result.checked_at
    job.liveness_attempts = int(entry.get("attempts", 0))
    job.expired_reason = result.reason if result.status == EXPIRED else None
    return result


def check_batch(
    jobs: list[Job],
    *,
    settings: Settings,
    limit: int | None = None,
    force: bool = False,
    client: httpx.Client | None = None,
) -> dict[str, int]:
    """Check a batch and return status counts."""
    max_checks = settings.liveness.max_checks if limit is None else limit
    considered = jobs[: max(0, int(max_checks))]
    counts = {ACTIVE: 0, EXPIRED: 0, UNCERTAIN: 0}
    for job in considered:
        result = check_liveness(job, settings=settings, client=client, force=force)
        counts[result.status] = counts.get(result.status, 0) + 1
    return counts
