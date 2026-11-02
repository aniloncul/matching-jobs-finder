"""Background runner for batch CV tailoring (career-ops `batch-tailor` analogue).

Sequential on purpose: every CV hits the same LLM endpoint as the letters and
runs a LaTeX compile, so stacking them only makes each one slower. Progress is
relayed through the RunManager, which makes the Writer agent light up in the
graph and keeps the whole event stream honest.
"""

from __future__ import annotations

import copy
import threading
import time
from typing import Any

from jobscout.config import Settings
from jobscout.control import RunConflict
from jobscout.models import AgentStatus, Job


class CvBatchRunner:
    """Build tailored CVs for several saved jobs on a daemon thread."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._state: dict[str, Any] = self._idle_state()

    @staticmethod
    def _idle_state() -> dict[str, Any]:
        return {
            "running": False,
            "cancelled": False,
            "total": 0,
            "done": 0,
            "current": None,
            "results": [],
            "error": None,
            "started_at": None,
            "finished_at": None,
        }

    @property
    def running(self) -> bool:
        with self._lock:
            return bool(self._state["running"])

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._state)

    def wait(self, timeout: float | None = None) -> None:
        with self._lock:
            thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def cancel(self) -> None:
        """Stop after the CV currently being built."""
        self._cancel.set()

    def start(self, settings: Settings, manager: Any, jobs: list[Job]) -> None:
        if not jobs:
            raise RunConflict("There are no jobs to build CVs for")
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise RunConflict("A CV batch is already running")
            self._cancel.clear()
            self._state = self._idle_state()
            self._state.update({"running": True, "total": len(jobs),
                                "started_at": time.time()})
        self._thread = threading.Thread(
            target=self._run,
            args=(settings, manager, list(jobs)),
            daemon=True,
            name="cv-batch",
        )
        self._thread.start()

    def _run(self, settings: Settings, manager: Any, jobs: list[Job]) -> None:
        # Imported lazily (like the other slow paths) so importing the UI
        # server never pulls in the LaTeX/LLM stack.
        from jobscout.cv.pipeline import CVBuildError, build_cv  # noqa: PLC0415

        total = len(jobs)
        cancelled = False
        try:
            for index, job in enumerate(jobs, start=1):
                if self._cancel.is_set():
                    cancelled = True
                    break
                with self._lock:
                    self._state["current"] = f"{index}/{total} · {job.company}"
                base = (index - 1) / total
                span = 1.0 / total

                def progress(
                    task: str,
                    value: float | None = None,
                    *,
                    base: float = base,
                    span: float = span,
                    index: int = index,
                    job: Job = job,
                ) -> None:
                    manager.writer_status(
                        AgentStatus.working,
                        f"CV {index}/{total} · {job.company} · {task}",
                        base + span * float(value or 0.0),
                    )

                entry: dict[str, Any] = {
                    "source": job.source.value,
                    "job_id": job.job_id,
                    "company": job.company,
                    "title": job.title,
                    "key": None,
                    "status": "error",
                    "verdict": None,
                    "coverage_pct": None,
                    "has_pdf": False,
                    "error": None,
                }
                try:
                    result = build_cv(settings, job=job, on_progress=progress)
                    entry.update(
                        {
                            "key": result.key,
                            "status": (
                                "blocked"
                                if result.blocked
                                else ("reused" if result.reused else "built")
                            ),
                            "verdict": result.verdict,
                            "coverage_pct": result.coverage_pct,
                            "has_pdf": bool(result.pdf_path),
                            "error": (
                                "; ".join(result.blockers)
                                if result.blocked
                                else result.pdf_error
                            ),
                        }
                    )
                except CVBuildError as exc:
                    entry["error"] = str(exc)
                except Exception as exc:  # noqa: BLE001 - one bad job must not kill the batch
                    entry["error"] = f"CV build failed: {exc}"
                with self._lock:
                    self._state["results"].append(entry)
                    self._state["done"] += 1
                    self._state["current"] = None
                    if cancelled:
                        self._state["cancelled"] = True
                level = "warn" if entry["status"] in {"blocked", "error"} else "info"
                manager.writer_log(
                    f"CV batch {index}/{total} · {job.company}: {entry['status']}"
                    + (f" · {entry['error']}" if entry["error"] else ""),
                    level=level,
                )
        except Exception as exc:  # noqa: BLE001 - the thread must always settle
            with self._lock:
                self._state["error"] = str(exc)
        finally:
            with self._lock:
                results = list(self._state["results"])
                self._state["cancelled"] = self._state["cancelled"] or cancelled
                self._state["running"] = False
                self._state["current"] = None
                self._state["finished_at"] = time.time()
            built = sum(1 for item in results if item["status"] in {"built", "reused"})
            blocked = sum(1 for item in results if item["status"] == "blocked")
            failed = sum(1 for item in results if item["status"] == "error")
            summary = (
                f"CV batch finished · {built} built"
                + (f" · {blocked} blocked" if blocked else "")
                + (f" · {failed} failed" if failed else "")
                + (" · cancelled" if cancelled else "")
            )
            manager.writer_message(summary, kind="result")
            manager.writer_status(AgentStatus.idle, "Waiting for a job", None)
