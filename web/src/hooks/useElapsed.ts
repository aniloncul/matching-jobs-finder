import { useEffect, useState } from "react";
import type { RunStatus } from "../types";

export function formatDuration(seconds: number): string {
  const safe = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(safe / 3600);
  const minutes = Math.floor((safe % 3600) / 60);
  const secs = safe % 60;
  return [hours, minutes, secs]
    .map((part) => String(part).padStart(2, "0"))
    .join(":");
}

export function useElapsed(
  startedAt: number | null,
  finishedAt: number | null,
  status: RunStatus,
): string {
  const [now, setNow] = useState(() => Date.now() / 1000);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => window.clearInterval(timer);
  }, []);

  if (!startedAt) return "00:00:00";
  const running =
    status === "running" || status === "paused" || status === "cancelling";
  const end = finishedAt ?? (running ? now : startedAt);
  return formatDuration(end - startedAt);
}
