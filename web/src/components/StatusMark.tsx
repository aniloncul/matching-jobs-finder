import type { AgentStatus } from "../types";

const LABELS: Record<AgentStatus, string> = {
  idle: "IDLE",
  working: "WORKING",
  waiting: "WAITING",
  paused: "PAUSED",
  done: "DONE",
  error: "ERROR",
  blocked: "BLOCKED",
};

const GLYPHS: Record<AgentStatus, string> = {
  idle: "□",
  working: "■",
  waiting: "◇",
  paused: "Ⅱ",
  done: "✓",
  error: "×",
  blocked: "×",
};

export function StatusMark({ status }: { status: AgentStatus }) {
  return (
    <span
      className={`status-mark status-mark--${status}`}
      role="img"
      aria-label={`Status: ${LABELS[status]}`}
    >
      <span className="status-glyph" aria-hidden="true">
        {GLYPHS[status]}
      </span>
      <span className="status-word">{LABELS[status]}</span>
    </span>
  );
}
