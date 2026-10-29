import { useCallback, useState } from "react";
import { DEFAULT_AGENTS } from "./agents";
import { AgentGraph } from "./components/AgentGraph";
import { AgentLogModal } from "./components/AgentLogModal";
import { ControlBar } from "./components/ControlBar";
import { CoverLetters } from "./components/CoverLetters";
import { ProfileEditor } from "./components/ProfileEditor";
import { EventFeed } from "./components/EventFeed";
import { TopBar } from "./components/TopBar";
import { useRunStream } from "./hooks/useRunStream";
import { useSection } from "./hooks/useSection";
import { useTheme } from "./hooks/useTheme";
import type { AgentId } from "./types";

export default function App() {
  const {
    state,
    signals,
    apiError,
    start,
    pause,
    resume,
    cancel,
  } = useRunStream();
  const [section, selectSection] = useSection();
  const [theme, toggleTheme] = useTheme();
  const [selectedAgent, setSelectedAgent] = useState<AgentId | null>(null);
  const closeAgent = useCallback(() => setSelectedAgent(null), []);

  const status = state?.status ?? "idle";
  const agents = state?.agents ?? DEFAULT_AGENTS;
  const errorMessage = apiError || state?.error || null;
  const activeAgent = selectedAgent ? agents[selectedAgent] : null;
  return (
    <div className="app" data-section={section}>
      <TopBar
        sheetUrl={state?.sheet_url ?? null}
        section={section}
        onSectionChange={selectSection}
        theme={theme}
        onThemeToggle={toggleTheme}
      />

      <main className="page">
        {section === "run" ? (
          <>
            <section className="hero">
              <div className="hero-console">
                <ControlBar
                  state={state}
                  onStart={(sources, cached, query) => {
                    void start(sources, cached, query);
                  }}
                  onPause={pause}
                  onResume={resume}
                  onCancel={cancel}
                />
              </div>
            </section>

            {errorMessage ? (
              <div className="error-strip" role="alert">
                <span className="error-tag">ERROR</span>
                <p>{errorMessage}</p>
              </div>
            ) : null}

            <section className="workspace">
              <div className="graph-panel">
                {status === "paused" ? (
                  <div className="paused-banner">
                    PAUSED · THE CURRENT BROWSER ACTION WILL FINISH, THEN HOLD
                  </div>
                ) : null}

                <AgentGraph
                  agents={agents}
                  signals={signals}
                  onAgentSelect={setSelectedAgent}
                />
              </div>

              <EventFeed
                messages={state?.messages ?? []}
                logs={state?.logs ?? []}
              />
            </section>
          </>
        ) : null}

        {section === "cover-letters" ? <CoverLetters /> : null}
        {section === "profile" ? <ProfileEditor alwaysEdit /> : null}
      </main>

      {activeAgent && selectedAgent ? (
        <AgentLogModal
          agent={activeAgent}
          logs={state?.agent_logs?.[selectedAgent] ?? []}
          history={state?.agent_status_history?.[selectedAgent] ?? []}
          messages={(state?.messages ?? []).filter(
            (message) =>
              message.from === selectedAgent || message.to === selectedAgent,
          )}
          onClose={closeAgent}
        />
      ) : null}
    </div>
  );
}
