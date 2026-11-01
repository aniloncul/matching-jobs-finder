import type { ReactNode } from "react";
import type { AgentId } from "../types";

interface AvatarProps {
  id: AgentId;
}

function Frame({ children }: { children: ReactNode }) {
  return (
    <svg
      viewBox="0 0 64 64"
      className="avatar-svg"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="square"
      strokeLinejoin="miter"
      aria-hidden="true"
      focusable="false"
    >
      {children}
    </svg>
  );
}

export function AgentAvatar({ id }: AvatarProps) {
  if (id === "coordinator") {
    return (
      <Frame>
        <path d="M32 16V8" />
        <rect x="27" y="3" width="10" height="4" fill="currentColor" />
        <rect x="12" y="16" width="40" height="28" />
        <rect x="18" y="22" width="6" height="6" fill="currentColor" />
        <rect x="40" y="22" width="6" height="6" fill="currentColor" />
        <path d="M22 37H42M18 44H46M22 50H42" />
        <path d="M6 24H10M6 30H10M54 24H58M54 30H58" />
        <rect x="18" y="44" width="28" height="10" />
      </Frame>
    );
  }

  if (id === "writer") {
    return (
      <Frame>
        <path d="M14 12H42L52 22V52H14Z" />
        <path d="M42 12V22H52" />
        <path d="M21 30H43M21 37H43M21 44H35" />
        <path d="M48 44L54 50L46 56L40 57L41 51Z" fill="var(--white)" />
      </Frame>
    );
  }

  if (id === "registrar") {
    return (
      <Frame>
        <rect x="12" y="14" width="40" height="30" />
        <rect x="18" y="20" width="6" height="6" fill="currentColor" />
        <rect x="40" y="20" width="6" height="6" fill="currentColor" />
        <path d="M22 34H42" />
        <path d="M18 44H46" />
        <rect x="32" y="40" width="22" height="20" fill="var(--white)" />
        <path d="M37 45H49M37 50H45M37 55H42" />
        <path d="M47 54L49 56L53 51" />
      </Frame>
    );
  }

  if (id === "verifier") {
    return (
      <Frame>
        <path d="M32 6L52 13V28C52 41 43 51 32 57C21 51 12 41 12 28V13Z" />
        <path d="M22 30L29 37L43 22" />
        <path d="M12 45L6 51M52 45L58 51" />
      </Frame>
    );
  }

  if (id === "analyst") {
    return (
      <Frame>
        <rect x="10" y="12" width="44" height="34" />
        <path d="M16 40V30M24 40V24M32 40V33M40 40V21M48 40V27" />
        <path d="M10 46H54" />
        <path d="M32 46V58M22 58H42" />
      </Frame>
    );
  }

  const letter = id === "linkedin" ? "L" : "I";
  return (
    <Frame>
      <rect x="10" y="16" width="44" height="30" />
      <rect x="17" y="22" width="6" height="6" fill="currentColor" />
      <rect x="41" y="22" width="6" height="6" fill="currentColor" />
      <path d="M21 38H43" />
      <rect x="8" y="8" width="12" height="12" />
      <path d="M20 20L27 27M26 24L30 20" />
      <rect x="36" y="40" width="20" height="20" fill="var(--white)" />
      <text
        x="46"
        y="54"
        textAnchor="middle"
        fontFamily="Menlo, monospace"
        fontSize="10"
        fill="currentColor"
        stroke="none"
      >
        {letter}
      </text>
    </Frame>
  );
}
