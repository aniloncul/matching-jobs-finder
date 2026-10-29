import { useEffect, useMemo, useState } from "react";
import { fetchAgentLogs } from "../api";
import type {
  AgentLogData,
  AgentState,
  AgentStatusEntry,
  FeedLog,
  FeedMessage,
} from "../types";
import { AgentAvatar } from "./Avatars";
import { StatusMark } from "./StatusMark";

export interface AgentLogModalProps {
  agent: AgentState;
  logs?: FeedLog[];
  history?: AgentStatusEntry[];
  messages?: FeedMessage[];
  isOpen?: boolean;
  onClose: () => void;
}

export function AgentLogModal({
  agent,
  logs: liveLogs = [],
  history: liveHistory = [],
  messages: liveMessages = [],
  isOpen = true,
  onClose,
}: AgentLogModalProps) {
  const [fetchedData, setFetchedData] = useState<AgentLogData | null>(null);
  const [loading, setLoading] = useState(false);
  const [activeTab, setActiveTab] = useState<"logs" | "history" | "messages">("logs");

  useEffect(() => {
    if (!isOpen || !agent.id) return;
    let active = true;
    setLoading(true);

    fetchAgentLogs(agent.id)
      .then((data) => {
        if (active) {
          setFetchedData(data);
          setLoading(false);
        }
      })
      .catch((err) => {
        if (active) {
          console.warn("Could not fetch extra logs from server:", err);
          setLoading(false);
        }
      });

    return () => {
      active = false;
    };
  }, [isOpen, agent.id]);

  useEffect(() => {
    if (!isOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    return () => {
      window.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = previousOverflow;
    };
  }, [isOpen, onClose]);

  // Merge live stream logs with server-fetched logs (dedup by seq)
  const mergedLogs = useMemo(() => {
    const map = new Map<number, FeedLog>();
    for (const log of fetchedData?.logs ?? []) {
      map.set(log.seq, log);
    }
    for (const log of liveLogs) {
      map.set(log.seq, log);
    }
    return Array.from(map.values()).sort((a, b) => b.seq - a.seq);
  }, [fetchedData?.logs, liveLogs]);

  const mergedHistory = useMemo(() => {
    const map = new Map<number, AgentStatusEntry>();
    for (const h of fetchedData?.history ?? []) {
      map.set(h.seq, h);
    }
    for (const h of liveHistory) {
      map.set(h.seq, h);
    }
    return Array.from(map.values()).sort((a, b) => b.seq - a.seq);
  }, [fetchedData?.history, liveHistory]);

  const mergedMessages = useMemo(() => {
    const map = new Map<number, FeedMessage>();
    for (const m of fetchedData?.messages ?? []) {
      map.set(m.seq, m);
    }
    for (const m of liveMessages) {
      map.set(m.seq, m);
    }
    return Array.from(map.values()).sort((a, b) => b.seq - a.seq);
  }, [fetchedData?.messages, liveMessages]);

  if (!isOpen) return null;

  return (
    <div
      className="modal-overlay"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-labelledby="modal-agent-title"
    >
      <div className="modal-content" onClick={(e) => e.stopPropagation()}>
        <header className="modal-header">
          <div className="modal-agent-info">
            <div className="avatar-frame">
              <AgentAvatar id={agent.id} />
            </div>
            <div>
              <div className="modal-title-row">
                <h2 id="modal-agent-title" className="modal-agent-name">
                  {agent.name}
                </h2>
                <div className="modal-badge-status">
                  <StatusMark status={agent.status} />
                  <span>{agent.status.toUpperCase()}</span>
                </div>
              </div>
              <p className="modal-agent-role">{agent.role}</p>
              <p className="modal-agent-current-task">
                <strong>Current:</strong> {agent.task}
              </p>
            </div>
          </div>
          <button
            className="modal-close"
            onClick={onClose}
            aria-label="Close modal"
          >
            <svg
              viewBox="0 0 24 24"
              width="22"
              height="22"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M18 6L6 18M6 6l12 12" />
            </svg>
          </button>
        </header>

        <div className="modal-tabs">
          <button
            className={`modal-tab${activeTab === "logs" ? " is-active" : ""}`}
            onClick={() => setActiveTab("logs")}
          >
            ACTIVITY LOGS ({mergedLogs.length})
          </button>
          <button
            className={`modal-tab${activeTab === "history" ? " is-active" : ""}`}
            onClick={() => setActiveTab("history")}
          >
            STATUS HISTORY ({mergedHistory.length})
          </button>
          <button
            className={`modal-tab${activeTab === "messages" ? " is-active" : ""}`}
            onClick={() => setActiveTab("messages")}
          >
            MESSAGES ({mergedMessages.length})
          </button>
        </div>

        <div className="modal-body">
          {loading && mergedLogs.length === 0 && mergedHistory.length === 0 ? (
            <div className="modal-loading">Loading agent telemetry...</div>
          ) : activeTab === "logs" ? (
            <AgentLogList logs={mergedLogs} />
          ) : activeTab === "history" ? (
            <AgentHistoryList history={mergedHistory} />
          ) : (
            <AgentMessageList messages={mergedMessages} />
          )}
        </div>
      </div>
    </div>
  );
}

function AgentLogList({ logs }: { logs: FeedLog[] }) {
  if (logs.length === 0) {
    return (
      <div className="modal-empty">
        <p>No activity logs recorded for this agent yet.</p>
        <small>Logs will appear here in real-time as tasks execute.</small>
      </div>
    );
  }
  return (
    <div className="modal-list">
      {logs.map((log) => {
        const isWarn = log.level === "warn" || log.level === "warning";
        const isError = log.level === "error";
        return (
          <div
            key={log.seq}
            className={`modal-entry modal-entry--log${
              isError
                ? " modal-entry--error"
                : isWarn
                  ? " modal-entry--warn"
                  : ""
            }`}
          >
            <time className="modal-time">{formatTime(log.ts)}</time>
            <span className={`modal-level level-${log.level.toLowerCase()}`}>
              {log.level.toUpperCase()}
            </span>
            <p className="modal-text">{log.text}</p>
          </div>
        );
      })}
    </div>
  );
}

function AgentHistoryList({ history }: { history: AgentStatusEntry[] }) {
  if (history.length === 0) {
    return (
      <div className="modal-empty">
        <p>No status transitions recorded yet.</p>
      </div>
    );
  }
  return (
    <div className="modal-list">
      {history.map((entry) => (
        <div key={entry.seq} className="modal-entry modal-entry--history">
          <time className="modal-time">{formatTime(entry.ts)}</time>
          <div className="modal-history-info">
            <span className={`modal-status status-${entry.status}`}>
              {entry.status.toUpperCase()}
            </span>
            {typeof entry.progress === "number" && entry.progress > 0 ? (
              <span className="modal-progress">
                {Math.round(entry.progress * 100)}%
              </span>
            ) : null}
          </div>
          <p className="modal-task">{entry.task}</p>
        </div>
      ))}
    </div>
  );
}

function AgentMessageList({ messages }: { messages: FeedMessage[] }) {
  if (messages.length === 0) {
    return (
      <div className="modal-empty">
        <p>No inter-agent messages sent or received yet.</p>
      </div>
    );
  }
  return (
    <div className="modal-list">
      {messages.map((msg) => (
        <div
          key={msg.seq}
          className={`modal-entry modal-entry--message modal-entry--${msg.kind}`}
        >
          <time className="modal-time">{formatTime(msg.ts)}</time>
          <div className="modal-message-meta">
            <span className={`modal-kind kind-${msg.kind}`}>
              {msg.kind.toUpperCase()}
            </span>
            <span className="modal-route">
              {msg.from.toUpperCase()} → {msg.to.toUpperCase()}
            </span>
          </div>
          <p className="modal-text">{msg.text}</p>
        </div>
      ))}
    </div>
  );
}

function formatTime(timestamp: number): string {
  if (!timestamp) return "--:--:--";
  return new Date(timestamp * 1000).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}
