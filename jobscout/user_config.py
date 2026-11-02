"""User-editable settings that back the in-app Setup view.

``config/search.yaml`` and environment variables stay authoritative for
automation; this overlay is the friendly layer the UI writes - LLM endpoint,
API key, and the Google Sheet wiring for people who never touch a terminal.

It lives next to the code and holds secrets, so it is git-ignored and
written with owner-only permissions.  Load order:

    config/search.yaml  <  config/settings.local.json  <  environment
"""

from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
# Containers mount a volume for persistent state; point these env vars at it
# (see fly.toml / Dockerfile). On a plain local run they stay in config/.
USER_SETTINGS_PATH = Path(
    os.getenv("JOBSCOUT_USER_SETTINGS") or ROOT / "config" / "settings.local.json"
)
SERVICE_ACCOUNT_PATH = Path(
    os.getenv("JOBSCOUT_GOOGLE_SA_FILE")
    or ROOT / "config" / "google-service-account.json"
)

_SHEET_ID_RE = re.compile(r"/spreadsheets/d/([A-Za-z0-9_-]+)")


def read_overlay() -> dict[str, Any]:
    """The saved UI settings, or {} when nothing was configured yet."""
    try:
        data = json.loads(USER_SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def deep_merge(base: dict, overlay: dict) -> dict:
    """Base with overlay applied: nested dicts merge, scalars replace."""
    merged = copy.deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def update_overlay(patch: dict[str, Any]) -> dict[str, Any]:
    """Merge ``patch`` into the overlay file and return the stored result."""
    merged = deep_merge(read_overlay(), patch)
    USER_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = USER_SETTINGS_PATH.with_name(f".{USER_SETTINGS_PATH.name}.tmp")
    temporary.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(temporary, USER_SETTINGS_PATH)
    try:  # the file holds API keys - owner only
        USER_SETTINGS_PATH.chmod(0o600)
    except OSError:
        pass
    return merged


def sheet_id_from_url(url: str | None) -> str | None:
    """The spreadsheet id from a pasted link, or from a bare id."""
    if not url:
        return None
    match = _SHEET_ID_RE.search(url)
    if match:
        return match.group(1)
    candidate = url.strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{20,}", candidate):
        return candidate
    return None


def save_service_account(text: str) -> str:
    """Validate and store a pasted service-account JSON; return its email."""
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ValueError("That is not valid JSON.") from exc
    if not isinstance(data, dict):
        raise ValueError("Expected the service-account JSON object.")
    email = data.get("client_email")
    if not email or not data.get("private_key"):
        raise ValueError(
            "The file is missing client_email or private_key - "
            "is it a service-account key from Google Cloud?"
        )
    SERVICE_ACCOUNT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SERVICE_ACCOUNT_PATH.write_text(json.dumps(data), encoding="utf-8")
    try:
        SERVICE_ACCOUNT_PATH.chmod(0o600)
    except OSError:
        pass
    return str(email)


def service_account_email(path: str | Path | None) -> str | None:
    """The client_email inside a stored service-account file, if readable."""
    if not path:
        return None
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    email = data.get("client_email") if isinstance(data, dict) else None
    return str(email) if email else None
