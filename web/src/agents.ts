import type { AgentId, AgentState } from "./types";

export const AGENT_ORDER: AgentId[] = [
  "coordinator",
  "linkedin",
  "indeed",
  "registrar",
  "writer",
];

export const DEFAULT_AGENTS: Record<AgentId, AgentState> = {
  coordinator: {
    id: "coordinator",
    name: "Coordinator",
    role: "Orchestrator",
    status: "idle",
    task: "Standing by",
    progress: 0,
    updated_at: 0,
  },
  linkedin: {
    id: "linkedin",
    name: "LinkedIn Scout",
    role: "Source agent",
    status: "idle",
    task: "Waiting for dispatch",
    progress: 0,
    updated_at: 0,
  },
  indeed: {
    id: "indeed",
    name: "Indeed Scout",
    role: "Source agent",
    status: "idle",
    task: "Waiting for dispatch",
    progress: 0,
    updated_at: 0,
  },
  registrar: {
    id: "registrar",
    name: "Registrar",
    role: "Sheet writer",
    status: "idle",
    task: "Waiting for results",
    progress: 0,
    updated_at: 0,
  },
  writer: {
    id: "writer",
    name: "Cover Writer",
    role: "Application agent",
    status: "idle",
    task: "Waiting for a job",
    progress: 0,
    updated_at: 0,
  },
};
