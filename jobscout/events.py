from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from typing import Any


Listener = Callable[[dict[str, Any]], None]


class EventBus:
    """Thread-safe fan-out from the scraper thread to SSE queues and CLI listeners."""

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._listeners: set[Listener] = set()

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=500)
        with self._lock:
            self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        with self._lock:
            self._subscribers.discard(queue)

    def add_listener(self, listener: Listener) -> None:
        with self._lock:
            self._listeners.add(listener)

    def remove_listener(self, listener: Listener) -> None:
        with self._lock:
            self._listeners.discard(listener)

    def publish(self, event: dict[str, Any]) -> None:
        with self._lock:
            listeners = list(self._listeners)
            subscribers = list(self._subscribers)
            loop = self._loop

        for listener in listeners:
            try:
                listener(event)
            except Exception:
                pass

        if loop is not None and loop.is_running() and subscribers:
            loop.call_soon_threadsafe(self._fanout, event, subscribers)

    def _fanout(
        self,
        event: dict[str, Any],
        subscribers: list[asyncio.Queue[dict[str, Any]]],
    ) -> None:
        for queue in subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    pass
