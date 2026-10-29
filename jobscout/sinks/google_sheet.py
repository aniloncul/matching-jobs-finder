from __future__ import annotations

from datetime import datetime
from pathlib import Path

import gspread
from gspread.exceptions import WorksheetNotFound

from jobscout.config import Settings
from jobscout.dedupe import dedupe_jobs, fingerprint_values
from jobscout.exclusions import add_excluded_job, get_excluded_keys_and_fingerprints
from jobscout.models import Job, now_iso
from jobscout.sinks.base import Sink, SinkError
from jobscout.sinks.local import JOB_HEADERS, RUN_HEADERS

EXCLUDED_HEADERS = [
    "Company Name",
    "Job Title",
    "Location",
    "Source",
    "Job ID",
    "Job Link",
    "Deleted At",
]


class GoogleSheetSink(Sink):
    kind = "google"

    def __init__(
        self,
        sheet_id: str,
        service_account_json: Path,
        settings: Settings | None = None,
    ) -> None:
        self.sheet_id = sheet_id
        self.service_account_json = service_account_json
        self.settings = settings
        self.sheet_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit"
        self._jobs = None
        self._runs = None
        self._keys: set[tuple[str, str]] = set()
        self._fingerprints: set[tuple[str, str, str]] = set()

    def _client(self):
        try:
            return gspread.service_account(filename=str(self.service_account_json))
        except Exception as exc:
            raise SinkError(f"Google authentication failed: {exc}") from exc

    def _worksheet(self, name: str, headers: list[str]):
        try:
            sheet = self._client().open_by_key(self.sheet_id)
        except Exception as exc:
            raise SinkError(f"Cannot open Google Sheet: {exc}") from exc

        try:
            worksheet = sheet.worksheet(name)
        except WorksheetNotFound:
            worksheet = sheet.add_worksheet(title=name, rows=1000, cols=len(headers))

        if not worksheet.row_values(1):
            worksheet.append_row(headers, value_input_option="USER_ENTERED")
        return worksheet

    def prepare(self) -> None:
        try:
            self._jobs = self._worksheet("Jobs", JOB_HEADERS)
            self._ensure_headers(self._jobs, JOB_HEADERS)
            self._runs = self._worksheet("Runs", RUN_HEADERS)
        except SinkError:
            raise
        except Exception as exc:
            raise SinkError(f"Cannot prepare Google Sheet: {exc}") from exc

    def existing(self) -> tuple[set[tuple[str, str]], set[tuple[str, str, str]]]:
        if self._jobs is None:
            self.prepare()
        assert self._jobs is not None
        rows = self._jobs.get_all_values()
        self._keys = set()
        self._fingerprints = set()
        if len(rows) >= 2:
            headers = rows[0]
            index = {header: i for i, header in enumerate(headers)}
            for row in rows[1:]:
                def value(name: str) -> str:
                    i = index.get(name)
                    return row[i].strip() if i is not None and i < len(row) else ""

                source = value("Source")
                job_id = value("Job ID")
                if source and job_id:
                    self._keys.add((source, job_id))
                self._fingerprints.add(
                    fingerprint_values(
                        value("Company Name"),
                        value("Job Title"),
                        value("Location"),
                    )
                )

        # Also load from Deleted Jobs worksheet if present
        try:
            sheet = self._client().open_by_key(self.sheet_id)
            try:
                ex_sheet = sheet.worksheet("Deleted Jobs")
                ex_rows = ex_sheet.get_all_values()
                if len(ex_rows) >= 2:
                    ex_idx = {h: i for i, h in enumerate(ex_rows[0])}
                    for row in ex_rows[1:]:
                        def ex_val(name: str) -> str:
                            i = ex_idx.get(name)
                            return row[i].strip() if i is not None and i < len(row) else ""
                        s = ex_val("Source").lower()
                        j = ex_val("Job ID")
                        if s and j:
                            self._keys.add((s, j))
                        c = ex_val("Company Name")
                        t = ex_val("Job Title")
                        l = ex_val("Location")
                        if c and t:
                            self._fingerprints.add(fingerprint_values(c, t, l))
            except WorksheetNotFound:
                pass
        except Exception:
            pass

        # Also merge persistent local exclusions
        ex_keys, ex_fps = get_excluded_keys_and_fingerprints(self.settings)
        self._keys.update(ex_keys)
        self._fingerprints.update(ex_fps)
        return self._keys, self._fingerprints

    def delete_job(self, source: str, job_id: str) -> bool:
        if self._jobs is None:
            self.prepare()
        assert self._jobs is not None
        rows = self._jobs.get_all_values()
        clean_src = source.strip().lower()
        clean_id = job_id.strip()

        if len(rows) < 2:
            add_excluded_job(self.settings, source=source, job_id=job_id)
            self._keys.add((clean_src, clean_id))
            return True

        headers = rows[0]
        index = {header: i for i, header in enumerate(headers)}
        source_col = index.get("Source")
        id_col = index.get("Job ID")

        row_to_delete = None
        deleted_data = None
        for row_number, row in enumerate(rows[1:], start=2):
            row_src = row[source_col].strip().lower() if source_col is not None and source_col < len(row) else ""
            row_id = row[id_col].strip() if id_col is not None and id_col < len(row) else ""
            if row_src == clean_src and row_id == clean_id:
                row_to_delete = row_number
                def value(name: str) -> str:
                    i = index.get(name)
                    return row[i].strip() if i is not None and i < len(row) else ""
                deleted_data = {
                    "source": row_src or source,
                    "job_id": row_id or job_id,
                    "company": value("Company Name"),
                    "title": value("Job Title"),
                    "location": value("Location"),
                    "link": value("Job Link"),
                }
                break

        if row_to_delete is not None:
            self._jobs.delete_rows(row_to_delete)
            try:
                ex_sheet = self._worksheet("Deleted Jobs", EXCLUDED_HEADERS)
                row_vals = [
                    deleted_data["company"],
                    deleted_data["title"],
                    deleted_data["location"],
                    deleted_data["source"],
                    deleted_data["job_id"],
                    deleted_data["link"],
                    now_iso(),
                ]
                ex_sheet.append_row(row_vals, value_input_option="USER_ENTERED")
            except Exception:
                pass

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

    @staticmethod
    def _ensure_headers(worksheet, headers: list[str]) -> None:
        existing = worksheet.row_values(1)
        if not existing:
            worksheet.append_row(headers, value_input_option="USER_ENTERED")
            return
        missing = [header for header in headers if header not in existing]
        if not missing:
            return
        start = len(existing) + 1
        worksheet.update(
            range_name=f"{_column_letter(start)}1:{_column_letter(start + len(missing) - 1)}1",
            values=[missing],
            value_input_option="USER_ENTERED",
        )

    def write(self, jobs: list[Job], cross_source: bool = False) -> int:
        if self._jobs is None:
            self.prepare()
        assert self._jobs is not None
        unique, _skipped = dedupe_jobs(
            jobs,
            existing_keys=self._keys,
            existing_fingerprints=self._fingerprints,
            cross_source=cross_source,
        )
        if not unique:
            return 0

        headers = self._jobs.row_values(1) or JOB_HEADERS
        rows = []
        for job in unique:
            data = job.row()
            rows.append([data.get(header, "") for header in headers])
            self._keys.add(job.key())
            self._fingerprints.add(
                fingerprint_values(job.company, job.title, job.location)
            )

        self._jobs.append_rows(rows, value_input_option="USER_ENTERED")
        return len(unique)

    def list_jobs(self, limit: int = 100) -> list[Job]:
        if self._jobs is None:
            self.prepare()
        assert self._jobs is not None
        rows = self._jobs.get_all_values()
        if len(rows) < 2:
            return []
        headers = rows[0]
        index = {header: i for i, header in enumerate(headers)}
        jobs: list[Job] = []
        for row in rows[1:]:
            def value(name: str) -> str:
                i = index.get(name)
                return row[i].strip() if i is not None and i < len(row) else ""

            try:
                jobs.append(
                    Job(
                        company=value("Company Name"),
                        title=value("Job Title"),
                        link=value("Job Link"),
                        posted_date=value("Post Created Date") or None,
                        source=value("Source").lower(),
                        posted_text=value("Posted Text") or None,
                        description=value("Description") or None,
                        estimated=value("Estimated").lower() == "yes",
                        job_id=value("Job ID"),
                        location=value("Location") or None,
                        search_query=value("Search Query"),
                        scraped_at=value("Scraped At") or now_iso(),
                    )
                )
            except Exception:
                continue
        return list(reversed(jobs))[: max(0, limit)]

    def save_description(self, source: str, job_id: str, description: str) -> bool:
        if self._jobs is None:
            self.prepare()
        assert self._jobs is not None
        self._ensure_headers(self._jobs, JOB_HEADERS)
        rows = self._jobs.get_all_values()
        if len(rows) < 2:
            return False
        headers = rows[0]
        index = {header: i for i, header in enumerate(headers)}
        description_col = index.get("Description")
        source_col = index.get("Source")
        id_col = index.get("Job ID")
        if description_col is None or source_col is None or id_col is None:
            return False
        for row_number, row in enumerate(rows[1:], start=2):
            def value(column: int | None) -> str:
                return row[column].strip() if column is not None and column < len(row) else ""

            if value(source_col).lower() == source.lower() and value(id_col) == job_id:
                self._jobs.update_cell(row_number, description_col + 1, description)
                return True
        return False

    def record_run(self, summary: dict) -> None:
        if self._runs is None:
            self.prepare()
        assert self._runs is not None
        headers = self._runs.row_values(1) or RUN_HEADERS
        data = {
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
        self._runs.append_row(
            [data.get(header, "") for header in headers],
            value_input_option="USER_ENTERED",
        )


def _column_letter(number: int) -> str:
    letters = ""
    while number > 0:
        number, remainder = divmod(number - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _format_ts(value: float | None) -> str:
    if not value:
        return ""
    return datetime.fromtimestamp(value).isoformat(timespec="seconds")
