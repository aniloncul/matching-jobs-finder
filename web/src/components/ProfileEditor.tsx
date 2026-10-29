import { useEffect, useRef, useState } from "react";
import { getProfileStatus, importProfileFromCv, saveProfile } from "../api";
import type { EducationData, ExperienceData, ProfileData } from "../types";

const EMPTY_PROFILE: ProfileData = {
  name: "",
  headline: "",
  email: "",
  phone: "",
  address: "",
  postal_code: "",
  city: "",
  country: "",
  linkedin: "",
  github: "",
  website: "",
  summary: "",
  motivation: "",
  skills: [],
  soft_skills: [],
  experience: [],
  education: [],
  languages: [],
  certifications: [],
  earliest_start: "",
  notice_period_months: 0,
  confidential_application: false,
  cv_text: "",
};

function newExperience(): ExperienceData {
  return {
    company: "",
    role: "",
    start: "",
    end: "",
    location: "",
    achievements: [],
  };
}

function newEducation(): EducationData {
  return { institution: "", degree: "", start: "", end: "", location: "" };
}

function normalise(profile: ProfileData | null): ProfileData {
  if (!profile) return { ...EMPTY_PROFILE };
  return {
    ...EMPTY_PROFILE,
    ...profile,
    skills: profile.skills ?? [],
    soft_skills: profile.soft_skills ?? [],
    experience: (profile.experience ?? []).map((item) => ({
      ...newExperience(),
      ...item,
      achievements: item.achievements ?? [],
    })),
    education: (profile.education ?? []).map((item) => ({
      ...newEducation(),
      ...item,
    })),
    languages: profile.languages ?? [],
    certifications: profile.certifications ?? [],
    notice_period_months: profile.notice_period_months ?? 0,
  };
}

function lines(value: string): string[] {
  return value.split("\n");
}

