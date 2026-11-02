export type AgentId =
  | "coordinator"
  | "linkedin"
  | "indeed"
  | "boards"
  | "verifier"
  | "analyst"
  | "registrar"
  | "writer";

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
  source_results: Record<string, SourceResult>;
  sheet_url: string | null;
  sink_kind: string | null;
  error: string | null;
  last_seq: number;
}

export type SourceStatus =
  | "ok"
  | "empty"
  | "blocked"
  | "login_required"
  | "layout_changed"
  | "error";

export interface SourceResult {
  status: SourceStatus;
  message: string | null;
  pages_fetched: number;
  jobs: number;
  ts?: number | null;
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
  has_cv_tex?: boolean;
  has_cv_pdf?: boolean;
  cv_urls?: { tex: string | null; pdf: string | null; json: string | null };
  has_description: boolean;
  description_url: string | null;
  applied?: boolean;
  canonical_url?: string | null;
  requisition_id?: string | null;
  status?: JobLivenessStatus;
  expired_reason?: string | null;
  liveness_checked_at?: string | null;
  liveness_attempts?: number;
  deadline?: string | null;
  deadline_status?: DeadlineStatus;
  rank_score?: number | null;
  rank_verdict?: string | null;
  ranked_at?: string | null;
  urls: {
    tex: string | null;
    pdf: string | null;
  };
}

export type JobLivenessStatus = "active" | "expired" | "uncertain" | "unknown";

export type DeadlineStatus =
  | "active"
  | "closing_soon"
  | "expired"
  | "unparseable"
  | "unknown";

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
  blocked: boolean;
  blockers: string[];
  ats_score: number | null;
  ats_grade: string | null;
}

export interface CoverLetterResponse {
  letter: CoverLetter;
  urls: {
    tex: string | null;
    pdf: string | null;
  };
}

export interface CvBuild {
  key: string;
  verdict: string;
  blocked: boolean;
  blockers: string[];
  warnings: string[];
  gaps: { term: string; priority?: string }[];
  coverage_pct: number;
  tex_path: string | null;
  pdf_path: string | null;
  pdf_error: string | null;
  pages: number | null;
  ats_score: number | null;
  ats_grade: string | null;
  reused: boolean;
  llm_used: boolean;
}

export interface CvBuildResponse {
  cv: CvBuild;
  urls: { tex: string | null; pdf: string | null; json: string | null };
}

export interface CvBatchItem {
  source: string;
  job_id: string;
  company: string;
  title: string;
  key: string | null;
  status: "built" | "reused" | "blocked" | "error";
  verdict: string | null;
  coverage_pct: number | null;
  has_pdf: boolean;
  error: string | null;
}

export interface CvBatchSnapshot {
  running: boolean;
  cancelled: boolean;
  total: number;
  done: number;
  current: string | null;
  results: CvBatchItem[];
  error: string | null;
  started_at: number | null;
  finished_at: number | null;
}

export interface CvBatchStartResponse {
  status: "started" | "empty";
  queued: {
    source: string;
    job_id: string;
    company: string;
    title: string;
    rank_score: number | null;
  }[];
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
