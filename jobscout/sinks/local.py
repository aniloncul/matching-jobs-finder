from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from jobscout.config import Settings
from jobscout.dedupe import (
    ExistingJobs,
    canonical_url,
    dedupe_jobs,
    fingerprint_values,
    role_key_values,
)
from jobscout.exclusions import (
    add_excluded_job,
    get_excluded_keys_and_fingerprints,
    get_excluded_urls_and_roles,
)
from jobscout.models import Job, now_iso
from jobscout.sinks.base import Sink

JOB_HEADERS = [
    "Company Name",
    "Job Title",
    "Job Link",
    "Post Created Date",
    "Source",
    "Posted Text",
    "Description",
    "Estimated",
    "Job ID",
    "Location",
    "Search Query",
    "Scraped At",
    "Applied",
    "Canonical URL",
    "Requisition ID",
    "Status",
    "Deadline",
    "Deadline Status",
    "Rank Score",
    "Rank Verdict",
]

RUN_HEADERS = [
    "Run ID",
    "Sources",
    "Mode",
    "Found",
    "New",
    "Skipped",
    "Errors",
    "Status",
    "Error",
    "Started",
    "Finished",
]


class LocalSink(Sink):
    kind = "local"

    def __init__(
        self,
        jobs_csv: Path,
        runs_csv: Path,
        settings: Settings | None = None,
    ) -> None:
        self.jobs_csv = jobs_csv
        self.runs_csv = runs_csv
        self.settings = settings
        self._keys: set[tuple[str, str]] = set()
        self._fingerprints: set[tuple[str, str, str]] = set()
        self._urls: set[str] = set()
        self._roles: set[tuple[str, str]] = set()

    def prepare(self) -> None:
        self.jobs_csv.parent.mkdir(parents=True, exist_ok=True)
        self.runs_csv.parent.mkdir(parents=True, exist_ok=True)
        if not self.jobs_csv.exists() or self.jobs_csv.stat().st_size == 0:
            with self.jobs_csv.open("w", newline="", encoding="utf-8") as handle:
                csv.DictWriter(handle, fieldnames=JOB_HEADERS).writeheader()
        else:
            self._migrate_headers()
        if not self.runs_csv.exists() or self.runs_csv.stat().st_size == 0:
            with self.runs_csv.open("w", newline="", encoding="utf-8") as handle:
                csv.DictWriter(handle, fieldnames=RUN_HEADERS).writeheader()

    def existing(self) -> ExistingJobs:
        self._keys = set()
        self._fingerprints = set()
        self._urls = set()
        self._roles = set()
        if self.jobs_csv.exists():
            with self.jobs_csv.open(newline="", encoding="utf-8") as handle:
                for row in csv.DictReader(handle):
                    source = (row.get("Source") or "").strip()
                    job_id = (row.get("Job ID") or "").strip()
                    if source and job_id:
                        self._keys.add((source, job_id))
                    self._fingerprints.add(
                        fingerprint_values(
                            row.get("Company Name"),
                            row.get("Job Title"),
                            row.get("Location"),
                        )
                    )
                    url = canonical_url(row.get("Canonical URL") or row.get("Job Link"))
                    if url:
                        self._urls.add(url)
                    company = (row.get("Company Name") or "").strip()
                    title = (row.get("Job Title") or "").strip()
                    if company and title:
                        self._roles.add(role_key_values(company, title))

        # Include excluded / deleted jobs so they are never re-scraped
        ex_keys, ex_fps = get_excluded_keys_and_fingerprints(self.settings)
        self._keys.update(ex_keys)
        self._fingerprints.update(ex_fps)
        ex_urls, ex_roles = get_excluded_urls_and_roles(self.settings)
        self._urls.update(ex_urls)
        self._roles.update(ex_roles)
        return ExistingJobs(
            keys=self._keys,
            fingerprints=self._fingerprints,
            urls=self._urls,
            roles=self._roles,
        )

    def delete_job(self, source: str, job_id: str) -> bool:
        deleted_data = None
        clean_src = source.strip().lower()
        clean_id = job_id.strip()

        if self.jobs_csv.exists():
            with self.jobs_csv.open(newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                headers = reader.fieldnames or JOB_HEADERS
                rows = list(reader)

            remaining = []
            for row in rows:
                row_src = (row.get("Source") or "").strip().lower()
                row_id = (row.get("Job ID") or "").strip()
                if row_src == clean_src and row_id == clean_id:
                    deleted_data = {
                        "source": row.get("Source", source),
                        "job_id": row_id,
                        "company": row.get("Company Name", ""),
                        "title": row.get("Job Title", ""),
                        "location": row.get("Location", ""),
                        "link": row.get("Job Link", ""),
                    }
                else:
                    remaining.append(row)

            temporary = self.jobs_csv.with_name(f".{self.jobs_csv.name}.tmp")
            with temporary.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=headers, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(remaining)
            temporary.replace(self.jobs_csv)

        if deleted_data:
            add_excluded_job(self.settings, **deleted_data)
        else:
            add_excluded_job(self.settings, source=source, job_id=job_id)

        self._keys.add((clean_src, clean_id))
        if deleted_data and deleted_data.get("company") and deleted_data.get("title"):
            self._fingerprints.add(
                fingerprint_values(
                    deleted_data["company"],
                    deleted_data["title"],
                    deleted_data.get("location"),
                )
            )
        return True

    def write(self, jobs: list[Job], cross_source: bool = False) -> int:
        unique, _skipped = dedupe_jobs(
            jobs,
            existing_keys=self._keys,
            existing_fingerprints=self._fingerprints,
            cross_source=cross_source,
            existing_urls=self._urls,
            existing_roles=self._roles,
        )
        if not unique:
            return 0

        with self.jobs_csv.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=JOB_HEADERS)
            for job in unique:
                writer.writerow(job.row())
                self._keys.add(job.key())
                self._fingerprints.add(
                    fingerprint_values(job.company, job.title, job.location)
                )
                if job.canonical_url:
                    self._urls.add(job.canonical_url)
                self._roles.add(role_key_values(job.company, job.title))
        return len(unique)

    def _migrate_headers(self) -> None:
        with self.jobs_csv.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            headers = reader.fieldnames or []
            rows = list(reader)
        if all(header in headers for header in JOB_HEADERS):
            return
        merged = list(headers) + [
            header for header in JOB_HEADERS if header not in headers
        ]
        temporary = self.jobs_csv.with_name(f".{self.jobs_csv.name}.tmp")
        with temporary.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=merged, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        temporary.replace(self.jobs_csv)

    def list_jobs(self, limit: int = 100) -> list[Job]:
        if not self.jobs_csv.exists():
            return []
        jobs: list[Job] = []
        with self.jobs_csv.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                try:
                    jobs.append(_job_from_row(row))
                except Exception:
                    continue
        return list(reversed(jobs))[: max(0, limit)]

    def save_description(self, source: str, job_id: str, description: str) -> bool:
        if not self.jobs_csv.exists():
            return False
        self._migrate_headers()
        with self.jobs_csv.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            headers = reader.fieldnames or JOB_HEADERS
            rows = list(reader)
        changed = False
        for row in rows:
            if (
                (row.get("Source") or "").strip().lower() == source.lower()
                and (row.get("Job ID") or "").strip() == job_id
            ):
                row["Description"] = description
                changed = True
        if not changed:
            return False
        temporary = self.jobs_csv.with_name(f".{self.jobs_csv.name}.tmp")
        with temporary.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=headers, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        temporary.replace(self.jobs_csv)
        return True

    def update_job_applied(self, source: str, job_id: str, applied: bool) -> bool:
        if not self.jobs_csv.exists():
            return False
        self._migrate_headers()
        with self.jobs_csv.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            headers = reader.fieldnames or JOB_HEADERS
            rows = list(reader)
        changed = False
        for row in rows:
            if (
                (row.get("Source") or "").strip().lower() == source.lower()
                and (row.get("Job ID") or "").strip() == job_id
            ):
                row["Applied"] = "Applied" if applied else "Will Apply"
                changed = True
        if not changed:
            return False
        temporary = self.jobs_csv.with_name(f".{self.jobs_csv.name}.tmp")
        with temporary.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=headers, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        temporary.replace(self.jobs_csv)
        return True

    def update_job_fields(
        self,
        source: str,
        job_id: str,
        fields: dict[str, str],
    ) -> bool:
        """Overwrite selected CSV columns for one job row."""
        if not self.jobs_csv.exists() or not fields:
            return False
        self._migrate_headers()
        with self.jobs_csv.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            headers = reader.fieldnames or JOB_HEADERS
            rows = list(reader)
        changed = False
        for row in rows:
            if (
                (row.get("Source") or "").strip().lower() == source.lower()
                and (row.get("Job ID") or "").strip() == job_id
            ):
                for key, value in fields.items():
                    if key in headers:
                        row[key] = value
                        changed = True
        if not changed:
            return False
        temporary = self.jobs_csv.with_name(f".{self.jobs_csv.name}.tmp")
        with temporary.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=headers, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        temporary.replace(self.jobs_csv)
        return True

    def record_run(self, summary: dict) -> None:
        row = {
            "Run ID": summary.get("run_id", ""),
            "Sources": ", ".join(summary.get("sources", [])),
            "Mode": summary.get("mode", ""),
            "Found": summary.get("found", 0),
            "New": summary.get("new", 0),
            "Skipped": summary.get("skipped", 0),
            "Errors": summary.get("errors", 0),
            "Status": summary.get("status", ""),
            "Error": summary.get("error", ""),
            "Started": _format_ts(summary.get("started_at")),
            "Finished": _format_ts(summary.get("finished_at")),
        }
        with self.runs_csv.open("a", newline="", encoding="utf-8") as handle:
            csv.DictWriter(handle, fieldnames=RUN_HEADERS).writerow(row)


