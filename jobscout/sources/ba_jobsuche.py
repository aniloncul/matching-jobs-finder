"""Bundesagentur fuer Arbeit (BA) official job search API.

Germany's public employment service publishes a documented REST API.  Unlike
the other keyless sources it needs a *free* API key from the Bundesagentur
developer portal, so the source degrades to status ``blocked`` with a clear
message when ``JOBSCOUT_BA_API_KEY`` is not set - never a silent empty run.

Request/response field names follow the public ``jobsuche`` contract; the
mapping below is intentionally defensive (``.get`` everywhere) because the
agency versions the schema independently of us.  Endpoint and key are
configurable, so a schema or host change is a config edit, not a code edit.
"""

from __future__ import annotations

from typing import Any

from jobscout.models import Job, Source
from jobscout.sources.keyless import KeylessFetchError, KeylessSource, clean_text, html_to_text

DEFAULT_BA_BASE_URL = "https://api-arkon.jobs.ec.arbeitsagentur.de"

PAGE_SIZE_FIELD = "anzahl"


class BundesagenturSource(KeylessSource):
    source_name = "ba"
    agent_id = "boards"

    @property
    def label(self) -> str:
        return "Bundesagentur"

    def fetch_jobs(self, cfg: Any) -> list[Job]:
        api_key = clean_text(getattr(cfg, "ba_api_key", ""))
        if not api_key:
            raise KeylessFetchError(
                "Bundesagentur needs a free API key: set JOBSCOUT_BA_API_KEY "
                "(portal: https://developer-arbeitsagentur-openapi.de).",
                blocked=True,
            )
        base_url = (
            clean_text(getattr(cfg, "ba_base_url", "")) or DEFAULT_BA_BASE_URL
        ).rstrip("/")
        query = self.resolve_query(cfg)
        location = clean_text(getattr(cfg, "location", ""))
        limit = max(0, int(getattr(cfg, "max_jobs", 25))) or 25
        body: dict[str, Any] = {
            "suchbegriff": query or None,
            "anzahl": min(100, limit),
            "seite": 1,
            "sortierung": "ERFCV",  # most recent first
            "remotePreferred": False,
            "umkreis": int(getattr(cfg, "ba_radius_km", 50) or 50),
            "nachalo": None,
        }
        if location:
            body["ort"] = location
        data = self._post(f"{base_url}/arkon/jobsuche", api_key, body)
        hits = _extract_hits(data)
        jobs: list[Job] = []
        for item in hits:
            job = self._to_job(item, query)
            if job is not None:
                jobs.append(job)
                if limit and len(jobs) >= limit:
                    break
        return jobs

    def _post(self, url: str, api_key: str, body: dict) -> Any:
        self.control.checkpoint("ba request")
        try:
            response = self.client.post(
                url,
                headers={
                    "Authorization": f"ApiKey {api_key}",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                json=body,
            )
        except Exception as exc:
            raise KeylessFetchError(f"Bundesagentur request failed: {exc}") from exc
        if response.status_code in (401, 403, 429):
            raise KeylessFetchError(
                f"Bundesagentur returned {response.status_code}; check "
                "JOBSCOUT_BA_API_KEY and rate limits",
                blocked=True,
            )
        if response.status_code >= 400:
            raise KeylessFetchError(
                f"Bundesagentur returned {response.status_code}: {response.text[:200]}"
            )
        try:
            return response.json()
        except ValueError as exc:
            raise KeylessFetchError("Bundesagentur returned invalid JSON") from exc

    def _to_job(self, item: dict, query: str) -> Job | None:
        job_id = str(item.get("id") or "")
        headlines = item.get("headlines") or []
        title = clean_text(headlines[0].get("titel")) if headlines else ""
        if not job_id or not title:
            return None
        dienstort = item.get("dienstort") or {}
        location = clean_text(dienstort.get("ort")) or None
        link = clean_text(item.get("url")) or clean_text(
            item.get("kriereportalUrl")
        ) or f"https://www.arbeitsagentur.de/vor-ort/iba-jobsuche/stellenangebote?resultId={job_id}"
        contact = item.get("kontakt") or {}
        company = clean_text(contact.get("name")) or "Bundesagentur-Vermittlung"
        published = clean_text(item.get("veroeffentlichungsdatum"))
        description = html_to_text(item.get("stellenangebotText")) or None
        return Job(
            company=company,
            title=title,
            link=link,
            source=Source.ba,
            job_id=f"ba-{job_id}",
            location=location,
            posted_date=published[:10] or None,
            description=description,
            canonical_url=link,
            requisition_id=job_id,
            search_query=query,
        )


def _extract_hits(data: Any) -> list[dict]:
    """The payload nests results under ".jobsuche"; tolerate both shapes."""
    if isinstance(data, dict):
        inner = data.get(".jobsuche") or data
        hits = inner.get("treffer") if isinstance(inner, dict) else None
        if isinstance(hits, list):
            return [h for h in hits if isinstance(h, dict)]
    return []


__all__ = ["BundesagenturSource", "DEFAULT_BA_BASE_URL"]
