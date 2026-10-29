from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from jobscout.dedupe import dedupe_jobs, fingerprint_values
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

    def __init__(self, jobs_csv: Path, runs_csv: Path) -> None:
        self.jobs_csv = jobs_csv
        self.runs_csv = runs_csv
        self._keys: set[tuple[str, str]] = set()
        self._fingerprints: set[tuple[str, str, str]] = set()

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

    def existing(self) -> tuple[set[tuple[str, str]], set[tuple[str, str, str]]]:
        self._keys = set()
        self._fingerprints = set()
        if not self.jobs_csv.exists():
            return self._keys, self._fingerprints

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
        return self._keys, self._fingerprints

    def write(self, jobs: list[Job], cross_source: bool = False) -> int:
        unique, _skipped = dedupe_jobs(
            jobs,
            existing_keys=self._keys,
            existing_fingerprints=self._fingerprints,
            cross_source=cross_source,
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
    )
