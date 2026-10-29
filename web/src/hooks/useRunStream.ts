import { useCallback, useEffect, useRef, useState } from "react";
import { DEFAULT_AGENTS } from "../agents";
import { controlRun, getState, startRun } from "../api";
import type {
  AgentId,
  AgentStatusEntry,
  ConnectionState,
  FeedLog,
  FeedMessage,
  MessageKind,
  RunEvent,
  RunState,
  Signal,
  SnapshotEvent,
  Stats,
} from "../types";

const SIGNAL_MS = 1700;

const EMPTY_STATS: Stats = {
  found: 0,
  new: 0,
  skipped: 0,
  errors: 0,
};

function asNumber(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function asString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function reduceState(previous: RunState, event: RunEvent): RunState {
  const seq = asNumber(event.seq);
  const withSeq = (next: RunState): RunState => ({
    ...next,
    last_seq: Math.max(previous.last_seq, seq),
  });

  switch (event.type) {
    case "run.started":
      return withSeq({
        ...previous,
        run_id: asString(event.run_id) || previous.run_id,
        status: "running",
        mode:
          event.mode === "cached"
            ? "cached"
            : event.mode === "live"
              ? "live"
              : previous.mode,
        sources: Array.isArray(event.sources)
          ? event.sources.map((item) => String(item))
          : previous.sources,
        started_at: asNumber(event.ts),
        finished_at: null,
        error: null,
        agents: DEFAULT_AGENTS,
        stats: EMPTY_STATS,
        messages: [],
        logs: [],
        agent_logs: {},
        agent_status_history: {},
        sheet_url: null,
        sink_kind: null,
      });
    case "run.paused":
      return withSeq({ ...previous, status: "paused" });
    case "run.resumed":
      return withSeq({ ...previous, status: "running" });
    case "run.cancelling":
      return withSeq({ ...previous, status: "cancelling" });
    case "run.cancelled": {
      const ts = asNumber(event.ts);
      const history = { ...previous.agent_status_history };
      const agents = Object.fromEntries(
        Object.entries(previous.agents).map(([id, agent]) => {
          if (agent.status !== "working" && agent.status !== "waiting") {
            return [id, agent];
          }
          const next = { ...agent, status: "idle" as const, task: "Run cancelled", updated_at: ts };
          history[id] = [
            ...(history[id] ?? []),
            { seq, ts, status: next.status, task: next.task, progress: next.progress },
          ].slice(-100);
          return [id, next];
        }),
      );
      return withSeq({
        ...previous,
        status: "cancelled",
        finished_at: ts,
        agents,
        agent_status_history: history,
      });
    }
    case "run.finished":
      return withSeq({
        ...previous,
        status: "completed",
        finished_at: asNumber(event.ts),
      });
    case "run.failed": {
      const ts = asNumber(event.ts);
      const coordinator = previous.agents.coordinator;
      if (!coordinator) {
        return withSeq({
          ...previous,
          status: "failed",
          finished_at: ts,
          error: asString(event.error, "Run failed"),
        });
      }
      const nextCoordinator = { ...coordinator, status: "error" as const, task: "Run failed", updated_at: ts };
      return withSeq({
        ...previous,
        status: "failed",
        finished_at: ts,
        error: asString(event.error, "Run failed"),
        agents: { ...previous.agents, coordinator: nextCoordinator },
        agent_status_history: {
          ...previous.agent_status_history,
          coordinator: [
            ...(previous.agent_status_history.coordinator ?? []),
            { seq, ts, status: nextCoordinator.status, task: nextCoordinator.task, progress: nextCoordinator.progress },
          ].slice(-100),
        },
      });
    }
    case "agent.status": {
      const agentId = asString(event.agent);
      const agent = previous.agents[agentId];
      if (!agent) return previous;
      const nextAgent = {
        ...agent,
        status: asString(event.status, "idle") as typeof agent.status,
        task: asString(event.task, agent.task),
        progress:
          typeof event.progress === "number" ? event.progress : agent.progress,
        updated_at: asNumber(event.ts),
      };
      const entry: AgentStatusEntry = {
        seq,
        ts: asNumber(event.ts),
        status: nextAgent.status,
        task: nextAgent.task,
        progress: nextAgent.progress,
      };
      return withSeq({
        ...previous,
        agents: { ...previous.agents, [agentId]: nextAgent },
        agent_status_history: {
          ...previous.agent_status_history,
          [agentId]: [...(previous.agent_status_history[agentId] ?? []), entry].slice(-100),
        },
      });
    }
    case "stats":
      return withSeq({
        ...previous,
        stats: {
          found: asNumber(event.found),
          new: asNumber(event.new),
          skipped: asNumber(event.skipped),
          errors: asNumber(event.errors),
        },
      });
    case "sink.ready":
      return withSeq({
        ...previous,
        sink_kind: asString(event.kind) || null,
        sheet_url: asString(event.sheet_url) || null,
      });
    case "message": {
      const message: FeedMessage = {
        seq: asNumber(event.seq),
        ts: asNumber(event.ts),
        from: asString(event.from) as AgentId,
        to: asString(event.to) as AgentId,
        kind: (asString(event.kind, "status") as MessageKind) || "status",
        text: asString(event.text),
      };
      if (previous.messages.some((item) => item.seq === message.seq)) {
        return withSeq(previous);
      }
      return withSeq({
        ...previous,
        messages: [...previous.messages, message].slice(-100),
      });
    }
    case "log": {
      const agent = asString(event.agent) || null;
      const log: FeedLog = {
        seq: asNumber(event.seq),
        ts: asNumber(event.ts),
        agent: agent as AgentId | null,
        level: asString(event.level, "info"),
        text: asString(event.text),
      };
      if (previous.logs.some((item) => item.seq === log.seq)) {
        return withSeq(previous);
      }
      return withSeq({
        ...previous,
        logs: [...previous.logs, log].slice(-100),
        agent_logs: agent
          ? {
              ...previous.agent_logs,
              [agent]: [...(previous.agent_logs[agent] ?? []), log].slice(-100),
            }
          : previous.agent_logs,
      });
    }
    default:
      return previous;
  }
}

export function useRunStream() {
  const [state, setState] = useState<RunState | null>(null);
  const [signals, setSignals] = useState<Record<string, Signal>>({});
  const [connection, setConnection] = useState<ConnectionState>("connecting");
  const [apiError, setApiError] = useState<string | null>(null);
  const timers = useRef<Record<string, number>>({});
  const startedRef = useRef(false);

  const clearSignals = useCallback(() => {
    for (const timer of Object.values(timers.current)) {
      window.clearTimeout(timer);
    }
    timers.current = {};
    setSignals({});
  }, []);

  const pulse = useCallback((event: RunEvent) => {
    const from = asString(event.from);
    const to = asString(event.to);
    if (!from || !to) return;
    const edgeKey = `${from}:${to}`;
    const signal: Signal = {
      edgeKey,
      from: from as AgentId,
      to: to as AgentId,
      kind: (asString(event.kind, "status") as MessageKind) || "status",
      text: asString(event.text),
      seq: asNumber(event.seq),
      ts: asNumber(event.ts),
    };

    setSignals((previous) => ({ ...previous, [edgeKey]: signal }));
    if (timers.current[edgeKey]) {
      window.clearTimeout(timers.current[edgeKey]);
    }
    timers.current[edgeKey] = window.setTimeout(() => {
      setSignals((previous) => {
        if (previous[edgeKey]?.seq !== signal.seq) return previous;
        const next = { ...previous };
        delete next[edgeKey];
        return next;
      });
      delete timers.current[edgeKey];
    }, SIGNAL_MS);
  }, []);

  const applyEvent = useCallback(
    (event: RunEvent) => {
      setState((previous) =>
        previous ? reduceState(previous, event) : previous,
      );
      if (event.type === "message") {
        pulse(event);
      }
    },
    [pulse],
  );

  const applySnapshot = useCallback(
    (snapshot: SnapshotEvent) => {
      setState((previous) => {
        if (
          previous &&
          previous.run_id === snapshot.state.run_id &&
          snapshot.seq < previous.last_seq
        ) {
          return previous;
        }
        return {
          ...snapshot.state,
          agent_logs: snapshot.state.agent_logs ?? {},
          agent_status_history: snapshot.state.agent_status_history ?? {},
        };
      });
      clearSignals();
      setConnection("live");
    },
    [clearSignals],
  );

  useEffect(() => {
    let disposed = false;

    getState()
      .then((snapshot) => {
        if (!disposed && !startedRef.current) applySnapshot(snapshot);
      })
      .catch((error: Error) => {
        if (!disposed) setApiError(error.message);
      });

    const source = new EventSource("/api/events");
    source.onopen = () => {
      if (!disposed) setConnection("live");
    };
    source.onerror = () => {
      if (!disposed) setConnection("reconnecting");
    };
    source.onmessage = (message) => {
      if (disposed) return;
      try {
        const event = JSON.parse(message.data) as RunEvent | SnapshotEvent;
        if (event.type === "snapshot") {
          applySnapshot(event as SnapshotEvent);
          return;
        }
        setConnection("live");
        applyEvent(event as RunEvent);
      } catch {
        // Ignore malformed frames and keep the stream alive.
      }
    };

    return () => {
      disposed = true;
      source.close();
      clearSignals();
    };
  }, [applyEvent, applySnapshot, clearSignals]);

  const start = useCallback(
    async (sources: string[], cached: boolean, query?: string) => {
      startedRef.current = true;
      setApiError(null);
      try {
        applySnapshot(await startRun(sources, cached, query));
      } catch (error) {
        setApiError(error instanceof Error ? error.message : String(error));
      }
    },
    [applySnapshot],
  );

  const control = useCallback(
    async (action: "pause" | "resume" | "cancel") => {
      if (!state?.run_id) return;
      setApiError(null);
      try {
        applySnapshot(await controlRun(state.run_id, action));
      } catch (error) {
        setApiError(error instanceof Error ? error.message : String(error));
      }
    },
    [applySnapshot, state?.run_id],
  );

  return {
    state,
    signals,
    connection,
    apiError,
    start,
    pause: () => control("pause"),
    resume: () => control("resume"),
    cancel: () => control("cancel"),
  };
}
