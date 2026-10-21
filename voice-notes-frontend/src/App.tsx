// src/App.tsx
import { useCallback, useEffect, useState } from "react";
import { AUTH_EXPIRED, useDictation } from "./hooks/useDictation";
import { AuthExpired, authFetch, getToken, setToken } from "./auth";
import Login from "./Login";

type Language = "en" | "hi" | "hinglish";

type Note = {
  id: string;
  title: string | null;
  summary: string | null;
  key_points: string[] | null;
  language: string | null;
  raw_transcript: string;
  created_at: string;
};

export default function App() {
  const [token, setTokenState] = useState<string | null>(getToken);

  const logout = useCallback(() => {
    setToken(null);
    setTokenState(null);
  }, []);

  if (!token) return <Login onLogin={setTokenState} />;
  return <Workspace token={token} onLogout={logout} />;
}

function Workspace({ token, onLogout }: { token: string; onLogout: () => void }) {
  const [language, setLanguage] = useState<Language>("en");
  const { liveText, segments, status, error, start, stop } = useDictation(language, token);
  const [notes, setNotes] = useState<Note[]>([]);
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

  const loadNotes = useCallback(async () => {
    try {
      const res = await authFetch("/notes");
      if (!res.ok) throw new Error(`GET /notes ${res.status}`);
      setNotes(await res.json());
    } catch (e) {
      fail(e);
    }
  }, [fail]);

  useEffect(() => { void loadNotes(); }, [loadNotes]);

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
      if (!res.ok) throw new Error(`POST /notes ${res.status}`);
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
      else setApiError(`DELETE /notes/${id} ${res.status}`);
    } catch (e) {
      fail(e);
    }
  };

  return (
    <div style={{ maxWidth: 600, margin: "40px auto", fontFamily: "sans-serif" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
        <div>
          <select value={language} onChange={(e) => setLanguage(e.target.value as Language)} disabled={status !== "idle"}>
            <option value="en">English</option>
            <option value="hi">Hindi</option>
            <option value="hinglish">Hinglish</option>
          </select>{" "}
          <button onClick={status === "idle" ? () => start() : handleStop} disabled={status === "finishing" || saving}>
            {status === "idle" ? "Start" : status === "finishing" ? "Finishing…" : "Stop"}
          </button>
        </div>
        <button onClick={onLogout} disabled={status !== "idle"}>Log out</button>
      </div>

      {error && error !== AUTH_EXPIRED && <p style={{ color: "crimson" }}>{error}</p>}
      {apiError && <p style={{ color: "crimson" }}>{apiError}</p>}

      <p style={{ minHeight: 100, whiteSpace: "pre-wrap" }}>
        {segments.join(" ")} <span style={{ opacity: 0.5 }}>{liveText}</span>
      </p>

      {saving && <p>Summarizing & saving…</p>}

      <h2 style={{ fontSize: 18, marginTop: 32 }}>Notes</h2>
      {notes.length === 0 && !saving && <p style={{ opacity: 0.6 }}>No notes yet.</p>}
      {notes.map((n) => (
        <article key={n.id} style={{ borderTop: "1px solid #ddd", padding: "12px 0" }}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
            <h3 style={{ margin: 0 }}>{n.title ?? "(untitled)"}</h3>
            <button onClick={() => handleDelete(n.id)} aria-label="Delete note">✕</button>
          </div>
          <small style={{ opacity: 0.6 }}>
            {new Date(n.created_at).toLocaleString()} · {n.language}
          </small>
          <p>{n.summary}</p>
          {n.key_points && <ul>{n.key_points.map((p, i) => <li key={i}>{p}</li>)}</ul>}
        </article>
      ))}
    </div>
  );
}
