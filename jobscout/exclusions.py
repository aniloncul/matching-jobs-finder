from __future__ import annotations

import json
import os
from pathlib import Path

from jobscout.config import Settings
from jobscout.dedupe import fingerprint_values
from jobscout.models import now_iso


def _exclusions_file(settings: Settings | None = None) -> Path:
    if settings is not None and hasattr(settings, "root"):
        return settings.root / "data" / "excluded_jobs.json"
    from jobscout.config import ROOT
    return ROOT / "data" / "excluded_jobs.json"


def load_excluded_jobs(settings: Settings | None = None) -> list[dict]:
    path = _exclusions_file(settings)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return []


def add_excluded_job(
    settings: Settings | None = None,
    *,
    source: str = "",
    job_id: str = "",
    company: str = "",
    title: str = "",
    location: str = "",
    link: str = "",
) -> dict:
    path = _exclusions_file(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    records = load_excluded_jobs(settings)

    clean_source = source.strip().lower()
    clean_id = job_id.strip()

    # Avoid duplicate exclusion entries
    for record in records:
        if (
            record.get("source", "").lower() == clean_source
            and record.get("job_id", "") == clean_id
            and clean_id
        ):
            return record

    new_record = {
        "source": clean_source,
        "job_id": clean_id,
        "company": company.strip(),
        "title": title.strip(),
        "location": location.strip(),
        "link": link.strip(),
        "deleted_at": now_iso(),
    }
    records.append(new_record)

    temp_path = path.with_name(f".{path.name}.tmp")
    temp_path.write_text(
        json.dumps(records, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temp_path, path)
    return new_record


def get_excluded_keys_and_fingerprints(
    settings: Settings | None = None,
) -> tuple[set[tuple[str, str]], set[tuple[str, str, str]]]:
    records = load_excluded_jobs(settings)
    keys: set[tuple[str, str]] = set()
    fingerprints: set[tuple[str, str, str]] = set()

    for item in records:
        src = (item.get("source") or "").strip().lower()
        jid = (item.get("job_id") or "").strip()
        if src and jid:
            keys.add((src, jid))

        comp = (item.get("company") or "").strip()
        title = (item.get("title") or "").strip()
        loc = (item.get("location") or "").strip()
        if comp and title:
            fp = fingerprint_values(comp, title, loc)
            if fp != ("", "", ""):
                fingerprints.add(fp)

    return keys, fingerprints


def delete_job_files(settings: Settings, source: str, job_id: str) -> None:
    from jobscout.cover_letter.description_store import description_path
    from jobscout.cover_letter.generator import letter_paths

    try:
        desc = description_path(settings, source, job_id)
        if desc.exists():
            desc.unlink()
    except Exception:
        pass

    try:
        paths = letter_paths(settings, source, job_id)
        for p in paths.values():
            if p.exists():
                p.unlink()
    except Exception:
        pass
