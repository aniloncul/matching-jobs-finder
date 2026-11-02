import { useCallback, useEffect, useState } from "react";
import type { ChangeEvent } from "react";
import {
  getSettings,
  saveLlmSettings,
  saveSinkSettings,
  saveSourceKeys,
  testLlmSettings,
  testSinkSettings,
} from "../api";
import type { SettingsSummary } from "../types";

const PROVIDERS = ["auto", "openai", "ollama", "anthropic", "gemini"] as const;
const SINK_MODES = ["auto", "local", "google"] as const;

interface Feedback {
  key: string;
  ok: boolean;
  text: string;
}

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/**
 * Setup: the LLM endpoint, API key, and the Google Sheet belong to whoever
 * runs the app locally.  Everything is saved to config/settings.local.json
 * through the API - never into git, never echoed back raw.
 */
export function SettingsPanel() {
  const [summary, setSummary] = useState<SettingsSummary | null>(null);

  const [provider, setProvider] = useState<string>("auto");
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [allowRemote, setAllowRemote] = useState(false);

  const [mode, setMode] = useState<string>("auto");
  const [sheetUrl, setSheetUrl] = useState("");
  const [serviceJson, setServiceJson] = useState("");

  const [baKey, setBaKey] = useState("");

  const [busy, setBusy] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<Feedback | null>(null);

  const load = useCallback(async () => {
    try {
      const next = await getSettings();
      setSummary(next);
      setProvider(next.llm.provider);
      setBaseUrl(next.llm.base_url);
      setModel(next.llm.model);
      setAllowRemote(next.llm.allow_remote);
      setMode(next.sink.mode);
      setSheetUrl(next.sink.sheet_url ?? "");
    } catch (error) {
      setFeedback({ key: "load", ok: false, text: message(error) });
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function run(key: string, action: () => Promise<unknown>, render: (value: any) => Feedback) {
    setBusy(key);
    setFeedback(null);
    try {
      const value = await action();
      if (key !== "test-llm" && key !== "test-sink" && value && typeof value === "object") {
        const next = value as SettingsSummary;
        if (next.llm && next.sink) setSummary(next);
      }
      setFeedback(render(value));
    } catch (error) {
      setFeedback({ key, ok: false, text: message(error) });
    } finally {
      setBusy(null);
    }
  }

  function onServiceFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    void file.text().then((text) => {
      setServiceJson(text);
      setFeedback({ key: "file", ok: true, text: `${file.name} loaded - press Save below.` });
    });
  }

  return (
    <section className="profile-panel" aria-labelledby="setup-title">
      <div className="profile-heading">
        <div>
          <h2 id="setup-title" className="profile-title">SETUP</h2>
          <p className="profile-subtitle">
            This app runs entirely on your machine. Connect your own model
            endpoint and your own Google Sheet - keys are stored locally in
            config/settings.local.json and never leave this computer.
          </p>
        </div>
      </div>

      <div className="setup-grid">
        <article className="setup-card">
          <h3>
            MODEL&nbsp;
            {summary?.llm.has_key ? (
              <span className="setup-chip" data-ok="true">● key set</span>
            ) : (
              <span className="setup-chip" data-ok="false">○ no key</span>
            )}
          </h3>
          <label className="setup-field">
            <span>Provider</span>
            <select value={provider} onChange={(e) => setProvider(e.target.value)}>
              {PROVIDERS.map((item) => (
                <option key={item} value={item}>{item}</option>
              ))}
            </select>
          </label>
          <label className="setup-field">
            <span>Base URL</span>
            <input
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              placeholder="https://api.openai.com/v1 or http://localhost:11434/v1"
            />
          </label>
          <label className="setup-field">
            <span>Model</span>
            <input value={model} onChange={(e) => setModel(e.target.value)} placeholder="gpt-4o-mini, qwen2.5:7b, …" />
          </label>
          <label className="setup-field">
            <span>API key {summary?.llm.key_hint ? `(stored: ${summary.llm.key_hint})` : ""}</span>
            <input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={summary?.llm.has_key ? "kept - type a new key to replace" : "sk-…"}
              autoComplete="off"
            />
          </label>
          {baseUrl && !baseUrl.includes("localhost") && !baseUrl.includes("127.0.0.1") ? (
            <label className="setup-check">
              <input type="checkbox" checked={allowRemote} onChange={(e) => setAllowRemote(e.target.checked)} />
              Allow remote (non-local) endpoints for local-style providers
            </label>
          ) : null}
          <div className="setup-actions">
            <button
              className="button"
              type="button"
              disabled={busy !== null}
              onClick={() =>
                void run(
                  "save-llm",
                  () =>
                    saveLlmSettings({
                      provider,
                      baseUrl,
                      model,
                      // Only send the key when the user typed one.
                      ...(apiKey.trim() ? { apiKey: apiKey.trim() } : {}),
                      allowRemote,
                    }),
                  () => {
                    setApiKey("");
                    return { key: "save-llm", ok: true, text: "Model settings saved." };
                  },
                )
              }
            >
              Save
            </button>
            <button
              className="button"
              type="button"
              disabled={busy !== null}
              onClick={() =>
                void run("test-llm", () => testLlmSettings(), (r: any) => ({
                  key: "test-llm",
                  ok: Boolean(r.ok),
                  text: r.ok ? `Endpoint answered: “${r.reply || "(empty)"}”` : r.error || "Test failed",
                }))
              }
            >
              Test
            </button>
            {summary?.llm.has_key ? (
              <button
                className="button"
                type="button"
                disabled={busy !== null}
                onClick={() =>
                  void run(
                    "clear-key",
                    () => saveLlmSettings({ apiKey: "" }),
                    () => ({ key: "clear-key", ok: true, text: "API key cleared." }),
                  )
                }
              >
                Clear key
              </button>
            ) : null}
          </div>
        </article>

        <article className="setup-card">
          <h3>
            GOOGLE SHEET&nbsp;
            {summary?.sink.has_service_account && summary?.sink.sheet_id ? (
              <span className="setup-chip" data-ok="true">● connected</span>
            ) : (
              <span className="setup-chip" data-ok="false">○ local CSV</span>
            )}
          </h3>
          <label className="setup-field">
            <span>Where jobs are stored</span>
            <select value={mode} onChange={(e) => setMode(e.target.value)}>
              {SINK_MODES.map((item) => (
                <option key={item} value={item}>
                  {item === "auto"
                    ? "auto (Google if set up, else local CSV)"
                    : item === "google"
                      ? "google (your shared sheet)"
                      : "local (CSV files in data/)"}
                </option>
              ))}
            </select>
          </label>
          <label className="setup-field">
            <span>Your sheet link</span>
            <input
              value={sheetUrl}
              onChange={(e) => setSheetUrl(e.target.value)}
              placeholder="https://docs.google.com/spreadsheets/d/…"
            />
          </label>
          <label className="setup-field">
            <span>Service-account JSON (from Google Cloud)</span>
            <input type="file" accept=".json,application/json" onChange={onServiceFile} />
            <textarea
              rows={4}
              value={serviceJson}
              onChange={(e) => setServiceJson(e.target.value)}
              placeholder='Or paste the whole file: {"client_email": "…", "private_key": "…", …}'
              spellCheck={false}
            />
          </label>
          {summary?.sink.service_account_email ? (
            <p className="setup-hint">
              Share your sheet as <strong>Editor</strong> with:{" "}
              <code>{summary.sink.service_account_email}</code>
            </p>
          ) : (
            <p className="setup-hint">
              Create a service account in Google Cloud, download its JSON key, paste it here,
              then share your sheet with the e-mail shown after saving.
            </p>
          )}
          <div className="setup-actions">
            <button
              className="button"
              type="button"
              disabled={busy !== null}
              onClick={() =>
                void run(
                  "save-sink",
                  () =>
                    saveSinkSettings({
                      mode,
                      sheetUrl,
                      ...(serviceJson.trim() ? { serviceAccountJson: serviceJson.trim() } : {}),
                    }),
                  () => {
                    setServiceJson("");
                    return { key: "save-sink", ok: true, text: "Sheet settings saved." };
                  },
                )
              }
            >
              Save
            </button>
            <button
              className="button"
              type="button"
              disabled={busy !== null}
              onClick={() =>
                void run("test-sink", () => testSinkSettings(), (r: any) => ({
                  key: "test-sink",
                  ok: Boolean(r.ok),
                  text: r.ok
                    ? `Sink “${r.kind}” opened · ${r.jobs_seen} job(s) read${r.notes?.length ? ` · ${r.notes.join("; ")}` : ""}`
                    : r.error || "Test failed",
                }))
              }
            >
              Test
            </button>
          </div>
        </article>

        <article className="setup-card">
          <h3>
            OPTIONAL KEYS&nbsp;
            {summary?.sources.ba_has_key ? (
              <span className="setup-chip" data-ok="true">● Browse AI set</span>
            ) : (
              <span className="setup-chip" data-ok="false">○ Browse AI not set</span>
            )}
          </h3>
          <label className="setup-field">
            <span>Browse AI API key (optional - keyless boards work without it)</span>
            <input
              type="password"
              value={baKey}
              onChange={(e) => setBaKey(e.target.value)}
              placeholder={summary?.sources.ba_has_key ? "kept - type a new key to replace" : "paste key"}
              autoComplete="off"
            />
          </label>
          <div className="setup-actions">
            <button
              className="button"
              type="button"
              disabled={busy !== null || !baKey.trim()}
              onClick={() =>
                void run(
                  "save-ba",
                  () => saveSourceKeys(baKey.trim()),
                  () => {
                    setBaKey("");
                    return { key: "save-ba", ok: true, text: "Source key saved." };
                  },
                )
              }
            >
              Save
            </button>
          </div>
        </article>
      </div>

      {feedback ? (
        <p className="setup-result" data-ok={feedback.ok ? "true" : "false"} role="status">
          {feedback.text}
        </p>
      ) : null}
    </section>
  );
}
