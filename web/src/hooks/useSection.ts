import { useCallback, useEffect, useState } from "react";

export type Section = "run" | "cover-letters" | "profile";

export const SECTIONS: { id: Section; label: string }[] = [
  { id: "run", label: "RUN" },
  { id: "cover-letters", label: "COVER LETTERS" },
  { id: "profile", label: "APPLICANT PROFILE" },
];

const HASH_TO_SECTION: Record<string, Section> = {
  "#run": "run",
  "#cover-letters": "cover-letters",
  "#profile": "profile",
};

const SECTION_TO_HASH: Record<Section, string> = {
  run: "#run",
  "cover-letters": "#cover-letters",
  profile: "#profile",
};

function readHash(): Section {
  return HASH_TO_SECTION[window.location.hash] ?? "run";
}

/**
 * Keeps the visible section in sync with the location hash so a section can be
 * bookmarked, shared, and restored on reload.
 */
export function useSection(): [Section, (section: Section) => void] {
  const [section, setSection] = useState<Section>(readHash);

  useEffect(() => {
    const onHashChange = () => setSection(readHash());
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  const select = useCallback((next: Section) => {
    setSection(next);
    if (window.location.hash !== SECTION_TO_HASH[next]) {
      window.location.hash = SECTION_TO_HASH[next];
    }
    window.scrollTo({ top: 0, behavior: "smooth" });
  }, []);

  return [section, select];
}
