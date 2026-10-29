from __future__ import annotations

import threading
import time


class RunCancelled(Exception):
    """Raised at the next safe checkpoint after cancellation."""


class RunConflict(Exception):
    """Raised when a run control transition is not valid."""


class RunControl:
    """Cooperative pause/cancel signal shared by the UI thread and scraper thread."""

    def __init__(self) -> None:
        self._paused = threading.Event()
        self._cancelled = threading.Event()
        self._wake = threading.Event()

    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def pause(self) -> None:
        self._paused.set()
        self._wake.set()

    def resume(self) -> None:
        self._paused.clear()
        self._wake.set()

    def cancel(self) -> None:
        self._cancelled.set()
        self._paused.clear()
        self._wake.set()

    def checkpoint(self, label: str = "checkpoint") -> None:
        while self._paused.is_set() and not self._cancelled.is_set():
            self._wake.wait(0.2)
            self._wake.clear()
        if self._cancelled.is_set():
            raise RunCancelled(label)

    def sleep(self, seconds: float) -> None:
        deadline = time.monotonic() + max(0.0, seconds)
        while time.monotonic() < deadline:
            self.checkpoint("sleep")
            time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
