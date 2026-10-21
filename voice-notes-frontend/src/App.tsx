// src/App.tsx
import { useCallback, useEffect, useState } from "react";
import { AUTH_EXPIRED, useDictation } from "./hooks/useDictation";
import { AuthExpired, authFetch, logout as endSession, restoreSession } from "./auth";
import Brand from "./Brand";
import Login from "./Login";
import { ForgotPassword, ResetPassword } from "./PasswordReset";
import "./App.css";

type Language = "en" | "hi" | "hinglish";

const LANGUAGE_LABEL: Record<string, string> = { en: "English", hi: "Hindi", hinglish: "Hinglish" };

type Note = {
  id: string;
  title: string | null;
  summary: string | null;
  key_points: string[] | null;
  language: string | null;
  raw_transcript: string;
  created_at: string;
};

type Session = "restoring" | "out" | "forgot" | "in";

// The only deep link the app has is the emailed reset link, so a full router
// would be overkill: read the path once at startup.
function resetTokenFromUrl(): string | null {
  if (window.location.pathname !== "/reset-password") return null;
  return new URLSearchParams(window.location.search).get("token") ?? "";
}

export default function App() {
  const [resetToken, setResetToken] = useState<string | null>(resetTokenFromUrl);
  // On load, a stored refresh token is traded for a live access token before
  // anything renders, so a reload never flashes the login screen.
  const [session, setSession] = useState<Session>("restoring");

  useEffect(() => {
    restoreSession().then((ok) => setSession(ok ? "in" : "out"));
  }, []);

  const logout = useCallback(() => {
    void endSession();
    setSession("out");
  }, []);

  const leaveReset = useCallback(() => {
    window.history.replaceState(null, "", "/");
    setResetToken(null);
    setSession("out");
  }, []);

  if (resetToken !== null) return <ResetPassword token={resetToken} onDone={leaveReset} />;
  if (session === "restoring") return null;
  if (session === "forgot") return <ForgotPassword onBack={() => setSession("out")} />;
  if (session === "out") return <Login onLogin={() => setSession("in")} onForgot={() => setSession("forgot")} />;
  return <Workspace onLogout={logout} />;
}

function Workspace({ onLogout }: { onLogout: () => void }) {
  const [language, setLanguage] = useState<Language>("en");
  const { liveText, segments, status, error, start, stop } = useDictation(language);
  const [notes, setNotes] = useState<Note[]>([]);
  const [email, setEmail] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [apiError, setApiError] = useState<string | null>(null);

  // Any 401 from the API means the token is stale: drop it and show the login screen.
  const fail = useCallback(
    (e: unknown) => {
      if (e instanceof AuthExpired) onLogout();
      else setApiError(e instanceof Error ? e.message : String(e));
    },
    [onLogout],
  );

  const load = useCallback(async () => {
    try {
      const [notesRes, meRes] = await Promise.all([authFetch("/notes"), authFetch("/auth/me")]);
      if (!notesRes.ok) throw new Error(`Could not load notes (${notesRes.status})`);
      setNotes(await notesRes.json());
      if (meRes.ok) setEmail((await meRes.json()).email);
    } catch (e) {
      fail(e);
    }
  }, [fail]);

  useEffect(() => { void load(); }, [load]);

  // The WebSocket reports an expired token through the hook's error string.
  useEffect(() => {
    if (error === AUTH_EXPIRED) onLogout();
  }, [error, onLogout]);

  const handleStop = async () => {
    const finalSegments = await stop(); // resolves after the server flushes the last sentence
    const transcript = finalSegments.join(" ").trim();
    if (!transcript) return;

    setSaving(true);
    setApiError(null);
    try {
      const res = await authFetch("/notes", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ transcript, language }),
      });
      if (!res.ok) throw new Error(`Could not save the note (${res.status})`);
      const note: Note = await res.json();
      setNotes((prev) => [note, ...prev]);
    } catch (e) {
      fail(e);
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: string) => {
    try {
      const res = await authFetch(`/notes/${id}`, { method: "DELETE" });
      if (res.ok) setNotes((prev) => prev.filter((n) => n.id !== id));
      else setApiError(`Could not delete the note (${res.status})`);
    } catch (e) {
      fail(e);
    }
  };

  const recording = status === "recording";
  const busy = status === "finishing" || saving;
  const hasText = segments.length > 0 || liveText.length > 0;

  return (
    <div className="app">
      <header className="topbar">
        <Brand />
        <div className="topbar-right">
          {email && <span className="user-email" title={email}>{email}</span>}
          <button className="btn btn-ghost" onClick={onLogout} disabled={status !== "idle"}>Log out</button>
        </div>
      </header>

      <section className="card">
        <div className="controls">
          <select
            className="select"
            aria-label="Language"
            value={language}
            onChange={(e) => setLanguage(e.target.value as Language)}
            disabled={status !== "idle"}
          >
            <option value="en">English</option>
            <option value="hi">Hindi</option>
            <option value="hinglish">Hinglish</option>
          </select>

          <button
            className={`btn btn-primary${recording ? " btn-recording" : ""}`}
            onClick={status === "idle" ? () => start() : handleStop}
            disabled={busy}
          >
            {recording && <span className="rec-dot" aria-hidden="true" />}
            {status === "idle" ? "Start recording" : status === "finishing" ? "Finishing…" : "Stop"}
          </button>

          <span className="hint">
            {status === "idle"
              ? "Press Start, speak, then Stop. The note is summarized and saved automatically."
              : recording
                ? "Listening…"
                : "Waiting for the last sentence…"}
          </span>
        </div>

        <div className={`transcript${hasText ? "" : " is-empty"}`} aria-live="polite">
          {hasText ? (
            <>
              {segments.join(" ")}
              {liveText && <span className="live"> {liveText}</span>}
            </>
          ) : (
            "Your words will appear here."
          )}
        </div>

        {error && error !== AUTH_EXPIRED && <p className="alert alert-error" role="alert">{error}</p>}
        {apiError && <p className="alert alert-error" role="alert">{apiError}</p>}
        {saving && <p className="alert alert-info">Summarizing and saving…</p>}
      </section>

      <section className="notes">
        <div className="notes-header">
          <h2>Notes</h2>
          {notes.length > 0 && <span className="notes-count">{notes.length}</span>}
        </div>

        {notes.length === 0 && !saving && <p className="empty">No notes yet. Your first recording will show up here.</p>}

        {notes.map((n) => (
          <article key={n.id} className="note">
            <div className="note-head">
              <div>
                <h3>{n.title ?? "Untitled note"}</h3>
                <small className="note-meta">
                  {new Date(n.created_at).toLocaleString()} · {LANGUAGE_LABEL[n.language ?? ""] ?? n.language}
                </small>
              </div>
              <button className="icon-btn" onClick={() => handleDelete(n.id)} aria-label="Delete note" title="Delete">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
                  <path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3" />
                </svg>
              </button>
            </div>
            {n.summary && <p className="note-summary">{n.summary}</p>}
            {n.key_points && n.key_points.length > 0 && (
              <ul className="note-points">{n.key_points.map((p, i) => <li key={i}>{p}</li>)}</ul>
            )}
            <details className="note-raw">
              <summary>Transcript</summary>
              <p>{n.raw_transcript}</p>
            </details>
          </article>
        ))}
      </section>
    </div>
  );
}