export function ProfileEditor({ alwaysEdit = false }: { alwaysEdit?: boolean }) {
  const [profile, setProfile] = useState<ProfileData | null>(null);
  const [issues, setIssues] = useState<string[]>([]);
  const [ready, setReady] = useState(false);
  const [path, setPath] = useState("config/profile.yaml");
  const [editing, setEditing] = useState(alwaysEdit);
  const [draft, setDraft] = useState<ProfileData>({ ...EMPTY_PROFILE });
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [importing, setImporting] = useState(false);
  const [importProgress, setImportProgress] = useState(0);
  const [importStageText, setImportStageText] = useState("");
  const [importFileName, setImportFileName] = useState("");
  const [importFilled, setImportFilled] = useState<string[]>([]);
  const fileInput = useRef<HTMLInputElement>(null);

  // In the dedicated section the form is always open.
  useEffect(() => {
    if (alwaysEdit) setEditing(true);
  }, [alwaysEdit]);

  async function load() {
    try {
      const status = await getProfileStatus();
      setProfile(status.profile);
      // Keep the form in sync with the file on disk, also when the editor is
      // open on mount instead of being opened by the user.
      setDraft(normalise(status.profile));
      setIssues(status.issues);
      setReady(status.ready);
      setPath(status.path);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    }
  }

  useEffect(() => {
    void load();
  }, []);

  function openEditor() {
    setDraft(normalise(profile));
    setMessage(null);
    setEditing(true);
  }

  function closeEditor() {
    setEditing(false);
    setMessage(null);
  }

  function setField<K extends keyof ProfileData>(key: K, value: ProfileData[K]) {
    setDraft((previous) => ({ ...previous, [key]: value }));
  }

  function setExperience(index: number, patch: Partial<ExperienceData>) {
    setDraft((previous) => ({
      ...previous,
      experience: previous.experience.map((item, itemIndex) =>
        itemIndex === index ? { ...item, ...patch } : item,
      ),
    }));
  }

  function setEducation(index: number, patch: Partial<EducationData>) {
    setDraft((previous) => ({
      ...previous,
      education: previous.education.map((item, itemIndex) =>
        itemIndex === index ? { ...item, ...patch } : item,
      ),
    }));
  }

  async function handleImport(file: File) {
    setImporting(true);
    setMessage(null);
    setImportFilled([]);
    setImportFileName(file.name);
    setImportProgress(20);
    setImportStageText("Reading PDF & extracting text...");

    const t1 = setTimeout(() => {
      setImportProgress(55);
      setImportStageText("Analyzing experience, skills & education...");
    }, 1200);

    const t2 = setTimeout(() => {
      setImportProgress(85);
      setImportStageText("Structuring profile fields...");
    }, 2800);

    try {
      const result = await importProfileFromCv(file);
      clearTimeout(t1);
      clearTimeout(t2);
      setImportProgress(100);
      setImportStageText("Import complete!");
      setDraft(normalise(result.profile));
      setImportFilled(result.filled);
      setEditing(true);
      const count = result.filled.length;
      setMessage(
        count > 0
          ? `✓ Successfully filled ${count} field${count === 1 ? "" : "s"} from ${file.name}. Review the form and save it.`
          : `Nothing new was found in ${file.name}. The form is unchanged.`,
      );
    } catch (error) {
      clearTimeout(t1);
      clearTimeout(t2);
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setTimeout(() => {
        setImporting(false);
        setImportProgress(0);
      }, 700);
      if (fileInput.current) fileInput.current.value = "";
    }
  }

  async function handleSave() {
    setSaving(true);
    setMessage(null);
    try {
      const status = await saveProfile(draft);
      setProfile(status.profile);
      setIssues(status.issues);
      setReady(status.ready);
      setPath(status.path);
      setEditing(false);
      setMessage(status.ready ? "Profile saved." : `Saved, but ${status.issues.length} item(s) still need attention.`);
      window.dispatchEvent(
        new CustomEvent("jobscout:profile-updated", { detail: status }),
      );
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="profile-panel" aria-labelledby="profile-title">
      <div className="profile-heading">
        <div>
          <h2 id="profile-title" className="profile-title">
            Applicant Profile
          </h2>
          <p className="profile-subtitle">
            {ready ? "Ready for cover letters" : `${issues.length} item(s) need attention`} ·{" "}
            {path}
          </p>
        </div>
        {alwaysEdit ? null : (
          <button className="button" type="button" onClick={editing ? closeEditor : openEditor}>
            {editing ? "Close editor" : "Edit profile"}
          </button>
        )}
      </div>

      <div className="profile-import">
        <input
          ref={fileInput}
          className="profile-import-input"
          type="file"
          accept=".pdf,.png,.jpg,.jpeg,.webp,application/pdf,image/png,image/jpeg,image/webp"
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) void handleImport(file);
          }}
        />
        <button
          className="button profile-import-button"
          type="button"
          disabled={importing}
          onClick={() => fileInput.current?.click()}
        >
          {importing ? "Reading CV…" : "Upload CV"}
        </button>
        <p className="profile-import-hint">
          PDF or image · text-first extraction with AI analysis. Existing values are kept.
        </p>
      </div>

      {importing ? (
        <div className="profile-import-progress-card" role="status" aria-live="polite">
          <div className="profile-import-progress-header">
            <span className="profile-import-progress-file">📄 {importFileName}</span>
            <span className="profile-import-progress-pct">{importProgress}%</span>
          </div>
          <div className="profile-import-bar-track">
            <div
              className="profile-import-bar-fill"
              style={{ width: `${importProgress}%` }}
            />
          </div>
          <div className="profile-import-status-line">
            <span className="profile-import-pulse-dot" />
            <span className="profile-import-stage-text">{importStageText}</span>
          </div>
        </div>
      ) : null}

      {importFilled.length > 0 ? (
        <ul className="profile-import-list" aria-label="Fields filled from the CV">
          {importFilled.map((field) => (
            <li key={field}>{field.replace(/_/g, " ")}</li>
          ))}
        </ul>
      ) : null}

      {!editing && issues.length > 0 ? (
        <ul className="profile-issues">
          {issues.map((issue) => (
            <li key={issue}>{issue}</li>
          ))}
        </ul>
      ) : null}

      {message ? (
        <p className="profile-message" role="status">
          {message}
        </p>
      ) : null}

      {editing ? (
        <form
          className="profile-form"
          onSubmit={(event) => {
            event.preventDefault();
            void handleSave();
          }}
        >
          <fieldset className="profile-fieldset">
            <legend className="profile-legend">Contact</legend>
            <div className="profile-grid">
              <label className="profile-field">
                <span>Name</span>
                <input
                  className="profile-input"
                  value={draft.name}
                  onChange={(event) => setField("name", event.target.value)}
                />
              </label>
              <label className="profile-field">
                <span>Headline</span>
                <input
                  className="profile-input"
                  value={draft.headline}
                  onChange={(event) => setField("headline", event.target.value)}
                />
              </label>
              <label className="profile-field">
                <span>Email</span>
                <input
                  className="profile-input"
                  value={draft.email}
                  onChange={(event) => setField("email", event.target.value)}
                />
              </label>
              <label className="profile-field">
                <span>Phone</span>
                <input
                  className="profile-input"
                  value={draft.phone}
                  onChange={(event) => setField("phone", event.target.value)}
                />
              </label>
              <label className="profile-field">
                <span>Address</span>
                <input
                  className="profile-input"
                  value={draft.address}
                  onChange={(event) => setField("address", event.target.value)}
                />
              </label>
              <label className="profile-field">
                <span>Postal code</span>
                <input
                  className="profile-input"
                  value={draft.postal_code}
                  onChange={(event) => setField("postal_code", event.target.value)}
                />
              </label>
              <label className="profile-field">
                <span>City</span>
                <input
                  className="profile-input"
                  value={draft.city}
                  onChange={(event) => setField("city", event.target.value)}
                />
              </label>
              <label className="profile-field">
                <span>Country</span>
                <input
                  className="profile-input"
                  value={draft.country}
                  onChange={(event) => setField("country", event.target.value)}
                />
              </label>
              <label className="profile-field">
                <span>Earliest start date</span>
                <input
                  className="profile-input"
                  type="date"
                  value={draft.earliest_start}
                  onChange={(event) => setField("earliest_start", event.target.value)}
                />
              </label>
              <label className="profile-field">
                <span>Notice period in months</span>
                <input
                  className="profile-input"
                  type="number"
                  min={0}
                  max={12}
                  value={draft.notice_period_months}
                  onChange={(event) =>
                    setField("notice_period_months", Number(event.target.value))
                  }
                />
              </label>
              <label className="profile-field">
                <span>LinkedIn</span>
                <input
                  className="profile-input"
                  value={draft.linkedin}
                  onChange={(event) => setField("linkedin", event.target.value)}
                />
              </label>
              <label className="profile-field">
                <span>GitHub</span>
                <input
                  className="profile-input"
                  value={draft.github}
                  onChange={(event) => setField("github", event.target.value)}
                />
              </label>
              <label className="profile-field">
                <span>Website</span>
                <input
                  className="profile-input"
                  value={draft.website}
                  onChange={(event) => setField("website", event.target.value)}
                />
              </label>
            </div>
          </fieldset>

          <fieldset className="profile-fieldset">
            <legend className="profile-legend">Profile</legend>
            <label className="profile-field">
              <span>Professional summary</span>
              <textarea
                className="profile-input profile-textarea"
                rows={4}
                value={draft.summary}
                onChange={(event) => setField("summary", event.target.value)}
              />
            </label>
            <label className="profile-field">
              <span>
                Motivation — your own argument for this kind of role. 2-4 sentences that make the
                introduction specific instead of generic.
              </span>
              <textarea
                className="profile-input profile-textarea"
                rows={4}
                value={draft.motivation}
                onChange={(event) => setField("motivation", event.target.value)}
              />
            </label>
            <div className="profile-grid">
              <label className="profile-field">
                <span>Skills — one per line</span>
                <textarea
                  className="profile-input profile-textarea"
                  rows={5}
                  value={draft.skills.join("\n")}
                  onChange={(event) => setField("skills", lines(event.target.value))}
                />
              </label>
              <label className="profile-field">
                <span>Soft skills — one per line</span>
                <textarea
                  className="profile-input profile-textarea"
                  rows={5}
                  value={draft.soft_skills.join("\n")}
                  onChange={(event) => setField("soft_skills", lines(event.target.value))}
                />
              </label>
              <label className="profile-field">
                <span>Languages — one per line</span>
                <textarea
                  className="profile-input profile-textarea"
                  rows={5}
                  value={draft.languages.join("\n")}
                  onChange={(event) => setField("languages", lines(event.target.value))}
                />
              </label>
              <label className="profile-field">
                <span>Certifications — one per line</span>
                <textarea
                  className="profile-input profile-textarea"
                  rows={5}
                  value={draft.certifications.join("\n")}
                  onChange={(event) => setField("certifications", lines(event.target.value))}
                />
              </label>
            </div>
            <label className="profile-toggle">
              <input
                type="checkbox"
                checked={draft.confidential_application}
                onChange={(event) =>
                  setField("confidential_application", event.target.checked)
                }
              />
              <span>
                Confidential application — adds a request to the conclusion to handle the
                application confidentially.
              </span>
            </label>
          </fieldset>

          <fieldset className="profile-fieldset">
            <legend className="profile-legend">Experience</legend>
            {draft.experience.map((item, index) => (
              <div className="profile-entry" key={`experience-${index}`}>
                <div className="profile-entry-head">
                  <span>Experience {index + 1}</span>
                  <button
                    className="button button--cancel"
                    type="button"
                    onClick={() =>
                      setField(
                        "experience",
                        draft.experience.filter((_entry, entryIndex) => entryIndex !== index),
                      )
                    }
                  >
                    Remove
                  </button>
                </div>
                <div className="profile-grid">
                  <label className="profile-field">
                    <span>Company</span>
                    <input
                      className="profile-input"
                      value={item.company}
                      onChange={(event) => setExperience(index, { company: event.target.value })}
                    />
                  </label>
                  <label className="profile-field">
                    <span>Role</span>
                    <input
                      className="profile-input"
                      value={item.role}
                      onChange={(event) => setExperience(index, { role: event.target.value })}
                    />
                  </label>
                  <label className="profile-field">
                    <span>Start</span>
                    <input
                      className="profile-input"
                      value={item.start}
                      onChange={(event) => setExperience(index, { start: event.target.value })}
                    />
                  </label>
                  <label className="profile-field">
                    <span>End</span>
                    <input
                      className="profile-input"
                      value={item.end}
                      onChange={(event) => setExperience(index, { end: event.target.value })}
                    />
                  </label>
                  <label className="profile-field">
                    <span>Location</span>
                    <input
                      className="profile-input"
                      value={item.location}
                      onChange={(event) => setExperience(index, { location: event.target.value })}
                    />
                  </label>
                </div>
                <label className="profile-field">
                  <span>Achievements — one per line</span>
                  <textarea
                    className="profile-input profile-textarea"
                    rows={5}
                    value={item.achievements.join("\n")}
                    onChange={(event) =>
                      setExperience(index, { achievements: lines(event.target.value) })
                    }
                  />
                </label>
              </div>
            ))}
            <button
              className="button profile-add"
              type="button"
              onClick={() => setField("experience", [...draft.experience, newExperience()])}
            >
              Add experience
            </button>
          </fieldset>

          <fieldset className="profile-fieldset">
            <legend className="profile-legend">Education</legend>
            {draft.education.map((item, index) => (
              <div className="profile-entry" key={`education-${index}`}>
                <div className="profile-entry-head">
                  <span>Education {index + 1}</span>
                  <button
                    className="button button--cancel"
                    type="button"
                    onClick={() =>
                      setField(
                        "education",
                        draft.education.filter((_entry, entryIndex) => entryIndex !== index),
                      )
                    }
                  >
                    Remove
                  </button>
                </div>
                <div className="profile-grid">
                  <label className="profile-field">
                    <span>Institution</span>
                    <input
                      className="profile-input"
                      value={item.institution}
                      onChange={(event) => setEducation(index, { institution: event.target.value })}
                    />
                  </label>
                  <label className="profile-field">
                    <span>Degree</span>
                    <input
                      className="profile-input"
                      value={item.degree}
                      onChange={(event) => setEducation(index, { degree: event.target.value })}
                    />
                  </label>
                  <label className="profile-field">
                    <span>Start</span>
                    <input
                      className="profile-input"
                      value={item.start}
                      onChange={(event) => setEducation(index, { start: event.target.value })}
                    />
                  </label>
                  <label className="profile-field">
                    <span>End</span>
                    <input
                      className="profile-input"
                      value={item.end}
                      onChange={(event) => setEducation(index, { end: event.target.value })}
                    />
                  </label>
                  <label className="profile-field">
                    <span>Location</span>
                    <input
                      className="profile-input"
                      value={item.location}
                      onChange={(event) => setEducation(index, { location: event.target.value })}
                    />
                  </label>
                </div>
              </div>
            ))}
            <button
              className="button profile-add"
              type="button"
              onClick={() => setField("education", [...draft.education, newEducation()])}
            >
              Add education
            </button>
          </fieldset>

          <fieldset className="profile-fieldset">
            <legend className="profile-legend">CV text</legend>
            <label className="profile-field">
              <span>Paste your CV text here if you do not want to enter every section above.</span>
              <textarea
                className="profile-input profile-textarea"
                rows={12}
                value={draft.cv_text}
                onChange={(event) => setField("cv_text", event.target.value)}
              />
            </label>
          </fieldset>

          <div className="profile-form-actions">
            <button className="button" type="submit" disabled={saving}>
              {saving ? "Saving…" : "Save to config/profile.yaml"}
            </button>
            {alwaysEdit ? null : (
              <button className="button" type="button" onClick={closeEditor} disabled={saving}>
                Cancel
              </button>
            )}
          </div>
        </form>
      ) : null}
    </section>
  );
}
