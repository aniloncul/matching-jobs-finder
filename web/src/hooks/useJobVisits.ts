import { useCallback, useEffect, useState } from "react";

const STORAGE_KEY = "jobscout:job-visits:v1";

export interface JobVisit {
  count: number;
  lastVisited: string;
}

type VisitMap = Record<string, JobVisit>;

function readVisits(): VisitMap {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return {};
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      return {};
    }
    const visits: VisitMap = {};
    for (const [key, value] of Object.entries(parsed)) {
      if (!value || typeof value !== "object") continue;
      const record = value as { count?: unknown; lastVisited?: unknown };
      const count = Number(record.count);
      if (!Number.isFinite(count) || count < 1) continue;
      visits[key] = {
        count: Math.floor(count),
        lastVisited:
          typeof record.lastVisited === "string" ? record.lastVisited : "",
      };
    }
    return visits;
  } catch {
    return {};
  }
}

function writeVisits(visits: VisitMap): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(visits));
  } catch {
    // Storage can be disabled; the in-memory count still works.
  }
}

export function useJobVisits() {
  const [visits, setVisits] = useState<VisitMap>(readVisits);

  useEffect(() => {
    writeVisits(visits);
  }, [visits]);

  useEffect(() => {
    function sync(event: StorageEvent) {
      if (event.key === STORAGE_KEY) setVisits(readVisits());
    }
    window.addEventListener("storage", sync);
    return () => window.removeEventListener("storage", sync);
  }, []);

  const recordVisit = useCallback((key: string) => {
    setVisits((previous) => {
      const current = previous[key];
      return {
        ...previous,
        [key]: {
          count: (current?.count ?? 0) + 1,
          lastVisited: new Date().toISOString(),
        },
      };
    });
  }, []);

  return { visits, recordVisit };
}

export function formatVisitTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}
