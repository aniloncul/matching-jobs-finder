import { useEffect, useMemo, useRef } from "react";
import type { FeedLog, FeedMessage } from "../types";

interface EventFeedProps {
  messages: FeedMessage[];
  logs: FeedLog[];
}

type Entry =
  | ({ entryType: "message" } & FeedMessage)
  | ({ entryType: "log" } & FeedLog);

export function EventFeed({ messages, logs }: EventFeedProps) {
  const scrollRef = useRef<HTMLDivElement | null>(null);

  const entries = useMemo(() => {
    const merged: Entry[] = [
      ...messages.map((message) => ({ ...message, entryType: "message" as const })),
      ...logs.map((log) => ({ ...log, entryType: "log" as const })),
    ];
    return merged.sort((a, b) => a.seq - b.seq).slice(-120);
  }, [logs, messages]);

  useEffect(() => {
    const node = scrollRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [entries.length]);

  return (
    <aside className="feed-panel" aria-label="Live agent feed">
      <div className="feed-header">
        <span>LIVE FEED</span>
        <span>{String(entries.length).padStart(2, "0")} EVENTS</span>
      </div>

      <div className="feed-scroll" ref={scrollRef} role="log" aria-live="polite">
        {entries.length === 0 ? (
          <div className="feed-empty">
            <p>NO SIGNAL YET.</p>
            <p>Start a run to watch commands travel between agents.</p>
          </div>
        ) : (
          entries.map((entry, index) => {
            const latest = index === entries.length - 1;
            if (entry.entryType === "log") {
              return (
                <div
                  key={`log-${entry.seq}`}
                  className={`feed-row feed-row--log${latest ? " is-latest" : ""}`}
                >
                  <time className="feed-time">{formatTime(entry.ts)}</time>
                  <div className="feed-body">
                    <div className="feed-meta">
                      <span className="feed-kind">LOG</span>
                      <span className="feed-route">
                        {entry.agent ? entry.agent.toUpperCase() : "SYSTEM"}
                      </span>
                    </div>
                    <p className="feed-text">{entry.text}</p>
                  </div>
                </div>
              );
            }

            return (
              <div
                key={`message-${entry.seq}`}
                className={`feed-row feed-row--${entry.kind}${
                  latest ? " is-latest" : ""
                }`}
              >
                <time className="feed-time">{formatTime(entry.ts)}</time>
                <div className="feed-body">
                  <div className="feed-meta">
                    <span className="feed-kind">{entry.kind.toUpperCase()}</span>
                    <span className="feed-route">
                      {entry.from.toUpperCase()} → {entry.to.toUpperCase()}
                    </span>
                  </div>
                  <p className="feed-text">{entry.text}</p>
                </div>
              </div>
            );
          })
        )}
      </div>
    </aside>
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
