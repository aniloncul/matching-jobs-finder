from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from typing import Any

from jobscout.config import Settings
from jobscout.control import RunCancelled, RunConflict, RunControl
from jobscout.events import EventBus
from jobscout.models import AgentState, AgentStatus, RunState, RunStatus, Stats
from jobscout.orchestrator import Orchestrator


AGENT_DEFS = (
    ("coordinator", "Coordinator", "Orchestrator", "Standing by"),
    ("linkedin", "LinkedIn Scout", "Source agent", "Waiting for dispatch"),
    ("indeed", "Indeed Scout", "Source agent", "Waiting for dispatch"),
    ("registrar", "Registrar", "Sheet writer", "Waiting for results"),
    ("writer", "Cover Writer", "Application agent", "Waiting for a job"),
)


def default_agents() -> dict[str, AgentState]:
    return {
        agent_id: AgentState(
            id=agent_id,
            name=name,
            role=role,
            status=AgentStatus.idle,
            task=task,
        )
        for agent_id, name, role, task in AGENT_DEFS
    }


def _push_agent_history(
    state: RunState,
    agent: AgentState,
    seq: int | None,
    ts: float | None,
) -> None:
    """Remember an agent status transition for the operator log modal."""
    history = state.agent_status_history.setdefault(agent.id, [])
    history.append(
        {
            "seq": seq or 0,
            "ts": ts or time.time(),
            "status": agent.status.value,
            "task": agent.task,
            "progress": agent.progress,
        }
    )
    state.agent_status_history[agent.id] = history[-100:]


