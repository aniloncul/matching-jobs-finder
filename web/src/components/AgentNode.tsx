import { NODE_POSITIONS } from "../graph";
import type { AgentState } from "../types";
import { AgentAvatar } from "./Avatars";
import { StatusMark } from "./StatusMark";

interface AgentNodeProps {
  agent: AgentState;
  onOpen: (agentId: string) => void;
}

export function AgentNode({ agent, onOpen }: AgentNodeProps) {
  const position = NODE_POSITIONS[agent.id];
  const progress = Math.min(100, Math.max(0, Math.round(agent.progress * 100)));

  const handleClick = () => onOpen(agent.id);
  const handleKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onOpen(agent.id);
    }
  };

  return (
    <article
      className="agent-card"
      data-agent={agent.id}
      data-status={agent.status}
      style={{
        left: `${position.x / 10}%`,
        top: `${position.y / 7}%`,
      }}
      aria-label={`${agent.name}, ${agent.status}. Open agent log`}
      aria-haspopup="dialog"
      role="button"
      tabIndex={0}
      title="Open agent log"
      onClick={handleClick}
      onKeyDown={handleKeyDown}
    >
      <div className="agent-card-top">
        <div className="avatar-frame">
          <AgentAvatar id={agent.id} />
        </div>
        <StatusMark status={agent.status} />
      </div>
      <h2 className="agent-name">{agent.name}</h2>
      <p className="agent-role">{agent.role}</p>
      <p className="agent-task" title={agent.task}>
        {agent.task}
      </p>
      <div className="progress-track" aria-hidden="true">
        <span style={{ width: `${progress}%` }} />
      </div>
    </article>
  );
}
