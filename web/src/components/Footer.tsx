import { useEffect, useState } from "react";
import type { RunStatus, Stats } from "../types";

interface FooterProps {
  status: RunStatus;
  stats: Stats;
  elapsed: string;
  sinkKind: string | null;
}

export function Footer({ status, stats, elapsed, sinkKind }: FooterProps) {
  const [clock, setClock] = useState(() => formatClock(new Date()));

  useEffect(() => {
    const timer = window.setInterval(
      () => setClock(formatClock(new Date())),
      1000,
    );
    return () => window.clearInterval(timer);
  }, []);

  return (
    <footer className="footer">
      <div className="footer-main">
        <span>JOB SCOUT</span>
        <span className="footer-sep">/</span>
        <span>{sinkKind === "google" ? "GOOGLE SHEETS" : "LOCAL ONLY"}</span>
      </div>
      <div className="footer-meta">
        <span>4 AGENTS</span>
        <span className="footer-sep">/</span>
        <span>LINKEDIN + INDEED</span>
        <span className="footer-sep">/</span>
        <span>
          {stats.new} NEW · {stats.found} FOUND
        </span>
        <span className="footer-sep">/</span>
        <span>{status.toUpperCase()}</span>
        <span className="footer-sep">/</span>
        <span>{elapsed}</span>
      </div>
      <div className="footer-clock" aria-label="Local time">
        LOCAL {clock}
      </div>
    </footer>
  );
}

function formatClock(date: Date): string {
  return date.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}
