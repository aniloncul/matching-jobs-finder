import { useEffect, useMemo, useState } from "react";
import {
  buildCv,
  cancelCvBatch,
  checkCvReuse,
  deleteJob,
  generateCoverLetter,
  getCvBatch,
  getJobs,
  getProfileStatus,
  startCvBatch,
  updateJobApplied,
} from "../api";
import { formatVisitTime, useJobVisits } from "../hooks/useJobVisits";
import type { CvBatchSnapshot, JobListing, ProfileStatus } from "../types";
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

  const [cvBusy, setCvBusy] = useState<string | null>(null);
  const [cvStage, setCvStage] = useState("");

  const [batch, setBatch] = useState<CvBatchSnapshot | null>(null);
  const [batchStarting, setBatchStarting] = useState(false);

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

  // Track the CV batch on a steady light poll: it survives a page refresh
  // (state lives on the server) and never misses a batch started after mount.
  useEffect(() => {
    let cancelled = false;
    async function tick() {
      try {
        const snap = await getCvBatch();
        if (cancelled) return;
        setBatch((prev) => {
          const wasRunning = prev?.running ?? false;
          if (wasRunning && !snap.running) void loadJobs();
          return snap;
        });
      } catch {
        // transient poll error; the next tick retries
      }
    }
    void tick();
    const id = window.setInterval(() => void tick(), 2000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
      if (response.letter.blocked) {
        setNotice(
          `Draft blocked by fact check: ${response.letter.company} · ${response.letter.title}. ${response.letter.blockers.join("; ")}`,
        );
      } else {
        const suffix = response.letter.ats_score !== null ? ` · ATS ${response.letter.ats_score}/100 (${response.letter.ats_grade})` : "";
        setNotice(`Cover letter ready (${response.letter.language.toUpperCase()} · DIN 5008): ${response.letter.company} · ${response.letter.title}${suffix}`);
      }
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
      if (response.letter.blocked) {
        parts.push(`PDF blocked: ${response.letter.blockers.join("; ")}`);
      } else if (response.letter.ats_score !== null) {
        parts.push(`ATS ${response.letter.ats_score}/100 (${response.letter.ats_grade})`);
      }
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

  async function handleBuildCv(job: JobListing) {
    const key = jobKey(job);
    if (!profileReady) {
      setNotice("Fill config/profile.yaml with your real details before building a CV.");
      return;
    }
    if (job.has_cv_tex && !window.confirm("This CV already exists. Regenerate and overwrite it?")) {
      return;
    }
    setCvBusy(key);
    setNotice(null);
    setCvStage("Preparing the master CV…");

    // JD-similarity recommendation (career-ops jd-similarity): before building
    // a fresh CV, ask whether an existing tailored CV already covers this
    // posting. Recommendation only - declining falls through to a full build.
    let reuseFrom: string | null = null;
    if (!job.has_cv_tex) {
      setCvStage("Checking similar CVs…");
      try {
        const check = await checkCvReuse(job.source, job.job_id);
        const best = check.best;
        if (best && best.decision !== "regenerate") {
          const pct = Math.round(best.score * 100);
          reuseFrom =
            window.confirm(
              best.decision === "reuse"
                ? `“${best.title}” at ${best.company} is ${pct}% similar to this posting.\n\nReuse its CV for ${job.company} instead of building a new one?`
                : `“${best.title}” at ${best.company} is ${pct}% similar — reusable with small edits.\n\nCopy its CV for ${job.company} now, or build a fresh one?`,
            )
              ? best.key
              : null;
        }
      } catch {
        // The recommendation is optional; a failed check never blocks a build.
      }
    }
    if (reuseFrom) setCvStage("Copying the matching CV…");

    const t1 = setTimeout(() => setCvStage("Tailoring bullets to the job description…"), 3000);
    const t2 = setTimeout(() => setCvStage("Verifying facts and rendering LaTeX…"), 9000);

    try {
      const response = await buildCv(job.source, job.job_id, Boolean(job.has_cv_tex), reuseFrom);
      clearTimeout(t1);
      clearTimeout(t2);
      setJobs((previous) =>
        previous.map((item) =>
          jobKey(item) === key
            ? {
                ...item,
                has_cv_tex: true,
                has_cv_pdf: Boolean(response.cv.pdf_path),
                cv_urls: response.urls,
              }
            : item,
        ),
      );
      const cv = response.cv;
      const parts = [`${job.company} · ${job.title}`];
      if (cv.reused) parts.push(reuseFrom ? `reused CV from ${reuseFrom}` : "existing CV");
      else parts.push(`CV ${cv.verdict.toUpperCase()} · coverage ${cv.coverage_pct}%`);
      if (cv.blocked) parts.push(`PDF blocked: ${cv.blockers.join("; ")}`);
      else if (cv.ats_score !== null) parts.push(`ATS ${cv.ats_score}/100 (${cv.ats_grade})`);
      if (cv.pdf_error && !cv.blocked) parts.push(cv.pdf_error);
      if (cv.gaps.length) parts.push(`${cv.gaps.length} gap(s): ${cv.gaps.slice(0, 3).map((g) => g.term).join(", ")}`);
      setNotice(parts.join(" · "));
    } catch (error) {
      clearTimeout(t1);
      clearTimeout(t2);
      setNotice(error instanceof Error ? error.message : String(error));
    } finally {
      setTimeout(() => {
        setCvBusy(null);
        setCvStage("");
      }, 400);
    }
  }

  async function handleStartBatch() {
    if (!profileReady) {
      setNotice("Fill config/profile.yaml with your real details before building CVs.");
      return;
    }
    setBatchStarting(true);
    setNotice(null);
    try {
      const response = await startCvBatch({ limit: 5 });
      if (response.status === "empty") {
        setNotice("No jobs left to tailor — every top-fit job already has a CV.");
      } else {
        setBatch((prev) => ({
          ...(prev ?? {
            running: true, cancelled: false, total: 0, done: 0,
            current: null, results: [], error: null,
            started_at: null, finished_at: null,
          }),
          running: true,
          total: response.queued.length,
          done: 0,
          results: [],
          cancelled: false,
        }));
        setNotice(
          `CV batch queued for ${response.queued.length} job(s): ` +
          response.queued.map((q) => q.company).join(", "),
        );
      }
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error));
    } finally {
      setBatchStarting(false);
    }
  }

  async function handleCancelBatch() {
    try {
      await cancelCvBatch();
      setNotice("Cancelling the CV batch after the current job…");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error));
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
            className="button cover-batch"
            type="button"
            onClick={() => void handleStartBatch()}
            disabled={loading || busy !== null || batchStarting || batch?.running === true || !profileReady}
            title={!profileReady
              ? "Complete config/profile.yaml first"
              : "Build CVs for the top-fit jobs that do not have one yet"}
          >
            {batch?.running ? `Batch CVs (${batch.done}/${batch.total})` : "Batch CVs"}
          </button>
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

      {batch && (batch.running || batch.results.length > 0) ? (
        <div className="profile-import-progress-card" role="status" aria-live="polite">
          <div className="profile-import-progress-header">
            <span className="profile-import-progress-file">
              🗂 CV BATCH: {batch.done}/{batch.total}
              {batch.cancelled ? " · cancelled" : ""}
            </span>
            <span className="profile-import-progress-pct">
              {batch.total > 0 ? Math.round((batch.done / batch.total) * 100) : 0}%
            </span>
          </div>
          <div className="profile-import-bar-track">
            <div
              className="profile-import-bar-fill"
              style={{ width: `${batch.total > 0 ? (batch.done / batch.total) * 100 : 0}%` }}
            />
          </div>
          <div className="profile-import-status-line">
            <span className="profile-import-pulse-dot" />
            <span className="profile-import-stage-text">
              {batch.running
                ? batch.current ?? "Starting…"
                : batch.results
                    .map((r) => `${r.company}: ${r.status}`)
                    .join(" · ") || "Idle"}
            </span>
            {batch.running ? (
              <button className="button cover-cancel-batch" type="button" onClick={() => void handleCancelBatch()}>
                Cancel
              </button>
            ) : null}
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
                  {job.cv_urls?.tex ? (
                    <a
                      className="cover-link cover-link-cv"
                      href={job.cv_urls.tex}
                      target="_blank"
                      rel="noreferrer"
                      title="Open the tailored CV LaTeX source"
                    >
                      CV TEX
                    </a>
                  ) : null}
                  {job.cv_urls?.pdf ? (
                    <a
                      className="cover-link cover-link-cv"
                      href={job.cv_urls.pdf}
                      target="_blank"
                      rel="noreferrer"
                      title="Open the tailored CV PDF"
                    >
                      CV PDF
                    </a>
                  ) : null}
                  <button
                    className="button cover-generate"
                    type="button"
                    onClick={() => void handleGenerate(job)}
                    disabled={busy !== null || cvBusy !== null || batch?.running === true || !profileReady}
                    title={!profileReady ? "Complete config/profile.yaml first" : undefined}
                  >
                    {isBusy ? `Writing… (${genProgress}%)` : job.has_tex ? "Regenerate" : "Generate"}
                  </button>
                  <button
                    className="button cover-cv"
                    type="button"
                    onClick={() => void handleBuildCv(job)}
                    disabled={busy !== null || cvBusy !== null || batch?.running === true || !profileReady}
                    title={!profileReady ? "Complete config/profile.yaml first" : "Build a CV tailored to this job"}
                  >
                    {cvBusy === key ? cvStage || "Building CV…" : job.has_cv_tex ? "Rebuild CV" : "Build CV"}
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
                  {job.deadline ? (
                    <span
                      className="cover-badge"
                      data-state={job.deadline_status ?? "unknown"}
                      title={`Deadline ${job.deadline}`}
                    >
                      ⏳ {job.deadline}
                      {job.deadline_status === "closing_soon" ? " · closing soon" : ""}
                    </span>
                  ) : null}
                  {typeof job.rank_score === "number" ? (
                    <span
                      className="cover-badge"
                      data-state={job.rank_verdict === "Veto" ? "veto" : "ranked"}
                      title={job.rank_verdict ?? "Fit score"}
                    >
                      ★ {job.rank_score}
                    </span>
                  ) : null}
                  {job.status === "expired" ? (
                    <span
                      className="cover-badge"
                      data-state="expired"
                      title={job.expired_reason ?? "Posting is closed"}
                    >
                      ✕ expired
                    </span>
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
