from __future__ import annotations

from collections.abc import Callable
from typing import Any


Emit = Callable[..., None]


class AgentRole:
    def __init__(self, agent_id: str, name: str, emit: Emit) -> None:
        self.id = agent_id
        self.name = name
        self.emit = emit

    def status(self, status: str, task: str, progress: float | None = None) -> None:
        payload: dict[str, Any] = {
            "agent": self.id,
            "status": status,
            "task": task,
        }
        if progress is not None:
            payload["progress"] = max(0.0, min(1.0, progress))
        self.emit("agent.status", **payload)

    def send(self, to: str, kind: str, text: str) -> None:
        self.emit(
            "message",
            **{
                "from": self.id,
                "to": to,
                "kind": kind,
                "text": text,
            },
        )

    def log(self, text: str, level: str = "info") -> None:
        self.emit(
            "log",
            agent=self.id,
            level=level,
            text=text,
        )


class Coordinator(AgentRole):
    def __init__(self, emit: Emit) -> None:
        super().__init__("coordinator", "Coordinator", emit)

    def command(self, target: str, text: str) -> None:
        self.status("working", f"Dispatching {target}")
        self.send(target, "command", text)

    def complete(self, text: str = "Run complete") -> None:
        self.status("done", text, 1.0)


class Scout(AgentRole):
    def __init__(self, source: str, emit: Emit) -> None:
        label = "LinkedIn Scout" if source == "linkedin" else "Indeed Scout"
        super().__init__(source, label, emit)

    def result(self, count: int) -> None:
        self.send("registrar", "result", f"{count} jobs extracted")

    def failed(self, message: str) -> None:
        self.status("error", message)
        self.send("coordinator", "error", message)


class Registrar(AgentRole):
    def __init__(self, emit: Emit) -> None:
        super().__init__("registrar", "Registrar", emit)

    def ready(self) -> None:
        self.status("waiting", "Waiting for source results")

    def announce_ready(self) -> None:
        self.ready()
        self.send("coordinator", "status", "Sheet and run log ready")

    def receiving(self, source: str, count: int) -> None:
        label = "LinkedIn" if source == "linkedin" else "Indeed"
        self.status("working", f"Receiving {count} jobs from {label}")

    def writing(self, count: int) -> None:
        self.status("working", f"Writing {count} rows", 0.85)

    def finished(self, new_count: int, skipped: int) -> None:
        self.status("done", f"{new_count} new rows written", 1.0)
        self.send(
            "coordinator",
            "status",
            f"{new_count} new rows · {skipped} duplicates skipped",
        )
