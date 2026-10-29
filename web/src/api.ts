import type {
  AgentLogData,
  CoverLetterResponse,
  CvImportResponse,
  JobListing,
  ProfileData,
  ProfileStatus,
  SnapshotEvent,
} from "./types";

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = (await response.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      // Keep the status text.
    }
    throw new Error(detail);
  }
  return (await response.json()) as T;
}

export function getState(): Promise<SnapshotEvent> {
  return request<SnapshotEvent>("/api/state");
}

export function startRun(
  sources: string[],
  cached: boolean,
  query?: string,
): Promise<SnapshotEvent> {
  return request<SnapshotEvent>("/api/runs", {
    method: "POST",
    body: JSON.stringify({ sources, cached, query: query || null }),
  });
}

export function controlRun(
  runId: string,
  action: "pause" | "resume" | "cancel",
): Promise<SnapshotEvent> {
  return request<SnapshotEvent>(`/api/runs/${runId}/${action}`, {
    method: "POST",
  });
}

export function getJobs(limit = 100): Promise<{ jobs: JobListing[] }> {
  return request<{ jobs: JobListing[] }>(`/api/jobs?limit=${limit}`);
}

export function saveJobDescription(
  source: string,
  jobId: string,
  refresh = false,
): Promise<{
  saved: boolean;
  cached: boolean;
  characters: number;
  url: string;
  sheet_saved?: boolean;
  path?: string | null;
}> {
  return request(`/api/jobs/${source}/${jobId}/description?refresh=${refresh ? "true" : "false"}`, {
    method: "POST",
  });
}

export function generateCoverLetter(
  source: string,
  jobId: string,
  force = false,
  customData?: { company?: string; title?: string; description?: string },
): Promise<CoverLetterResponse> {
  return request<CoverLetterResponse>("/api/cover-letters", {
    method: "POST",
    body: JSON.stringify({
      source,
      job_id: jobId,
      force,
      company: customData?.company,
      title: customData?.title,
      description: customData?.description,
    }),
  });
}


export function getProfileStatus(): Promise<ProfileStatus> {
  return request<ProfileStatus>("/api/profile");
}

export function saveProfile(profile: ProfileData): Promise<ProfileStatus> {
  return request<ProfileStatus>("/api/profile", {
    method: "PUT",
    body: JSON.stringify(profile),
  });
}

export async function importProfileFromCv(file: File): Promise<CvImportResponse> {
  const body = new FormData();
  body.append("file", file);
  const response = await fetch("/api/profile/import", {
    method: "POST",
    body,
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const payload = (await response.json()) as { detail?: string };
      if (payload.detail) detail = payload.detail;
    } catch {
      // Keep the status text.
    }
    throw new Error(detail);
  }
  return (await response.json()) as CvImportResponse;
}

export function fetchAgentLogs(agentId: string): Promise<AgentLogData> {
  return request<AgentLogData>(`/api/agents/${agentId}/logs`);
}
