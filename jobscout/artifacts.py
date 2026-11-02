"""Artifact archive + rebuildable search index (roadmap item 19).

The Google Sheet / jobs.csv stays the user-facing flow untouched.  Next to it
every run archives the things worth keeping forever under
``data/artifacts/{source}/{job_id}/``:

    job.json      - the Job model, exactly as captured
    jd.md         - the job description, verbatim (never rewritten)
    letter.json   - cover letter metadata, copied when a letter is generated
    documents.json- links to the original tex/pdf artifacts, never copies

The SQLite file (``index.sqlite``) is *only* an index: delete it and
``jobscout index rebuild`` recreates every row from the artifact folders.
That keeps the sheet, the archive and the index independently survivable.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jobscout.models import Job

logger = logging.getLogger("jobscout.artifacts")

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    source TEXT NOT NULL,
    job_id TEXT NOT NULL,
    company TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    location TEXT,
    link TEXT,
    status TEXT,
    rank_score INTEGER,
    scraped_at TEXT,
    archived_at TEXT,
    dir TEXT,
    description TEXT,
    PRIMARY KEY (source, job_id)
);
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    job_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (source, job_id, kind, path)
);
CREATE VIRTUAL TABLE IF NOT EXISTS jobs_fts USING fts5(company, title, description);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def safe_name(value: str) -> str:
    cleaned = _SAFE_NAME_RE.sub("_", (value or "").strip()).strip("._")
    return cleaned[:120] or "unknown"


class ArtifactStore:
    def __init__(self, root: Path, db_path: Path | None = None) -> None:
        self.root = Path(root)
        self.db_path = Path(db_path) if db_path else self.root / "index.sqlite"
        self._db_lock = threading.Lock()

    # -- layout ---------------------------------------------------------------

    def job_dir(self, source: str, job_id: str) -> Path:
        return self.root / safe_name(source) / safe_name(job_id)

    # -- writing ----------------------------------------------------------------

    def archive_job(self, job: Job) -> Path | None:
        """Write job.json (+ verbatim jd.md when a description is attached)."""
        try:
            directory = self.job_dir(job.source.value, job.job_id)
            directory.mkdir(parents=True, exist_ok=True)
            payload = job.model_dump(mode="json")
            _write_json(directory / "job.json", payload)
            if job.description:
                _write_text(directory / "jd.md", job.description)
            self._index_job(job, directory)
            return directory
        except Exception:  # archiving must never sink a run
            logger.exception("artifact archive_job failed")
            return None

    def archive_jobs(self, jobs: list[Job]) -> int:
        count = 0
        for job in jobs:
            if self.archive_job(job) is not None:
                count += 1
        return count

    def archive_description(self, source: str, job_id: str, text: str) -> Path | None:
        """Persist a freshly fetched JD verbatim, keeping any job.json."""
        try:
            directory = self.job_dir(source, job_id)
            directory.mkdir(parents=True, exist_ok=True)
            _write_text(directory / "jd.md", text)
            job = _read_job_json(directory)
            if job is not None:
                self._index_job(job, directory)
            return directory
        except Exception:
            logger.exception("artifact archive_description failed")
            return None

    def save_letter(self, source: str, job_id: str, metadata: dict) -> Path | None:
        try:
            directory = self.job_dir(source, job_id)
            directory.mkdir(parents=True, exist_ok=True)
            _write_json(directory / "letter.json", metadata)
            self.link_document(source, job_id, "letter-json", directory / "letter.json")
            return directory
        except Exception:
            logger.exception("artifact save_letter failed")
            return None

    def link_document(self, source: str, job_id: str, kind: str, path: Path | str) -> None:
        """Record (never copy) a generated file next to the job archive."""
        try:
            directory = self.job_dir(source, job_id)
            directory.mkdir(parents=True, exist_ok=True)
            entry = {
                "kind": kind,
                "path": str(Path(path)),
                "created_at": now_iso(),
            }
            documents_path = directory / "documents.json"
            entries: list[dict] = []
            if documents_path.exists():
                try:
                    loaded = json.loads(documents_path.read_text(encoding="utf-8"))
                    if isinstance(loaded, list):
                        entries = [item for item in loaded if isinstance(item, dict)]
                except ValueError:
                    entries = []
            if not any(
                item.get("kind") == kind and item.get("path") == entry["path"]
                for item in entries
            ):
                entries.append(entry)
            _write_json(documents_path, entries)
            with self._connect() as conn, self._db_lock:
                conn.execute(
                    "INSERT OR IGNORE INTO documents (source, job_id, kind, path, created_at)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (source, job_id, kind, entry["path"], entry["created_at"]),
                )
        except Exception:
            logger.exception("artifact link_document failed")

    # -- index --------------------------------------------------------------------

    @contextmanager
    def _connect(self):
        """Open, commit-on-exit, always close."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.executescript(_SCHEMA)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _index_job(self, job: Job, directory: Path) -> None:
        row = {
            "source": job.source.value,
            "job_id": job.job_id,
            "company": job.company,
            "title": job.title,
            "location": job.location,
            "link": job.link,
            "status": job.status,
            "rank_score": job.rank_score,
            "scraped_at": job.scraped_at,
            "archived_at": now_iso(),
            "dir": str(directory),
            "description": job.description or "",
        }
        with self._connect() as conn, self._db_lock:
            conn.execute(
                "INSERT OR REPLACE INTO jobs (source, job_id, company, title, location,"
                " link, status, rank_score, scraped_at, archived_at, dir, description)"
                " VALUES (:source, :job_id, :company, :title, :location, :link, :status,"
                " :rank_score, :scraped_at, :archived_at, :dir, :description)",
                row,
            )
            self._index_fts(conn, row)

    def _index_fts(self, conn: sqlite3.Connection, row: dict) -> None:
        existing = conn.execute(
            "SELECT rowid FROM jobs WHERE source = ? AND job_id = ?",
            (row["source"], row["job_id"]),
        ).fetchone()
        if existing is None:
            return
        rowid = existing["rowid"]
        conn.execute("DELETE FROM jobs_fts WHERE rowid = ?", (rowid,))
        conn.execute(
            "INSERT INTO jobs_fts (rowid, company, title, description) VALUES (?, ?, ?, ?)",
            (rowid, row["company"], row["title"], row["description"]),
        )

    def rebuild_index(self) -> int:
        """Drop the index and rebuild it from the artifact folders alone."""
        with self._db_lock:
            if self.db_path.exists():
                self.db_path.unlink()
        count = 0
        if not self.root.exists():
            return count
        for job_file in sorted(self.root.glob("*/*/job.json")):
            job = _read_job_json(job_file.parent)
            if job is None:
                continue
            with self._connect() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO jobs (source, job_id, company, title, location,"
                    " link, status, rank_score, scraped_at, archived_at, dir, description)"
                    " VALUES (:source, :job_id, :company, :title, :location, :link, :status,"
                    " :rank_score, :scraped_at, :archived_at, :dir, :description)",
                    {
                        "source": job.source.value,
                        "job_id": job.job_id,
                        "company": job.company,
                        "title": job.title,
                        "location": job.location,
                        "link": job.link,
                        "status": job.status,
                        "rank_score": job.rank_score,
                        "scraped_at": job.scraped_at,
                        "archived_at": now_iso(),
                        "dir": str(job_file.parent),
                        "description": job.description or "",
                    },
                )
                rowid = conn.execute(
                    "SELECT rowid FROM jobs WHERE source = ? AND job_id = ?",
                    (job.source.value, job.job_id),
                ).fetchone()["rowid"]
                conn.execute(
                    "INSERT INTO jobs_fts (rowid, company, title, description)"
                    " VALUES (?, ?, ?, ?)",
                    (rowid, job.company, job.title, job.description or ""),
                )
            count += 1
        for documents_path in sorted(self.root.glob("*/*/documents.json")):
            self._reindex_documents(documents_path)
        return count

    def _reindex_documents(self, documents_path: Path) -> None:
        try:
            entries = json.loads(documents_path.read_text(encoding="utf-8"))
        except ValueError:
            return
        if not isinstance(entries, list):
            return
        source = documents_path.parent.parent.name
        job_id = documents_path.parent.name
        with self._connect() as conn:
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                conn.execute(
                    "INSERT OR IGNORE INTO documents (source, job_id, kind, path, created_at)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (
                        source,
                        job_id,
                        str(entry.get("kind") or ""),
                        str(entry.get("path") or ""),
                        str(entry.get("created_at") or ""),
                    ),
                )

    # -- reading -------------------------------------------------------------------

    def search(self, query: str, source: str | None = None, limit: int = 25) -> list[dict]:
        query = (query or "").strip()
        if not query:
            return []
        limit = max(1, min(200, int(limit)))
        try:
            with self._connect() as conn:
                rows = self._search_rows(conn, query, source, limit)
        except sqlite3.OperationalError:
            # No FTS5 in this SQLite build: LIKE keeps search usable.
            with self._connect() as conn:
                rows = self._like_rows(conn, query, source, limit)
        return [dict(row) for row in rows]

    def _search_rows(
        self, conn: sqlite3.Connection, query: str, source: str | None, limit: int
    ) -> list[sqlite3.Row]:
        match = " ".join(f'"{term}"' for term in query.split()) or f'"{query}"'
        sql = (
            "SELECT j.source, j.job_id, j.company, j.title, j.location, j.link,"
            " j.rank_score, j.archived_at, j.dir"
            " FROM jobs_fts f JOIN jobs j ON j.rowid = f.rowid"
            " WHERE jobs_fts MATCH ?"
        )
        params: list[Any] = [match]
        if source:
            sql += " AND j.source = ?"
            params.append(source)
        sql += " ORDER BY rank LIMIT ?"
        params.append(limit)
        return conn.execute(sql, params).fetchall()

    def _like_rows(
        self, conn: sqlite3.Connection, query: str, source: str | None, limit: int
    ) -> list[sqlite3.Row]:
        like = f"%{query}%"
        sql = (
            "SELECT source, job_id, company, title, location, link, rank_score,"
            " archived_at, dir FROM jobs"
            " WHERE company LIKE ? OR title LIKE ? OR description LIKE ?"
        )
        params: list[Any] = [like, like, like]
        if source:
            sql += " AND source = ?"
            params.append(source)
        sql += " ORDER BY archived_at DESC LIMIT ?"
        params.append(limit)
        return conn.execute(sql, params).fetchall()

    def documents(self, source: str, job_id: str) -> list[dict]:
        path = self.job_dir(source, job_id) / "documents.json"
        if not path.exists():
            return []
        try:
            entries = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            return []
        return [entry for entry in entries if isinstance(entry, dict)] if isinstance(entries, list) else []


