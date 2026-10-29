import { SECTIONS, type Section } from "../hooks/useSection";
import { type Theme } from "../hooks/useTheme";

interface TopBarProps {
  sheetUrl: string | null;
  section: Section;
  onSectionChange: (section: Section) => void;
  theme: Theme;
  onThemeToggle: () => void;
}

export function TopBar({
  sheetUrl,
  section,
  onSectionChange,
  theme,
  onThemeToggle,
}: TopBarProps) {
  return (
    <header className="topbar">
      <div className="brand">
        <span className="brand-mark" aria-hidden="true">
          JS
        </span>
        <span className="brand-name">JOB SCOUT</span>
        <span className="brand-sub">AGENT CONTROL ROOM</span>
      </div>

      <nav className="sectionnav" aria-label="Sections">
        {SECTIONS.map((item) => (
          <button
            className="sectionnav-item"
            data-active={section === item.id}
            type="button"
            key={item.id}
            aria-current={section === item.id ? "page" : undefined}
            onClick={() => onSectionChange(item.id)}
          >
            {item.label}
          </button>
        ))}
      </nav>

      <div className="topbar-right">
        {sheetUrl ? (
          <a
            className="topnav-link"
            href={sheetUrl}
            target="_blank"
            rel="noreferrer"
          >
            SHEET ↗
          </a>
        ) : null}
        <button
          className="theme-toggle"
          type="button"
          role="switch"
          aria-checked={theme === "dark"}
          aria-label="Dark theme"
          onClick={onThemeToggle}
        >
          <span className="theme-toggle-track" aria-hidden="true">
            <span className="theme-toggle-knob" />
          </span>
          <span className="theme-toggle-label">{theme.toUpperCase()}</span>
        </button>
      </div>
    </header>
  );
}