class RunManager:
    def __init__(self, settings: Settings, bus: EventBus) -> None:
        self.settings = settings
        self.bus = bus
        self._lock = threading.RLock()
        self._seq = 0
        self._history: deque[dict[str, Any]] = deque(maxlen=500)
        self._thread: threading.Thread | None = None
        self._control: RunControl | None = None
        self.state = RunState(agents=default_agents())

    @property
    def active(self) -> bool:
        with self._lock:
            return self.state.status in {
                RunStatus.running,
                RunStatus.paused,
                RunStatus.cancelling,
            }

    def snapshot_event(self) -> dict[str, Any]:
        with self._lock:
            return {
                "type": "snapshot",
                "seq": self._seq,
                "state": self.state.model_dump(mode="json"),
            }

    def history_since(self, seq: int) -> list[dict[str, Any]]:
        with self._lock:
            return [event for event in self._history if event.get("seq", 0) > seq]

    def start(
        self,
        sources: list[str],
        cached: bool = False,
        query: str | None = None,
    ) -> str:
        with self._lock:
            if self.active or (self._thread is not None and self._thread.is_alive()):
                raise RunConflict("A run is already active")
            if not sources:
                raise RunConflict("Select at least one source")

            run_id = uuid.uuid4().hex[:10]
            started_at = time.time()
            cleaned_query = (query or "").strip()
            run_settings = self.settings
            if cleaned_query:
                run_settings = self.settings.model_copy(deep=True)
                run_settings.sources.linkedin.query = cleaned_query
                run_settings.sources.indeed.query = cleaned_query
            self._control = RunControl()
            self.state = RunState(
                run_id=run_id,
                status=RunStatus.running,
                mode="cached" if cached else "live",
                sources=list(sources),
                started_at=started_at,
                agents=default_agents(),
                stats=Stats(),
                messages=[],
                logs=[],
                agent_logs={},
                agent_status_history={},
                sheet_url=None,
                sink_kind=None,
                error=None,
                last_seq=self._seq,
            )
            self._thread = threading.Thread(
                target=self._execute,
                args=(run_id, list(sources), cached, started_at, run_settings),
                name=f"jobscout-{run_id}",
                daemon=True,
            )

        self._emit(
            "run.started",
            run_id=run_id,
            sources=sources,
            mode="cached" if cached else "live",
        )
        with self._lock:
            thread = self._thread
        if thread is not None:
            thread.start()
        return run_id

    def _execute(
        self,
        run_id: str,
        sources: list[str],
        cached: bool,
        started_at: float,
        run_settings: Settings,
    ) -> None:
        control = self._control
        if control is None:
            return
        orchestrator = Orchestrator(
            settings=run_settings,
            control=control,
            emit=self._emit,
            sources=sources,
            cached=cached,
            run_id=run_id,
            mode="cached" if cached else "live",
            started_at=started_at,
        )
        try:
            orchestrator.run()
            self._emit("run.finished")
        except RunCancelled:
            self._emit("run.cancelled", reason="Cancelled by user")
        except Exception as exc:
            self._emit("run.failed", error=str(exc))
        finally:
            with self._lock:
                self._thread = None

    def pause(self) -> None:
        with self._lock:
            if self._control is None or self.state.status != RunStatus.running:
                raise RunConflict("Only a running job can be paused")
            self._control.pause()
        self._emit("run.paused")

    def resume(self) -> None:
        with self._lock:
            if self._control is None or self.state.status != RunStatus.paused:
                raise RunConflict("Only a paused job can be resumed")
            self._control.resume()
        self._emit("run.resumed")

    def cancel(self) -> None:
        with self._lock:
            if self._control is None or self.state.status not in {
                RunStatus.running,
                RunStatus.paused,
                RunStatus.cancelling,
            }:
                raise RunConflict("There is no active job to cancel")
            self._control.cancel()
        self._emit("run.cancelling")

    def writer_status(
        self,
        status: AgentStatus,
        task: str,
        progress: float | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "agent": "writer",
            "status": status.value,
            "task": task,
        }
        if progress is not None:
            payload["progress"] = max(0.0, min(1.0, progress))
        self._emit("agent.status", **payload)

    def writer_message(self, text: str, kind: str = "status") -> None:
        self._emit(
            "message",
            **{"from": "writer", "to": "coordinator"},
            kind=kind,
            text=text,
        )

    def writer_log(self, text: str, level: str = "info") -> None:
        self._emit("log", agent="writer", level=level, text=text)

    def wait(self, timeout: float | None = None) -> None:
        with self._lock:
            thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def _emit(self, event_type: str, **payload: Any) -> dict[str, Any]:
        with self._lock:
            self._seq += 1
            event: dict[str, Any] = {
                "type": event_type,
                "seq": self._seq,
                "ts": time.time(),
                **payload,
            }
            self._apply_locked(event)
            self.state.last_seq = self._seq
            self._history.append(event)
        self.bus.publish(event)
        return event

    def _apply_locked(self, event: dict[str, Any]) -> None:
        event_type = event["type"]
        state = self.state

        if event_type == "run.started":
            state.status = RunStatus.running
            state.mode = event.get("mode", state.mode)
            state.sources = list(event.get("sources", state.sources))
            state.started_at = event.get("ts")
            state.finished_at = None
            state.error = None
            return

        if event_type == "run.paused":
            state.status = RunStatus.paused
            return

        if event_type == "run.resumed":
            state.status = RunStatus.running
            return

        if event_type == "run.cancelling":
            state.status = RunStatus.cancelling
            return

        if event_type == "run.cancelled":
            state.status = RunStatus.cancelled
            state.finished_at = event.get("ts")
            for agent in state.agents.values():
                if agent.status in {AgentStatus.working, AgentStatus.waiting}:
                    agent.status = AgentStatus.idle
                    agent.task = "Run cancelled"
                    agent.updated_at = event.get("ts", time.time())
                    _push_agent_history(state, agent, event.get("seq"), agent.updated_at)
            return

        if event_type == "run.finished":
            state.status = RunStatus.completed
            state.finished_at = event.get("ts")
            return

        if event_type == "run.failed":
            state.status = RunStatus.failed
            state.finished_at = event.get("ts")
            state.error = event.get("error")
            coordinator = state.agents.get("coordinator")
            if coordinator:
                coordinator.status = AgentStatus.error
                coordinator.task = "Run failed"
                coordinator.updated_at = event.get("ts", time.time())
                _push_agent_history(state, coordinator, event.get("seq"), coordinator.updated_at)
            return

        if event_type == "agent.status":
            agent_id = event.get("agent")
            agent = state.agents.get(agent_id)
            if agent is None:
                return
            agent.status = AgentStatus(event.get("status", "idle"))
            agent.task = event.get("task", agent.task)
            if "progress" in event:
                agent.progress = float(event["progress"])
            agent.updated_at = event.get("ts", time.time())
            _push_agent_history(state, agent, event.get("seq"), agent.updated_at)
            return

        if event_type == "message":
            state.messages.append(
                {
                    "seq": event["seq"],
                    "ts": event["ts"],
                    "from": event.get("from"),
                    "to": event.get("to"),
                    "kind": event.get("kind", "status"),
                    "text": event.get("text", ""),
                }
            )
            state.messages = state.messages[-100:]
            return

        if event_type == "log":
            log_entry = {
                "seq": event["seq"],
                "ts": event["ts"],
                "agent": event.get("agent"),
                "level": event.get("level", "info"),
                "text": event.get("text", ""),
            }
            state.logs.append(log_entry)
            state.logs = state.logs[-100:]
            agent_id = event.get("agent")
            if agent_id:
                agent_list = state.agent_logs.setdefault(agent_id, [])
                agent_list.append(log_entry)
                state.agent_logs[agent_id] = agent_list[-100:]
            return

        if event_type == "stats":
            state.stats = Stats(
                found=int(event.get("found", 0)),
                new=int(event.get("new", 0)),
                skipped=int(event.get("skipped", 0)),
                errors=int(event.get("errors", 0)),
            )
            return

        if event_type == "sink.ready":
            state.sink_kind = event.get("kind")
            state.sheet_url = event.get("sheet_url")
            return
