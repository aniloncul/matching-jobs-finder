from __future__ import annotations

from collections.abc import Callable

from jobscout.config import Settings
from jobscout.sinks.base import Sink, SinkError
from jobscout.sinks.google_sheet import GoogleSheetSink
from jobscout.sinks.local import LocalSink


def build_sink(settings: Settings, log: Callable[[str], None] | None = None) -> Sink:
    log = log or (lambda _message: None)
    wants_google = settings.sink in {"auto", "google"}
    has_google = bool(settings.google_sheet_id and settings.google_service_account_json)

    if wants_google and has_google:
        path = settings.google_service_account_json
        if path and path.exists():
            try:
                sink = GoogleSheetSink(settings.google_sheet_id or "", path, settings=settings)
                sink.prepare()
                return sink
            except Exception as exc:
                log(f"Google Sheets unavailable ({exc}); using local CSV")
        else:
            log(f"Service account file not found at {path}; using local CSV")

    if settings.sink == "google" and not has_google:
        log("SINK=google but Google credentials are incomplete; using local CSV")

    sink = LocalSink(settings.local_jobs_csv, settings.local_runs_csv, settings=settings)
    sink.prepare()
    return sink


__all__ = ["Sink", "SinkError", "LocalSink", "GoogleSheetSink", "build_sink"]
