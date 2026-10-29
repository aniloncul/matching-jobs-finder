import type { Stats } from "../types";

interface StatsBarProps {
  stats: Stats;
}

export function StatsBar({ stats }: StatsBarProps) {
  return (
    <section className="stats-bar" aria-label="Run statistics">
      <Stat label="FOUND" value={stats.found} />
      <Stat label="NEW" value={stats.new} accent={stats.new > 0} />
      <Stat label="SKIPPED" value={stats.skipped} />
      <Stat label="ERRORS" value={stats.errors} invert={stats.errors > 0} />
    </section>
  );
}

function Stat({
  label,
  value,
  accent = false,
  invert = false,
}: {
  label: string;
  value: number;
  accent?: boolean;
  invert?: boolean;
}) {
  const classes = [
    "stat",
    accent ? "stat--accent" : "",
    invert ? "stat--invert" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div className={classes}>
      <span className="stat-value">{String(value).padStart(2, "0")}</span>
      <span className="stat-label">{label}</span>
    </div>
  );
}