def _format_ts(value: float | None) -> str:
    if not value:
        return ""
    return datetime.fromtimestamp(value).isoformat(timespec="seconds")


def _job_from_row(row: dict[str, str]) -> Job:
    source = (row.get("Source") or "").strip().lower()
    applied_val = (row.get("Applied") or "").strip().lower()
    applied = applied_val in ("applied", "yes", "true", "y", "1")
    rank_raw = (row.get("Rank Score") or "").strip()
    try:
        rank_score = int(rank_raw) if rank_raw else None
    except ValueError:
        rank_score = None
    return Job(
        company=(row.get("Company Name") or "").strip(),
        title=(row.get("Job Title") or "").strip(),
        link=(row.get("Job Link") or "").strip(),
        posted_date=(row.get("Post Created Date") or "").strip() or None,
        source=source,
        posted_text=(row.get("Posted Text") or "").strip() or None,
        description=(row.get("Description") or "").strip() or None,
        estimated=(row.get("Estimated") or "").strip().lower() == "yes",
        job_id=(row.get("Job ID") or "").strip(),
        location=(row.get("Location") or "").strip() or None,
        search_query=(row.get("Search Query") or "").strip(),
        scraped_at=(row.get("Scraped At") or "").strip() or now_iso(),
        applied=applied,
        canonical_url=(row.get("Canonical URL") or "").strip() or None,
        requisition_id=(row.get("Requisition ID") or "").strip() or None,
        status=(row.get("Status") or "").strip() or "unknown",
        deadline=(row.get("Deadline") or "").strip() or None,
        deadline_status=(row.get("Deadline Status") or "").strip() or "unknown",
        rank_score=rank_score,
        rank_verdict=(row.get("Rank Verdict") or "").strip() or None,
    )
