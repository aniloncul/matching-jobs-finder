import { useState } from "react";
import type { RunState } from "../types";

interface ControlBarProps {
  state: RunState | null;
  onStart: (sources: string[], cached: boolean, query?: string) => void;
  onPause: () => void;
  onResume: () => void;
  onCancel: () => void;
}

export function ControlBar({
  state,
  onStart,
  onPause,
  onResume,
  onCancel,
}: ControlBarProps) {
  const [query, setQuery] = useState("");
  const status = state?.status ?? "idle";
  const active =
    status === "running" || status === "paused" || status === "cancelling";
  const locked = active;

  return (
    <div className="control-bar" aria-label="Run controls">
      <div className="control-group control-group--query">
        <label className="query-field" htmlFor="search-query">
          <span className="query-label">QUERY</span>
          <input
            id="search-query"
            className="query-input"
            type="text"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !locked) {
                onStart(["linkedin", "indeed"], false, query);
              }
            }}
            placeholder={'n8n, low-code, "workflow automation"'}
            autoComplete="off"
            spellCheck={false}
          />
        </label>
      </div>

      <div className="control-group control-group--actions">
        <button
          type="button"
          className="button button--run"
          onClick={() => onStart(["linkedin", "indeed"], false, query)}
          disabled={locked}
        >
          RUN BOTH
        </button>
        <button
          type="button"
          className="button"
          onClick={() => onStart(["linkedin"], false, query)}
          disabled={locked}
        >
          LINKEDIN
        </button>
        <button
          type="button"
          className="button"
          onClick={() => onStart(["indeed"], false, query)}
          disabled={locked}
        >
          INDEED
        </button>
        {status === "paused" ? (
          <button
            type="button"
            className="button"
            onClick={onResume}
            disabled={status !== "paused"}
          >
            RESUME
          </button>
        ) : (
          <button
            type="button"
            className="button"
            onClick={onPause}
            disabled={status !== "running"}
          >
            PAUSE
          </button>
        )}
        <button
          type="button"
          className="button button--cancel"
          onClick={onCancel}
          disabled={!active || status === "cancelling"}
        >
          CANCEL
        </button>
      </div>
    </div>
  );
}