# -- module helpers -------------------------------------------------------------


def _write_json(path: Path, payload: Any) -> None:
    _write_text(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def _read_job_json(directory: Path) -> Job | None:
    path = directory / "job.json"
    if not path.exists():
        return None
    try:
        return Job.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        logger.warning("unreadable job.json in %s", directory)
        return None


def store_for(settings) -> ArtifactStore:  # noqa: ANN001 - Settings, avoiding cycle
    """The store for this installation; disabled by JOBSCOUT_ARTIFACTS=0."""
    if not getattr(getattr(settings, "artifacts", None), "enabled", True):
        return DisabledStore(settings.root / "data" / "artifacts-disabled")
    return ArtifactStore(settings.root / "data" / "artifacts")


class DisabledStore(ArtifactStore):
    """No-op store used when archiving is switched off."""

    def archive_job(self, job: Job) -> Path | None:  # noqa: ARG002
        return None

    def archive_jobs(self, jobs: list[Job]) -> int:  # noqa: ARG002
        return 0

    def archive_description(self, source: str, job_id: str, text: str) -> Path | None:  # noqa: ARG002
        return None

    def save_letter(self, source: str, job_id: str, metadata: dict) -> Path | None:  # noqa: ARG002
        return None

    def link_document(self, source: str, job_id: str, kind: str, path) -> None:  # noqa: ANN001, ARG002
        return None


__all__ = ["ArtifactStore", "DisabledStore", "store_for"]
