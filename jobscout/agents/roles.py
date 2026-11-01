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


class Verifier(AgentRole):
    """Liveness and deadline checks.  Reports to the Analyst, never to a source."""

    def __init__(self, emit: Emit) -> None:
        super().__init__("verifier", "Verifier", emit)

    def checking_deadlines(self, count: int) -> None:
        self.status("working", f"Reading deadlines · {count} jobs", 0.2)

    def deadlines_done(self, counts: dict[str, int]) -> None:
        expired = counts.get("expired", 0)
        closing = counts.get("closing_soon", 0)
        unparseable = counts.get("unparseable", 0)
        self.log(
            f"Deadlines · {closing} closing soon · {expired} expired · "
            f"{unparseable} unparseable"
        )
        self.send(
            "analyst",
            "result",
            f"{closing} closing soon · {expired} expired",
        )

    def checking_liveness(self, count: int) -> None:
        self.status("working", f"Probing liveness · {count} jobs", 0.5)

    def liveness_done(self, counts: dict[str, int]) -> None:
        active = counts.get("active", 0)
        expired = counts.get("expired", 0)
        uncertain = counts.get("uncertain", 0)
        self.log(
            f"Liveness · {active} active · {expired} expired · {uncertain} uncertain"
        )
        self.send(
            "analyst",
            "status",
            f"{active} active · {expired} expired · {uncertain} uncertain",
        )

    def finished(self, message: str) -> None:
        self.status("done", message, 1.0)

    def failed(self, message: str) -> None:
        self.status("error", message)
        self.send("coordinator", "error", message)


class Analyst(AgentRole):
    """Deterministic fit scoring.  Consumes verified jobs, hands them to the Registrar."""

    def __init__(self, emit: Emit) -> None:
        super().__init__("analyst", "Analyst", emit)

    def ranking(self, count: int) -> None:
        self.status("working", f"Scoring fit · {count} jobs", 0.6)

    def ranked(self, count: int, min_score: int) -> None:
        self.log(f"Fit scoring complete · {count} jobs kept at ≥{min_score}")
        self.status("done", f"{count} jobs ready to write", 1.0)
        self.send("registrar", "result", f"{count} ranked jobs")

    def skipped(self, message: str) -> None:
        self.log(message)
        self.status("waiting", "Fit scoring disabled")

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


class Writer(AgentRole):
    def __init__(self, emit: Emit) -> None:
        super().__init__("writer", "Cover Writer", emit)

    def idle(self) -> None:
        self.status("waiting", "Waiting for a job")

    def drafting(self, company: str, title: str) -> None:
        self.status("working", f"Writing letter for {company} · {title}", 0.4)

    def saved(self, company: str) -> None:
        self.status("done", f"Letter saved for {company}", 1.0)
        self.send("coordinator", "result", f"Cover letter ready for {company}")
