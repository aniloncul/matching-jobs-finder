from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from jobscout.config import Settings
from jobscout.models import Job


def _safe_key(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")
    return cleaned[:120] or "job"


def description_path(settings: Settings, source: str, job_id: str) -> Path:
    directory = Path(settings.cover_letter.description_dir)
    base = f"{_safe_key(source)}-{_safe_key(job_id)}"
    return directory / f"{base}.json"


def load_saved_description(
    settings: Settings,
    source: str,
    job_id: str,
) -> str | None:
    path = description_path(settings, source, job_id)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    text = payload.get("description")
    if not isinstance(text, str) or len(text.strip()) < 20:
        return None
    return text.strip()


def save_job_description(
    settings: Settings,
    job: Job,
    description: str,
) -> Path:
    text = description.strip()
    if len(text) < 20:
        raise ValueError("Job description is too short to save")
    path = description_path(settings, job.source.value, job.job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "source": job.source.value,
        "job_id": job.job_id,
        "company": job.company,
        "title": job.title,
        "link": job.link,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "description": text,
    }
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, path)
    return path


def has_saved_description(settings: Settings, source: str, job_id: str) -> bool:
    path = description_path(settings, source, job_id)
    try:
        return path.is_file() and path.stat().st_size > 40
    except OSError:
        return False
