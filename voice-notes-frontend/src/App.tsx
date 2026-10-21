// src/App.tsx
import { useCallback, useEffect, useRef, useState } from "react";
import { AUTH_EXPIRED, useDictation } from "./hooks/useDictation";
import { AuthExpired, authFetch, logout as endSession, restoreSession } from "./auth";
import { hideWindow, isDesktop, microphoneName, onHotkey, pasteIntoFocusedApp } from "./desktop";
import Brand from "./Brand";
import Login from "./Login";
import { ForgotPassword, ResetPassword } from "./PasswordReset";
import "./App.css";

type Language = "en" | "hi" | "hinglish";
type Mode = "notes" | "type";

const LANGUAGE_LABEL: Record<string, string> = { en: "English", hi: "Hindi", hinglish: "Hinglish" };
const MODE_KEY = "voice-notes-mode";
const SAVE_TYPED_KEY = "voice-notes-save-typed";

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

function readPref<T extends string>(key: string, fallback: T): T {
  try {
    return (localStorage.getItem(key) as T) || fallback;
  } catch {
    return fallback;
  }
}

function writePref(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* storage blocked */
  }
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
  const desktop = isDesktop();
  const [language, setLanguage] = useState<Language>(() => readPref("voice-notes-lang", "en"));
  const [mode, setMode] = useState<Mode>(() => (desktop ? readPref(MODE_KEY, "notes") : "notes"));
  const [saveTyped, setSaveTyped] = useState<boolean>(() => readPref<string>(SAVE_TYPED_KEY, "no") === "yes");
  const { liveText, segments, status, error, start, stop } = useDictation(language);
  const [notes, setNotes] = useState<Note[]>([]);
  const [email, setEmail] = useState<string | null>(null);
  const [micName, setMicName] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [apiError, setApiError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const chooseLanguage = (l: Language) => { setLanguage(l); writePref("voice-notes-lang", l); };
  const chooseMode = (m: Mode) => { setMode(m); writePref(MODE_KEY, m); };
  const chooseSaveTyped = (v: boolean) => { setSaveTyped(v); writePref(SAVE_TYPED_KEY, v ? "yes" : "no"); };

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

  useEffect(() => {
    if (desktop) microphoneName().then(setMicName).catch(() => setMicName(null));
  }, [desktop]);

  // The WebSocket reports an expired token through the hook's error string.
  useEffect(() => {
    if (error === AUTH_EXPIRED) onLogout();
  }, [error, onLogout]);

  const saveNote = async (transcript: string) => {
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

  const handleStop = async () => {
    const finalSegments = await stop(); // resolves after the server flushes the last sentence
    const transcript = finalSegments.join(" ").trim();
    if (!transcript) return;

    if (mode === "type") {
      // Get our window out of the way so focus returns to the user's app,
      // then paste. The hotkey path never had focus, but the button path does.
      setNotice(null);
      try {
        await hideWindow();
        await new Promise((r) => setTimeout(r, 350));
        await pasteIntoFocusedApp(transcript);
        setNotice(`Pasted ${transcript.length} characters into the active app.`);
      } catch (e) {
        setApiError(`Could not paste: ${e instanceof Error ? e.message : String(e)}`);
      }
      if (saveTyped) await saveNote(transcript);
      return;
    }
    await saveNote(transcript);
  };

  // Global hotkey: start if idle, stop if recording. Keep the latest handlers
  // in a ref so the one-time listener never calls a stale closure.
  const toggleRef = useRef<() => void>(() => {});
  useEffect(() => {
    toggleRef.current = () => {
      if (status === "idle") void start();
      else if (status === "recording") void handleStop();
    };
  });
  useEffect(() => {
    if (!desktop) return;
    let unlisten: (() => void) | undefined;
    onHotkey(() => toggleRef.current()).then((fn) => { unlisten = fn; });
    return () => unlisten?.();
  }, [desktop]);

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

  const hint =
    status === "recording"
      ? "Listening…"
      : status === "finishing"
        ? "Waiting for the last sentence…"
        : mode === "type"
          ? "Put the cursor where you want the text, press Ctrl+Shift+M, speak, press Ctrl+Shift+M again."
          : desktop
            ? "Press Start or Ctrl+Shift+M, speak, then Stop. The note is summarized and saved."
            : "Press Start, speak, then Stop. The note is summarized and saved automatically.";

  return (
    <div className="app">
      <header className="topbar">
        <Brand />
        <div className="topbar-right">
          {email && <span className="user-email" title={email}>{email}</span>}
          <button className="btn btn-ghost" onClick={onLogout} disabled={status !== "idle"}>Log out</button>
        </div>
      </header>

      {desktop && (
        <div className="mode-switch" role="tablist" aria-label="Mode">
          <button role="tab" aria-selected={mode === "notes"} className={`mode${mode === "notes" ? " is-active" : ""}`}
                  onClick={() => chooseMode("notes")} disabled={status !== "idle"}>
            <strong>Notes</strong><span>Speak, get a summary saved here</span>
          </button>
          <button role="tab" aria-selected={mode === "type"} className={`mode${mode === "type" ? " is-active" : ""}`}
                  onClick={() => chooseMode("type")} disabled={status !== "idle"}>
            <strong>Type into any app</strong><span>Speak, stop, text appears where your cursor is</span>
          </button>
        </div>
      )}

      <section className="card">
        <div className="controls">
          <select
            className="select"
            aria-label="Language"
            value={language}
            onChange={(e) => chooseLanguage(e.target.value as Language)}
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

          {mode === "type" && (
            <label className="check">
              <input type="checkbox" checked={saveTyped} onChange={(e) => chooseSaveTyped(e.target.checked)} />
              Also save as a note
            </label>
          )}

          <span className="hint">{hint}</span>
          {desktop && micName && <span className="hint">Microphone: {micName}</span>}
          {desktop && micName === null && <span className="hint">No microphone detected.</span>}
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
        {notice && <p className="alert alert-info" role="status">{notice}</p>}
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
