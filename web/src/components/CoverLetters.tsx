import { useEffect, useMemo, useState } from "react";
import {
  deleteJob,
  generateCoverLetter,
  getJobs,
  getProfileStatus,
  updateJobApplied,
} from "../api";
import { formatVisitTime, useJobVisits } from "../hooks/useJobVisits";
import type { JobListing, ProfileStatus } from "../types";
import { SourceIcon } from "./SourceIcon";

function jobKey(job: JobListing): string {
  return `${job.source}:${job.job_id}`;
}

export function CoverLetters() {
  const [jobs, setJobs] = useState<JobListing[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [togglingApplied, setTogglingApplied] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<"all" | "will_apply" | "applied">("will_apply");
  const [notice, setNotice] = useState<string | null>(null);
  const { visits, recordVisit, latestVisitedKey } = useJobVisits();
  const [profile, setProfile] = useState<ProfileStatus | null>(null);
  const [query, setQuery] = useState("");

  const [genProgress, setGenProgress] = useState(0);
  const [genStage, setGenStage] = useState("");
  const [genTarget, setGenTarget] = useState("");

  const [showCustom, setShowCustom] = useState(false);
  const [customCompany, setCustomCompany] = useState("");
  const [customTitle, setCustomTitle] = useState("");
  const [customDescription, setCustomDescription] = useState("");
  const [customSubmitting, setCustomSubmitting] = useState(false);

  async function loadJobs() {
    setLoading(true);
    setNotice(null);
    try {
      const [payload, profilePayload] = await Promise.all([
        getJobs(200),
        getProfileStatus(),
      ]);
      setJobs(payload.jobs);
      setProfile(profilePayload);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadJobs();
    const refresh = (event: Event) => {
      const detail = (event as CustomEvent<ProfileStatus>).detail;
      if (detail) setProfile(detail);
    };
    window.addEventListener("jobscout:profile-updated", refresh);
    return () => window.removeEventListener("jobscout:profile-updated", refresh);
  }, []);

  const willApplyCount = useMemo(
    () => jobs.filter((job) => !job.applied).length,
    [jobs],
  );

  const appliedCount = useMemo(
    () => jobs.filter((job) => Boolean(job.applied)).length,
    [jobs],
  );

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return jobs.filter((job) => {
      if (statusFilter === "will_apply" && job.applied) return false;
      if (statusFilter === "applied" && !job.applied) return false;
      if (!needle) return true;
      return [job.company, job.title, job.location ?? "", job.search_query]
        .join(" ")
        .toLowerCase()
        .includes(needle);
    });
  }, [jobs, query, statusFilter]);

  const profileReady = profile?.ready === true;

  async function handleCustomGenerate(e: React.FormEvent) {
    e.preventDefault();
    if (!profileReady) {
      setNotice("Fill config/profile.yaml with your real details before generating.");
      return;
    }
    if (!customCompany.trim() || !customTitle.trim() || !customDescription.trim()) {
      setNotice("Please provide the company name, job title, and job description.");
      return;
    }
    const cleanId = `custom-${Date.now()}`;
    setCustomSubmitting(true);
    setNotice(null);
    setGenProgress(20);
    setGenStage("Parsing job requirements...");
    setGenTarget(`${customCompany.trim()} · ${customTitle.trim()}`);

    const t1 = setTimeout(() => {
      setGenProgress(50);
      setGenStage("Semantic matching: Selecting best CV achievements...");
    }, 1500);

    const t2 = setTimeout(() => {
      setGenProgress(75);
      setGenStage("AI generation: Drafting DIN 5008 cover letter...");
    }, 3500);

    const t3 = setTimeout(() => {
      setGenProgress(90);
      setGenStage("Auditing facts & compiling LaTeX PDF...");
    }, 7000);

    try {
      const response = await generateCoverLetter("custom", cleanId, true, {
        company: customCompany.trim(),
        title: customTitle.trim(),
        description: customDescription.trim(),
      });
      clearTimeout(t1);
      clearTimeout(t2);
      clearTimeout(t3);
      setGenProgress(100);
      setGenStage("Complete!");
      const newJob: JobListing = {
        source: "custom",
        job_id: cleanId,
        company: response.letter.company,
        title: response.letter.title,
        link: "",
        posted_date: new Date().toLocaleDateString("de-DE"),
        location: "Custom Application",
        search_query: "custom",
        scraped_at: new Date().toISOString(),
        has_tex: true,
        has_pdf: Boolean(response.letter.pdf_path),
        has_description: false,
        description_url: null,
        urls: response.urls,
      };
      setJobs((previous) => [newJob, ...previous]);
      setNotice(`Cover letter ready (${response.letter.language.toUpperCase()} · DIN 5008): ${response.letter.company} · ${response.letter.title}`);
      setShowCustom(false);
      setCustomCompany("");
      setCustomTitle("");
      setCustomDescription("");
    } catch (error) {
      clearTimeout(t1);
      clearTimeout(t2);
      clearTimeout(t3);
      setNotice(error instanceof Error ? error.message : String(error));
    } finally {
      setTimeout(() => {
        setCustomSubmitting(false);
        setGenProgress(0);
      }, 600);
    }
  }

  async function handleGenerate(job: JobListing) {
    const key = jobKey(job);
    if (!profileReady) {
      setNotice("Fill config/profile.yaml with your real details before generating.");
      return;
    }
    if (job.has_tex && !window.confirm("This letter already exists. Regenerate and overwrite it?")) {
      return;
    }
    setBusy(key);
    setNotice(null);
    setGenProgress(15);
    setGenStage("Fetching job description via browser...");
    setGenTarget(`${job.company} · ${job.title}`);

    const t1 = setTimeout(() => {
      setGenProgress(40);
      setGenStage("Matching CV bullets against job requirements...");
    }, 2000);

    const t2 = setTimeout(() => {
      setGenProgress(70);
      setGenStage("AI drafting DIN 5008 Anschreiben...");
    }, 5000);

    const t3 = setTimeout(() => {
      setGenProgress(90);
      setGenStage("Fact verification & compiling LaTeX PDF...");
    }, 11000);

    try {
      const response = await generateCoverLetter(job.source, job.job_id, job.has_tex);
      clearTimeout(t1);
      clearTimeout(t2);
      clearTimeout(t3);
      setGenProgress(100);
      setGenStage("Complete!");
      setJobs((previous) =>
        previous.map((item) =>
          jobKey(item) === key
            ? {
                ...item,
                has_tex: true,
                has_pdf: Boolean(response.letter.pdf_path),
                has_description:
                  job.source === "custom" ? item.has_description : true,
                description_url:
                  job.source === "custom"
                    ? item.description_url
                    : `/api/jobs/${job.source}/${job.job_id}/description`,
                urls: response.urls,
              }
            : item,
        ),
      );
      const parts = [
        `${response.letter.company} · ${response.letter.title}`,
        response.letter.reused ? "existing letter" : `letter ready (${response.letter.language.toUpperCase()})`,
      ];
      if (response.letter.warning) parts.push(response.letter.warning);
      if (response.letter.pdf_error) parts.push(response.letter.pdf_error);
      setNotice(parts.join(" · "));
    } catch (error) {
      clearTimeout(t1);
      clearTimeout(t2);
      clearTimeout(t3);
      setNotice(error instanceof Error ? error.message : String(error));
    } finally {
      setTimeout(() => {
        setBusy(null);
        setGenProgress(0);
      }, 600);
    }
  }

  async function handleDelete(job: JobListing) {
    const key = jobKey(job);
    const confirmMsg = `Delete "${job.title}" at "${job.company}"?\n\nThis will remove the row from Google Sheets and permanently exclude it from future searches.`;
    if (!window.confirm(confirmMsg)) return;

    setDeleting(key);
    setNotice(null);
    try {
      await deleteJob(job.source, job.job_id);
      setJobs((prev) => prev.filter((item) => jobKey(item) !== key));
      setNotice(`Deleted: ${job.company} · ${job.title} (removed from Google Sheets & permanently excluded)`);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error));
    } finally {
      setDeleting(null);
    }
  }

  async function handleToggleApplied(job: JobListing) {
    const nextApplied = !job.applied;
    const key = jobKey(job);
    setTogglingApplied(key);
    // Optimistic local state update
    setJobs((prev) =>
      prev.map((item) =>
        jobKey(item) === key ? { ...item, applied: nextApplied } : item,
      ),
    );
    try {
      await updateJobApplied(job.source, job.job_id, nextApplied);
      setNotice(
        `Updated: ${job.company} · ${job.title} → ${nextApplied ? "Applied" : "Will Apply"} (saved to Google Sheets)`,
      );
    } catch (error) {
      // Revert if error
      setJobs((prev) =>
        prev.map((item) =>
          jobKey(item) === key ? { ...item, applied: !nextApplied } : item,
        ),
      );
      setNotice(error instanceof Error ? error.message : String(error));
    } finally {
      setTogglingApplied(null);
    }
  }



  return (
    <section className="cover-letters" aria-labelledby="cover-letters-title">
      <div className="cover-heading">
        <div className="cover-heading-left">
          <h2 id="cover-letters-title" className="cover-title">
            Job Offers
          </h2>
          <div className="cover-filter-toggle" role="group" aria-label="Filter by application status">
            <button
              type="button"
              className={`cover-filter-btn ${statusFilter === "will_apply" ? "active" : ""}`}
              onClick={() => setStatusFilter((prev) => prev === "will_apply" ? "all" : "will_apply")}
              title="Show offers you plan to apply to"
            >
              Will Apply ({willApplyCount})
            </button>
            <button
              type="button"
              className={`cover-filter-btn ${statusFilter === "applied" ? "active" : ""}`}
              onClick={() => setStatusFilter((prev) => prev === "applied" ? "all" : "applied")}
              title="Show offers you already applied to"
            >
              ✓ Applied ({appliedCount})
            </button>
          </div>
        </div>
        <div className="cover-controls">
          <input
            className="query-input cover-search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Filter company, title, location"
            aria-label="Filter jobs"
          />
          <button
            className="button"
            type="button"
            onClick={() => setShowCustom((prev) => !prev)}
            disabled={loading || busy !== null}
          >
            {showCustom ? "Close Editor" : "+ Custom Job Letter"}
          </button>
          <button
            className="button"
            type="button"
            onClick={() => void loadJobs()}
            disabled={loading || busy !== null}
          >
            Refresh
          </button>
        </div>
      </div>

      {showCustom ? (
        <form className="cover-custom-panel" onSubmit={handleCustomGenerate}>
          <div className="cover-custom-header">
            <h3 className="cover-custom-title">
              DIN 5008 Cover Letter Editor (Şirkete & İlana Özel Anschreiben)
            </h3>
            <button
              type="button"
              className="button"
              onClick={() => setShowCustom(false)}
            >
              Close
            </button>
          </div>
          <p style={{ opacity: 0.75, fontSize: "0.85rem", margin: 0 }}>
            İlanın şirket adını, pozisyonunu ve iş tanımını/gereksinimlerini yapıştırın. Sistem profilinizdeki deneyimleri bu gereksinimlere tam uyarlar, eşleşenleri öne çıkarır ve büyük tecrübe farklarında yüksek motivasyon & hızlı öğrenme kabiliyetinizi (DIN 5008 standartlarında) mektuba işler.
          </p>
          <div className="cover-custom-row">
            <div>
              <label style={{ display: "block", marginBottom: "4px", fontSize: "0.8rem", fontWeight: 600 }}>
                Unternehmen (Şirket Adı)
              </label>
              <input
                className="query-input"
                style={{ width: "100%" }}
                value={customCompany}
                onChange={(e) => setCustomCompany(e.target.value)}
                placeholder="Örn: Creative Pool Marketing-Agentur GmbH"
                required
              />
            </div>
            <div>
              <label style={{ display: "block", marginBottom: "4px", fontSize: "0.8rem", fontWeight: 600 }}>
                Position / Rolle (Pozisyon Başlığı)
              </label>
              <input
                className="query-input"
                style={{ width: "100%" }}
                value={customTitle}
                onChange={(e) => setCustomTitle(e.target.value)}
                placeholder="Örn: Kauffrau für Dialogmarketing / AI Engineer"
                required
              />
            </div>
          </div>
          <div>
            <label style={{ display: "block", marginBottom: "4px", fontSize: "0.8rem", fontWeight: 600 }}>
              Stellenbeschreibung & Anforderungen (İş Tanımı ve Gereksinimler)
            </label>
            <textarea
              className="cover-custom-textarea"
              style={{ width: "100%" }}
              value={customDescription}
              onChange={(e) => setCustomDescription(e.target.value)}
              placeholder="İlandaki 'Aufgaben', 'Dein Profil', 'Anforderungen' metnini buraya yapıştırın..."
              required
            />
          </div>
          <div className="cover-custom-actions">
            <button
              className="button"
              type="submit"
              disabled={customSubmitting || !profileReady}
              title={!profileReady ? "Complete config/profile.yaml first" : undefined}
            >
              {customSubmitting ? "Writing DIN 5008 Letter…" : "Generate Tailored Letter"}
            </button>
          </div>
        </form>
      ) : null}

      {profile && !profile.ready ? (
        <div className="cover-warning" role="alert">
          <p className="cover-warning-title">PROFILE REQUIRED</p>
          <p>
            Fill <code>config/profile.yaml</code> with your real details before
            generating a cover letter. The file is read for every generation.
          </p>
          <ul>
            {profile.issues.map((issue) => (
              <li key={issue}>{issue}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {(busy !== null || customSubmitting) ? (
        <div className="profile-import-progress-card" role="status" aria-live="polite">
          <div className="profile-import-progress-header">
            <span className="profile-import-progress-file">⚡ GENERATING LETTER: {genTarget}</span>
            <span className="profile-import-progress-pct">{genProgress}%</span>
          </div>
          <div className="profile-import-bar-track">
            <div
              className="profile-import-bar-fill"
              style={{ width: `${genProgress}%` }}
            />
          </div>
          <div className="profile-import-status-line">
            <span className="profile-import-pulse-dot" />
            <span className="profile-import-stage-text">{genStage}</span>
          </div>
        </div>
      ) : null}

      {notice ? (
        <p className="cover-notice" role="status">
          {notice}
        </p>
      ) : null}

      <div className="cover-list">
        {loading ? <p className="cover-empty">Loading jobs…</p> : null}
        {!loading && filtered.length === 0 ? (
          <p className="cover-empty">No jobs found.</p>
        ) : null}
        {filtered.map((job) => {
          const key = jobKey(job);
          const isBusy = busy === key;
          const isLatestVisited = key === latestVisitedKey;
          return (
            <article
              className={`cover-row ${isLatestVisited ? "cover-row-latest-visited" : ""}`}
              data-latest-visited={isLatestVisited ? "true" : undefined}
              key={key}
            >
              <div className="cover-row-main">
                <div className="cover-job">
                  <p className="cover-meta">
                    {job.source.toUpperCase()}
                    {job.posted_date ? ` · ${job.posted_date}` : ""}
                    {job.location ? ` · ${job.location}` : ""}
                  </p>
                  <h3 className="cover-company">{job.company}</h3>
                  <p className="cover-role">{job.title}</p>
                </div>
                <div className="cover-actions">
                  <button
                    className={`button cover-status-btn ${job.applied ? "is-applied" : "is-will-apply"}`}
                    type="button"
                    onClick={() => void handleToggleApplied(job)}
                    disabled={togglingApplied === key}
                    title={
                      job.applied
                        ? "Applied (click to unmark)"
                        : "Click to mark as Applied"
                    }
                  >
                    {togglingApplied === key ? "Saving…" : job.applied ? "✓ Applied" : "+ Mark Applied"}
                  </button>
                  {job.urls.tex ? (
                    <a
                      className="cover-link"
                      href={job.urls.tex}
                      target="_blank"
                      rel="noreferrer"
                      title="Open the editable LaTeX source"
                    >
                      TEX
                    </a>
                  ) : null}
                  {job.urls.pdf ? (
                    <a className="cover-link" href={job.urls.pdf} target="_blank" rel="noreferrer">
                      PDF
                    </a>
                  ) : null}
                  <button
                    className="button cover-generate"
                    type="button"
                    onClick={() => void handleGenerate(job)}
                    disabled={busy !== null || !profileReady}
                    title={!profileReady ? "Complete config/profile.yaml first" : undefined}
                  >
                    {isBusy ? `Writing… (${genProgress}%)` : job.has_tex ? "Regenerate" : "Generate"}
                  </button>
                  <button
                    className="button cover-delete"
                    type="button"
                    onClick={() => void handleDelete(job)}
                    disabled={deleting !== null || busy !== null}
                    title="Delete offer from Google Sheets and exclude from future runs"
                  >
                    {deleting === key ? "Deleting…" : "Delete"}
                  </button>
                </div>
              </div>
              <div className="cover-row-footer">
                <div className="cover-row-footer-left">
                  {job.link ? (
                    <a
                      className="cover-offer"
                      href={job.link}
                      target="_blank"
                      rel="noopener noreferrer"
                      onClick={() => recordVisit(key)}
                      aria-label={`Visit job offer on ${job.source}`}
                    >
                      <SourceIcon source={job.source} />
                      <span>Visit the page ↗</span>
                    </a>
                  ) : null}
                </div>

                {visits[key] ? (
                  <span
                    className="cover-visit"
                    title={
                      visits[key].lastVisited
                        ? `Last opened ${formatVisitTime(visits[key].lastVisited)}`
                        : undefined
                    }
                  >
                    Visited {visits[key].count}×
                  </span>
                ) : null}
              </div>
            </article>
          );
        })}
      </div>
    </section>
  );
}
