export type AgentId = "coordinator" | "linkedin" | "indeed" | "registrar" | "writer";

export type AgentStatus =
  | "idle"
  | "working"
  | "waiting"
  | "paused"
  | "done"
  | "error"
  | "blocked";

export type RunStatus =
  | "idle"
  | "running"
  | "paused"
  | "cancelling"
  | "cancelled"
  | "completed"
  | "failed";

export type MessageKind = "command" | "result" | "status" | "error";

export interface AgentState {
  id: AgentId;
  name: string;
  role: string;
  status: AgentStatus;
  task: string;
  progress: number;
  updated_at: number;
}

export interface Stats {
  found: number;
  new: number;
  skipped: number;
  errors: number;
}

export interface FeedMessage {
  seq: number;
  ts: number;
  from: AgentId;
  to: AgentId;
  kind: MessageKind;
  text: string;
}

export interface FeedLog {
  seq: number;
  ts: number;
  agent?: AgentId | null;
  level: string;
  text: string;
}

export interface AgentStatusEntry {
  seq: number;
  ts: number;
  status: AgentStatus;
  task: string;
  progress?: number;
}

export interface AgentLogData {
  agent: AgentState;
  logs: FeedLog[];
  history: AgentStatusEntry[];
  messages: FeedMessage[];
}

export interface RunState {
  run_id: string | null;
  status: RunStatus;
  mode: "live" | "cached";
  sources: string[];
  started_at: number | null;
  finished_at: number | null;
  agents: Record<string, AgentState>;
  stats: Stats;
  messages: FeedMessage[];
  logs: FeedLog[];
  agent_logs: Record<string, FeedLog[]>;
  agent_status_history: Record<string, AgentStatusEntry[]>;
  sheet_url: string | null;
  sink_kind: string | null;
  error: string | null;
  last_seq: number;
}

export interface SnapshotEvent {
  type: "snapshot";
  seq: number;
  state: RunState;
}

export interface RunEvent {
  type: string;
  seq: number;
  ts: number;
  [key: string]: unknown;
}

export interface Signal {
  edgeKey: string;
  from: AgentId;
  to: AgentId;
  kind: MessageKind;
  text: string;
  seq: number;
  ts: number;
}

export type ConnectionState = "connecting" | "live" | "reconnecting";

export interface JobListing {
  source: "linkedin" | "indeed" | "custom";
  job_id: string;
  company: string;
  title: string;
  link: string;
  posted_date: string | null;
  location: string | null;
  search_query: string;
  scraped_at: string;
  has_tex: boolean;
  has_pdf: boolean;
  has_description: boolean;
  description_url: string | null;
  applied?: boolean;
  urls: {
    tex: string | null;
    pdf: string | null;
  };
}

export interface CoverLetter {
  source: "linkedin" | "indeed" | "custom";
  job_id: string;
  company: string;
  title: string;
  language: string;
  tex_path: string;
  pdf_path: string | null;
  metadata_path: string | null;
  reused: boolean;
  warning: string | null;
  pdf_error: string | null;
}

export interface CoverLetterResponse {
  letter: CoverLetter;
  urls: {
    tex: string | null;
    pdf: string | null;
  };
}


export interface ExperienceData {
  company: string;
  role: string;
  start: string;
  end: string;
  location: string;
  achievements: string[];
}

export interface EducationData {
  institution: string;
  degree: string;
  start: string;
  end: string;
  location: string;
}

export interface ProfileData {
  name: string;
  headline: string;
  email: string;
  phone: string;
  address: string;
  postal_code: string;
  city: string;
  country: string;
  linkedin: string;
  github: string;
  website: string;
  summary: string;
  motivation: string;
  skills: string[];
  soft_skills: string[];
  experience: ExperienceData[];
  education: EducationData[];
  languages: string[];
  certifications: string[];
  earliest_start: string;
  notice_period_months: number;
  confidential_application: boolean;
  cv_text: string;
}

export interface ProfileStatus {
  ready: boolean;
  name: string | null;
  path: string;
  issues: string[];
  profile: ProfileData | null;
}

export interface CvImportResponse {
  profile: ProfileData;
  filled: string[];
}
